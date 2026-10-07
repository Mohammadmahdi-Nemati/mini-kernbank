# Ergebnis Teil 1: gleichzeitige Überweisungen

8 parallele Verbindungen buchen je 250 zufällige Überweisungen zwischen 10 Konten (Startguthaben je 500,00 €). Die Geldmenge muss am Ende exakt 5.000,00 € betragen.

| Variante | gebucht | abgelehnt | Wiederholungen | Geldmenge am Ende | Konten mit falschem Stand | Summe der Abweichungen | Dauer |
|---|---|---|---|---|---|---|---|
| 1. naiv (lesen, rechnen, schreiben) | 1993 | 7 | 0 | 5.163,55 € | 10 von 10 | 2.244,75 € | 2,6 s |
| 2. naiv + SERIALIZABLE + Wiederholung | 1978 | 22 | 5463 | 5.000,00 € | 0 von 10 | 0,00 € | 6,7 s |
| 3. `bank.transfer()` mit Zeilensperren | 1972 | 28 | 0 | 5.000,00 € | 0 von 10 | 0,00 € | 1,6 s |

„Konten mit falschem Stand“: Der gespeicherte Kontostand weicht von der Summe der Buchungszeilen ab (Sicht `bank.v_reconciliation`).

Die genauen Zahlen der naiven Variante schwanken von Lauf zu Lauf, weil sie vom Zufall der zeitlichen Überschneidung abhängen. Die Varianten 2 und 3 müssen immer 0 Abweichungen zeigen.
