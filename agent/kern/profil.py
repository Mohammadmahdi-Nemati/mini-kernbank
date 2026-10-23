"""Projektprofil: agent/profil.yaml beschreibt alles, was diesen Agenten individuell macht."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import WURZEL

STANDARD_DATEIEN = ["**/*.py", "**/*.sql", "**/*.md", "**/*.toml", "**/*.yml",
                    "**/*.yaml", "**/*.txt", "**/*.cfg", "**/*.ini", "**/*.sh"]
STANDARD_AUSSCHLUSS = [".git", ".venv", "venv", "__pycache__", ".pytest_cache",
                       "node_modules", ".mypy_cache", ".idea", ".vscode",
                       "agent"]          # der Agent indexiert sich nicht selbst


@dataclass
class Profil:
    id: str
    name: str
    pfad: Path
    beschreibung: str
    wissen: str
    farbe: str = "blue"
    port: int = 7860
    emoji: str = "🤖"
    modell: str | None = None
    dateien: list = field(default_factory=lambda: list(STANDARD_DATEIEN))
    ausschluss: list = field(default_factory=lambda: list(STANDARD_AUSSCHLUSS))
    max_dateigroesse_kb: int = 300
    datenbank: dict | None = None        # {"dsn": ..., "hinweis": ...}
    befehle: dict = field(default_factory=dict)   # name -> {"befehl": [...], "beschreibung": ...}
    beispiele: list = field(default_factory=list)
    echttest: list = field(default_factory=list)  # Fragen für echttest.py (echtes Modell)
    datenordner_basis: Path = WURZEL / "daten"

    @property
    def datenordner(self) -> Path:
        self.datenordner_basis.mkdir(parents=True, exist_ok=True)
        return self.datenordner_basis


def laden(datei: Path | str | None = None, **ueberschreiben) -> Profil:
    datei = Path(datei or os.getenv("AGENT_PROFIL") or WURZEL / "profil.yaml")
    if not datei.exists():
        raise SystemExit(f"Profil nicht gefunden: {datei}")
    roh = yaml.safe_load(datei.read_text(encoding="utf-8"))
    roh.update(ueberschreiben)

    pfad = Path(roh.pop("pfad", "..")).expanduser()
    if not pfad.is_absolute():                     # relativ zur Profil-Datei
        pfad = datei.parent / pfad
    pfad = pfad.resolve()      # Symlinks auflösen (macOS: /var -> /private/var), sonst scheitern Pfadvergleiche
    if not pfad.is_dir():
        raise SystemExit(f"Projektordner nicht gefunden: {pfad}")

    # Die Datenbank-Adresse kann von außen gesetzt werden (start.command macht das,
    # wenn die Datenbank wegen eines belegten Ports auf 5433 ausweichen musste).
    if roh.get("datenbank") and os.getenv("AGENT_DB_DSN"):
        roh["datenbank"]["dsn"] = os.environ["AGENT_DB_DSN"]

    ausschluss = list(STANDARD_AUSSCHLUSS) + list(roh.pop("ausschluss_extra", []) or [])
    return Profil(id=roh.get("name", pfad.name), pfad=pfad, ausschluss=ausschluss, **roh)
