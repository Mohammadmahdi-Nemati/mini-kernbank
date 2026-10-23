# Echttest: mini-kernbank

- Modell: **ollama / qwen3:14b**
- Embeddings: `BAAI/bge-m3`
- Datum: 22.10.2026 21:31
- Änderungen wurden automatisch freigegeben, aber nur auf einer Kopie des Projekts.

**Ergebnis: 3 von 5 Fragen bestanden.**

## 1. Wie verhindert bank.transfer() einen Lost Update? Zeig mir die Stelle im Code.

**⚠️ nicht bestanden** · 152 s · ✔ nutzt projekt_durchsuchen oder text_suchen oder datei_lesen · ✘ zitiert `datei:zeile` · ✔ Belege echt (gelesen, existieren) · ✔ Antwort nicht leer · ✔ keine unbelegten Angaben

<details><summary>Weg des Agenten</summary>

```
projekt_durchsuchen({'frage': 'Wie verhindert bank.transfer() einen Lost Update', 'anzahl': 10})
```
</details>

**Antwort:**

> `bank.transfer()` verhindert einen Lost Update durch die Verwendung von Zeilensperren (`SELECT ... FOR UPDATE`). Diese Sperren stellen sicher, dass keine andere Transaktion die gleichen Zeilen in der gleichen Zeit ändern kann, bis die aktuelle Transaktion abgeschlossen ist.
> 
> Die Stelle im Code, an der dies erwähnt wird, ist in der Datei `sql/02_functions.sql`:
> 
> ```sql
> --   * Zeilensperren (FOR UPDATE) in fester Reihenfolge der account_id: verhindert Lost Updates
> --     und Deadlocks, wenn zwei Überweisungen dieselben Konten in umgekehrter Richtung betreffen.
> ```
> 
> Diese Zeilen erklären, dass die Zeilensperren in fester Reihenfolge der `account_id` verwendet werden, um Lost Updates und Deadlocks zu verhindern.

## 2. Wie viele Alarme gibt es pro AML-Regel aktuell in der Datenbank?

**✅ bestanden** · 16 s · ✔ nutzt datenbank_abfragen · ✔ Antwort enthält „23“ · ✔ Antwort nicht leer · ✔ keine unbelegten Angaben

<details><summary>Weg des Agenten</summary>

```
datenbank_abfragen({'sql': 'SELECT regel, COUNT(*) AS anzahl FROM aml.v_alert GROUP BY regel ORDER BY regel;'})
```
</details>

**Antwort:**

> Es gibt aktuell folgende Alarme pro AML-Regel in der Datenbank:
> 
> - **durchlauf**: 15 Alarme
> - **kreis**: 23 Alarme
> - **smurfing**: 17 Alarme

## 3. Welche Schwellenwerte nutzt die Smurfing-Regel und wo sind sie definiert?

**✅ bestanden** · 96 s · ✔ nutzt projekt_durchsuchen oder text_suchen oder datei_lesen · ✔ zitiert `datei:zeile` · ✔ Belege echt (gelesen, existieren) · ✔ Antwort nicht leer · ✔ keine unbelegten Angaben

<details><summary>Weg des Agenten</summary>

```
projekt_durchsuchen({'frage': 'Smurfing-Regel Schwellenwerte', 'anzahl': 10})
```
</details>

**Antwort:**

> Die Smurfing-Regel nutzt folgende Schwellenwerte:
> 
> 1. **`smurf_nur_privat`**: Bestimmt, ob nur Privatkunden berücksichtigt werden (Standardwert: 1).
> 2. **`smurf_nah_anteil`**: Gibt den Anteil der Beträge an, die unter der Schwelle liegen müssen (Standardwert: 1, was bedeutet, dass alle Beträge unter der Schwelle berücksichtigt werden).
> 
> Diese Schwellenwerte sind in der Tabelle `aml.param` definiert und können dort geändert werden. Sie sind in der Datei `sql/03_aml.sql` definiert, wie in `sql/03_aml.sql:1-10` gezeigt.

## 4. Ergänze im Modul-Docstring von src/kernbank/iban.py einen Satz, dass die Prüfziffer nach ISO 13616 (Modulo 97) berechnet wird.

**⚠️ nicht bestanden** · 33 s · ✘ Änderung vorgeschlagen + nach Freigabe geschrieben · ✘ Antwort nicht leer · ✔ keine unbelegten Angaben

<details><summary>Weg des Agenten</summary>

```
datei_lesen({'pfad': 'src/kernbank/iban.py', 'von_zeile': 1, 'bis_zeile': 20})
```
</details>

**Antwort:**

> 

## 5. Was ist der Unterschied zwischen SELECT FOR UPDATE und SELECT FOR NO KEY UPDATE in PostgreSQL? Recherchiere in der offiziellen Dokumentation.

**✅ bestanden** · 61 s · ✔ nutzt web_suchen oder webseite_lesen · ✔ Antwort nicht leer · ✔ keine unbelegten Angaben

<details><summary>Weg des Agenten</summary>

```
web_suchen({'anfrage': 'PostgreSQL SELECT FOR UPDATE vs FOR NO KEY UPDATE Dokumentation'})
```
</details>

**Antwort:**

> Der Unterschied zwischen `SELECT FOR UPDATE` und `SELECT FOR NO KEY UPDATE` in PostgreSQL liegt in der Art der Sperre, die auf die ausgewählten Zeilen angewendet wird:
> 
> - **`SELECT FOR UPDATE`**:
>   - Sperre alle ausgewählten Zeilen, sodass andere Transaktionen diese Zeilen nicht aktualisieren oder löschen können.
>   - Verhindert, dass andere Transaktionen Änderungen an den Zeilen vornehmen, die durch die Abfrage ausgewählt wurden.
>   - Dies kann zu höherer Blockierung führen, da andere Transaktionen nicht einmal Änderungen an nicht-schlüsselbezogenen Feldern vornehmen können.
> 
> - **`SELECT FOR NO KEY UPDATE`**:
>   - Sperre die ausgewählten Zeilen, aber nur so, dass andere Transaktionen keine Änderungen an den Schlüsselwerten (z. B. Primärschlüssel) vornehmen können.
>   - Erlaubt anderen Transaktionen, nicht-schlüsselbezogene Felder zu aktualisieren.
>   - Dies ermöglicht eine höhere Konkurrenz, da andere Transaktionen nicht vollständig blockiert werden, solange sie keine Schlüsselwerte ändern.
> 
> **Quellen**:
> - [PostgreSQL FOR UPDATE vs FOR NO KEY UPDATE - Vlad Mihalcea](https://vladmihalcea.com/postgresql-for-no-key-update/)
> - [PostgreSQL: Documentation: 16: 13.3. Explicit Locking](https://www.postgresql.org/docs/16/explicit-locking.html)
> - [PostgreSQL SELECT FOR UPDATE: Row-Level Locks Explained](https://www.cybertec-postgresql.com/en/select-for-update-considered-harmful-postgresql/)
