"""
Belegprüfung – mit den echten Fehlern aus dem Echttest mit qwen3:14b (22.10.2026).

Die Antworttexte unten sind (gekürzt) die tatsächlichen Antworten des Modells.
"""

import os

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from kern import belege, graph
from conftest import SkriptModell, werkzeugaufruf as wa

DATEIEN = {"sql/02_functions.sql": 110, "sql/03_aml.sql": 160, "src/kernbank/concurrency.py": 150,
           "src/kernbank/iban.py": 25, "README.md": 170}


def _verlauf(frage, *runden):
    msgs = [HumanMessage(frage)]
    for i, (name, args, ergebnis) in enumerate(runden):
        msgs.append(AIMessage("", tool_calls=[wa(name, args, f"t{i}")]))
        msgs.append(ToolMessage(ergebnis, tool_call_id=f"t{i}"))
    return msgs


def test_fall1_zitat_aus_nicht_gelesener_datei():
    """qwen3 las concurrency.py, zitierte aber `sql/02_functions.sql:123` (gibt es nicht)."""
    msgs = _verlauf("Wie verhindert bank.transfer() einen Lost Update?",
                    ("datei_lesen", {"pfad": "src/kernbank/concurrency.py"},
                     "src/kernbank/concurrency.py (Zeilen 1-150):\n   1| ..."))
    p = belege.probleme("Die Sperre steht in `sql/02_functions.sql:123`.", msgs, DATEIEN)
    assert any("nur 110 Zeilen" in x for x in p)
    p = belege.probleme("Die Sperre steht in `sql/02_functions.sql:92`.", msgs, DATEIEN)
    assert any("nicht gelesen" in x for x in p)


def test_fall1_richtig_belegt_ist_ok():
    msgs = _verlauf("Wie verhindert bank.transfer() einen Lost Update?",
                    ("projekt_durchsuchen", {"frage": "FOR UPDATE"},
                     "Datei: sql/02_functions.sql (Zeilen 85-105)\n... FOR UPDATE ..."))
    assert belege.probleme("Siehe `sql/02_functions.sql:92`.", msgs, DATEIEN) == []
    assert belege.probleme("Siehe `sql/02_functions.sql:88-95`.", msgs, DATEIEN) == []


def test_fall3_datei_genannt_aber_nie_gelesen():
    """qwen3 fragte nur die Datenbank ab und erfand dann Code „aus sql/03_aml.sql“."""
    msgs = _verlauf("Welche Schwellenwerte nutzt die Smurfing-Regel?",
                    ("datenbank_abfragen", {"sql": "SELECT * FROM aml.param WHERE name='Smurfing'"},
                     "| name | value |\n|---|---|"))
    p = belege.probleme("Die Werte sind hardcoded in `sql/03_aml.sql`.", msgs, DATEIEN)
    assert any("sql/03_aml.sql" in x and "nicht gelesen" in x for x in p)


def test_fall5_web_markiert_ohne_websuche_und_erfundene_url():
    msgs = _verlauf("Was ist der Unterschied …? Recherchiere in der offiziellen Dokumentation.")
    antwort = "FOR NO KEY UPDATE ist schwächer. Quelle: https://www.postgresql.org/docs/current/explicit-locking.html (Web)"
    p = belege.probleme(antwort, msgs, DATEIEN)
    assert any("(Web)" in x for x in p)
    assert any("postgresql.org" in x for x in p)
    assert any("Recherche verlangt" in x for x in p)


def test_url_aus_suchergebnis_ist_ok():
    msgs = _verlauf("Recherchiere FOR UPDATE",
                    ("web_suchen", {"anfrage": "for update"},
                     "<<<DATEN>>>\n[1] Doku\nURL: https://www.postgresql.org/docs/current/explicit-locking.html\n"))
    antwort = "(Web) Siehe https://www.postgresql.org/docs/current/explicit-locking.html."
    assert belege.probleme(antwort, msgs, DATEIEN) == []


def test_kurzform_und_normale_antworten_sind_ok():
    msgs = _verlauf("Was macht iban.py?", ("datei_lesen", {"pfad": "src/kernbank/iban.py"},
                                          "src/kernbank/iban.py (Zeilen 1-25):\n   1| \"\"\"…\"\"\""))
    assert belege.probleme("In `iban.py:1` steht der Docstring.", msgs, DATEIEN) == []
    assert belege.probleme("Das Projekt nutzt PostgreSQL.", msgs, DATEIEN) == []


def test_graph_korrigiert_unbelegte_antwort(profil, index, tmp_path):
    """Das Modell zitiert zuerst falsch, bekommt die Rückmeldung, liest nach und antwortet richtig."""
    sql = next(p for p in index.dateien() if p.suffix == ".sql").as_posix()
    m = SkriptModell([
        AIMessage(f"Steht in `{sql}:2`."),                                       # ungelesen zitiert
        AIMessage("", tool_calls=[wa("datei_lesen", {"pfad": sql, "von_zeile": 1, "bis_zeile": 5}, "k")]),
        AIMessage(f"Korrigiert: `{sql}:2`."),
    ])
    g = graph.bauen(profil, index, modell=m, db_pfad=tmp_path / "g.sqlite")
    cfg = {"configurable": {"thread_id": "b"}, "recursion_limit": 30}
    r = g.invoke({"messages": [HumanMessage("Wo steht das?")]}, cfg)
    assert r["messages"][-1].content == f"Korrigiert: `{sql}:2`."
    rueck = [x for x in r["messages"] if belege.ist_pruefung(x)]
    assert len(rueck) == 1 and "nicht gelesen" in rueck[0].content


