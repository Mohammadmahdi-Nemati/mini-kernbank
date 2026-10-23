"""
Die Werkzeuge des Agenten.

Lesende Werkzeuge laufen sofort. Werkzeuge, die etwas verändern
(Datei ändern/erstellen, Befehl ausführen), stehen in GENEHMIGUNG:
Der Graph hält vor ihnen an und zeigt dir erst eine Vorschau.
"""

import difflib
import fnmatch
import hashlib
import ipaddress
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from langchain_core.tools import StructuredTool

from . import config, guard
from .index import ProjektIndex
from .profil import Profil

GENEHMIGUNG = {"datei_aendern", "datei_erstellen", "befehl_ausfuehren"}
MAX_AUSGABE = 14000

_SOZIAL = ("instagram.com", "facebook.com", "pinterest.", "tiktok.com",
           "twitter.com", "x.com/", "apps.apple.com", "play.google.com")


# Funktionen, die auch in einer READ-ONLY-Transaktion Schaden anrichten oder Dateien des
# Datenbank-Servers lesen könnten (die Projektdatenbanken laufen als Superuser).
_SQL_GESPERRT = re.compile(
    r"\b(pg_terminate_backend|pg_cancel_backend|pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|"
    r"lo_import|lo_export|dblink\w*|pg_reload_conf|pg_rotate_logfile|pg_sleep\w*|set_config|"
    r"pg_advisory\w*|pg_promote|pg_switch_wal|query_to_xml\w*)\s*\(", re.I)


def _kuerzen(text: str, n: int = MAX_AUSGABE) -> str:
    return text if len(text) <= n else text[:n] + f"\n… [{len(text) - n} Zeichen gekürzt]"


def _sql_vorspann_entfernen(sql: str) -> str:
    """Entfernt führende Kommentare und Klammern – kleine Modelle schreiben oft `-- Abfrage` davor."""
    while True:
        alt = sql
        sql = sql.lstrip()
        if sql.startswith("--"):
            sql = sql.split("\n", 1)[1] if "\n" in sql else ""
        elif sql.startswith("/*") and "*/" in sql:
            sql = sql.split("*/", 1)[1]
        if sql == alt:
            return sql


