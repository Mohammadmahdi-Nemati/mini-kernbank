"""
Regressionstests für die Funde aus dem zweiten Code-Review (22.10.2026).

Jeder Test steht für einen Fehler, der mit einem echten (vor allem kleinen, lokalen)
Sprachmodell aufgetreten wäre, aber mit dem Skript-Modell der ersten Testrunde nicht.
"""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from kern import graph, guard
from conftest import SkriptModell, werkzeugaufruf as wa


def _cfg(t="r"):
    return {"configurable": {"thread_id": t}, "recursion_limit": 30}


# ------------------------------------------------ 1. Kontextfenster (kritisch für Ollama)

def _runde(i, ausgabe=14_000, aufrufe=3):
    ai = AIMessage("", tool_calls=[wa("datei_lesen", {"pfad": f"x{j}"}, f"c{i}_{j}") for j in range(aufrufe)])
    return [ai] + [ToolMessage("y" * ausgabe, tool_call_id=f"c{i}_{j}") for j in range(aufrufe)]


def _struktur_ok(msgs):
    """Jeder Werkzeugaufruf hat seine Antwort direkt dahinter, keine verwaisten Antworten."""
    offen = set()
    for m in msgs:
        if isinstance(m, ToolMessage):
            assert m.tool_call_id in offen, "Werkzeugantwort ohne zugehörigen Aufruf"
            offen.discard(m.tool_call_id)
        else:
            assert not offen, "Aufruf ohne Antwort"
            if isinstance(m, AIMessage):
                offen = {tc["id"] for tc in m.tool_calls}
    return True


def test_lange_einzelne_runde_bleibt_im_budget_und_behaelt_die_frage():
    """Früher: eine einzige lange Recherche (eine Frage, viele Werkzeuge) lieferte am Ende
    nur noch die letzte Werkzeugantwort – ohne Frage, ohne zugehörigen Aufruf."""
    msgs = [HumanMessage("Analysiere alles")]
    for i in range(8):
        msgs += _runde(i)
    budget = 30_000                                   # ≈ Ollama mit 16k Tokens nach Abzug des Systemprompts
    out = graph._kuerzen(msgs, budget)
    assert graph._laenge(out) <= budget * 1.05
    assert isinstance(out[0], HumanMessage) and out[0].content == "Analysiere alles"
    assert _struktur_ok(out)
    # die neueste Runde ist möglichst vollständig erhalten, ältere sind gekürzt
    assert graph.GEKUERZT in out[2].content


def test_alte_runden_fallen_weg_aktuelle_frage_bleibt():
    msgs = []
    for i in range(6):
        msgs += [HumanMessage(f"Frage {i}")] + _runde(i) + [AIMessage(f"Antwort {i}")]
    out = graph._kuerzen(msgs, 25_000)
    assert graph._laenge(out) <= 25_000 * 1.05
    assert any(isinstance(m, HumanMessage) and m.content == "Frage 5" for m in out)
    assert isinstance(out[0], HumanMessage) and _struktur_ok(out)


def test_kurzer_verlauf_bleibt_unveraendert():
    msgs = [HumanMessage("a"), AIMessage("b")]
    assert graph._kuerzen(msgs, 10_000) is msgs


def test_ollama_budget_ist_klein(monkeypatch):
    from kern import config
    monkeypatch.setattr(config, "ANBIETER", "ollama")
    monkeypatch.delenv("KONTEXT_ZEICHEN", raising=False)
    assert 30_000 < config.kontext_zeichen() < 60_000


def test_werkzeugausgaben_passen_sich_dem_fenster_an(monkeypatch, profil, index):
    from kern import config
    from kern.werkzeuge import Werkzeugkasten
    monkeypatch.setattr(config, "kontext_zeichen", lambda: 42_000)
    k = Werkzeugkasten(profil, index)
    assert k.max_ausgabe == 7_000
    assert len(k.projekt_durchsuchen("README", 12)) <= 7_100


