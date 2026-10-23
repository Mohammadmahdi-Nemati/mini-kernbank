"""
Chat-Oberfläche (Gradio) für den Projekt-Agenten.

    python app.py               # Agent starten, Browser öffnet sich
    python app.py --nur-index   # nur Wissensbasis aktualisieren
    python app.py --neu         # Wissensbasis + Gedächtnis löschen, neu aufbauen

Jeder Werkzeug-Schritt erscheint im Chat als aufklappbarer Kasten („Agenten-Weg“
wie im Video). Schlägt der Agent eine Änderung vor, erscheint unten ein Diff
mit „Übernehmen“ / „Ablehnen“. Schreibst du stattdessen eine Nachricht,
gilt das als Ablehnung mit deinem Kommentar.
"""

import argparse
import html
import json
import shutil
import uuid

import gradio as gr
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from kern import config, graph, guard, profil as profile
from kern.index import ProjektIndex

SYMBOLE = {"projekt_durchsuchen": "🔍", "datei_lesen": "📄", "dateien_auflisten": "🗂️",
           "text_suchen": "🔎", "datenbank_abfragen": "🗄️", "web_suchen": "🌐",
           "webseite_lesen": "🌐", "datei_aendern": "✏️", "datei_erstellen": "🆕",
           "befehl_ausfuehren": "▶️"}


def _kurz(args: dict) -> str:
    teile = []
    for k, v in args.items():
        if k in ("alter_text", "neuer_text", "inhalt"):
            v = f"[{len(str(v))} Zeichen]"
        teile.append(f"**{k}**: `{str(v)[:160]}`")
    return "  \n".join(teile)


def _html_diff(text: str) -> str:
    """Diff mit Farben: grün = neu, rot = entfernt."""
    farben = {"+": "#dafbe1", "-": "#ffebe9", "@": "#ddf4ff"}
    zeilen = []
    for z in text.splitlines():
        bg = "" if z.startswith(("+++", "---")) else farben.get(z[:1], "")
        stil = f"background:{bg};" if bg else ""
        zeilen.append(f'<div style="{stil}white-space:pre-wrap;color:#1f2328">{html.escape(z) or "&nbsp;"}</div>')
    return ('<div style="font-family:ui-monospace,Menlo,monospace;font-size:13px;border:1px solid #d0d7de;'
            'border-radius:8px;padding:8px;max-height:420px;overflow:auto;background:#fff">' + "".join(zeilen) + "</div>")


