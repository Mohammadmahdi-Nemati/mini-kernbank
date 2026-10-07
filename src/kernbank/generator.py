"""Simuliert 91 Tage Zahlungsverkehr einer kleinen Bank und versteckt darin Geldwäsche-Muster.

Alle Daten sind frei erfunden. Die Zufallszahlen haben einen festen Startwert (seed),
deshalb entstehen bei jedem Lauf dieselben Daten.
"""

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import psycopg

from .iban import make_iban

START = datetime(2026, 4, 1, tzinfo=timezone.utc)
DAYS = 91                      # 1. April bis 30. Juni 2026
N_PRIVAT = 300
N_GESCHAEFT = 40
EXTERN, KASSE = 0, 1           # interne Verrechnungskonten: Fremdbanken und Bargeldkasse

VORNAMEN = ["Anna", "Ben", "Clara", "David", "Elif", "Felix", "Greta", "Hasan", "Ida", "Jonas",
            "Kira", "Leon", "Mia", "Noah", "Olga", "Paul", "Rosa", "Sami", "Tina", "Uwe"]
NACHNAMEN = ["Becker", "Demir", "Fischer", "Hoffmann", "Klein", "Krause", "Lang", "Meier", "Nowak",
             "Peters", "Richter", "Schmitz", "Schulz", "Vogel", "Wagner", "Weber", "Wolf", "Yilmaz"]
BRANCHEN = ["Kiosk", "Friseur", "Café", "Blumenladen", "Buchhandlung", "Fahrradladen", "Imbiss",
            "Autohandel", "Juwelier", "Getränkemarkt"]


@dataclass
class Event:
    at: datetime
    kind: str
    src: int
    dst: int
    cents: object                 # int oder Funktion(Kontostand des Absenders) -> int
    purpose: str
    must: bool = False            # True: gehört zu einem versteckten Muster und darf nicht ausfallen


@dataclass
class World:
    names: list = field(default_factory=list)       # Index -> (Name, Kundentyp) oder None für intern
    events: list = field(default_factory=list)
    truth: list = field(default_factory=list)       # (Konto-Index, Muster, Variante)


def _eur(rng: random.Random, low: float, high: float, step: float = 0.01) -> int:
    """Zufälliger Betrag in Cent zwischen low und high EUR, gerundet auf step EUR."""
    steps = int(round((high - low) / step))
    return int(round((low + rng.randint(0, steps) * step) * 100))