# ------------------------------------------------ 2. Eigenheiten kleiner Modelle

NAMEN = {"datei_lesen", "projekt_durchsuchen"}


def test_werkzeugaufruf_als_text_wird_erkannt():
    for text in ['{"name": "datei_lesen", "arguments": {"pfad": "README.md"}}',
                 '```json\n{"name": "datei_lesen", "parameters": {"pfad": "README.md"}}\n```',
                 '[{"function": "functions.datei_lesen", "arguments": "{\\"pfad\\": \\"README.md\\"}"}]']:
        m = graph._aufraeumen(AIMessage(text), NAMEN)
        assert m.tool_calls and m.tool_calls[0]["name"] == "datei_lesen", text
        assert m.tool_calls[0]["args"] == {"pfad": "README.md"} and m.tool_calls[0]["id"]
        assert m.content == ""


def test_normales_json_in_antwort_bleibt_text():
    text = 'Beispiel: {"name": "Max", "alter": 3}'
    m = graph._aufraeumen(AIMessage(text), NAMEN)
    assert not m.tool_calls and m.content == text


def test_denk_bloecke_und_praefixe_werden_entfernt():
    m = graph._aufraeumen(AIMessage("<think>hmm, erst suchen</think>\nAntwort.",
                                    tool_calls=[{"name": "functions.datei_lesen", "args": {"pfad": "a"},
                                                 "id": None, "type": "tool_call"}]), NAMEN)
    assert m.content == "Antwort." and m.tool_calls[0]["name"] == "datei_lesen" and m.tool_calls[0]["id"]


def test_argumente_werden_umgewandelt_und_geprueft(kasten):
    out = kasten.ausfuehren("datei_lesen", {"pfad": "README.md", "von_zeile": "2", "bis_zeile": "3"})
    assert "   2| " in out and "   4|" not in out
    with pytest.raises(ValueError, match="Erlaubte Argumente"):
        kasten.ausfuehren("datei_lesen", {"datei": "README.md"})


def test_falsche_argumente_gehen_als_hinweis_ans_modell(profil, index, tmp_path):
    m = SkriptModell([AIMessage("", tool_calls=[wa("datei_lesen", {"dateiname": "README.md"}, "a")]),
                      AIMessage("ok")])
    g = graph.bauen(profil, index, modell=m, db_pfad=tmp_path / "g.sqlite")
    r = g.invoke({"messages": [HumanMessage("x")]}, _cfg())
    t = [x.content for x in r["messages"] if isinstance(x, ToolMessage)][-1]
    assert "Erlaubte Argumente" in t and "pfad" in t


def test_ungueltige_aenderungsargumente_ohne_freigabe(profil, index, tmp_path):
    """Fehlt ein Pflichtargument, darf der Nutzer gar nicht erst gefragt werden."""
    m = SkriptModell([AIMessage("", tool_calls=[wa("datei_aendern", {"pfad": "README.md", "neu": "x"}, "a")]),
                      AIMessage("ok")])
    g = graph.bauen(profil, index, modell=m, db_pfad=tmp_path / "g.sqlite")
    g.invoke({"messages": [HumanMessage("x")]}, _cfg())
    assert not g.get_state(_cfg()).interrupts


def test_begruendung_ist_optional(kasten, projektkopie):
    (projektkopie / "b.txt").write_text("alt\n")
    assert "gespeichert" in kasten.ausfuehren("datei_aendern", {"pfad": "b.txt", "alter_text": "alt",
                                                                "neuer_text": "neu"})


# ------------------------------------------------ 3. Freigabe: Datei inzwischen verändert

