"""
Swing-screener S&P 500 (long + short)
-------------------------------------
Draait dagelijks via GitHub Actions. Haalt koersdata op voor alle S&P 500-aandelen,
berekent trend, momentum, risico en winstpotentieel, sluit aandelen met kwartaalcijfers
binnen de swinghorizon uit en schrijft de beste kandidaten weg naar:

    results/latest.json      <- dit leest Claude elke werkdag om 15:00
    results/latest.csv
    results/prijzen.json     <- dagkoersen (60 dagen) van alle aandelen, voor het logboek
    results/archief/JJJJ-MM-DD.csv

Horizon: 3-15 handelsdagen.
"""

import datetime as dt
import json
import os
import time
from io import StringIO

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- instellingen
TOP_N = 10                     # aantal kandidaten per richting (long en short)
EARNINGS_CHECK_N = 30          # zoveel beste per richting worden op earnings gecheckt
EARNINGS_WINDOW_DAYS = 21      # ~15 handelsdagen
MIN_PRICE = 10.0
MIN_DOLLAR_VOL = 20_000_000    # gemiddelde dagomzet in dollars (20 dagen)
MIN_RR = 1.5                   # minimale winst/verlies-verhouding
RISK_ATR_MIN, RISK_ATR_MAX = 0.75, 3.5
OUT_DIR = "results"


# ---------------------------------------------------------------- data ophalen
def get_sp500() -> pd.DataFrame:
    import requests
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    html = requests.get(url, headers={"User-Agent": "Mozilla/5.0 swing-screener"}, timeout=30).text
    table = pd.read_html(StringIO(html), attrs={"id": "constituents"})[0]
    table["Symbol"] = table["Symbol"].astype(str).str.replace(".", "-", regex=False)
    return table[["Symbol", "Security", "GICS Sector"]].rename(
        columns={"Symbol": "ticker", "Security": "naam", "GICS Sector": "sector"}
    )


def download(tickers):
    import yfinance as yf
    data = yf.download(
        tickers, period="14mo", interval="1d", auto_adjust=True,
        group_by="ticker", threads=True, progress=False,
    )
    frames = {}
    available = set(data.columns.get_level_values(0))
    for t in tickers:
        if t in available:
            frames[t] = data[t].dropna(subset=["Close"])
    return frames


def next_earnings(ticker):
    """Geeft de eerstvolgende earningsdatum (date) of None als onbekend."""
    import yfinance as yf
    try:
        cal = yf.Ticker(ticker).calendar
        dates = None
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date")
        elif isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
            dates = list(cal.loc["Earnings Date"].values)
        if not dates:
            return None
        if not isinstance(dates, (list, tuple, np.ndarray)):
            dates = [dates]
        parsed = sorted(pd.to_datetime(d).date() for d in dates if d is not None)
        today = dt.date.today()
        future = [d for d in parsed if d >= today]
        return future[0] if future else None
    except Exception:
        return None


# ---------------------------------------------------------------- analyse
def roc(close: pd.Series, n=20) -> float:
    return float(close.iloc[-1] / close.iloc[-(n + 1)] - 1)


