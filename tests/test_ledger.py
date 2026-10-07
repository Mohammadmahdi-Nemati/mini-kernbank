"""Tests für das Kontobuch: Was die Datenbank garantieren muss."""

import threading

import psycopg
import pytest
from psycopg import errors

from kernbank import concurrency, db, iban


def test_iban_pruefziffer_python_und_sql_stimmen_ueberein(conn):
    assert iban.is_valid("DE89370400440532013000")          # offizielles Beispiel
    assert not iban.is_valid("DE88370400440532013000")      # eine Ziffer geändert
    sql = conn.execute("SELECT bank.iban_valid(%s), bank.iban_valid(%s), bank.iban_valid(%s)",
                       ("DE89370400440532013000", "DE88370400440532013000", iban.make_iban(4711))).fetchone()
    assert sql == (True, False, True)


def test_konto_mit_falscher_iban_wird_abgelehnt(conn):
    with pytest.raises(errors.CheckViolation):
        conn.execute("INSERT INTO bank.account (iban, account_type) VALUES ('DE00123', 'intern')")


def test_ueberweisung_bewegt_geld_und_schreibt_zwei_zeilen(bank):
    a, b = bank.giro(100), bank.giro(0)
    booking = bank.pay(a, b, 30)
    assert (bank.balance(a), bank.balance(b)) == (7000, 3000)
    lines = bank.conn.execute(
        "SELECT account_id, amount_cents FROM bank.entry WHERE booking_id = %s ORDER BY line_no",
        (booking,)).fetchall()
    assert lines == [(a, -3000), (b, 3000)]


def test_kundenkonto_darf_nicht_ins_minus(bank):
    a, b = bank.giro(10), bank.giro(0)
    bank.conn.commit()
    with pytest.raises(psycopg.Error) as exc:
        bank.pay(a, b, 10.01)
    assert exc.value.sqlstate == "KB001"
    bank.conn.rollback()
    assert bank.balance(a) == 1000


def test_gleicher_schluessel_bucht_nur_einmal(bank):
    a, b = bank.giro(100), bank.giro(0)
    first = bank.pay(a, b, 25, key="doppelklick")
    second = bank.pay(a, b, 25, key="doppelklick")
    assert first == second
    assert bank.balance(a) == 7500


def test_unausgeglichene_buchung_scheitert_beim_commit(bank):
    a = bank.giro(100)
    bank.conn.commit()
    booking = bank.conn.execute(
        "INSERT INTO bank.booking (idempotency_key, kind) VALUES ('kaputt', 'ueberweisung') "
        "RETURNING booking_id").fetchone()[0]
    bank.conn.execute("INSERT INTO bank.entry VALUES (%s, 1, %s, -500), (%s, 2, %s, 400)",
                      (booking, a, booking, bank.extern))
    with pytest.raises(errors.CheckViolation):
        bank.conn.commit()


def test_buchungszeilen_sind_unveraenderlich(bank):
    a, b = bank.giro(100), bank.giro(0)
    booking = bank.pay(a, b, 30)
    bank.conn.commit()
    for statement in ("UPDATE bank.entry SET amount_cents = 1 WHERE booking_id = %s",
                      "DELETE FROM bank.entry WHERE booking_id = %s",
                      "DELETE FROM bank.booking WHERE booking_id = %s"):
        with pytest.raises(errors.RestrictViolation):
            bank.conn.execute(statement, (booking,))
        bank.conn.rollback()


def test_storno_stellt_stand_wieder_her_und_geht_nur_einmal(bank):
    a, b = bank.giro(100), bank.giro(0)
    booking = bank.pay(a, b, 30)
    bank.conn.execute("SELECT bank.reverse('storno-1', %s)", (booking,))
    assert (bank.balance(a), bank.balance(b)) == (10000, 0)
    bank.conn.commit()
    with pytest.raises(psycopg.Error) as exc:
        bank.conn.execute("SELECT bank.reverse('storno-2', %s)", (booking,))
    assert exc.value.sqlstate == "KB004"
    bank.conn.rollback()
    visible = bank.conn.execute("SELECT count(*) FROM bank.v_transfer WHERE booking_id = %s", (booking,)).fetchone()[0]
    assert visible == 0                                      # stornierte Zahlung zählt nicht mehr


def _run_parallel(variant, accounts):
    result = concurrency.Result(variante=variant)
    lock = threading.Lock()
    threads = [threading.Thread(target=concurrency._worker, args=(variant, t, accounts, result, lock))
               for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return result


@pytest.mark.parametrize("variant", ["gesperrt", "serialisierbar"])
def test_parallele_ueberweisungen_verlieren_kein_geld(conn, variant, monkeypatch):
    monkeypatch.setattr(concurrency, "TRANSFERS_PER_THREAD", 60)
    accounts = concurrency.setup_accounts(conn)
    _run_parallel(variant, accounts)
    with db.connect() as check:
        total = check.execute("SELECT sum(balance_cents) FROM bank.account WHERE account_type = 'giro'").fetchone()[0]
        wrong = check.execute("SELECT count(*) FROM bank.v_reconciliation WHERE diff_cents <> 0").fetchone()[0]
    assert total == concurrency.N_ACCOUNTS * concurrency.START_CENTS
    assert wrong == 0


def test_gleichzeitiger_doppelklick_bucht_nur_einmal(conn, bank):
    a, b = bank.giro(100), bank.giro(0)
    conn.commit()

    def click():
        with db.connect() as c:
            c.execute("SELECT bank.transfer('klick', %s, %s, 4000)", (a, b))

    threads = [threading.Thread(target=click) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert bank.balance(a) == 6000