def test_graph_gibt_nach_zwei_versuchen_mit_hinweis_auf(profil, index, tmp_path):
    m = SkriptModell([AIMessage("Siehe `gibt/es/nicht.py:3`.") for _ in range(3)])
    g = graph.bauen(profil, index, modell=m, db_pfad=tmp_path / "g.sqlite")
    cfg = {"configurable": {"thread_id": "c"}, "recursion_limit": 30}
    r = g.invoke({"messages": [HumanMessage("x")]}, cfg)
    assert "Automatische Belegprüfung" in r["messages"][-1].content
    assert sum(1 for x in r["messages"] if belege.ist_pruefung(x)) == 2


def test_fall4_schreiben_unter_symlink_pfad(tmp_path, original_profil):
    """macOS: /var ist ein Symlink auf /private/var. Früher wurde die Datei geschrieben, danach
    meldete das Werkzeug trotzdem einen Fehler – das Modell versuchte es erneut und scheiterte."""
    from kern import profil as profile
    from kern.index import ProjektIndex
    from kern.werkzeuge import Werkzeugkasten
    echt = tmp_path / "echt" / "projekt"
    echt.mkdir(parents=True)
    (echt / "a.py").write_text('"""Alt."""\n')
    os.symlink(tmp_path / "echt", tmp_path / "link")
    p = profile.laden(original_profil.pfad / "agent" / "profil.yaml" if (original_profil.pfad / "agent" / "profil.yaml").exists()
                      else None, pfad=str(tmp_path / "link" / "projekt"), datenordner_basis=tmp_path / "d")
    idx = ProjektIndex(p)
    idx.synchronisieren(log=lambda *_: None)
    k = Werkzeugkasten(p, idx)
    out = k.ausfuehren("datei_aendern", {"pfad": "a.py", "alter_text": '"""Alt."""', "neuer_text": '"""Neu."""'})
    assert "gespeichert" in out and (echt / "a.py").read_text() == '"""Neu."""\n'
    idx.schliessen()


# ------------------------------------------------ Echttest Lauf 2 (qwen3:14b, Denkmodus aus)

def test_lauf2_frage1_stelle_ohne_zeilenangabe():
    """Antwort war inhaltlich richtig, nannte aber nur die Datei – ohne Zeile."""
    msgs = _verlauf("Wie verhindert bank.transfer() einen Lost Update? Zeig mir die Stelle im Code.",
                    ("projekt_durchsuchen", {"frage": "transfer"},
                     "Datei: sql/02_functions.sql (Zeilen 1-40)\n-- Zeilensperren (FOR UPDATE) …"))
    p = belege.probleme("Die Stelle ist in der Datei `sql/02_functions.sql`.", msgs, DATEIEN)
    assert any("pfad:zeile" in x for x in p)
    assert belege.probleme("Siehe `sql/02_functions.sql:5`.", msgs, DATEIEN) == []


def test_lauf2_frage4_leere_antwort_wird_nicht_akzeptiert(profil, index, tmp_path, projektkopie):
    """qwen3 las die Datei und lieferte dann eine leere Antwort – der Vorschlag kam nie."""
    (projektkopie / "doc.py").write_text('"""Alt."""\n')
    m = SkriptModell([
        AIMessage("", tool_calls=[wa("datei_lesen", {"pfad": "doc.py"}, "l")]),
        AIMessage(""),                                                        # leer
        AIMessage("", tool_calls=[wa("datei_aendern", {"pfad": "doc.py", "alter_text": '"""Alt."""',
                                                       "neuer_text": '"""Neu."""'}, "a")]),
        AIMessage("Vorschlag gespeichert."),
    ])
    g = graph.bauen(profil, index, modell=m, db_pfad=tmp_path / "g.sqlite")
    cfg = {"configurable": {"thread_id": "leer"}, "recursion_limit": 30}
    g.invoke({"messages": [HumanMessage("Ändere den Docstring")]}, cfg)
    assert g.get_state(cfg).interrupts, "nach der Rückmeldung muss der Vorschlag kommen"
    assert any(belege.ist_pruefung(x) and "leer" in x.content for x in g.get_state(cfg).values["messages"])


def test_qwen_tool_call_format_im_text():
    text = 'Ich ändere die Datei.\n<tool_call>\n{"name": "datei_lesen", "arguments": {"pfad": "README.md"}}\n</tool_call>'
    m = graph._aufraeumen(AIMessage(text), {"datei_lesen"})
    assert m.tool_calls and m.tool_calls[0]["name"] == "datei_lesen" and m.tool_calls[0]["args"] == {"pfad": "README.md"}