def build_world(seed: int = 7) -> World:
    rng = random.Random(seed)
    w = World()
    w.names = [None, None]
    privat = list(range(2, 2 + N_PRIVAT))
    geschaeft = list(range(2 + N_PRIVAT, 2 + N_PRIVAT + N_GESCHAEFT))
    for _ in privat:
        w.names.append((f"{rng.choice(VORNAMEN)} {rng.choice(NACHNAMEN)}", "privat"))
    for i, _ in enumerate(geschaeft):
        w.names.append((f"{BRANCHEN[i % len(BRANCHEN)]} {rng.choice(NACHNAMEN)}", "geschaeft"))

    def add(day: float, kind, src, dst, cents, purpose, must=False):
        w.events.append(Event(START + timedelta(days=day), kind, src, dst, cents, purpose, must))

    def hour(low=8.0, high=20.0) -> float:
        return rng.uniform(low, high) / 24

    # Rollen verteilen: Jedes Privatkonto hat höchstens eine Sonderrolle.
    pool = privat[:]
    rng.shuffle(pool)
    take = lambda n: [pool.pop() for _ in range(n)]
    smurfer, mules, sparer = take(12), take(10), take(8)
    rings = [take(rng.randint(3, 5)) for _ in range(8)]
    darlehen = [take(2) for _ in range(10)]
    ketten = [take(3) for _ in range(6)]

    # --- normaler Zahlungsverkehr -------------------------------------------------------------
    for acc in privat:
        add(0, "eroeffnung", EXTERN, acc, _eur(rng, 500, 6000), "Eröffnungssaldo")
        is_sparer = acc in sparer
        gehalt = _eur(rng, 5200, 7500) if is_sparer else _eur(rng, 1800, 4500)
        miete = int(gehalt * rng.uniform(0.28, 0.40))
        for month in (4, 5, 6):
            payday = (datetime(2026, month, 24, tzinfo=timezone.utc) - START).days
            add(payday + 6 / 24, "ueberweisung", EXTERN, acc, gehalt, "Gehalt")
            if is_sparer:      # Gutverdiener, der sein Gehalt am nächsten Tag aufs Tagesgeld schiebt
                share = rng.uniform(0.90, 0.97)
                add(payday + 1 + 9 / 24, "ueberweisung", acc, EXTERN, int(gehalt * share),
                    "Umbuchung Tagesgeld")
            else:
                first = (datetime(2026, month, 1, tzinfo=timezone.utc) - START).days
                add(first + rng.randint(0, 2) + hour(), "ueberweisung", acc, EXTERN, miete, "Miete")
        for day in range(DAYS):
            if rng.random() < 0.6:
                add(day + hour(), "ueberweisung", acc, rng.choice(geschaeft), _eur(rng, 5, 150), "Einkauf")
            if rng.random() < 0.05:
                other = rng.choice(privat)
                if other != acc:
                    add(day + hour(), "ueberweisung", acc, other, _eur(rng, 10, 300), "Privat")
            if rng.random() < 0.08:
                add(day + hour(), "barauszahlung", acc, KASSE, _eur(rng, 20, 300, 10), "Geldautomat")
            if rng.random() < 0.01:
                add(day + hour(), "bareinzahlung", KASSE, acc, _eur(rng, 50, 2000, 10), "Bareinzahlung")

    for i, acc in enumerate(geschaeft):
        add(0, "eroeffnung", EXTERN, acc, _eur(rng, 2000, 15000), "Eröffnungssaldo")
        bargeldintensiv = i % len(BRANCHEN) in (7, 8)     # Autohandel und Juwelier
        hat_bargeld = bargeldintensiv or i < 15
        for day in range(DAYS):
            weekday = (START + timedelta(days=day)).weekday()
            if hat_bargeld and weekday < 6:               # Tageseinnahmen einzahlen, außer sonntags
                cents = _eur(rng, 3000, 9900, 50) if bargeldintensiv else _eur(rng, 1500, 7000, 50)
                add(day + hour(17, 19), "bareinzahlung", KASSE, acc, cents, "Tageseinnahmen")
            if weekday == 4:                              # freitags Lieferanten bezahlen
                share = rng.uniform(0.70, 0.90)
                add(day + 16 / 24, "ueberweisung", acc, EXTERN,
                    lambda balance, s=share: int(balance * s), "Lieferanten")

    # --- Lockvögel: sehen ähnlich aus, sind aber harmlos ----------------------------------------
    for a, b in darlehen:                                 # Privatdarlehen und Rückzahlung
        cents = _eur(rng, 3000, 10000, 100)
        day = rng.randint(3, 50)
        add(day + 0.3, "ueberweisung", EXTERN, a, cents, "Auflösung Sparvertrag", must=True)
        add(day + 0.5, "ueberweisung", a, b, cents, "Privatdarlehen", must=True)
        add(day + rng.randint(10, 30) + 0.5, "ueberweisung", b, a, cents, "Rückzahlung Darlehen")
    for a, b, c in ketten:                                # Kette ohne Rückfluss (z. B. Autokauf)
        cents = _eur(rng, 4000, 15000, 100)
        day = rng.randint(3, 80)
        add(day + 0.3, "ueberweisung", EXTERN, a, cents, "Kreditauszahlung", must=True)
        add(day + 0.5, "ueberweisung", a, b, cents, "Autokauf", must=True)
        add(day + 1.5, "ueberweisung", b, c, int(cents * 0.95), "Weiterleitung", must=True)

    # --- Muster 1: Smurfing ---------------------------------------------------------------------
    for n, acc in enumerate(smurfer):
        getarnt = n < 3            # getarnt: Beträge weit unter der Schwelle
        w.truth.append((acc, "smurfing", "getarnt: kleinere Beträge" if getarnt else "offensichtlich"))
        day = rng.randint(5, 70)
        span = rng.randint(7, 12)
        total = 0
        for _ in range(rng.randint(6, 12)):
            cents = _eur(rng, 5000, 7800, 50) if getarnt else _eur(rng, 8000, 9900, 50)
            total += cents
            add(day + rng.uniform(0, span), "bareinzahlung", KASSE, acc, cents, "Bareinzahlung", must=True)
        add(day + span + 1.5, "ueberweisung", acc, EXTERN, int(total * 0.9), "Auslandsüberweisung", must=True)

    # --- Muster 2: Durchlaufkonten (Finanzagenten) -----------------------------------------------
    for n, acc in enumerate(mules):
        variante = {0: "getarnt: leitet erst nach Tagen weiter", 1: "getarnt: leitet erst nach Tagen weiter",
                    2: "getarnt: behält einen Teil"}.get(n, "offensichtlich")
        w.truth.append((acc, "durchlauf", variante))
        for day in rng.sample(range(5, 84), rng.randint(4, 7)):
            total, last = 0, 0.0
            for _ in range(rng.randint(2, 4)):
                cents = _eur(rng, 2500, 6000, 10)
                last = hour(8, 12)
                total += cents
                add(day + last, "ueberweisung", EXTERN, acc, cents, "Rechnung", must=True)
            if n in (0, 1):
                delay, share = rng.uniform(3, 4), rng.uniform(0.92, 0.98)
            elif n == 2:
                delay, share = rng.uniform(2, 10) / 24, rng.uniform(0.60, 0.75)
            else:
                delay, share = rng.uniform(2, 10) / 24, rng.uniform(0.92, 0.98)
            add(day + last + delay, "ueberweisung", acc, EXTERN, int(total * share), "Weiterleitung", must=True)

    # --- Muster 3: Kreisüberweisungen ------------------------------------------------------------
    for n, ring in enumerate(rings):
        langsam = n < 2            # getarnt: mehrere Tage Pause zwischen den Stationen
        w.truth.extend((acc, "kreis", "getarnt: lange Pausen" if langsam else "offensichtlich") for acc in ring)
        day = rng.randint(5, 55)
        cents = _eur(rng, 4000, 25000, 100)
        add(day, "ueberweisung", EXTERN, ring[0], int(cents * 1.02), "Zahlungseingang", must=True)
        t = day + 1 / 24
        for i, acc in enumerate(ring):
            t += rng.uniform(4, 6) if langsam else rng.uniform(2, 40) / 24
            add(t, "ueberweisung", acc, ring[(i + 1) % len(ring)], cents, "Beratungsleistung", must=True)
            cents = int(cents * rng.uniform(0.95, 1.0))

    return w


