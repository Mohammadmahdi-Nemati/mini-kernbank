"""Das Profil muss zum Projekt passen: genannte Dateien existieren, Befehle sind gültig."""

import re


def test_genannte_dateien_existieren(original_profil):
    text = original_profil.wissen + "\n".join(original_profil.beispiele)
    pfade = set(re.findall(r"\b((?:sql|src|tests|docs|reports|generator)/[\w./-]+\.\w+)", text))
    assert pfade, "Profil nennt keine Dateien"
    fehlend = [p for p in pfade if not (original_profil.pfad / p).exists()]
    assert not fehlend, f"Profil verweist auf nicht existierende Dateien: {fehlend}"


def test_befehle_gueltig(original_profil):
    for name, b in original_profil.befehle.items():
        assert isinstance(b["befehl"], list) and b["befehl"], name
        assert b.get("beschreibung"), name


def test_datenbank_konfiguriert(original_profil):
    if original_profil.datenbank:
        assert original_profil.datenbank["dsn"].startswith("postgresql://")


def test_db_adresse_von_aussen_ueberschreibbar(monkeypatch, original_profil):
    from kern import profil as profile
    if not original_profil.datenbank:
        return
    monkeypatch.setenv("AGENT_DB_DSN", "postgresql://x:y@localhost:5433/z")
    assert profile.laden().datenbank["dsn"].endswith(":5433/z")
