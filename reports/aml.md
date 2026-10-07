# Ergebnis Teil 2: Geldwäsche-Erkennung

Simulation: 340 Kunden, 24.181 Buchungen in 91 Tagen (1. April bis 30. Juni 2026). Konten mit falschem Stand nach dem Laden: 0.

## Regeln mit den Standard-Schwellenwerten

| Regel | Alarme | richtig | Fehlalarme | verpasst | Precision | Recall |
|---|---|---|---|---|---|---|
| smurfing | 17 | 9 | 8 | 3 | 53 % | 75 % |
| durchlauf | 15 | 7 | 8 | 3 | 47 % | 70 % |
| kreis | 23 | 23 | 0 | 6 | 100 % | 79 % |

Precision: Anteil der Alarme, die wirklich verdächtig sind. Recall: Anteil der verdächtigen Konten, die gefunden wurden.

## Erkennung nach Variante

| Muster | Variante | Konten | erkannt |
|---|---|---|---|
| durchlauf | offensichtlich | 7 | 7 |
| durchlauf | getarnt: leitet erst nach Tagen weiter | 2 | 0 |
| durchlauf | getarnt: behält einen Teil | 1 | 0 |
| kreis | offensichtlich | 23 | 23 |
| kreis | getarnt: lange Pausen | 6 | 0 |
| smurfing | offensichtlich | 9 | 9 |
| smurfing | getarnt: kleinere Beträge | 3 | 0 |

## Was passiert, wenn man die Schwellenwerte ändert?

| Änderung | Alarme | richtig | Fehlalarme | verpasst | Precision | Recall |
|---|---|---|---|---|---|---|
| Smurfing: nur Privatkunden | 9 | 9 | 0 | 3 | 100 % | 75 % |
| Smurfing: nur Privatkunden, schon ab 50 % der Schwelle | 12 | 12 | 0 | 0 | 100 % | 100 % |
| Smurfing: alle Kunden, schon ab 50 % der Schwelle | 33 | 12 | 21 | 0 | 36 % | 100 % |
| Durchlauf: Muster an mindestens 4 Tagen | 6 | 6 | 0 | 4 | 100 % | 60 % |
| Durchlauf: Weiterleitung innerhalb von 5 Tagen | 17 | 9 | 8 | 1 | 53 % | 90 % |
| Kreis: bis zu 7 Tage zwischen zwei Stationen | 29 | 29 | 0 | 0 | 100 % | 100 % |

## Beispiele für Alarme

| Regel | Bewertung | Kunde (erfunden) | Begründung des Alarms |
|---|---|---|---|
| durchlauf | richtig | Mia Demir | An 7 Tagen Eingänge von zusammen 110.790,00 EUR, davon 96 % innerhalb von 48 Stunden weitergeleitet |
| durchlauf | Fehlalarm | Rosa Becker | An 3 Tagen Eingänge von zusammen 19.751,97 EUR, davon 95 % innerhalb von 48 Stunden weitergeleitet |
| kreis | richtig | Ben Fischer | Kreisüberweisung über 5 Konten (206 -> 295 -> 103 -> 9 -> 151 -> 206), Startbetrag 22.500,00 EUR, Rückfluss 20.003,50 EUR nach 111 Stunden |
| smurfing | richtig | Kira Klein | 10 Bareinzahlungen knapp unter 10.000,00 EUR innerhalb von 7 Tagen (Summe 90.550,00 EUR) |
| smurfing | Fehlalarm | Autohandel Lang | 5 Bareinzahlungen knapp unter 10.000,00 EUR innerhalb von 7 Tagen (Summe 44.000,00 EUR) |
