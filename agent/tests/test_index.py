"""Wissensbasis: richtige Dateien, korrekte Zeilennummern, Suche, inkrementelles Aktualisieren."""

import re
from pathlib import Path

from kern.index import tokens


def test_agent_und_interna_werden_nicht_indexiert(index):
    pfade = [p.as_posix() for p in index.dateien()]
    assert pfade, "keine Dateien gefunden"
    assert not any(p.startswith(("agent/", ".git/", ".venv/")) for p in pfade)
    for muster in index.p.ausschluss:
        if "/" not in muster and "*" not in muster:
            assert not any(muster in Path(p).parts for p in pfade), muster


def test_zeilennummern_stimmen_exakt(index):
    """Jeder Abschnitt muss genau den Zeilen von..bis der Datei entsprechen – sonst zitiert der Agent falsch."""
    roh = index.store._collection.get(include=["documents", "metadatas"])
    assert roh["documents"]
    for text, meta in zip(roh["documents"], roh["metadatas"]):
        kopf, _, inhalt = text.partition("\n")
        assert kopf == f"Datei: {meta['pfad']} (Zeilen {meta['von']}-{meta['bis']})"
        zeilen = (index.wurzel / meta["pfad"]).read_text(encoding="utf-8").splitlines()
        ausschnitt = zeilen[meta["von"] - 1: meta["bis"]]
        assert inhalt.splitlines() == [z.rstrip() if i == len(ausschnitt) - 1 else z
                                       for i, z in enumerate(ausschnitt)], meta


def test_bezeichner_werden_gefunden(index):
    """Ein Bezeichner, der nur in EINER Datei vorkommt, muss diese Datei auf Platz 1 bringen."""
    inhalte = {rel: (index.wurzel / rel).read_text(encoding="utf-8") for rel in index.dateien()}
    vorkommen = {}
    for rel, text in inhalte.items():
        for w in set(re.findall(r"\b[a-z_][a-z0-9_]{9,}\b", text)):
            vorkommen.setdefault(w, set()).add(rel)
    geprueft = 0
    for rel in inhalte:
        if rel.suffix not in (".py", ".sql"):
            continue
        eindeutig = sorted((w for w, dateien in vorkommen.items() if dateien == {rel}), key=len, reverse=True)
        if not eindeutig:
            continue
        treffer = [h["pfad"] for h in index.suchen(eindeutig[0], k=6)]
        assert treffer[0] == rel.as_posix(), (rel, eindeutig[0], treffer)
        geprueft += 1
    assert geprueft >= 2


def test_tokens_zerlegen_bezeichner():
    assert {"balance_cents", "balance", "cents"} <= set(tokens("balance_cents"))
    assert "transfer" in tokens("bank.transfer()")


def test_inkrementell(profil, index):
    assert index.synchronisieren(log=lambda *_: None)["neu_eingebettet"] == 0
    datei = next(p for p in index.dateien() if p.suffix == ".md")
    pfad = index.wurzel / datei
    pfad.write_text(pfad.read_text(encoding="utf-8") + "\n\nZebrastreifen-Testwort\n", encoding="utf-8")
    s = index.synchronisieren(log=lambda *_: None)
    assert s["neu_eingebettet"] == 1 and s["entfernt"] == 0
    assert index.suchen("Zebrastreifen-Testwort", k=3)[0]["pfad"] == datei.as_posix()

    pfad.unlink()
    s = index.synchronisieren(log=lambda *_: None)
    assert s["entfernt"] == 1
    assert all(h["pfad"] != datei.as_posix() for h in index.suchen("Zebrastreifen-Testwort", k=10))