def finalize(world: World) -> list[tuple]:
    """Sortiert nach Zeit und lässt normale Zahlungen aus, für die das Guthaben nicht reicht."""
    balance = [0] * len(world.names)
    rows = []
    for ev in sorted(world.events, key=lambda e: e.at):
        cents = ev.cents(balance[ev.src]) if callable(ev.cents) else ev.cents
        if cents <= 0:
            continue
        if ev.src not in (EXTERN, KASSE) and balance[ev.src] < cents:
            if not ev.must:
                continue
            # Zahlungen eines versteckten Musters dürfen nicht ausfallen: fehlendes Guthaben
            # kommt direkt davor als Eingang von einer Fremdbank.
            gap = cents - balance[ev.src]
            balance[ev.src] += gap
            rows.append((ev.at - timedelta(seconds=1), "ueberweisung", EXTERN, ev.src, gap, "Zahlungseingang"))
        balance[ev.src] -= cents
        balance[ev.dst] += cents
        rows.append((ev.at, ev.kind, ev.src, ev.dst, cents, ev.purpose))
    return rows


def load(conn: psycopg.Connection, world: World, rows: list[tuple]) -> dict:
    """Legt Kunden und Konten an und bucht alle Zahlungen über bank.transfer()."""
    ids = []
    for idx, entry in enumerate(world.names):
        if entry is None:
            label = "Verrechnungskonto Fremdbanken" if idx == EXTERN else "Kasse (Bargeld)"
            acc = conn.execute(
                "INSERT INTO bank.account (iban, account_type, label) VALUES (%s, 'intern', %s) "
                "RETURNING account_id", (make_iban(idx + 1), label)).fetchone()[0]
        else:
            name, ctype = entry
            cust = conn.execute(
                "INSERT INTO bank.customer (name, customer_type) VALUES (%s, %s) RETURNING customer_id",
                (name, ctype)).fetchone()[0]
            acc = conn.execute(
                "INSERT INTO bank.account (customer_id, iban, account_type) VALUES (%s, %s, 'giro') "
                "RETURNING account_id", (cust, make_iban(1000 + idx))).fetchone()[0]
        ids.append(acc)
    params = [(f"sim-{i:06d}", ids[src], ids[dst], cents, purpose, kind, at)
              for i, (at, kind, src, dst, cents, purpose) in enumerate(rows)]
    with conn.cursor() as cur:
        for start in range(0, len(params), 5000):
            cur.executemany("SELECT bank.transfer(%s, %s, %s, %s, %s, %s, %s)", params[start:start + 5000])
            conn.commit()
        cur.executemany("INSERT INTO eval.truth VALUES (%s, %s, %s)",
                        [(ids[acc], muster, variante) for acc, muster, variante in world.truth])
    conn.commit()
    return {"kunden": len(ids) - 2, "buchungen": len(params)}