def analyse(df: pd.DataFrame, spy_roc20: float):
    """Analyseert één aandeel. Geeft een lijst met 0, 1 setup-dicts terug."""
    if df is None or len(df) < 210:
        return []
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    close = float(c.iloc[-1])

    sma20, sma50, sma200 = c.rolling(20).mean(), c.rolling(50).mean(), c.rolling(200).mean()
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    if not np.isfinite(atr) or atr <= 0:
        return []

    dollar_vol = float((c * v).rolling(20).mean().iloc[-1])
    if close < MIN_PRICE or dollar_vol < MIN_DOLLAR_VOL:
        return []

    s20, s50, s200 = float(sma20.iloc[-1]), float(sma50.iloc[-1]), float(sma200.iloc[-1])
    s20_slope = (s20 - float(sma20.iloc[-6])) / atr
    s50_slope = (s50 - float(sma50.iloc[-11])) / atr
    s200_slope = (s200 - float(sma200.iloc[-21])) / atr
    roc20 = roc(c, 20)
    rs20 = roc20 - spy_roc20
    v50 = float(v.iloc[-50:].mean())
    vol_ratio = float(v.iloc[-5:].mean() / v50) if v50 > 0 else 1.0
    ext = (close - s20) / atr
    hi60, lo60 = float(h.iloc[-60:].max()), float(l.iloc[-60:].min())
    hi10, lo10 = float(h.iloc[-10:].max()), float(l.iloc[-10:].min())

    common = dict(
        koers=round(close, 2), sma20=round(s20, 2), sma50=round(s50, 2), sma200=round(s200, 2),
        atr=round(atr, 2), atr_pct=round(atr / close * 100, 2),
        roc20_pct=round(roc20 * 100, 2), rs20_pct=round(rs20 * 100, 2),
        sma20_helling=round(s20_slope, 2), sma50_helling=round(s50_slope, 2),
        sma200_helling=round(s200_slope, 2), volume_ratio=round(vol_ratio, 2),
        afstand_sma20_atr=round(ext, 2), dagomzet_mln=round(dollar_vol / 1e6, 1),
    )

    setups = []
    # LONG: koers > 50 > 200, 20 > 50, 50 SMA stijgt, niet ver onder de 20 SMA
    if close > s50 > s200 and s20 > s50 and s50_slope > 0 and ext > -0.5:
        stop = min(lo10, s20) - 0.25 * atr
        risk = close - stop
        if hi60 - close > atr:
            target, doeltype = hi60, "top 60 dagen"
        else:
            target, doeltype = close + 3 * atr, "uitbraak (+3 ATR)"
        setups.append(("LONG", stop, target, risk, target - close, doeltype))

    # SHORT: koers < 50 < 200, 20 < 50, 50 SMA daalt, niet ver boven de 20 SMA
    if close < s50 < s200 and s20 < s50 and s50_slope < 0 and ext < 0.5:
        stop = max(hi10, s20) + 0.25 * atr
        risk = stop - close
        if close - lo60 > atr:
            target, doeltype = lo60, "bodem 60 dagen"
        else:
            target, doeltype = close - 3 * atr, "uitbraak (-3 ATR)"
        setups.append(("SHORT", stop, target, risk, close - target, doeltype))

    out = []
    for richting, stop, target, risk, reward, doeltype in setups:
        if risk <= 0:
            continue
        risk_atr = risk / atr
        rr = reward / risk
        if not (RISK_ATR_MIN <= risk_atr <= RISK_ATR_MAX) or rr < MIN_RR:
            continue
        out.append(dict(
            richting=richting, stop=round(stop, 2), doel=round(target, 2), doeltype=doeltype,
            risico_pct=round(risk / close * 100, 2), winst_pct=round(reward / close * 100, 2),
            rr=round(rr, 2), risico_atr=round(risk_atr, 2), **common,
        ))
    return out


def pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True, method="average").fillna(0.5)


def score(group: pd.DataFrame) -> pd.DataFrame:
    """Score 0-100: momentum 35%, winstpotentieel 35%, laag risico 30%."""
    if group.empty:
        return group
    g = group.copy()
    sign = 1 if g["richting"].iloc[0] == "LONG" else -1
    momentum = (
        pct_rank(sign * g["rs20_pct"]) * 0.4
        + pct_rank(sign * g["sma20_helling"]) * 0.3
        + pct_rank(sign * g["sma200_helling"]) * 0.15
        + pct_rank(g["volume_ratio"]) * 0.15
    )
    potentieel = pct_rank(g["rr"].clip(upper=4)) * 0.6 + pct_rank(g["winst_pct"]) * 0.4
    laag_risico = (
        pct_rank(-g["risico_atr"]) * 0.4
        + pct_rank(-g["atr_pct"]) * 0.3
        + pct_rank(-g["afstand_sma20_atr"].abs()) * 0.3
    )
    g["score_momentum"] = (momentum * 100).round(1)
    g["score_potentieel"] = (potentieel * 100).round(1)
    g["score_risico"] = (laag_risico * 100).round(1)
    g["score"] = (0.35 * g["score_momentum"] + 0.35 * g["score_potentieel"] + 0.30 * g["score_risico"]).round(1)
    return g.sort_values("score", ascending=False)


