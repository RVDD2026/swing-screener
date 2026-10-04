# Swing-screener S&P 500

Dagelijkse filter van alle S&P 500-aandelen voor swingtrades (3–15 handelsdagen), long én short.

**Hoe het werkt**

1. GitHub draait elke nacht (di t/m za, 05:00 UTC) `screener.py` op de slotkoersen van de vorige handelsdag.
2. Het script rekent per aandeel trend (20/50/200 SMA), momentum, risico en winstpotentieel uit,
   sluit aandelen met kwartaalcijfers binnen ~15 handelsdagen uit en zet de 8 beste longs en 8 beste shorts in `results/latest.json`.
3. Claude leest dat bestand elke werkdag om ±15:00 (NL-tijd), doet nieuws- en cijferonderzoek op die kandidaten
   en stuurt de top 5 met scorekaart, scenario's, instap, stop en doel.

**Score (0–100)** = momentum 35% + winstpotentieel 35% + laag risico 30%.

| Onderdeel | Waar het naar kijkt |
|---|---|
| Momentum | sterkte t.o.v. de S&P 500 (20 dagen), helling 20 en 200 SMA, oplopend volume |
| Winstpotentieel | winst/verlies-verhouding (RR) en afstand tot het doel |
| Laag risico | afstand tot de stop in ATR, beweeglijkheid van het aandeel, niet te ver van de 20 SMA |

**Regels voor een setup**

- Long: koers > 50 SMA > 200 SMA, 20 SMA > 50 SMA, 50 SMA stijgt.
- Short: koers < 50 SMA < 200 SMA, 20 SMA < 50 SMA, 50 SMA daalt.
- Stop: achter de 20 SMA of de laatste 10-daagse bodem/top, plus een kwart ATR marge.
- Doel: de top/bodem van de laatste 60 dagen, of bij een uitbraak 3 ATR.
- Minimaal: koers $10, dagomzet $20 mln, RR 1,5.

Instellingen staan bovenaan `screener.py`.

**Handmatig starten:** tabblad *Actions* → *Swing screener* → *Run workflow*.