def test_aenderung_nach_vorschau_wird_nicht_ueberschrieben(profil, index, tmp_path, projektkopie):
    datei = projektkopie / "c.txt"
    datei.write_text("zeile eins\nzeile zwei\n")
    m = SkriptModell([AIMessage("", tool_calls=[wa("datei_erstellen", {"pfad": "c.txt", "inhalt": "komplett neu\n"}, "a")]),
                      AIMessage("ok")])
    g = graph.bauen(profil, index, modell=m, db_pfad=tmp_path / "g.sqlite")
    g.invoke({"messages": [HumanMessage("x")]}, _cfg())
    v = g.get_state(_cfg()).interrupts[0].value["vorschlaege"][0]
    datei.write_text("zeile eins\nzeile zwei\nvom Nutzer inzwischen ergänzt\n")      # Nutzer arbeitet weiter
    r = g.invoke(Command(resume={"genehmigt": True, "staende": {v["id"]: v["stand"]}}), _cfg())
    assert "vom Nutzer inzwischen ergänzt" in datei.read_text()
    assert "NICHT ausgeführt" in [x.content for x in r["messages"] if isinstance(x, ToolMessage)][-1]


def test_unveraenderte_datei_wird_geschrieben(profil, index, tmp_path, projektkopie):
    (projektkopie / "d.txt").write_text("a\n")
    m = SkriptModell([AIMessage("", tool_calls=[wa("datei_aendern", {"pfad": "d.txt", "alter_text": "a",
                                                                      "neuer_text": "b"}, "a")]), AIMessage("ok")])
    g = graph.bauen(profil, index, modell=m, db_pfad=tmp_path / "g.sqlite")
    g.invoke({"messages": [HumanMessage("x")]}, _cfg())
    v = g.get_state(_cfg()).interrupts[0].value["vorschlaege"][0]
    g.invoke(Command(resume={"genehmigt": True, "staende": {v["id"]: v["stand"]}}), _cfg())
    assert (projektkopie / "d.txt").read_text() == "b\n"


def test_warnung_wenn_datei_stark_schrumpft(kasten, projektkopie):
    (projektkopie / "lang.py").write_text("\n".join(f"x{i} = {i}" for i in range(100)) + "\n")
    v = kasten.vorschau("datei_erstellen", {"pfad": "lang.py", "inhalt": "x0 = 0\n"})
    assert v.startswith("⚠ ACHTUNG") and "100 auf 1 Zeilen" in v


def test_windows_zeilenenden_bleiben_erhalten(kasten, projektkopie):
    (projektkopie / "w.txt").write_bytes(b"eins\r\nzwei\r\ndrei\r\n")
    kasten.ausfuehren("datei_aendern", {"pfad": "w.txt", "alter_text": "eins\nzwei", "neuer_text": "EINS\nZWEI"})
    assert (projektkopie / "w.txt").read_bytes() == b"EINS\r\nZWEI\r\ndrei\r\n"


# ------------------------------------------------ 4. Sicherheit

def test_agent_darf_sich_nicht_selbst_aendern(tmp_path):
    (tmp_path / "agent" / "kern").mkdir(parents=True)
    (tmp_path / "agent" / "kern" / "guard.py").write_text("x")
    assert guard.sicherer_pfad(tmp_path, "agent/kern/guard.py").name == "guard.py"     # lesen ok
    with pytest.raises(PermissionError, match="Agenten selbst"):
        guard.sicherer_pfad(tmp_path, "agent/kern/guard.py", schreiben=True)
    with pytest.raises(PermissionError):
        guard.sicherer_pfad(tmp_path, "", schreiben=True)


@pytest.mark.parametrize("url", ["http://localhost:7861/", "http://127.0.0.1:11434/api/tags",
                                 "http://192.168.0.1/", "http://10.0.0.5/", "http://[::1]/",
                                 "http://169.254.169.254/latest/meta-data/"])
def test_webseite_lesen_sperrt_lokale_adressen(kasten, url):
    assert "gesperrt" in kasten.webseite_lesen(url)