def screen(frames: dict, meta: pd.DataFrame, spy: pd.DataFrame, earnings_fn=next_earnings):
    spy_c = spy["Close"]
    spy_roc20 = roc(spy_c, 20)
    rows = []
    for t, df in frames.items():
        try:
            for s in analyse(df, spy_roc20):
                rows.append(dict(ticker=t, **s))
        except Exception as e:  # één kapot aandeel mag de run niet stoppen
            print(f"  ! {t}: {e}")
    all_df = pd.DataFrame(rows)
    if not all_df.empty:
        all_df = all_df.merge(meta, on="ticker", how="left")

    result, excluded = {}, []
    today = dt.date.today()
    for richting in ("LONG", "SHORT"):
        g = score(all_df[all_df["richting"] == richting]) if not all_df.empty else pd.DataFrame()
        picks = []
        for _, r in g.head(EARNINGS_CHECK_N).iterrows():
            ed = earnings_fn(r["ticker"])
            if ed is not None and (ed - today).days <= EARNINGS_WINDOW_DAYS:
                excluded.append({"ticker": r["ticker"], "richting": richting, "earnings": ed.isoformat()})
                continue
            rec = r.to_dict()
            rec["earnings"] = ed.isoformat() if ed else "onbekend"
            picks.append(rec)
            if len(picks) >= TOP_N:
                break
            time.sleep(0.2)
        result[richting] = picks

    spy_close = float(spy_c.iloc[-1])
    spy200 = float(spy_c.rolling(200).mean().iloc[-1])
    spy50 = float(spy_c.rolling(50).mean().iloc[-1])
    markt = dict(
        spy_koers=round(spy_close, 2), spy_sma50=round(spy50, 2), spy_sma200=round(spy200, 2),
        spy_roc20_pct=round(spy_roc20 * 100, 2),
        regime="bullish" if spy_close > spy50 > spy200 else "bearish" if spy_close < spy50 < spy200 else "gemengd",
    )
    return dict(
        datum_slotkoers=str(spy.index[-1].date()),
        gegenereerd_utc=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M"),
        markt=markt,
        aantal_gescand=len(frames),
        aantal_setups=dict(LONG=int((all_df["richting"] == "LONG").sum()) if not all_df.empty else 0,
                           SHORT=int((all_df["richting"] == "SHORT").sum()) if not all_df.empty else 0),
        uitgesloten_earnings=excluded,
        long=result["LONG"],
        short=result["SHORT"],
    )


def price_history(frames: dict, days: int = 60) -> dict:
    """Compacte dagkoersen (datum, high, low, close) per ticker, om picks na te rekenen."""
    out = {}
    for t, df in frames.items():
        tail = df.iloc[-days:]
        out[t] = [[str(i.date()), round(float(r.High), 2), round(float(r.Low), 2), round(float(r.Close), 2)]
                  for i, r in tail.iterrows()]
    return out


def write_output(res: dict, prices: dict | None = None):
    os.makedirs(os.path.join(OUT_DIR, "archief"), exist_ok=True)
    if prices is not None:
        with open(os.path.join(OUT_DIR, "prijzen.json"), "w", encoding="utf-8") as f:
            json.dump({"datum_slotkoers": res["datum_slotkoers"], "kolommen": ["datum", "high", "low", "close"],
                       "koersen": prices}, f, separators=(",", ":"))
    with open(os.path.join(OUT_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    flat = pd.DataFrame(res["long"] + res["short"])
    flat.to_csv(os.path.join(OUT_DIR, "latest.csv"), index=False)
    flat.to_csv(os.path.join(OUT_DIR, "archief", f"{res['datum_slotkoers']}.csv"), index=False)


def main():
    meta = get_sp500()
    tickers = meta["ticker"].tolist()
    print(f"S&P 500: {len(tickers)} aandelen ophalen...")
    frames = download(tickers + ["SPY"])
    spy = frames.pop("SPY")
    res = screen(frames, meta, spy)
    write_output(res, price_history({**frames, "SPY": spy}))
    print(f"Klaar: {len(res['long'])} long, {len(res['short'])} short "
          f"(slotkoers {res['datum_slotkoers']}, regime {res['markt']['regime']}).")


if __name__ == "__main__":
    main()
