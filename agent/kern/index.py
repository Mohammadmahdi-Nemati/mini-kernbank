"""
Wissensbasis eines Projekts: Code zerlegen, einbetten, hybrid durchsuchen.

Unterschiede zum Video:
  * Code-bewusstes Zerlegen (Python an Funktions-/Klassengrenzen, SQL an
    Anweisungen, Markdown an Überschriften) statt Fließtext-Absätzen.
  * Jedes Stück kennt Datei und Zeilen -> Antworten können `datei:zeile` zitieren.
  * Hybride Suche: Vektoren (Bedeutung) + BM25 (exakte Bezeichner wie
    `bank.transfer`), zusammengeführt per Reciprocal Rank Fusion.
  * Inkrementell: Beim Start werden nur geänderte Dateien neu eingebettet.
"""

import fnmatch
import hashlib
import json
import re
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

from .config import embedder
from .profil import Profil

CHUNK = 1400
UEBERLAPPUNG = 200

_SQL_TRENNER = ["\n-- ====", "\n-- ----", ";\n\n", "\nCREATE ", "\n-- ", ";\n", "\n\n", "\n", " ", ""]


# Leerraum NICHT abschneiden: sonst verliert die erste Code-Zeile ihre Einrückung.
_OPT = {"add_start_index": True, "strip_whitespace": False, "keep_separator": "end"}


def _splitter(endung: str):
    if endung == ".py":
        return RecursiveCharacterTextSplitter.from_language(
            Language.PYTHON, chunk_size=CHUNK, chunk_overlap=UEBERLAPPUNG, **_OPT)
    if endung == ".md":
        return RecursiveCharacterTextSplitter.from_language(
            Language.MARKDOWN, chunk_size=CHUNK, chunk_overlap=UEBERLAPPUNG, **_OPT)
    if endung == ".sql":
        return RecursiveCharacterTextSplitter(
            separators=_SQL_TRENNER, chunk_size=CHUNK, chunk_overlap=UEBERLAPPUNG, **_OPT)
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK, chunk_overlap=UEBERLAPPUNG, **_OPT)


_WORT = re.compile(r"[A-Za-zÄÖÜäöüß_][A-Za-zÄÖÜäöüß0-9_]*|\d+")


def tokens(text: str) -> list[str]:
    """Für BM25: Bezeichner zusätzlich an _ zerlegen (balance_cents -> balance, cents)."""
    out = []
    for w in _WORT.findall(text.lower()):
        out.append(w)
        if "_" in w:
            out.extend(t for t in w.split("_") if t)
    return out