def oberflaeche(p, idx, agent):
    stat = idx.statistik()
    try:
        modelle = " → ".join(config.modell_kette(p.modell))
    except SystemExit as e:
        modelle = str(e)

    def cfg(thread):
        return {"configurable": {"thread_id": thread}, "recursion_limit": 60}

    # ------------------------------------------------------------- Ablauf

    def laufen(eingabe, verlauf, thread):
        """Lässt den Graphen laufen und zeigt jeden Schritt sofort an."""
        aus = gr.update(visible=False)
        try:
            for update in agent.stream(eingabe, cfg(thread), stream_mode="updates"):
                for knoten, daten in update.items():
                    if knoten == "__interrupt__":
                        vs = daten[0].value["vorschlaege"]
                        teile = []
                        for v in vs:
                            teile.append(f"<h3>{SYMBOLE.get(v['werkzeug'], '•')} <code>{html.escape(v['werkzeug'])}</code> → "
                                         f"<code>{html.escape(v['pfad'])}</code></h3><p>{html.escape(v['begruendung'])}</p>"
                                         + _html_diff(v["vorschau"]))
                        verlauf.append({"role": "assistant",
                                        "content": f"⏸️ **Ich warte auf deine Freigabe** ({len(vs)} Vorschlag/Vorschläge, siehe unten)."})
                        yield verlauf, gr.update(visible=True), "\n\n".join(teile)
                        return
                    if not daten:
                        continue
                    for m in daten.get("messages", []):
                        if knoten == "pruefung":
                            if isinstance(m, HumanMessage):           # Rückmeldung der Belegprüfung
                                verlauf.append({"role": "assistant", "content": str(m.content).split("\n", 1)[-1],
                                                "metadata": {"title": "🧪 Belegprüfung: Antwort wird korrigiert",
                                                             "status": "done"}})
                            elif isinstance(m, AIMessage):            # Antwort mit Prüfhinweis ersetzt die alte
                                for j in range(len(verlauf) - 1, -1, -1):
                                    if verlauf[j]["role"] == "assistant" and not verlauf[j].get("metadata"):
                                        verlauf[j]["content"] = str(m.content)
                                        break
                            continue
                        if isinstance(m, AIMessage):
                            text = m.content if isinstance(m.content, str) else "".join(
                                b.get("text", "") for b in m.content if isinstance(b, dict))
                            if text.strip():
                                verlauf.append({"role": "assistant", "content": text})
                            for tc in m.tool_calls:
                                verlauf.append({"role": "assistant", "content": _kurz(tc["args"]),
                                                "metadata": {"title": f"{SYMBOLE.get(tc['name'], '🔧')} {tc['name']}",
                                                             "status": "pending", "id": tc["id"]}})
                        elif isinstance(m, ToolMessage):
                            # Schritt-Kasten suchen (auch über eine Freigabe-Pause hinweg)
                            i = next((j for j in range(len(verlauf) - 1, -1, -1)
                                      if (verlauf[j].get("metadata") or {}).get("id") == m.tool_call_id), None)
                            if i is None:
                                continue
                            erg = str(m.content)
                            verlauf[i]["content"] += f"\n\n---\n```\n{erg[:1500].replace('```', 'ˋˋˋ')}\n```" \
                                                     + (f"\n… ({len(erg)} Zeichen)" if len(erg) > 1500 else "")
                            verlauf[i]["metadata"]["status"] = "done"
                    yield verlauf, aus, ""
        except GraphRecursionError:
            verlauf.append({"role": "assistant", "content": "⚠️ Zu viele Schritte hintereinander – ich habe abgebrochen. Formuliere die Aufgabe enger."})
        except SystemExit as e:
            verlauf.append({"role": "assistant", "content": f"⚠️ Konfiguration: {e}"})
        except Exception as e:
            verlauf.append({"role": "assistant", "content": f"⚠️ Fehler: `{type(e).__name__}: {str(e)[:500]}`\n\n"
                            "Häufige Ursachen: API-Schlüssel in `.env`, Rate-Limit des kostenlosen Modells, Ollama läuft nicht."})
        yield verlauf, aus, ""

    def wartet(thread) -> bool:
        return bool(agent.get_state(cfg(thread)).interrupts)

    def staende(thread) -> dict:
        """Fingerabdrücke der Dateien, wie der Nutzer sie im Diff gesehen hat."""
        st = agent.get_state(cfg(thread))
        if not st.interrupts:
            return {}
        return {v["id"]: v.get("stand", "") for v in st.interrupts[0].value["vorschlaege"]}

    # ------------------------------------------------------------ Handler

    def senden(text, verlauf, thread):
        verlauf = verlauf or []
        text = (text or "").strip()
        if not text:
            yield verlauf, "", gr.update(), gr.update()
            return
        verlauf.append({"role": "user", "content": text})

        if wartet(thread):        # Nachricht während offener Freigabe = Ablehnung mit Kommentar
            verlauf.append({"role": "assistant", "content": "❌ Vorschlag abgelehnt – dein Kommentar geht an den Agenten."})
            eingabe = Command(resume={"genehmigt": False, "kommentar": text})
        else:
            ok, grund = guard.eingabe_pruefen(text)
            if not ok:
                verlauf.append({"role": "assistant", "content": f"🛡️ Nicht verarbeitet: {grund}."})
                yield verlauf, "", gr.update(), gr.update()
                return
            eingabe = {"messages": [HumanMessage(text)]}

        yield verlauf, "", gr.update(visible=False), ""
        for v, panel, diff in laufen(eingabe, verlauf, thread):
            yield v, "", panel, diff

    def entscheiden(genehmigt):
        def handler(verlauf, thread):
            if not wartet(thread):
                yield verlauf, gr.update(visible=False), ""
                return
            verlauf.append({"role": "user", "content": "✅ Übernehmen" if genehmigt else "❌ Ablehnen"})
            yield verlauf, gr.update(visible=False), ""
            yield from laufen(Command(resume={"genehmigt": genehmigt, "kommentar": "",
                                              "staende": staende(thread)}), verlauf, thread)
        return handler

    def neuer_chat():
        return [], str(uuid.uuid4()), gr.update(visible=False), ""

    def index_neu():
        s = idx.synchronisieren(log=lambda *_: None)
        st = idx.statistik()
        return (f"✔ {s['neu_eingebettet']} Datei(en) neu eingelesen, {s['entfernt']} entfernt. "
                f"Jetzt {st['dateien']} Dateien / {st['stuecke']} Abschnitte.")

    # ------------------------------------------------------------- Layout

    with gr.Blocks(title=f"{p.emoji} {p.name} · Agent") as demo:
        thread = gr.State(lambda: str(uuid.uuid4()))
        gr.Markdown(f"# {p.emoji} {p.name} · Projekt-Agent\n{p.beschreibung}")
        with gr.Row():
            with gr.Column(scale=3):
                chat = gr.Chatbot(height=620, label="Chat", buttons=["copy", "copy_all"],
                                  placeholder=f"Frag mich alles zu **{p.name}** – Code, Datenbank, Ideen.")
                with gr.Group(visible=False) as freigabe:
                    gr.Markdown("## ✋ Freigabe nötig\nNichts wird geschrieben oder ausgeführt, bevor du zustimmst. "
                                "Für Änderungswünsche einfach unten eine Nachricht schreiben.")
                    diff = gr.HTML()
                    with gr.Row():
                        ja = gr.Button("✅ Übernehmen", variant="primary")
                        nein = gr.Button("❌ Ablehnen", variant="stop")
                with gr.Row():
                    eingabe = gr.Textbox(placeholder="Frage oder Auftrag … (Enter = senden, Shift+Enter = neue Zeile)",
                                         show_label=False, scale=8, lines=1, max_lines=10)
                    senden_btn = gr.Button("Senden", variant="primary", scale=1)
            with gr.Column(scale=1, min_width=260):
                gr.Markdown(f"**Projekt:** `{p.pfad}`  \n**Modell:** {modelle}  \n"
                            f"**Wissensbasis:** {stat['dateien']} Dateien, {stat['stuecke']} Abschnitte  \n"
                            f"**Datenbank:** {'ja (nur lesend)' if p.datenbank else 'nein'}  \n"
                            f"**Befehle:** {', '.join(p.befehle) or '–'}")
                neu_btn = gr.Button("🧹 Neuer Chat")
                idx_btn = gr.Button("🔄 Wissensbasis aktualisieren")
                idx_info = gr.Markdown()
                if p.beispiele:
                    gr.Examples(p.beispiele, inputs=eingabe, label="Beispiele")

        ausgaben = [chat, eingabe, freigabe, diff]
        eingabe.submit(senden, [eingabe, chat, thread], ausgaben)
        senden_btn.click(senden, [eingabe, chat, thread], ausgaben)
        ja.click(entscheiden(True), [chat, thread], [chat, freigabe, diff])
        nein.click(entscheiden(False), [chat, thread], [chat, freigabe, diff])
        neu_btn.click(neuer_chat, None, [chat, thread, freigabe, diff])
        idx_btn.click(index_neu, None, idx_info)
    return demo


