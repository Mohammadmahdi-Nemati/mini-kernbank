"""Experiment: Was passiert, wenn viele Überweisungen gleichzeitig dieselben Konten treffen?

Drei Varianten buchen exakt dieselbe Arbeitslast:
  1. naiv:          Kontostand lesen, in Python rechnen, neuen Stand zurückschreiben (READ COMMITTED)
  2. serialisierbar: dieselbe naive Logik, aber Isolationslevel SERIALIZABLE mit Wiederholung
  3. gesperrt:      bank.transfer() mit Zeilensperren (SELECT ... FOR UPDATE)
"""

import random
import threading
import time
from dataclasses import dataclass

import psycopg
from psycopg import errors

from . import db
from .iban import make_iban

N_ACCOUNTS = 10
START_CENTS = 50_000          # 500 EUR je Konto
N_THREADS = 8
TRANSFERS_PER_THREAD = 250
MAX_CENTS = 5_000             # Überweisungen von 0,01 bis 50 EUR


@dataclass
class Result:
    variante: str
    gebucht: int = 0
    abgelehnt: int = 0
    wiederholungen: int = 0
    sekunden: float = 0.0
    soll_cents: int = 0
    ist_cents: int = 0
    konten_falsch: int = 0
    fehlbetrag_cents: int = 0


def setup_accounts(conn: psycopg.Connection) -> list[int]:
    """Legt Testkonten an und zahlt das Startguthaben über eine normale Buchung ein."""
    extern = conn.execute(
        "INSERT INTO bank.account (iban, account_type, label) VALUES (%s, 'intern', 'Fremdbanken') "
        "RETURNING account_id", (make_iban(1),)).fetchone()[0]
    ids = []
    for i in range(N_ACCOUNTS):
        cust = conn.execute(
            "INSERT INTO bank.customer (name, customer_type) VALUES (%s, 'privat') RETURNING customer_id",
            (f"Testkunde {i + 1}",)).fetchone()[0]
        acc = conn.execute(
            "INSERT INTO bank.account (customer_id, iban, account_type) VALUES (%s, %s, 'giro') "
            "RETURNING account_id", (cust, make_iban(100 + i))).fetchone()[0]
        conn.execute("SELECT bank.transfer(%s, %s, %s, %s, 'Startguthaben', 'eroeffnung')",
                     (f"start-{i}", extern, acc, START_CENTS))
        ids.append(acc)
    conn.commit()
    return ids


def naive_transfer(conn: psycopg.Connection, key: str, src: int, dst: int, cents: int) -> bool:
    """Lesen, rechnen, schreiben. Zwischen Lesen und Schreiben kann ein anderer Vorgang dazwischenkommen."""
    with conn.transaction():
        bal_src = conn.execute(
            "SELECT balance_cents FROM bank.account WHERE account_id = %s", (src,)).fetchone()[0]
        bal_dst = conn.execute(
            "SELECT balance_cents FROM bank.account WHERE account_id = %s", (dst,)).fetchone()[0]
        if bal_src < cents:
            return False
        booking = conn.execute(
            "INSERT INTO bank.booking (idempotency_key, kind) VALUES (%s, 'ueberweisung') "
            "RETURNING booking_id", (key,)).fetchone()[0]
        conn.execute(
            "INSERT INTO bank.entry VALUES (%s, 1, %s, %s), (%s, 2, %s, %s)",
            (booking, src, -cents, booking, dst, cents))
        # Feste Reihenfolge der Updates, damit hier nur der Lost Update sichtbar wird, kein Deadlock.
        for acc, new_balance in sorted([(src, bal_src - cents), (dst, bal_dst + cents)]):
            conn.execute("UPDATE bank.account SET balance_cents = %s WHERE account_id = %s",
                         (new_balance, acc))
    return True


def safe_transfer(conn: psycopg.Connection, key: str, src: int, dst: int, cents: int) -> bool:
    try:
        with conn.transaction():
            conn.execute("SELECT bank.transfer(%s, %s, %s, %s)", (key, src, dst, cents))
        return True
    except psycopg.Error as exc:
        if exc.sqlstate == "KB001":      # Deckung nicht ausreichend
            return False
        raise


def _worker(variant: str, thread_no: int, accounts: list[int], result: Result, lock: threading.Lock):
    rng = random.Random(1000 + thread_no)
    booked = rejected = retries = 0
    with db.connect() as conn:
        if variant == "serialisierbar":
            conn.isolation_level = psycopg.IsolationLevel.SERIALIZABLE
        for i in range(TRANSFERS_PER_THREAD):
            src, dst = rng.sample(accounts, 2)
            cents = rng.randint(1, MAX_CENTS)
            key = f"{variant}-{thread_no}-{i}"
            while True:
                try:
                    if variant == "gesperrt":
                        ok = safe_transfer(conn, key, src, dst, cents)
                    else:
                        ok = naive_transfer(conn, key, src, dst, cents)
                    break
                except (errors.SerializationFailure, errors.DeadlockDetected):
                    retries += 1          # Transaktion wurde zurückgerollt: einfach noch einmal
                except errors.CheckViolation:
                    ok = False            # Konto wäre ins Minus gerutscht
                    break
            booked += ok
            rejected += not ok
    with lock:
        result.gebucht += booked
        result.abgelehnt += rejected
        result.wiederholungen += retries


def run_variant(variant: str) -> Result:
    with db.connect() as conn:
        db.reset_schema(conn)
        accounts = setup_accounts(conn)
    result = Result(variante=variant, soll_cents=N_ACCOUNTS * START_CENTS)
    lock = threading.Lock()
    threads = [threading.Thread(target=_worker, args=(variant, t, accounts, result, lock))
               for t in range(N_THREADS)]
    started = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    result.sekunden = time.perf_counter() - started
    with db.connect() as conn:
        result.ist_cents = conn.execute(
            "SELECT sum(balance_cents) FROM bank.account WHERE account_type = 'giro'").fetchone()[0]
        result.konten_falsch, result.fehlbetrag_cents = conn.execute(
            "SELECT count(*) FILTER (WHERE diff_cents <> 0), coalesce(sum(abs(diff_cents)), 0) "
            "FROM bank.v_reconciliation").fetchone()
    return result


def run_all() -> list[Result]:
    return [run_variant(v) for v in ("naiv", "serialisierbar", "gesperrt")]
