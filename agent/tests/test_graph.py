"""
Der LangGraph-Ablauf mit einem Skript-Modell.

Kernaussage, die hier bewiesen wird: Ohne Freigabe wird NICHTS geschrieben
oder ausgeführt – und mit Freigabe genau einmal.
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from kern import graph
from conftest import SkriptModell, werkzeugaufruf as wa


def _cfg(t="t1"):
    return {"configurable": {"thread_id": t}, "recursion_limit": 30}


def _letzte_tool(r, n=1):
    return [m.content for m in r["messages"] if isinstance(m, ToolMessage)][-n:]


def _eindeutige_zeile(text):
    return next(z for z in text.splitlines() if len(z.strip()) > 8 and text.count(z) == 1)


def _py(index):
    rel = next(p for p in index.dateien() if p.suffix == ".py").as_posix()
    return rel, (index.wurzel / rel).read_text(encoding="utf-8")


def test_lesende_schleife_ohne_pause(profil, index, tmp_path):
    m = SkriptModell([
        AIMessage("", tool_calls=[wa("projekt_durchsuchen", {"frage": "README"}, "a"),
                                  wa("dateien_auflisten", {}, "b")]),
        AIMessage("Fertig."),
    ])
    g = graph.bauen(profil, index, modell=m, db_pfad=tmp_path / "g.sqlite")
    r = g.invoke({"messages": [HumanMessage("Worum geht es?")]}, _cfg())
    assert not g.get_state(_cfg()).interrupts
    assert r["messages"][-1].content == "Fertig."
    assert "Datei: " in _letzte_tool(r, 2)[0] and "README.md" in _letzte_tool(r, 2)[1]
    system = m.aufrufe[0][0].content
    assert profil.name in system and "Was du über das Projekt weißt" in system


def test_freigabe_genehmigt_schreibt_genau_einmal(profil, index, tmp_path):
    rel, alt = _py(index)
    z = _eindeutige_zeile(alt)
    m = SkriptModell([
        AIMessage("Vorschlag", tool_calls=[wa("datei_aendern", {"pfad": rel, "alter_text": z,
                                                                 "neuer_text": z + "  # AGENT-TESTMARKE", "begruendung": "b"}, "x")]),
        AIMessage("Erledigt."),
    ])
    g = graph.bauen(profil, index, modell=m, db_pfad=tmp_path / "g.sqlite")
    g.invoke({"messages": [HumanMessage("ändern")]}, _cfg())

    st = g.get_state(_cfg())
    assert st.interrupts, "Graph hätte pausieren müssen"
    v = st.interrupts[0].value["vorschlaege"]
    assert len(v) == 1 and v[0]["pfad"] == rel and "# AGENT-TESTMARKE" in v[0]["vorschau"]
    assert (index.wurzel / rel).read_text(encoding="utf-8") == alt, "vor der Freigabe geschrieben!"

    r = g.invoke(Command(resume={"genehmigt": True, "kommentar": ""}), _cfg())
    neu = (index.wurzel / rel).read_text(encoding="utf-8")
    assert neu.count("# AGENT-TESTMARKE") == 1
    assert "genehmigt" in _letzte_tool(r)[0] and r["messages"][-1].content == "Erledigt."


def test_zwei_aenderungen_eine_freigabe_kein_doppeltes_schreiben(profil, index, tmp_path, projektkopie):
    (projektkopie / "a.txt").write_text("eins\n")
    (projektkopie / "b.txt").write_text("zwei\n")
    m = SkriptModell([
        AIMessage("", tool_calls=[
            wa("datei_aendern", {"pfad": "a.txt", "alter_text": "eins", "neuer_text": "eins!", "begruendung": "1"}, "1"),
            wa("datei_aendern", {"pfad": "b.txt", "alter_text": "zwei", "neuer_text": "zwei!", "begruendung": "2"}, "2"),
            wa("datei_lesen", {"pfad": "a.txt"}, "3"),
        ]),
        AIMessage("ok"),
    ])
    g = graph.bauen(profil, index, modell=m, db_pfad=tmp_path / "g.sqlite")
    g.invoke({"messages": [HumanMessage("x")]}, _cfg())
    assert len(g.get_state(_cfg()).interrupts[0].value["vorschlaege"]) == 2
    g.invoke(Command(resume={"genehmigt": True}), _cfg())
    assert (projektkopie / "a.txt").read_text() == "eins!\n"
    assert (projektkopie / "b.txt").read_text() == "zwei!\n"


def test_ablehnung_mit_kommentar(profil, index, tmp_path):
    rel, alt = _py(index)
    z = _eindeutige_zeile(alt)
    m = SkriptModell([
        AIMessage("", tool_calls=[wa("datei_aendern", {"pfad": rel, "alter_text": z, "neuer_text": "kaputt",
                                                       "begruendung": "b"}, "x")]),
        AIMessage("Verstanden, anderer Vorschlag?"),
    ])
    g = graph.bauen(profil, index, modell=m, db_pfad=tmp_path / "g.sqlite")
    g.invoke({"messages": [HumanMessage("ändern")]}, _cfg())
    r = g.invoke(Command(resume={"genehmigt": False, "kommentar": "bitte als eigene Funktion"}), _cfg())
    assert (index.wurzel / rel).read_text(encoding="utf-8") == alt
    t = _letzte_tool(r)[0]
    assert t.startswith("ABGELEHNT") and "bitte als eigene Funktion" in t
    assert any(isinstance(x, ToolMessage) and "ABGELEHNT" in x.content for x in m.aufrufe[-1]), \
        "Modell muss die Ablehnung sehen"


def test_befehl_braucht_freigabe(profil, index, tmp_path):
    name = next(iter(profil.befehle))
    m = SkriptModell([AIMessage("", tool_calls=[wa("befehl_ausfuehren", {"name": name}, "c")]), AIMessage("ok")])
    g = graph.bauen(profil, index, modell=m, db_pfad=tmp_path / "g.sqlite")
    g.invoke({"messages": [HumanMessage("teste")]}, _cfg())
    assert g.get_state(_cfg()).interrupts
    r = g.invoke(Command(resume={"genehmigt": False, "kommentar": ""}), _cfg())
    assert "ABGELEHNT" in _letzte_tool(r)[0]


def test_ungueltiger_vorschlag_ohne_pause(profil, index, tmp_path):
    rel, _ = _py(index)
    m = SkriptModell([
        AIMessage("", tool_calls=[wa("datei_aendern", {"pfad": rel, "alter_text": "GIBT_ES_NICHT_123",
                                                       "neuer_text": "x", "begruendung": "b"}, "x")]),
        AIMessage("Ich lese nochmal."),
    ])
    g = graph.bauen(profil, index, modell=m, db_pfad=tmp_path / "g.sqlite")
    r = g.invoke({"messages": [HumanMessage("ändern")]}, _cfg())
    assert not g.get_state(_cfg()).interrupts
    assert "ungültig" in _letzte_tool(r)[0] and "nicht vor" in _letzte_tool(r)[0]


def test_unbekanntes_werkzeug_und_fehler_crashen_nicht(profil, index, tmp_path):
    m = SkriptModell([
        AIMessage("", tool_calls=[wa("rm_rf", {}, "u"), wa("datei_lesen", {"falscher_parameter": 1}, "v")]),
        AIMessage("ok"),
    ])
    g = graph.bauen(profil, index, modell=m, db_pfad=tmp_path / "g.sqlite")
    r = g.invoke({"messages": [HumanMessage("x")]}, _cfg())
    a, b = _letzte_tool(r, 2)
    assert "Unbekanntes Werkzeug" in a and b.startswith("Fehler in datei_lesen")
    assert r["messages"][-1].content == "ok"


def test_gedaechtnis_pro_chat(profil, index, tmp_path):
    m = SkriptModell([AIMessage("A1"), AIMessage("A2"), AIMessage("B1")])
    g = graph.bauen(profil, index, modell=m, db_pfad=tmp_path / "g.sqlite")
    g.invoke({"messages": [HumanMessage("Frage 1")]}, _cfg("chat1"))
    g.invoke({"messages": [HumanMessage("Frage 2")]}, _cfg("chat1"))
    gesehen = [x.content for x in m.aufrufe[-1][1:]]
    assert gesehen == ["Frage 1", "A1", "Frage 2"]
    g.invoke({"messages": [HumanMessage("Andere")]}, _cfg("chat2"))
    assert [x.content for x in m.aufrufe[-1][1:]] == ["Andere"], "Chats dürfen sich nicht vermischen"


def test_kontext_wird_gekuerzt_aber_beginnt_bei_nutzer():
    msgs = []
    for i in range(30):
        msgs += [HumanMessage(f"f{i}"), AIMessage("x" * 10_000)]
    gekuerzt = graph._kuerzen(msgs, 150_000)
    assert len(gekuerzt) < len(msgs) and isinstance(gekuerzt[0], HumanMessage)
    assert graph._laenge(gekuerzt) <= 150_000
    assert gekuerzt[-1] is msgs[-1]
