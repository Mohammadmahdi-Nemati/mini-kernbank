"""Führt die Experimente aus und schreibt die Ergebnisse nach reports/.

    python run.py nebenlaeufigkeit   # Teil 1: gleichzeitige Überweisungen
    python run.py aml                # Teil 2: Daten simulieren und Regeln bewerten
    python run.py alles
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from kernbank import concurrency, db, evaluate, generator  # noqa: E402

REPORTS = Path(__file__).parent / "reports"


def eur(cents: int) -> str:
    return f"{cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def pct(value: float) -> str:
    return f"{100 * value:.0f} %"


def run_concurrency() -> None:
    results = concurrency.run_all()
    c = concurrency
    lines = [
        "# Ergebnis Teil 1: gleichzeitige Überweisungen", "",
        f"{c.N_THREADS} parallele Verbindungen buchen je {c.TRANSFERS_PER_THREAD} zufällige Überweisungen "
        f"zwischen {c.N_ACCOUNTS} Konten (Startguthaben je {eur(c.START_CENTS)}). "
        f"Die Geldmenge muss am Ende exakt {eur(c.N_ACCOUNTS * c.START_CENTS)} betragen.", "",
        "| Variante | gebucht | abgelehnt | Wiederholungen | Geldmenge am Ende | Konten mit falschem Stand | Summe der Abweichungen | Dauer |",
        "|---|---|---|---|---|---|---|---|",
    ]
    labels = {"naiv": "1. naiv (lesen, rechnen, schreiben)",
              "serialisierbar": "2. naiv + SERIALIZABLE + Wiederholung",
              "gesperrt": "3. `bank.transfer()` mit Zeilensperren"}
    for r in results:
        lines.append(f"| {labels[r.variante]} | {r.gebucht} | {r.abgelehnt} | {r.wiederholungen} | "
                     f"{eur(r.ist_cents)} | {r.konten_falsch} von {c.N_ACCOUNTS} | {eur(r.fehlbetrag_cents)} | "
                     + f"{r.sekunden:.1f} s |".replace(".", ","))
    lines += ["", "„Konten mit falschem Stand“: Der gespeicherte Kontostand weicht von der Summe der "
              "Buchungszeilen ab (Sicht `bank.v_reconciliation`).", "",
              "Die genauen Zahlen der naiven Variante schwanken von Lauf zu Lauf, weil sie vom Zufall der "
              "zeitlichen Überschneidung abhängen. Die Varianten 2 und 3 müssen immer 0 Abweichungen zeigen."]
    (REPORTS / "nebenlaeufigkeit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def run_aml() -> None:
    world = generator.build_world()
    rows = generator.finalize(world)
    with db.connect() as conn:
        db.reset_schema(conn)
        stats = generator.load(conn, world, rows)
        diff = conn.execute("SELECT count(*) FROM bank.v_reconciliation WHERE diff_cents <> 0").fetchone()[0]
        lines = [
            "# Ergebnis Teil 2: Geldwäsche-Erkennung", "",
            f"Simulation: {stats['kunden']} Kunden, " + f"{stats['buchungen']:,}".replace(",", ".")
            + f" Buchungen in {generator.DAYS} Tagen (1. April bis 30. Juni 2026). "
            f"Konten mit falschem Stand nach dem Laden: {diff}.", "",
            "## Regeln mit den Standard-Schwellenwerten", "",
            "| Regel | Alarme | richtig | Fehlalarme | verpasst | Precision | Recall |",
            "|---|---|---|---|---|---|---|",
        ]
        for m in evaluate.metrics(conn):
            lines.append(f"| {m.regel} | {m.alarme} | {m.treffer} | {m.fehlalarme} | {m.verpasst} | "
                         f"{pct(m.precision)} | {pct(m.recall)} |")
        lines += ["", "Precision: Anteil der Alarme, die wirklich verdächtig sind. "
                  "Recall: Anteil der verdächtigen Konten, die gefunden wurden.", "",
                  "## Erkennung nach Variante", "",
                  "| Muster | Variante | Konten | erkannt |", "|---|---|---|---|"]
        for muster, variante, konten, erkannt in evaluate.by_variant(conn):
            lines.append(f"| {muster} | {variante} | {konten} | {erkannt} |")
        lines += ["", "## Was passiert, wenn man die Schwellenwerte ändert?", "",
                  "| Änderung | Alarme | richtig | Fehlalarme | verpasst | Precision | Recall |",
                  "|---|---|---|---|---|---|---|"]
        for name, m in evaluate.scenarios(conn):
            lines.append(f"| {name} | {m.alarme} | {m.treffer} | {m.fehlalarme} | {m.verpasst} | "
                         f"{pct(m.precision)} | {pct(m.recall)} |")
        lines += ["", "## Beispiele für Alarme", "",
                  "| Regel | Bewertung | Kunde (erfunden) | Begründung des Alarms |", "|---|---|---|---|"]
        for regel, richtig, name, grund in evaluate.examples(conn):
            lines.append(f"| {regel} | {'richtig' if richtig else 'Fehlalarm'} | {name} | {grund} |")
    (REPORTS / "aml.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "alles"
    if what not in ("nebenlaeufigkeit", "aml", "alles"):
        sys.exit(__doc__)
    REPORTS.mkdir(exist_ok=True)
    if what in ("nebenlaeufigkeit", "alles"):
        run_concurrency()
    if what in ("aml", "alles"):       # zuletzt, damit die Simulationsdaten in der Datenbank bleiben
        run_aml()
