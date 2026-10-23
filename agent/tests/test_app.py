"""Die Oberfläche lässt sich aufbauen und spielt einen Durchlauf inkl. Freigabe korrekt ab."""

from langchain_core.messages import AIMessage

import app
from kern import graph
from conftest import SkriptModell, werkzeugaufruf as wa


def _durchlauf(gen):
    letzte = None
    for letzte in gen:
        pass
    return letzte


def _freie_variable(fn, name):
    code = getattr(fn, "__code__", None)
    if code is None or name not in code.co_freevars:
        return None
    return fn.__closure__[code.co_freevars.index(name)].cell_contents


def test_ui_ablauf_mit_freigabe(profil, index, tmp_path, projektkopie):
    (projektkopie / "a.txt").write_text("alt\n")
    m = SkriptModell([
        AIMessage("", tool_calls=[wa("projekt_durchsuchen", {"frage": "README"}, "s")]),
        AIMessage("Antwort mit Beleg `README.md:1`"),
        AIMessage("Vorschlag:", tool_calls=[wa("datei_aendern", {"pfad": "a.txt", "alter_text": "alt",
                                                                  "neuer_text": "neu", "begruendung": "b"}, "c")]),
        AIMessage("Gespeichert."),
    ])
    g = graph.bauen(profil, index, modell=m, db_pfad=tmp_path / "g.sqlite")
    demo = app.oberflaeche(profil, index, g)
    assert demo is not None

    # Die Handler direkt aufrufen (wie Gradio es tut)
    fns = [f.fn for f in demo.fns.values()]
    senden = next(fn for fn in fns if getattr(fn, "__name__", "") == "senden")
    verlauf, _, panel, _ = _durchlauf(senden("Frage", [], "ui"))
    assert verlauf[-1]["content"].startswith("Antwort mit Beleg")
    schritt = next(v for v in verlauf if (v.get("metadata") or {}).get("title", "").endswith("projekt_durchsuchen"))
    assert schritt["metadata"]["status"] == "done" and "Datei: " in schritt["content"]

    verlauf, _, panel, diff = _durchlauf(senden("Ändere a.txt", verlauf, "ui"))
    assert panel["visible"] is True and "+neu" in diff and "-alt" in diff
    assert (projektkopie / "a.txt").read_text() == "alt\n"

    uebernehmen = next(fn for fn in fns if _freie_variable(fn, "genehmigt") is True)
    verlauf, panel, _ = _durchlauf(uebernehmen(verlauf, "ui"))
    assert (projektkopie / "a.txt").read_text() == "neu\n"
    assert verlauf[-1]["content"] == "Gespeichert."
    aendern = next(v for v in verlauf if (v.get("metadata") or {}).get("title", "").endswith("datei_aendern"))
    assert aendern["metadata"]["status"] == "done", "Schritt muss nach Freigabe als erledigt markiert sein"


def test_nachricht_waehrend_freigabe_ist_ablehnung(profil, index, tmp_path, projektkopie):
    (projektkopie / "a.txt").write_text("alt\n")
    m = SkriptModell([
        AIMessage("", tool_calls=[wa("datei_aendern", {"pfad": "a.txt", "alter_text": "alt",
                                                       "neuer_text": "neu", "begruendung": "b"}, "c")]),
        AIMessage("Ok, anders."),
    ])
    g = graph.bauen(profil, index, modell=m, db_pfad=tmp_path / "g.sqlite")
    demo = app.oberflaeche(profil, index, g)
    senden = next(f.fn for f in demo.fns.values() if getattr(f.fn, "__name__", "") == "senden")
    verlauf, *_ = _durchlauf(senden("Ändere", [], "ui"))
    verlauf, *_ = _durchlauf(senden("Nein, lieber kleiner", verlauf, "ui"))
    assert (projektkopie / "a.txt").read_text() == "alt\n"
    assert "lieber kleiner" in m.aufrufe[-1][-1].content
