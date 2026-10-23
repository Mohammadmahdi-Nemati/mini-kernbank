"""
Gemeinsame Test-Bausteine.

Die Tests brauchen KEIN Sprachmodell und KEINEN API-Schlüssel:
  * Embeddings: deterministische Fake-Embeddings (EMBED_MODEL=fake)
  * Sprachmodell: `SkriptModell` spielt vorher festgelegte Antworten ab,
    inklusive Werkzeug-Aufrufen. So wird der Graph selbst exakt geprüft.
Alles, was schreibt, läuft auf einer KOPIE des Projekts in einem Temp-Ordner.
Datenbank-Tests werden übersprungen, wenn die Projektdatenbank nicht läuft.
"""

import os
import shutil
from pathlib import Path

try:   # macOS erlaubt standardmäßig nur 256 offene Dateien; Chroma braucht mehrere je Index
    import resource
    _weich, _hart = resource.getrlimit(resource.RLIMIT_NOFILE)
    _ziel = 4096 if _hart == resource.RLIM_INFINITY else min(4096, _hart)
    if _weich < _ziel:
        resource.setrlimit(resource.RLIMIT_NOFILE, (_ziel, _hart))
except (ImportError, ValueError, OSError):
    pass

os.environ["EMBED_MODEL"] = "fake"

import pytest  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402

from kern import profil as profile  # noqa: E402
from kern.index import ProjektIndex  # noqa: E402
from kern.werkzeuge import Werkzeugkasten  # noqa: E402

AGENT = Path(__file__).resolve().parents[1]


class SkriptModell:
    """Ersetzt das Sprachmodell: gibt der Reihe nach die vorbereiteten Antworten zurück."""

    def __init__(self, antworten):
        self.antworten = list(antworten)
        self.aufrufe = []          # was das "Modell" jeweils zu sehen bekam

    def invoke(self, nachrichten):
        self.aufrufe.append(nachrichten)
        if not self.antworten:
            return AIMessage("(Skript zu Ende)")
        return self.antworten.pop(0)


def werkzeugaufruf(name, args, id_):
    return {"name": name, "args": args, "id": id_, "type": "tool_call"}


@pytest.fixture(scope="session")
def original_profil():
    return profile.laden(AGENT / "profil.yaml")


@pytest.fixture
def projektkopie(tmp_path, original_profil):
    """Kopie des Projekts (ohne agent/, .git, .venv, data), damit Tests gefahrlos schreiben können."""
    ziel = tmp_path / "projekt"
    ignorieren = shutil.ignore_patterns("agent", ".git", ".venv", "__pycache__", "data", ".pytest_cache")
    shutil.copytree(original_profil.pfad, ziel, ignore=ignorieren)
    return ziel


@pytest.fixture
def profil(projektkopie, tmp_path):
    return profile.laden(AGENT / "profil.yaml", pfad=str(projektkopie),
                         datenordner_basis=tmp_path / "daten")


@pytest.fixture
def index(profil):
    idx = ProjektIndex(profil)
    idx.synchronisieren(log=lambda *_: None)
    yield idx
    idx.schliessen()


@pytest.fixture
def kasten(profil, index):
    return Werkzeugkasten(profil, index)


@pytest.fixture
def db_kasten(original_profil, tmp_path):
    """Werkzeugkasten auf dem ECHTEN Projekt, aber nur für lesende Datenbank-Tests."""
    if not original_profil.datenbank:
        pytest.skip("Projekt hat keine Datenbank")
    import psycopg
    try:
        psycopg.connect(original_profil.datenbank["dsn"], connect_timeout=3).close()
    except Exception:
        pytest.skip("Projektdatenbank läuft nicht (docker compose up -d)")
    p = profile.laden(AGENT / "profil.yaml", datenordner_basis=tmp_path / "daten")
    idx = ProjektIndex(p)
    yield Werkzeugkasten(p, idx)
    idx.schliessen()
