"""
Konfiguration: Modelle und Embeddings.

Wie im Video steht kein Modellname im Code, alles kommt aus der .env.
Anders als im Video ist der Anbieter frei wählbar (NVIDIA, Claude, Ollama ...),
und die Ausfallkette nutzt LangChains eingebautes `with_fallbacks`.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

WURZEL = Path(__file__).resolve().parents[1]
load_dotenv(WURZEL / ".env")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")      # Chroma: keine Telemetrie, keine Warnungen

ANBIETER = os.getenv("LLM_ANBIETER", "nvidia").strip().lower()
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "16384"))


def kontext_zeichen() -> int:
    """Wie viele Zeichen das Modell insgesamt sehen kann (grob: 1 Token ≈ 2,6 Zeichen Deutsch/Code).

    Wichtig für lokale Modelle: Ollama schneidet bei Überlauf STILL den Anfang ab –
    also den Systemprompt und die eigentliche Frage. Der Graph hält sich deshalb an
    dieses Budget (siehe graph._kuerzen)."""
    if os.getenv("KONTEXT_ZEICHEN"):
        return int(os.environ["KONTEXT_ZEICHEN"])
    if ANBIETER == "ollama":
        return int(OLLAMA_NUM_CTX * 2.6)
    return 300_000

_BASE_URLS = {
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openai": None,
}
_KEYS = {
    "nvidia": "NVIDIA_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
}


def modell_kette(hauptmodell: str | None = None) -> list[str]:
    haupt = hauptmodell or os.getenv("MODELL", "")
    extra = [m.strip() for m in os.getenv("FALLBACK_MODELLE", "").split(",") if m.strip()]
    kette = [m for m in [haupt, *extra] if m]
    if not kette:
        raise SystemExit("Kein Modell eingetragen. Setze MODELL in der .env.")
    return list(dict.fromkeys(kette))


def _ein_modell(name: str):
    if ANBIETER == "anthropic":
        from langchain_anthropic import ChatAnthropic
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise SystemExit("ANTHROPIC_API_KEY fehlt in der .env.")
        return ChatAnthropic(model=name, temperature=0.1, max_tokens=8192,
                             timeout=120, max_retries=3)

    if ANBIETER == "ollama":
        # Eigener Ollama-Client statt der OpenAI-Schnittstelle: nur so lässt sich das
        # Kontextfenster setzen. Ollamas Standard (oft 4096 Tokens) ist für Systemprompt
        # + Werkzeugergebnisse zu klein – das Modell "vergisst" dann still den Anfang.
        from langchain_ollama import ChatOllama
        basis = os.getenv("BASE_URL") or "http://localhost:11434"
        return ChatOllama(model=name, temperature=0.1,
                          num_ctx=OLLAMA_NUM_CTX,
                          base_url=basis,
                          reasoning=_ollama_denken(name, basis),
                          client_kwargs={"timeout": 900})

    from langchain_openai import ChatOpenAI
    if ANBIETER not in _BASE_URLS:
        raise SystemExit(f"Unbekannter Anbieter '{ANBIETER}'. Erlaubt: nvidia, anthropic, ollama, openai, groq")
    key = os.getenv(_KEYS[ANBIETER], "")
    if not key or key.endswith("xxxxxxxx"):
        raise SystemExit(f"{_KEYS[ANBIETER]} fehlt in der .env.")
    return ChatOpenAI(
        model=name,
        base_url=os.getenv("BASE_URL") or _BASE_URLS[ANBIETER],
        api_key=key,
        temperature=0.1,
        timeout=180,
        max_retries=3,      # exponentielles Warten bei 429, wie im Video
    )


def _ollama_denken(name: str, basis: str):
    """Denkmodelle (qwen3, deepseek-r1 …) „denken“ vor jedem Schritt laut nach. Im Echttest mit
    qwen3:14b auf einem MacBook Air (16 GB) dauerte eine einzige Frage dadurch bis zu 16 Minuten.
    Standard deshalb: Denken AUS, wenn das Modell es kann. OLLAMA_DENKEN=an schaltet es ein."""
    wunsch = os.getenv("OLLAMA_DENKEN", "aus").strip().lower()
    try:
        import json
        import urllib.request
        anfrage = urllib.request.Request(f"{basis}/api/show", data=json.dumps({"model": name}).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(anfrage, timeout=5) as r:
            faehig = "thinking" in json.load(r).get("capabilities", [])
    except Exception:
        return None                          # unbekannt: Ollama-Standard verwenden
    if not faehig:
        return None
    return wunsch in ("an", "ja", "1", "true")


def chat_modell(werkzeuge, hauptmodell: str | None = None):
    """Modell mit Werkzeugen. Fällt eins aus, übernimmt das nächste der Kette."""
    kette = [_ein_modell(n).bind_tools(werkzeuge) for n in modell_kette(hauptmodell)]
    return kette[0].with_fallbacks(kette[1:]) if len(kette) > 1 else kette[0]


def embedder():
    """Lokale Embeddings: kostenlos, ohne Rate-Limit, nach dem ersten Download offline."""
    if EMBED_MODEL == "fake":                       # nur für automatische Tests
        from langchain_core.embeddings import DeterministicFakeEmbedding
        return DeterministicFakeEmbedding(size=256)

    from langchain_huggingface import HuggingFaceEmbeddings
    geraet = "cpu"
    try:
        import torch
        if torch.backends.mps.is_available():       # Apple-Silicon-GPU
            geraet = "mps"
    except Exception:
        pass
    return HuggingFaceEmbeddings(
        model_name=EMBED_MODEL,
        model_kwargs={"device": geraet},
        encode_kwargs={"normalize_embeddings": True, "batch_size": 16},
    )
