"""Bewertung der Regeln gegen die bekannte Wahrheit der Simulation (Konto-Ebene)."""

from dataclasses import dataclass

import psycopg

REGELN = ("smurfing", "durchlauf", "kreis")

# Was passiert, wenn man einen Schwellenwert ändert? (Name, Regel, geänderte Parameter)
SZENARIEN = [
    ("Smurfing: nur Privatkunden", "smurfing", {"smurf_nur_privat": 1}),
    ("Smurfing: nur Privatkunden, schon ab 50 % der Schwelle", "smurfing",
     {"smurf_nur_privat": 1, "smurf_nah_anteil": 0.5}),
    ("Smurfing: alle Kunden, schon ab 50 % der Schwelle", "smurfing", {"smurf_nah_anteil": 0.5}),
    ("Durchlauf: Muster an mindestens 4 Tagen", "durchlauf", {"durchlauf_min_tage": 4}),
    ("Durchlauf: Weiterleitung innerhalb von 5 Tagen", "durchlauf", {"durchlauf_stunden": 120}),
    ("Kreis: bis zu 7 Tage zwischen zwei Stationen", "kreis", {"kreis_max_stunden": 168}),
]


@dataclass
class Metric:
    regel: str
    alarme: int
    treffer: int          # richtig erkannt
    fehlalarme: int
    verpasst: int

    @property
    def precision(self) -> float:
        return self.treffer / self.alarme if self.alarme else 0.0

    @property
    def recall(self) -> float:
        total = self.treffer + self.verpasst
        return self.treffer / total if total else 0.0


def metric(conn: psycopg.Connection, regel: str) -> Metric:
    alarme, treffer, verpasst = conn.execute(
        """
        SELECT count(a.account_id),
               count(*) FILTER (WHERE a.account_id IS NOT NULL AND t.account_id IS NOT NULL),
               count(*) FILTER (WHERE a.account_id IS NULL)
        FROM (SELECT account_id FROM aml.v_alert WHERE regel = %(r)s) a
        FULL JOIN (SELECT account_id FROM eval.truth WHERE muster = %(r)s) t USING (account_id)
        """, {"r": regel}).fetchone()
    return Metric(regel, alarme, treffer, alarme - treffer, verpasst)


def metrics(conn: psycopg.Connection) -> list[Metric]:
    return [metric(conn, r) for r in REGELN]


def by_variant(conn: psycopg.Connection) -> list[tuple]:
    """Erkennungsquote je Muster und Variante (offensichtlich oder getarnt)."""
    return conn.execute(
        """
        SELECT t.muster, t.variante, count(*) AS konten, count(a.account_id) AS erkannt
        FROM eval.truth t
        LEFT JOIN aml.v_alert a ON a.account_id = t.account_id AND a.regel = t.muster
        GROUP BY t.muster, t.variante
        ORDER BY t.muster, t.variante DESC
        """).fetchall()


def scenarios(conn: psycopg.Connection) -> list[tuple[str, Metric]]:
    """Ändert Schwellenwerte nur innerhalb einer Transaktion und rollt sie danach zurück."""
    out = []
    for name, regel, overrides in SZENARIEN:
        conn.rollback()
        for key, value in overrides.items():
            conn.execute("UPDATE aml.param SET value = %s WHERE name = %s", (value, key))
        out.append((name, metric(conn, regel)))
        conn.rollback()
    return out


def examples(conn: psycopg.Connection) -> list[tuple]:
    """Je Regel ein richtiger Alarm und, falls vorhanden, ein Fehlalarm, mit Begründung."""
    return conn.execute(
        """
        SELECT DISTINCT ON (a.regel, (t.account_id IS NOT NULL))
               a.regel, (t.account_id IS NOT NULL) AS richtig, c.name, a.begruendung
        FROM aml.v_alert a
        JOIN bank.account ac ON ac.account_id = a.account_id
        JOIN bank.customer c ON c.customer_id = ac.customer_id
        LEFT JOIN eval.truth t ON t.account_id = a.account_id AND t.muster = a.regel
        ORDER BY a.regel, (t.account_id IS NOT NULL) DESC, a.account_id
        """).fetchall()
