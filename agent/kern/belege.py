"""
Belegprüfung: Stimmt, was das Modell behauptet, mit dem überein, was es gesehen hat?

Entstanden aus dem Echttest mit qwen3:14b (22.10.2026). Das Modell hat dort
  * `sql/02_functions.sql:123` zitiert, ohne die Datei je gelesen zu haben (die Zeile gibt es nicht),
  * SQL-Code „aus sql/03_aml.sql“ wiedergegeben, der dort nicht steht,
  * eine Antwort mit „(Web)“ und einer URL markiert, ohne im Web gesucht zu haben.

Wie der Bewerter im Tutorial („reichen die gefundenen Stücke?“) prüft dieser Schritt die
fertige Antwort – aber regelbasiert und damit zuverlässig, ohne zweiten Modellaufruf.
Findet er etwas, bekommt das Modell die Antwort mit einer konkreten Rückmeldung zurück.
"""

import re

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

KENNUNG = "agent_pruefung"
WEB_WERKZEUGE = {"web_suchen", "webseite_lesen"}
_ZITAT = re.compile(r"`?([\w./-]+\.(?:py|sql|md|toml|ya?ml|txt|sh|cfg|ini)):(\d+)(?:\s*[-–]\s*(\d+))?")
_URL = re.compile(r"https?://[^\s)>\]`\"']+")
_STELLE = re.compile(r"\b(zeig\w*|wo)\b.{0,40}\b(stelle|steht|definiert|code|zeile)", re.I)
_RECHERCHE = re.compile(r"recherch|im (web|internet)|online nach|such(e|en)? im (web|netz|internet)|offiziellen? doku", re.I)
_BEREICHE = [
    re.compile(r"Datei: (\S+) \(Zeilen (\d+)-(\d+)\)"),          # projekt_durchsuchen
    re.compile(r"^(\S+) \(Zeilen (\d+)-(\d+)\):", re.M),          # datei_lesen
]
_GREP = re.compile(r"^(\S+?):(\d+): ", re.M)                       # text_suchen


def ist_pruefung(m) -> bool:
    return isinstance(m, HumanMessage) and bool(m.additional_kwargs.get(KENNUNG))


def letzte_frage_index(msgs) -> int:
    return max((i for i, m in enumerate(msgs) if isinstance(m, HumanMessage) and not ist_pruefung(m)), default=0)


def _gesehen(msgs):
    """Welche Dateibereiche und welcher Text sind in Werkzeugausgaben aufgetaucht?"""
    bereiche: dict[str, list[tuple[int, int]]] = {}
    text = []
    for m in msgs:
        if isinstance(m, ToolMessage):
            inhalt = str(m.content)
            text.append(inhalt)
            for rx in _BEREICHE:
                for pfad, von, bis in rx.findall(inhalt):
                    bereiche.setdefault(pfad, []).append((int(von), int(bis)))
            for pfad, zeile in _GREP.findall(inhalt):
                bereiche.setdefault(pfad, []).append((int(zeile), int(zeile)))
        elif isinstance(m, HumanMessage) and not ist_pruefung(m):
            text.append(str(m.content))
    return bereiche, "\n".join(text)


def probleme(antwort: str, msgs, projektdateien: dict[str, int]) -> list[str]:
    """`projektdateien`: relativer Pfad -> Zeilenzahl. Gibt eine Liste verständlicher Probleme zurück."""
    if not antwort.strip():
        # Echttest Lauf 2: qwen3 las die Datei und lieferte dann eine leere Antwort ohne Werkzeugaufruf.
        return ["Deine Antwort war leer. Führe den nächsten Schritt aus (z. B. den Änderungsvorschlag mit "
                "datei_aendern) oder antworte dem Nutzer."]
    bereiche, gesehen_text = _gesehen(msgs)
    out = []

    # 1) Zitate datei:zeile
    for pfad, von, bis in _ZITAT.findall(antwort):
        pfad = pfad.strip("`./")
        von = int(von)
        bis = int(bis) if bis else von
        if pfad not in projektdateien:
            if any(p.endswith("/" + pfad) for p in projektdateien):
                continue                                     # Kurzform, z. B. iban.py:3
            out.append(f"`{pfad}:{von}` – diese Datei gibt es im Projekt nicht.")
            continue
        if von > projektdateien[pfad]:
            out.append(f"`{pfad}:{von}` – die Datei hat nur {projektdateien[pfad]} Zeilen.")
            continue
        if not any(a - 5 <= von and bis <= b + 5 for a, b in bereiche.get(pfad, [])):
            out.append(f"`{pfad}:{von}` – diese Stelle hast du nicht gelesen. Lies sie mit datei_lesen, "
                       f"bevor du sie zitierst.")

    # 2) genannte Projektdateien, die in keiner Werkzeugausgabe vorkamen
    for pfad in projektdateien:
        if "/" in pfad and pfad in antwort and pfad not in gesehen_text and pfad not in bereiche:
            out.append(f"Du nennst `{pfad}`, hast die Datei aber nicht gelesen. Lies sie, bevor du "
                       f"Aussagen über ihren Inhalt machst.")

    # 3) Web-Angaben ohne Websuche
    web_genutzt = any(isinstance(m, AIMessage) and any(tc["name"] in WEB_WERKZEUGE for tc in m.tool_calls)
                      for m in msgs)
    fremde_urls = [u.rstrip(".,;:") for u in _URL.findall(antwort) if u.rstrip(".,;:") not in gesehen_text]
    if "(Web)" in antwort and not web_genutzt:
        out.append("Du markierst etwas mit „(Web)“, hast aber nicht im Web gesucht. Nutze web_suchen "
                   "oder entferne die Markierung.")
    if fremde_urls:
        out.append(f"Diese Adresse(n) stammen aus keinem Suchergebnis: {', '.join(fremde_urls[:3])}. "
                   f"Nenne nur URLs aus web_suchen/webseite_lesen.")

    i = letzte_frage_index(msgs)
    frage = str(msgs[i].content) if msgs else ""

    # 4) „Zeig mir die Stelle“ ohne Stellenangabe (Echttest Lauf 2, Frage 1)
    if _STELLE.search(frage) and not _ZITAT.search(antwort):
        out.append("Der Nutzer fragt nach der Stelle im Code. Nenne sie mit `pfad:zeile` "
                   "(die Zeilennummern stehen in den Werkzeugergebnissen).")

    # 5) ausdrücklich verlangte Recherche
    seit_frage = msgs[i:]
    if _RECHERCHE.search(frage) and not any(
            isinstance(m, AIMessage) and any(tc["name"] in WEB_WERKZEUGE for tc in m.tool_calls) for m in seit_frage):
        out.append("Der Nutzer hat ausdrücklich eine Recherche verlangt. Nutze web_suchen (und ggf. webseite_lesen).")

    return list(dict.fromkeys(out))


def rueckmeldung(liste: list[str]) -> HumanMessage:
    text = ("[Automatische Belegprüfung – keine Nachricht des Nutzers]\n"
            "Deine Antwort enthält Angaben, die nicht durch Werkzeugergebnisse belegt sind:\n"
            + "\n".join(f"- {p}" for p in liste)
            + "\nPrüfe die Stellen mit den Werkzeugen und gib dann eine korrigierte, vollständige Antwort. "
              "Wenn du etwas nicht belegen kannst, sag das ehrlich.")
    return HumanMessage(content=text, additional_kwargs={KENNUNG: True})