class ProjektIndex:
    def __init__(self, profil: Profil):
        self.p = profil
        self.wurzel = profil.pfad.resolve()
        self._manifest_datei = profil.datenordner / "manifest.json"
        self._store = None
        self._bm25 = None
        self._alle: list[tuple[str, dict]] = []

    # ------------------------------------------------------------ Dateien

    def ist_ausgeschlossen(self, rel: Path) -> bool:
        teile = rel.parts
        return any(fnmatch.fnmatch(t, m) for t in teile for m in self.p.ausschluss) \
            or any(fnmatch.fnmatch(rel.as_posix(), m) for m in self.p.ausschluss)

    def dateien(self) -> list[Path]:
        gefunden = set()
        for muster in self.p.dateien:
            for f in self.wurzel.glob(muster):
                if not f.is_file():
                    continue
                rel = f.relative_to(self.wurzel)
                if self.ist_ausgeschlossen(rel):
                    continue
                if f.stat().st_size > self.p.max_dateigroesse_kb * 1024:
                    continue
                gefunden.add(rel)
        # Wurzel-Dateien ohne Endung, die wichtig sind
        for name in ("Dockerfile", "Makefile", ".gitignore"):
            if (self.wurzel / name).is_file():
                gefunden.add(Path(name))
        return sorted(gefunden)

    @staticmethod
    def _hash(pfad: Path) -> str:
        return hashlib.sha1(pfad.read_bytes()).hexdigest()

    # ------------------------------------------------------------ Zerlegen

    def _stuecke(self, rel: Path) -> list[Document]:
        text = (self.wurzel / rel).read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            return []
        roh = _splitter(rel.suffix.lower()).create_documents([text])
        docs = []
        for d in roh:
            # Abschnitt auf ganze Zeilen ausrichten: Beginn = Zeilenanfang, Ende ohne Leerzeilen.
            start = max(d.metadata.get("start_index", 0), 0)
            ende = start + len(d.page_content)
            while start < ende and text[start] == "\n":
                start += 1
            start = text.rfind("\n", 0, start) + 1
            if ende > 0 and ende < len(text) and text[ende - 1] != "\n":   # mitten in der Zeile → bis Zeilenende
                nl = text.find("\n", ende)
                ende = len(text) if nl == -1 else nl
            koerper = text[start:ende].rstrip()
            if not koerper.strip():
                continue
            von = text.count("\n", 0, start) + 1
            bis = von + koerper.count("\n")
            docs.append(Document(
                page_content=f"Datei: {rel.as_posix()} (Zeilen {von}-{bis})\n{koerper}",
                metadata={"pfad": rel.as_posix(), "von": von, "bis": bis, "nr": len(docs)},
            ))
        return docs

    # ------------------------------------------------------------ Speicher

    @property
    def store(self):
        if self._store is None:
            from langchain_chroma import Chroma
            self._store = Chroma(
                collection_name="code",
                embedding_function=embedder(),
                persist_directory=str(self.p.datenordner / "chroma"),
                collection_metadata={"hnsw:space": "cosine"},
            )
        return self._store

    def _manifest(self) -> dict:
        if self._manifest_datei.exists():
            return json.loads(self._manifest_datei.read_text())
        return {}

    def _entfernen(self, pfad: str):
        self.store._collection.delete(where={"pfad": pfad})

    def _hinzufuegen(self, rel: Path) -> int:
        docs = self._stuecke(rel)
        if docs:
            ids = [f"{d.metadata['pfad']}::{d.metadata['nr']}" for d in docs]
            for i in range(0, len(docs), 32):
                self.store.add_documents(docs[i:i + 32], ids=ids[i:i + 32])
        return len(docs)

    def synchronisieren(self, log=print) -> dict:
        """Bringt den Index auf den Stand der Dateien. Nur Geänderte werden neu eingebettet."""
        alt = self._manifest()
        neu = {rel.as_posix(): self._hash(self.wurzel / rel) for rel in self.dateien()}

        weg = [p for p in alt if p not in neu]
        geaendert = [p for p in neu if alt.get(p) != neu[p]]

        for p in weg:
            self._entfernen(p)
        stuecke = 0
        for n, p in enumerate(geaendert, 1):
            self._entfernen(p)
            stuecke += self._hinzufuegen(Path(p))
            log(f"  [{n}/{len(geaendert)}] {p}")

        self._manifest_datei.write_text(json.dumps(neu, indent=1))
        self._bm25 = None
        return {"dateien": len(neu), "neu_eingebettet": len(geaendert),
                "entfernt": len(weg), "stuecke_neu": stuecke}

    def datei_aktualisieren(self, rel: str):
        """Nach einer genehmigten Änderung: nur diese Datei neu einbetten."""
        m = self._manifest()
        datei = self.wurzel / rel
        self._entfernen(rel)
        if datei.exists():
            self._hinzufuegen(Path(rel))
            m[rel] = self._hash(datei)
        else:
            m.pop(rel, None)
        self._manifest_datei.write_text(json.dumps(m, indent=1))
        self._bm25 = None

    def schliessen(self):
        """Gibt die Dateien der Vektordatenbank frei (wichtig, wenn viele Indizes nacheinander
        geöffnet werden – macOS erlaubt standardmäßig nur 256 offene Dateien pro Prozess)."""
        if self._store is not None:
            try:
                self._store._client.close()
            except Exception:
                pass
            self._store = None
        self._bm25 = None

    def statistik(self) -> dict:
        return {"dateien": len(self._manifest()), "stuecke": self.store._collection.count()}

    # ------------------------------------------------------------ Suchen

    def _bm25_laden(self):
        if self._bm25 is None:
            from rank_bm25 import BM25Okapi
            roh = self.store._collection.get(include=["documents", "metadatas"])
            self._alle = list(zip(roh["documents"], roh["metadatas"]))
            korpus = [tokens(t) for t, _ in self._alle] or [[""]]
            self._bm25 = BM25Okapi(korpus)
        return self._bm25

    def suchen(self, frage: str, k: int = 6) -> list[dict]:
        """Hybride Suche, zusammengeführt mit Reciprocal Rank Fusion (RRF)."""
        punkte: dict[str, float] = {}
        treffer: dict[str, dict] = {}

        def schluessel(meta):
            return f"{meta['pfad']}::{meta['nr']}"

        # 1) Vektoren
        for rang, (d, _) in enumerate(self.store.similarity_search_with_score(frage, k=20)):
            s = schluessel(d.metadata)
            punkte[s] = punkte.get(s, 0) + 1 / (60 + rang)
            treffer[s] = {"text": d.page_content, **d.metadata}

        # 2) Stichworte
        bm25 = self._bm25_laden()
        if self._alle:
            werte = bm25.get_scores(tokens(frage))
            beste = sorted(range(len(werte)), key=lambda i: werte[i], reverse=True)[:20]
            for rang, i in enumerate(beste):
                if werte[i] <= 0:
                    break
                text, meta = self._alle[i]
                s = schluessel(meta)
                punkte[s] = punkte.get(s, 0) + 1 / (60 + rang)
                treffer.setdefault(s, {"text": text, **meta})

        # 3) Exakte Treffer: Bezeichner wie `bank.transfer` oder `balance_cents` und kurze
        #    Suchbegriffe, die wörtlich im Code stehen, bekommen einen Bonus. Bei Code ist
        #    ein wörtlicher Treffer fast immer relevanter als ein nur ähnlich klingender.
        begriffe = {w.lower() for w in re.findall(r"[\w.\-]{4,}", frage)
                    if re.search(r"[_.\-]|[a-z][A-Z]", w.strip(".-"))}
        if len(frage.strip()) <= 40:
            begriffe.add(frage.strip().lower())
        begriffe = {b.strip(".-") for b in begriffe if len(b.strip(".-")) >= 4}
        if begriffe:
            for text, meta in self._alle:
                klein = text.lower()
                if any(b in klein for b in begriffe):
                    s = schluessel(meta)
                    punkte[s] = punkte.get(s, 0) + 1 / 60
                    treffer.setdefault(s, {"text": text, **meta})

        reihenfolge = sorted(punkte, key=punkte.get, reverse=True)[:k]
        return [treffer[s] for s in reihenfolge]