def test_sql_vorspann_und_sperren(kasten):
    from kern.werkzeuge import _SQL_GESPERRT, _sql_vorspann_entfernen
    assert _sql_vorspann_entfernen("-- Abfalls\n/* x */\n  SELECT 1").startswith("SELECT")
    assert _SQL_GESPERRT.search("select pg_read_file('/etc/passwd')")
    assert _SQL_GESPERRT.search("SELECT pg_terminate_backend (123)")
    assert not _SQL_GESPERRT.search("SELECT regel FROM aml.v_alert")
    if kasten.p.datenbank:
        assert "gesperrt" in kasten.datenbank_abfragen("SELECT pg_sleep(100)")


def test_sql_mit_kommentar_und_semikolon_im_text(db_kasten):
    out = db_kasten.datenbank_abfragen("-- Testabfrage\nSELECT 'a;b' AS t, NULL AS n;")
    assert "| a;b | NULL |" in out, out


# ------------------------------------------------ 5. Kleinkram aus dem Review

def test_datei_lesen_randfaelle(kasten, projektkopie):
    (projektkopie / "kurz.txt").write_text("1\n2\n3\n")
    assert "nur 3 Zeilen" in kasten.datei_lesen("kurz.txt", 10)
    out = kasten.datei_lesen("kurz.txt", 3, 1)                 # vertauscht
    assert "   1| 1" in out and "   3| 3" in out
    (projektkopie / "leer.txt").write_text("")
    assert "leer" in kasten.datei_lesen("leer.txt")


def test_text_suchen_mit_ordnermuster(kasten):
    sql = next(p for p in kasten.idx.dateien() if p.suffix == ".sql")
    ordner = sql.parent.as_posix()
    out = kasten.text_suchen(".", f"{ordner}/*.sql")
    assert f"{sql.as_posix()}:" in out
    assert f"{sql.as_posix()}:" in kasten.text_suchen(".", "**/*.sql")


def test_werkzeugschema_hat_beschreibungen(kasten):
    from langchain_core.utils.function_calling import convert_to_openai_tool
    schema = {convert_to_openai_tool(t)["function"]["name"]: convert_to_openai_tool(t)["function"]["parameters"]
              for t in kasten.als_tools()}
    assert "description" in schema["datei_aendern"]["properties"]["alter_text"]
    assert "begruendung" not in schema["datei_aendern"].get("required", [])


# ------------------------------------------------ 6. Echttest qwen3:14b: Denkmodus macht es sehr langsam

def _mini_ollama(faehigkeiten, protokoll):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/api/show":
                out = {"capabilities": faehigkeiten}
            else:
                protokoll.append(body)
                out = {"model": body["model"], "created_at": "2026-10-22T00:00:00Z", "done": True,
                       "done_reason": "stop", "message": {"role": "assistant", "content": "ok"}}
            data = (json.dumps(out) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.mark.parametrize("faehig,wunsch,erwartet", [
    (["completion", "tools", "thinking"], None, False),     # Denkmodell: standardmäßig aus
    (["completion", "tools", "thinking"], "an", True),      # ausdrücklich eingeschaltet
    (["completion", "tools"], None, None),                  # kein Denkmodell: Feld nicht senden
])
def test_ollama_denkmodus(monkeypatch, faehig, wunsch, erwartet):
    from kern import config
    protokoll = []
    srv = _mini_ollama(faehig, protokoll)
    monkeypatch.setattr(config, "ANBIETER", "ollama")
    monkeypatch.setenv("BASE_URL", f"http://127.0.0.1:{srv.server_port}")
    if wunsch:
        monkeypatch.setenv("OLLAMA_DENKEN", wunsch)
    else:
        monkeypatch.delenv("OLLAMA_DENKEN", raising=False)
    try:
        config._ein_modell("qwen3:14b").invoke("Hallo")
    finally:
        srv.shutdown()
    assert protokoll and protokoll[0].get("think") == erwartet
    assert protokoll[0]["options"]["num_ctx"] == config.OLLAMA_NUM_CTX
