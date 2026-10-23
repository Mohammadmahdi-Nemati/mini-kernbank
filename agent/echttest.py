"""
Echttest mit dem ECHTEN Sprachmodell aus agent/.env.

Die pytest-Tests prüfen die Mechanik mit einem Skript-Modell. Dieser Test prüft,
ob ein echtes Modell die Werkzeuge sinnvoll benutzt. Er stellt die Fragen aus
`echttest` in profil.yaml, gibt Änderungsvorschläge automatisch frei – aber nur
auf einer KOPIE des Projekts – und schreibt einen Bericht nach
agent/docs/echttest-<modell>.md.

    .venv/bin/python echttest.py
"""

import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from kern import config, graph, profil as profile
from kern.index import ProjektIndex

AGENT = Path(__file__).resolve().parent
BELEG = re.compile(r"[\w./-]+\.(py|sql|md|toml|yml|yaml|txt):\d+")


def kopie_anlegen(quelle: Path) -> Path:
    ziel = Path(tempfile.mkdtemp(prefix="agent-echttest-")) / quelle.name
    shutil.copytree(quelle, ziel, ignore=shutil.ignore_patterns(
        "agent", ".git", ".venv", "__pycache__", "data", ".pytest_cache"))
    return ziel


def frage_stellen(g, cfg, frage):
    """Stellt eine Frage, gibt Freigaben automatisch und protokolliert den Weg."""
    weg, freigaben, start = [], [], time.time()
    eingabe = {"messages": [HumanMessage(frage)]}
    for _ in range(5):                                   # max. 5 Freigaben pro Frage
        vorher = len(g.get_state(cfg).values.get("messages", []))
        g.invoke(eingabe, cfg)
        st = g.get_state(cfg)
        for m in st.values["messages"][vorher:]:
            if isinstance(m, AIMessage):
                for tc in m.tool_calls:
                    args = {k: (str(v)[:80] + "…" if len(str(v)) > 80 else v) for k, v in tc["args"].items()}
                    weg.append(f"{tc['name']}({args})")
            elif isinstance(m, ToolMessage) and m.content.startswith(("Fehler", "Vorschlag ungültig", "NICHT")):
                weg.append(f"  ↳ {m.content[:160]}")
            elif isinstance(m, HumanMessage) and m.additional_kwargs.get("agent_pruefung"):
                punkte = [z for z in str(m.content).splitlines() if z.startswith("- ")]
                weg.append("  🧪 Belegprüfung: " + " | ".join(p[2:120] for p in punkte))
        if not st.interrupts:
            break
        vs = st.interrupts[0].value["vorschlaege"]
        freigaben.extend(vs)
        eingabe = Command(resume={"genehmigt": True, "kommentar": "",
                                  "staende": {v["id"]: v.get("stand", "") for v in vs}})
    antwort = g.get_state(cfg).values["messages"][-1]
    text = antwort.content if isinstance(antwort.content, str) else str(antwort.content)
    return {"weg": weg, "freigaben": freigaben, "antwort": text.strip(), "sekunden": time.time() - start}


def main():
    original = profile.laden()
    kopie = kopie_anlegen(original.pfad)
    datenordner = Path(tempfile.mkdtemp(prefix="agent-echttest-daten-"))
    shutil.copytree(original.datenordner, datenordner, dirs_exist_ok=True)   # vorhandene Wissensbasis wiederverwenden
    p = profile.laden(pfad=str(kopie), datenordner_basis=datenordner)
    idx = ProjektIndex(p)
    idx.synchronisieren(log=lambda *_: None)
    g = graph.bauen(p, idx, db_pfad=datenordner / "echttest.sqlite")

    modell = " → ".join(config.modell_kette(p.modell))
    fragen = original.echttest or [{"frage": b} for b in original.beispiele]
    zeilen = [f"# Echttest: {p.name}", "",
              f"- Modell: **{config.ANBIETER} / {modell}**",
              f"- Embeddings: `{config.EMBED_MODEL}`",
              f"- Datum: {time.strftime('%d.%m.%Y %H:%M')}",
              f"- Änderungen wurden automatisch freigegeben, aber nur auf einer Kopie des Projekts.", ""]
    ergebnisse = []
    for i, f in enumerate(fragen, 1):
        cfg = {"configurable": {"thread_id": f"echt-{i}"}, "recursion_limit": 40}
        print(f"[{i}/{len(fragen)}] {f['frage']}", flush=True)
        try:
            r = frage_stellen(g, cfg, f["frage"])
        except Exception as e:
            r = {"weg": [], "freigaben": [], "antwort": f"FEHLER: {type(e).__name__}: {e}", "sekunden": 0}
        genutzt = {w.split("(")[0] for w in r["weg"] if not w.startswith(" ")}
        pruefungen = []
        if f.get("werkzeuge"):
            pruefungen.append((f"nutzt {' oder '.join(f['werkzeuge'])}", bool(genutzt & set(f["werkzeuge"]))))
        if f.get("beleg"):
            pruefungen.append(("zitiert `datei:zeile`", bool(BELEG.search(r["antwort"]))))
            pruefungen.append(("Belege echt (gelesen, existieren)", "Automatische Belegprüfung" not in r["antwort"]))
        if f.get("aenderung"):
            pruefungen.append(("Änderung vorgeschlagen + nach Freigabe geschrieben",
                               bool(r["freigaben"]) and "genehmigt" in str(g.get_state(cfg).values["messages"])))
        if f.get("enthaelt"):
            pruefungen.append((f"Antwort enthält „{f['enthaelt']}“", f["enthaelt"].lower() in r["antwort"].lower()))
        pruefungen.append(("Antwort nicht leer", bool(r["antwort"]) and not r["antwort"].startswith("FEHLER")))
        pruefungen.append(("keine unbelegten Angaben", "Automatische Belegprüfung" not in r["antwort"]))
        ok = all(x for _, x in pruefungen)
        ergebnisse.append(ok)
        print(f"     {'OK' if ok else 'PROBLEM'} in {r['sekunden']:.0f} s – Weg: {', '.join(sorted(genutzt)) or '—'}", flush=True)

        zeilen += [f"## {i}. {f['frage']}", "",
                   f"**{'✅ bestanden' if ok else '⚠️ nicht bestanden'}** · {r['sekunden']:.0f} s · "
                   + " · ".join(f"{'✔' if x else '✘'} {n}" for n, x in pruefungen), "",
                   "<details><summary>Weg des Agenten</summary>", "", "```"] + (r["weg"] or ["(keine Werkzeuge)"]) + ["```", "</details>", ""]
        for v in r["freigaben"]:
            zeilen += [f"Vorschlag `{v['werkzeug']}` → `{v['pfad']}`: {v['begruendung']}", "", "```diff", v["vorschau"][:2500], "```", ""]
        zeilen += ["**Antwort:**", "", "> " + r["antwort"][:3000].replace("\n", "\n> "), ""]

    zeilen.insert(6, f"\n**Ergebnis: {sum(ergebnisse)} von {len(ergebnisse)} Fragen bestanden.**")
    name = re.sub(r"[^\w.-]+", "-", config.modell_kette(p.modell)[0])
    ziel = AGENT / "docs" / f"echttest-{name}.md"
    ziel.parent.mkdir(exist_ok=True)
    ziel.write_text("\n".join(zeilen), encoding="utf-8")
    print(f"\nBericht: {ziel}\nERGEBNIS: {sum(ergebnisse)}/{len(ergebnisse)}", flush=True)


if __name__ == "__main__":
    os.chdir(AGENT)
    main()
