# Entwicklungsprotokoll: Projekt-Agent

Dieses Dokument hält fest, **was gebaut wurde, warum so und wie es geprüft wurde**, damit jeder die
Schritte nachvollziehen kann. Derselbe Agent-Kern wurde auch für bakery-sales-db gebaut (dort noch nicht veröffentlicht).
Unterschiedlich ist nur `profil.yaml`.

## 1. Ausgangspunkt

Vorlage war das Tutorial „AI-Agent mit Python, RAG + LangGraph“ von **Hozhi**
([Video](https://youtu.be/yAPT4Q9vEVY), [Code](https://github.com/Hozhabr1365/Ai-Agent---RAG-LangGraph)).
Dort beantwortet ein LangGraph-Agent Fragen aus Word-Dokumenten. Er bewertet selbst, ob die gefundenen Textstellen reichen,
formuliert die Frage bei Bedarf um, gibt ehrlich zu, wenn er etwas nicht weiß, und sucht dann im Web.

**Ziel hier:** dieselbe Idee für ein *Code-Projekt*, mit fünf Anforderungen:

1. das eigene Projekt verstehen (Code, SQL, Doku)
2. Code analysieren und Fragen beantworten
3. Änderungen **nur nach Zustimmung**
4. bei Bedarf selbstständig im Internet recherchieren
5. pro Projekt separat und individuell, mit einfacher Chat-Oberfläche

## 2. Entscheidungen: Tutorial → dieser Agent

| Thema | Tutorial | Hier | Begründung |
|---|---|---|---|
| Daten | Word-Dateien, Absätze à 900 Zeichen | Code-Dateien, Zerlegung an Funktionen / SQL-Anweisungen / Überschriften | Ein Abschnitt soll eine Sinneinheit des Codes sein |
| Belege | Videotitel | `datei:zeile` | Bei Code muss man die Stelle finden können |
| Suche | nur Vektoren | Vektoren **+ BM25** + Bonus für wörtliche Treffer | Bezeichner wie `bank.transfer` findet ein Embedding schlecht |
| Ablauf | fester Graph mit 6 Knoten (abrufen → bewerten → umformulieren …) | Werkzeug-Schleife: Das Modell wählt selbst aus 10 Werkzeugen | Fragen zu Code brauchen wechselnde Schritte (suchen, lesen, SQL, Web) |
| Handeln | nur antworten | Änderungen und Befehle **mit Freigabe** (`interrupt()`) | Anforderung 3 |
| Datenbank | – | SQL-Werkzeug, technisch nur lesend | Zahlen kommen aus der Datenbank, nicht aus dem Gedächtnis des Modells |
| Modell | NVIDIA-Free-Tier, eigene Retry-Schleife | wählbar (NVIDIA, Claude, Ollama …), `with_fallbacks` | Wechsel ohne Code-Änderung |
| Gedächtnis | letzte Nachrichten im Prompt | SQLite-Checkpointer pro Chat | Freigaben überstehen eine Pause, Chats bleiben getrennt |
| Schutz | 3 Schichten gegen Prompt-Injection | übernommen + Pfad-Sandbox + READ-ONLY-SQL | Ein Agent, der schreiben kann, braucht harte Grenzen |
| Übernommen | | lokale Embeddings `bge-m3`, Chroma, Gradio, ehrliches „weiß ich nicht“, Web als Rückfallebene | hat sich im Tutorial bewährt |

## 3. Wie getestet wurde

**Automatisch (`agent/tests/`, 90 Tests, beide Projekte, Python 3.11 und 3.13).** Ohne echtes Sprachmodell:
Ein „Skript-Modell“ gibt festgelegte Antworten samt Werkzeugaufrufen zurück. So lässt sich die Mechanik exakt prüfen,
zum Beispiel „wird ohne Klick wirklich nichts geschrieben?“. Die Datenbank-Tests liefen gegen echte PostgreSQL-16-Datenbanken
mit den Projektdaten.

**Oberfläche im echten Browser (Playwright/Chromium):** Frage stellen, Werkzeugschritte prüfen, Änderungsvorschlag,
Diff, „Übernehmen“ und die Prüfung der Datei auf der Platte, für beide Projekte.

**Startskripte:** Die Port-Logik wurde mit einer Docker-Attrappe in allen drei Fällen geprüft: Datenbank läuft schon,
Port 5432 frei, Port 5432 belegt. `einrichten.command` wurde auf einer frischen Kopie des Repos durchgespielt.

**Mit echtem Sprachmodell:** siehe Abschnitt 6.

## 4. Fehler, die beim Testen gefunden und behoben wurden

| # | Fehler | Auswirkung | Behebung |
|---|---|---|---|
| 1 | Die erste Zeile jedes Code-Abschnitts verlor ihre Einrückung (`strip_whitespace`) | Agent sah verfälschten Python-Code | Abschnitte werden auf ganze Zeilen ausgerichtet; Test vergleicht jeden Abschnitt Zeile für Zeile mit der Datei |
| 2 | Trennzeichen landeten am Anfang des *nächsten* Abschnitts (z. B. das `;` einer SQL-Anweisung) | Zeilenangaben und Inhalt passten nicht exakt | `keep_separator="end"` + Ausrichtung auf Zeilenende |
| 3 | bakery-sales-db: Alle Tabellen liegen im Schema `bakery`; Abfragen ohne Präfix scheiterten, und der Hinweis im Profil nannte fälschlich `public` | SQL-Werkzeug für dieses Projekt praktisch unbrauchbar | Profil-Option `search_path`; Test mit einer echten Prüfabfrage je Projekt |
| 4 | Beide Projekte belegen Port 5432 | Beide Datenbanken konnten nicht gleichzeitig laufen | `start.command` erkennt den belegten Port und weicht auf 5433 aus, ohne die `docker-compose.yml` zu ändern |
| 5 | Absolute Pfade (`/etc/passwd`) wurden still als Pfade *im* Projekt gelesen | kein Ausbruch, aber irreführend | werden jetzt ausdrücklich abgewiesen (Test mit 7 Ausbruchsversuchen + Symlink) |
| 6 | Bei Gleichstand gewann ein inhaltlich ähnlicher Abschnitt gegen einen *wörtlichen* Treffer | Suche nach einem exakten Namen lieferte die falsche Datei zuerst | Bonus für wörtliche Treffer bei Bezeichnern und kurzen Suchbegriffen |
| 7 | Mehrere Änderungen in einem Schritt hätten bei der Wiederaufnahme nach der Freigabe doppelt geschrieben werden können | Doppelte Änderungen | Alle Vorschläge werden gemeinsam freigegeben, *bevor* etwas ausgeführt wird; Test „genau einmal“ |
| 8 | Oberfläche: Enter schickte nicht ab; ein Schritt blieb nach der Freigabe auf „läuft“ stehen | Bedienung | einzeiliges, mitwachsendes Eingabefeld; Schritte tragen ihre Aufruf-ID |

## 5. Zweites Review (22.10.2026): Was echte Modelle anders machen

Die erste Testrunde nutzte ein Skript-Modell, das sich immer „richtig“ verhält. Ein zweiter, vollständiger
Durchgang durch den Code hat gezielt gefragt: *Was passiert mit einem echten, kleinen, lokalen Modell?*
Gefunden und behoben (jeweils mit Test in `tests/test_robustheit.py`):

| # | Fehler | Auswirkung | Behebung |
|---|---|---|---|
| 9 | **Kontextfenster:** Der Verlauf wurde erst ab 150 000 Zeichen gekürzt, Ollama hat aber nur ~16 000 Tokens (≈ 42 000 Zeichen). Bei einer langen Recherche blieb außerdem nur die letzte Werkzeugantwort übrig, ohne Frage und ohne zugehörigen Aufruf | Ollama schneidet still den Anfang ab, also Regeln und Frage; der Agent wird „vergesslich“ oder die API lehnt ab | Budget je Anbieter (`config.kontext_zeichen`); erst alte Werkzeugausgaben kürzen, dann alte Runden entfernen; Aufruf und Antwort bleiben immer zusammen; Werkzeugausgaben passen sich dem Fenster an |
| 10 | Kleine Modelle schreiben Werkzeugaufrufe als JSON-Text, mit Präfix (`functions.…`), ohne ID oder mit `<think>`-Block | Antwort bestand aus rohem JSON statt einer Recherche | `graph._aufraeumen` wandelt solche Antworten in echte Aufrufe um und entfernt Denkblöcke |
| 11 | Argumente wurden nicht nach dem Schema geprüft (`"10"` statt `10`, falscher Parametername) | unklare Python-Fehler; bei Änderungen erst *nach* der Freigabe | Prüfung über das Werkzeug-Schema, verständliche Fehlermeldung ans Modell, ungültige Vorschläge erreichen den Nutzer gar nicht |
| 12 | Datei konnte zwischen Vorschau und Klick verändert werden | Freigegeben wurde etwas anderes, als geschrieben wurde | Fingerabdruck der Datei wird mit der Freigabe zurückgegeben und vor dem Schreiben verglichen |
| 13 | `webseite_lesen` konnte lokale Adressen aufrufen (`localhost`, Heimnetz) | eingeschleuster Webtext hätte lokale Dienste ansprechen können | nur öffentliche Adressen |
| 14 | Der Agent hätte (nach Freigabe) seinen eigenen Code in `agent/` ändern können | Schutzmechanismen per Diff abschaltbar | `agent/` ist für ihn nur lesbar |
| 15 | SQL mit Kommentar am Anfang (`-- …`) wurde abgelehnt; `;` in Zeichenketten ebenso; Systemfunktionen wie `pg_read_file` waren erlaubt | kleine Modelle scheiterten an der Datenbank | Vorspann wird entfernt, Zeichenketten werden beachtet, gefährliche Funktionen sind gesperrt, `NULL` wird als `NULL` angezeigt |
| 16 | Kleinkram: Zeilenbereich vertauscht oder hinter Dateiende, Dateimuster wie `sql/*.sql`, Windows-Zeilenenden, stark schrumpfende Datei ohne Warnung, `begruendung` als Pflichtfeld, keine Versionsobergrenzen | verwirrende Ergebnisse | jeweils behoben; Abhängigkeiten mit getesteten Versionen und Obergrenzen |

**Gegenprobe mit simuliertem Kleinmodell.** Eine Ollama-Attrappe benimmt sich absichtlich wie ein schwaches Modell:
Aufruf als Text, Zahlen als Strings, Präfixe, falsche Parameternamen, `<think>`-Blöcke und lange Recherchen.
Mit ihr wurde `echttest.py` gegen beide Stände ausgeführt. Der alte Stand bestand 2 von 5 Fragen (rohes JSON statt Recherche,
SQL mit Kommentar abgelehnt), der neue 5 von 5 bei bakery-sales-db und 4 von 5 bei mini-kernbank. Die fehlende Frage
lag an der Attrappe, die für diese Frage kein Verhalten hatte, nicht am Agenten. Keine Anfrage überschritt das Kontextfenster.

## 6. Erster Echttest mit echtem Modell (qwen3:14b, lokal per Ollama, 22.10.2026)

Auf einem MacBook Air (M4, 16 GB). Ergebnis mini-kernbank: **2 von 5 Fragen bestanden.** Gefunden:

| # | Beobachtung | Behebung |
|---|---|---|
| 17 | Die Testsuite scheiterte auf macOS mit 35 Fehlern („unable to open database file“): macOS erlaubt nur 256 offene Dateien, die Tests schlossen die Vektordatenbank nicht | Index wird nach jedem Test geschlossen, Limit wird angehoben; Test läuft jetzt auch mit hartem Limit 256 |
| 18 | **Erfundene Belege:** zitiert `sql/02_functions.sql:123`, ohne die Datei gelesen zu haben (die Datei hat 110 Zeilen); gibt SQL-Code „aus sql/03_aml.sql“ wieder, der dort nicht steht | **Belegprüfung** (`kern/belege.py`): Vor der Ausgabe wird geprüft, ob jede zitierte Stelle existiert und gelesen wurde und ob genannte Dateien gelesen wurden. Sonst geht die Antwort mit Rückmeldung zurück ans Modell (max. 2×), danach mit sichtbarem Warnhinweis |
| 19 | **Vorgetäuschte Recherche:** „(Web)“ und eine URL, ohne Websuche – trotz ausdrücklicher Bitte | Belegprüfung: „(Web)“ nur nach Websuche, URLs nur aus Suchergebnissen, verlangte Recherche wird erzwungen |
| 20 | Auf macOS liegt `/var` hinter einem Symlink. Die Datei wurde geschrieben, das Werkzeug meldete trotzdem einen Fehler, und das Modell versuchte es erneut | Pfade werden aufgelöst, der relative Pfad wird *vor* dem Schreiben berechnet |
| 21 | Falsche Annahmen über `aml.param` (Spaltennamen geraten) | Aufbau von `aml.param` steht jetzt im Projektwissen; Regel „Liefert eine Abfrage nichts, lies die SQL-Datei“ |
| 22 | Sehr langsam: 3–20 Minuten pro Frage, weil qwen3 vor jedem Schritt ausführlich „denkt“ | Denkmodus ist bei Ollama standardmäßig aus (`OLLAMA_DENKEN=an` schaltet ihn ein) |

Jeder dieser Fälle ist mit dem echten Antworttext des Modells als Test nachgestellt (`tests/test_belege.py`).
Der Bericht des ersten Laufs liegt in `docs/echttest-qwen3-14b-lauf1.md`.

## 6b. Zweiter Echttest (gleiches Modell, verbesserter Agent, 22.10.2026)

Bericht: `docs/echttest-qwen3-14b.md`. Ergebnis mini-kernbank: **3 von 5 bestanden** (Lauf 1: 2 von 5).

| | Lauf 1 | Lauf 2 |
|---|---|---|
| Dauer je Frage | 3–20 Minuten | 16 Sekunden bis 2,5 Minuten |
| erfundene Zitate / Code | ja (Fragen 1 und 3) | keine |
| Web-Recherche | vorgetäuscht („(Web)“ ohne Suche) | echte Suche, Quellen aus den Ergebnissen |
| Änderungsvorschlag (Frage 4) | geschrieben, aber Fehlermeldung | leere Antwort, kein Vorschlag |

Was danach noch behoben wurde (jeweils mit Test, aber **noch nicht** mit dem echten Modell erneut geprüft):

| # | Beobachtung in Lauf 2 | Behebung |
|---|---|---|
| 23 | Frage 4: Das Modell las die Datei und lieferte dann eine **leere Antwort** | Leere Antworten gehen mit Rückmeldung zurück ans Modell; Werkzeugaufrufe im Qwen-Textformat `<tool_call>…</tool_call>` werden erkannt |
| 24 | Frage 1: inhaltlich richtig, nannte aber nur die Datei, nicht die Zeile | Fragt der Nutzer nach der Stelle („Zeig mir die Stelle“, „Wo …“), verlangt die Belegprüfung `pfad:zeile` |

**Was die Belegprüfung nicht kann:** Bei Frage 3 stammte die Antwort aus einer tatsächlich gelesenen Stelle,
die Zahlen waren aber falsch abgelesen (`smurf_nah_anteil` ist 0,8, nicht 1). Die Prüfung stellt sicher,
*woher* eine Aussage kommt, nicht, ob das Modell sie richtig verstanden hat. Ein 14B-Modell auf einem Laptop
liest Tabellen nicht immer zuverlässig. Mit einem größeren Modell (z. B. Claude) ist das deutlich seltener zu erwarten.

## 7. Bewusste Grenzen

- Die Tests prüfen die *Mechanik*. Wie gut die Antworten sind, hängt vom gewählten Modell ab.
- Feste Grenzen statt Vertrauen: Der Agent darf nur die Befehle aus `profil.yaml` ausführen, keine freie Shell.
- Daten-CSVs und Dateien über 300 KB werden nicht eingebettet; dafür gibt es das SQL-Werkzeug.

## 8. Transparenz

Der Agent wurde von Mohammadmahdi Nemati zusammen mit Claude (Anthropic) als KI-Assistent entwickelt:
Anforderungen, Projektwahl und Abnahme von mir, Umsetzung und Tests überwiegend durch Claude.
Die Grundidee stammt aus dem oben verlinkten Tutorial von Hozhi.