def _oeffentliche_url(url: str) -> bool:
    """Nur öffentliche Adressen: keine lokalen Dienste, kein Heimnetz (Schutz vor SSRF)."""
    host = urlparse(url).hostname
    if not host:
        return False
    try:
        adressen = {i[4][0] for i in socket.getaddrinfo(host, None)}
    except OSError:
        return False
    for a in adressen:
        ip = ipaddress.ip_address(a.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast \
                or ip.is_unspecified:
            return False
    return True


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class Werkzeugkasten:
    def __init__(self, profil: Profil, index: ProjektIndex):
        self.p = profil
        self.idx = index
        self.wurzel = profil.pfad.resolve()
        # Werkzeugausgaben an das Kontextfenster anpassen (lokale Modelle haben wenig Platz).
        self.max_ausgabe = min(MAX_AUSGABE, max(3000, config.kontext_zeichen() // 6))

    # ================================================================ lesen

    def projekt_durchsuchen(self, frage: Annotated[str, "Suchfrage, gern mit Funktions-/Tabellennamen"],
                            anzahl: Annotated[int, "Anzahl Ausschnitte (1-12)"] = 6) -> str:
        """Durchsucht den gesamten Projektcode und die Doku nach Bedeutung UND exakten
        Bezeichnern. Erste Wahl für jede Frage zum Projekt. Liefert Ausschnitte mit Datei
        und Zeilennummern. Formuliere die Frage konkret, gern mit Funktions-/Tabellennamen."""
        hits = self.idx.suchen(frage, k=max(1, min(int(anzahl), 12)))
        if not hits:
            return "Keine Treffer im Projekt."
        return _kuerzen("\n\n---\n\n".join(h["text"] for h in hits), self.max_ausgabe)

    def datei_lesen(self, pfad: Annotated[str, "Pfad relativ zum Projekt, z. B. sql/02_functions.sql"],
                    von_zeile: Annotated[int, "erste Zeile (ab 1)"] = 1,
                    bis_zeile: Annotated[int | None, "letzte Zeile; leer = bis zu 400 Zeilen"] = None) -> str:
        """Liest eine Projektdatei mit Zeilennummern (max. 400 Zeilen pro Aufruf).
        Nutze das, um Code genau zu verstehen, bevor du etwas erklärst oder änderst."""
        ziel = guard.sicherer_pfad(self.wurzel, pfad)
        if not ziel.is_file():
            return f"Datei '{pfad}' existiert nicht. Nutze dateien_auflisten."
        zeilen = ziel.read_text(encoding="utf-8", errors="replace").splitlines()
        if not zeilen:
            return f"{pfad} ist leer."
        von = max(1, int(von_zeile or 1))
        if von > len(zeilen):
            return f"{pfad} hat nur {len(zeilen)} Zeilen – von_zeile={von} liegt dahinter."
        bis = int(bis_zeile) if bis_zeile else von + 399
        if bis < von:
            von, bis = bis, von
            von = max(1, von)
        bis = min(len(zeilen), bis, von + 399)
        teil = "\n".join(f"{i:>4}| {zeilen[i - 1]}" for i in range(von, bis + 1))
        rest = f"\n… Datei hat {len(zeilen)} Zeilen insgesamt." if bis < len(zeilen) else ""
        return _kuerzen(f"{pfad} (Zeilen {von}-{bis}):\n{teil}{rest}", self.max_ausgabe)

    def dateien_auflisten(self, unterordner: Annotated[str, "optional: nur diesen Ordner zeigen"] = "") -> str:
        """Zeigt die Dateistruktur des Projekts (alle indexierten Dateien mit Größe)."""
        basis = unterordner.strip("/")
        zeilen = []
        for rel in self.idx.dateien():
            if basis and not rel.as_posix().startswith(basis):
                continue
            kb = (self.wurzel / rel).stat().st_size / 1024
            zeilen.append(f"{rel.as_posix()}  ({kb:.1f} KB)")
        # Ordner, die nicht indexiert werden, trotzdem erwähnen
        andere = sorted(d.name + "/" for d in self.wurzel.iterdir()
                        if d.is_dir() and self.idx.ist_ausgeschlossen(Path(d.name)) and not d.name.startswith("."))
        extra = f"\n\nNicht indexiert (ausgeschlossen): {', '.join(andere)}" if andere else ""
        return ("\n".join(zeilen) or "Keine Dateien gefunden.") + extra

    def text_suchen(self, muster: Annotated[str, "regulärer Ausdruck, z. B. 'def transfer|FOR UPDATE'"],
                    dateimuster: Annotated[str, "z. B. '*.sql' oder 'src/*.py'; Standard: alle"] = "*") -> str:
        """Sucht einen regulären Ausdruck (Groß/klein egal) in allen Projektdateien, wie grep.
        Ideal, um alle Stellen zu finden, an denen eine Funktion/Tabelle/Variable vorkommt."""
        if len(muster) > 300:
            return "Muster zu lang (max. 300 Zeichen)."
        try:
            rx = re.compile(muster, re.I)
        except re.error as e:
            return f"Ungültiger regulärer Ausdruck: {e}"
        out = []
        for rel in self.idx.dateien():
            muster_ok = (fnmatch.fnmatch(rel.name, dateimuster) or fnmatch.fnmatch(rel.as_posix(), dateimuster)
                         or fnmatch.fnmatch(rel.as_posix(), dateimuster.removeprefix("**/")))
            if not muster_ok:
                continue
            text = (self.wurzel / rel).read_text(encoding="utf-8", errors="replace")
            for nr, z in enumerate(text.splitlines(), 1):
                if rx.search(z):
                    out.append(f"{rel.as_posix()}:{nr}: {z.strip()[:200]}")
                    if len(out) >= 80:
                        return "\n".join(out) + "\n… (mehr als 80 Treffer, Muster verfeinern)"
        return "\n".join(out) or "Keine Treffer."

    def datenbank_abfragen(self, sql: Annotated[str, "genau eine lesende SQL-Anweisung (SELECT/WITH/EXPLAIN)"]) -> str:
        """Führt EINE nur-lesende SQL-Abfrage (SELECT, WITH, EXPLAIN, SHOW) auf der
        laufenden Projektdatenbank aus und zeigt bis zu 50 Zeilen. Schreiben ist technisch
        unmöglich (READ ONLY-Transaktion). Für Schema: information_schema.columns abfragen."""
        if not self.p.datenbank:
            return "Für dieses Projekt ist keine Datenbank eingerichtet."
        sql = _sql_vorspann_entfernen(sql).strip().rstrip(";").strip()
        erstes = (sql.lstrip("(").split(None, 1)[0] if sql else "").lower()
        if erstes not in {"select", "with", "explain", "show", "table", "values"}:
            return "Nur lesende Abfragen erlaubt (SELECT, WITH, EXPLAIN, SHOW)."
        if ";" in re.sub(r"'(?:[^']|'')*'", "''", sql):      # ';' in Zeichenketten ist erlaubt
            return "Bitte nur eine einzelne Anweisung ohne ';' in der Mitte."
        if _SQL_GESPERRT.search(sql):
            return "Diese Systemfunktion ist für den Agenten gesperrt (Server-Dateien, Verbindungen, Sperren)."
        import psycopg
        try:
            with psycopg.connect(self.p.datenbank["dsn"], connect_timeout=5) as conn:
                conn.read_only = True
                with conn.cursor() as cur:
                    cur.execute("SET statement_timeout = '20s'")
                    schemas = self.p.datenbank.get("search_path")
                    if schemas:                     # z. B. "bakery" – Tabellen ohne Schema-Präfix finden
                        namen = [x.strip() for x in str(schemas).split(",")]
                        if all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", n) for n in namen):
                            cur.execute("SET search_path TO " + ", ".join(namen + ["public"]))
                    cur.execute(sql)
                    if cur.description is None:
                        return "Abfrage ausgeführt, keine Ergebniszeilen."
                    spalten = [c.name for c in cur.description]
                    zeilen = cur.fetchmany(50)
                    mehr = cur.fetchone() is not None
                conn.rollback()
        except psycopg.OperationalError as e:
            return (f"Datenbank nicht erreichbar ({str(e).splitlines()[0]}). "
                    f"Läuft Docker? Im Projektordner: docker compose up -d")
        except Exception as e:
            return f"SQL-Fehler: {type(e).__name__}: {str(e).strip()[:600]}"

        def zelle(v):
            return "NULL" if v is None else str(v).replace("|", "\\|").replace("\n", " ")[:120]
        tab = ["| " + " | ".join(spalten) + " |", "|" + "---|" * len(spalten)]
        tab += ["| " + " | ".join(zelle(v) for v in z) + " |" for z in zeilen]
        if mehr:
            tab.append(f"… (mehr als 50 Zeilen, nur die ersten 50 gezeigt)")
        return "\n".join(tab)

    def web_suchen(self, anfrage: Annotated[str, "Suchbegriffe, z. B. 'PostgreSQL SELECT FOR UPDATE Doku'"]) -> str:
        """Sucht im Internet (DuckDuckGo). Nur nutzen, wenn das Projekt selbst die Antwort
        nicht enthält: Bibliotheks-Doku, Fehlermeldungen, Best Practices, aktuelle Infos."""
        import time
        from ddgs import DDGS
        ergebnisse, warte = [], 2
        for versuch in range(3):
            try:
                ergebnisse = list(DDGS().text(anfrage, max_results=10))
                break
            except Exception as e:
                if versuch == 2:
                    return f"Websuche fehlgeschlagen ({type(e).__name__}). Später erneut versuchen."
                time.sleep(warte)
                warte *= 2
        gut = [r for r in ergebnisse if not any(s in (r.get("href") or "") for s in _SOZIAL)][:6]
        if not gut:
            return "Keine brauchbaren Web-Ergebnisse."
        text = "\n\n".join(f"[{i}] {r.get('title', '')}\nURL: {r.get('href', '')}\n{r.get('body', '')}"
                           for i, r in enumerate(gut, 1))
        return guard.rahmen(text, "Websuche")

    def webseite_lesen(self, url: Annotated[str, "vollständige https-Adresse, z. B. aus web_suchen"]) -> str:
        """Lädt eine Webseite und gibt den Haupttext zurück (z. B. eine Doku-Seite aus web_suchen)."""
        if not re.match(r"^https?://", url or ""):
            return "Nur http(s)-Adressen."
        if not _oeffentliche_url(url):
            return "Nur öffentliche Webseiten – lokale Adressen und Heimnetz sind gesperrt."
        import trafilatura
        try:
            roh = trafilatura.fetch_url(url)
            text = trafilatura.extract(roh, include_tables=True, include_formatting=True) if roh else None
        except Exception as e:
            return f"Seite nicht ladbar ({type(e).__name__})."
        if not text:
            return "Seite enthält keinen lesbaren Text oder blockiert den Abruf."
        return guard.rahmen(_kuerzen(text, min(10000, self.max_ausgabe)), url)

    # ============================================================ verändern
    # Diese drei laufen NUR nach deiner Freigabe. `vorschau` erzeugt den Diff dafür.

    def datei_aendern(self, pfad: Annotated[str, "Pfad relativ zum Projekt"],
                      alter_text: Annotated[str, "exakt kopierter Ausschnitt (mit Einrückung), der genau einmal vorkommt"],
                      neuer_text: Annotated[str, "Ersatz für alter_text"],
                      begruendung: Annotated[str, "1-2 Sätze auf Deutsch, warum"] = "") -> str:
        """SCHLÄGT eine Änderung an einer bestehenden Datei VOR: `alter_text` muss exakt
        (inkl. Einrückung) genau einmal in der Datei vorkommen und wird durch `neuer_text`
        ersetzt. Der Nutzer sieht vorher einen Diff und muss zustimmen. Immer zuerst
        datei_lesen. Für mehrere Stellen mehrere Aufrufe. `begruendung` auf Deutsch, 1-2 Sätze."""
        ziel, neu = self._neuer_inhalt_aendern(pfad, alter_text, neuer_text)
        rel = ziel.relative_to(self.wurzel).as_posix()        # vor dem Schreiben: scheitert nichts halb
        self._schreiben(ziel, neu)
        self._index_nachziehen(rel)
        return f"Änderung an {pfad} gespeichert (vom Nutzer genehmigt)."

    def datei_erstellen(self, pfad: Annotated[str, "Pfad relativ zum Projekt"],
                        inhalt: Annotated[str, "kompletter Dateiinhalt"],
                        begruendung: Annotated[str, "1-2 Sätze auf Deutsch, warum"] = "") -> str:
        """SCHLÄGT eine neue Datei VOR (oder das komplette Ersetzen einer bestehenden).
        Der Nutzer sieht vorher den Inhalt/Diff und muss zustimmen."""
        ziel = guard.sicherer_pfad(self.wurzel, pfad, schreiben=True)
        rel = ziel.relative_to(self.wurzel).as_posix()
        ziel.parent.mkdir(parents=True, exist_ok=True)
        self._schreiben(ziel, inhalt)
        self._index_nachziehen(rel)
        return f"Datei {pfad} gespeichert (vom Nutzer genehmigt)."

    def befehl_ausfuehren(self, name: Annotated[str, "Name eines freigegebenen Befehls aus dem Systemprompt"]) -> str:
        """SCHLÄGT VOR, einen der freigegebenen Projektbefehle auszuführen (z. B. Tests).
        Nur Namen aus der Liste im Systemprompt sind erlaubt. Der Nutzer muss zustimmen."""
        befehl = self._befehl(name)
        try:
            r = subprocess.run(befehl, cwd=self.wurzel, capture_output=True, text=True,
                               timeout=int(self.p.befehle[name].get("timeout", 300)),
                               env={**os.environ, "PYTHONUNBUFFERED": "1"})
        except subprocess.TimeoutExpired:
            return f"Abgebrochen: '{name}' hat das Zeitlimit überschritten."
        except FileNotFoundError as e:
            return f"Programm nicht gefunden: {e}"
        aus = (r.stdout or "") + ("\n[stderr]\n" + r.stderr if r.stderr.strip() else "")
        return f"Exit-Code {r.returncode}\n" + _kuerzen(aus[-self.max_ausgabe:], self.max_ausgabe)

    # ---------------------------------------------------------- Hilfen

    def _index_nachziehen(self, rel: str):
        """Die Datei ist geschrieben; ein Fehler beim Neu-Einlesen darf das nicht als Fehlschlag melden."""
        try:
            self.idx.datei_aktualisieren(rel)
        except Exception as e:
            print(f"Hinweis: Wissensbasis für {rel} nicht aktualisiert ({e}); beim nächsten Start automatisch.")

    @staticmethod
    def _lesen(ziel: Path) -> str:
        with open(ziel, encoding="utf-8", newline="") as f:     # Zeilenenden (CRLF) erhalten
            return f.read()

    @staticmethod
    def _schreiben(ziel: Path, text: str):
        with open(ziel, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def stand(self, name: str, args: dict) -> str:
        """Fingerabdruck der Zieldatei. Die Oberfläche schickt ihn mit der Freigabe zurück;
        weicht er beim Ausführen ab, wurde die Datei inzwischen verändert und es wird nichts geschrieben."""
        if name not in ("datei_aendern", "datei_erstellen"):
            return ""
        ziel = guard.sicherer_pfad(self.wurzel, args.get("pfad", ""), schreiben=True)
        return _hash(self._lesen(ziel)) if ziel.is_file() else "neu"

    def _neuer_inhalt_aendern(self, pfad, alter_text, neuer_text):
        ziel = guard.sicherer_pfad(self.wurzel, pfad, schreiben=True)
        if not ziel.is_file():
            raise ValueError(f"Datei '{pfad}' existiert nicht (für neue Dateien: datei_erstellen).")
        alt = self._lesen(ziel)
        if "\r\n" in alt and alter_text and "\r\n" not in alter_text:   # Datei mit Windows-Zeilenenden
            alter_text = alter_text.replace("\n", "\r\n")
            neuer_text = neuer_text.replace("\n", "\r\n")
        n = alt.count(alter_text) if alter_text else 0
        if n == 0:
            raise ValueError("`alter_text` kommt in der Datei nicht vor. Lies die Datei erneut und "
                             "kopiere die Stelle exakt (Einrückung!).")
        if n > 1:
            raise ValueError(f"`alter_text` kommt {n}-mal vor. Nimm mehr umgebende Zeilen dazu, "
                             "damit die Stelle eindeutig ist.")
        return ziel, alt.replace(alter_text, neuer_text, 1)

    def _befehl(self, name):
        if name not in self.p.befehle:
            raise ValueError(f"Unbekannter Befehl '{name}'. Erlaubt: {', '.join(self.p.befehle) or 'keine'}")
        venv_py = self.wurzel / ".venv" / "bin" / "python"
        python = str(venv_py) if venv_py.exists() else sys.executable
        return [t.replace("{python}", python) for t in self.p.befehle[name]["befehl"]]

    @staticmethod
    def _diff(alt: str, neu: str, pfad: str) -> str:
        d = difflib.unified_diff(alt.splitlines(keepends=True), neu.splitlines(keepends=True),
                                 fromfile=f"a/{pfad}", tofile=f"b/{pfad}", n=3)
        return "".join(d) or "(keine inhaltliche Änderung)"

    def vorschau(self, name: str, args: dict) -> str:
        """Erzeugt, was der Nutzer vor der Freigabe sieht. Wirft ValueError, wenn ungültig."""
        if name == "datei_aendern":
            ziel, neu = self._neuer_inhalt_aendern(args.get("pfad", ""), args.get("alter_text", ""),
                                                   args.get("neuer_text", ""))
            return self._diff(self._lesen(ziel), neu, args["pfad"])
        if name == "datei_erstellen":
            ziel = guard.sicherer_pfad(self.wurzel, args.get("pfad", ""), schreiben=True)
            if ziel.is_dir():
                raise ValueError(f"'{args.get('pfad')}' ist ein Ordner, keine Datei.")
            alt = self._lesen(ziel) if ziel.is_file() else ""
            neu = args.get("inhalt", "")
            warnung = ""
            a, b = alt.count("\n"), neu.count("\n")
            if alt and b < a * 0.7:
                warnung = (f"⚠ ACHTUNG: Die bestehende Datei wird komplett ersetzt und schrumpft von {a} auf {b} Zeilen. "
                           "Prüfe, ob dabei nichts verloren geht.\n")
            return warnung + self._diff(alt, neu, args["pfad"])
        if name == "befehl_ausfuehren":
            n = args.get("name", "")
            befehl = self._befehl(n)
            return f"$ cd {self.wurzel.name} && {' '.join(befehl)}\n# {self.p.befehle[n].get('beschreibung', '')}"
        raise ValueError(f"Keine Vorschau für {name}")

    # ---------------------------------------------------------- Export

    def alle(self) -> dict:
        namen = ["projekt_durchsuchen", "datei_lesen", "dateien_auflisten", "text_suchen",
                 "web_suchen", "webseite_lesen", "datei_aendern", "datei_erstellen"]
        if self.p.datenbank:
            namen.insert(4, "datenbank_abfragen")
        if self.p.befehle:
            namen.append("befehl_ausfuehren")
        return {n: getattr(self, n) for n in namen}

    def als_tools(self):
        if not hasattr(self, "_tools"):
            self._tools = {n: StructuredTool.from_function(f, name=n) for n, f in self.alle().items()}
        return list(self._tools.values())

    def pruefen(self, name: str, args: dict) -> dict:
        """Prüft und wandelt Argumente nach dem Werkzeug-Schema (z. B. "10" -> 10).
        Wirft ValueError mit einer für das Modell verständlichen Meldung."""
        self.als_tools()
        werkzeug = self._tools[name]
        try:
            return werkzeug.args_schema.model_validate(args or {}).model_dump()
        except Exception as e:
            fehler = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in getattr(e, "errors", lambda: [])())
            erlaubt = ", ".join(werkzeug.args_schema.model_fields)
            raise ValueError(f"Ungültige Argumente für {name} ({fehler or e}). Erlaubte Argumente: {erlaubt}")

    def ausfuehren(self, name: str, args: dict) -> str:
        args = self.pruefen(name, args)
        return str(self.alle()[name](**args))