def main():
    ap = argparse.ArgumentParser(description="Projekt-Agent starten")
    ap.add_argument("--nur-index", action="store_true", help="nur die Wissensbasis aktualisieren")
    ap.add_argument("--neu", action="store_true", help="Wissensbasis und Gedächtnis löschen und neu aufbauen")
    ap.add_argument("--kein-browser", action="store_true")
    ap.add_argument("--port", type=int, help="anderer Port als im Profil")
    a = ap.parse_args()

    p = profile.laden()
    if a.port:
        p.port = a.port
    if a.neu:
        shutil.rmtree(p.datenordner, ignore_errors=True)
    idx = ProjektIndex(p)
    print(f"\n{p.emoji}  {p.name}: Wissensbasis prüfen (beim ersten Mal lädt das Embedding-Modell, ~2,3 GB) …")
    s = idx.synchronisieren()
    print(f"   {s['dateien']} Dateien, {s['neu_eingebettet']} neu eingelesen, {s['entfernt']} entfernt.")
    if a.nur_index:
        print(json.dumps(idx.statistik()))
        return

    agent = graph.bauen(p, idx)
    demo = oberflaeche(p, idx, agent)
    print(f"   Öffne http://localhost:{p.port}  (Beenden: Ctrl+C)\n")
    demo.queue().launch(server_name="127.0.0.1", server_port=p.port, inbrowser=not a.kein_browser,
                        theme=gr.themes.Soft(primary_hue=p.farbe), footer_links=[])


if __name__ == "__main__":
    main()
