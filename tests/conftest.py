"""Die Tests laufen in einer eigenen Datenbank (kernbank_test), die bei Bedarf angelegt wird."""

import os

import psycopg
import pytest

from kernbank import db
from kernbank.iban import make_iban

TEST_DSN = os.environ.get("KERNBANK_TEST_DSN", "postgresql://postgres:postgres@localhost:5432/kernbank_test")


@pytest.fixture(scope="session", autouse=True)
def test_database():
    base, name = TEST_DSN.rsplit("/", 1)
    with psycopg.connect(f"{base}/postgres", autocommit=True) as admin:
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            admin.execute(f'CREATE DATABASE "{name}"')
    db.DSN = TEST_DSN          # auch die Hilfs-Threads der Nebenläufigkeitstests nutzen die Test-Datenbank


@pytest.fixture
def conn():
    with psycopg.connect(TEST_DSN) as connection:
        db.reset_schema(connection)
        yield connection
        connection.rollback()


class Bank:
    """Kleine Hilfe, um in Tests Konten anzulegen und zu buchen."""

    def __init__(self, conn):
        self.conn = conn
        self.n = 0
        self.extern = self._account(None, "intern")
        self.kasse = self._account(None, "intern")

    def _account(self, customer, kind):
        self.n += 1
        return self.conn.execute(
            "INSERT INTO bank.account (customer_id, iban, account_type) VALUES (%s, %s, %s) RETURNING account_id",
            (customer, make_iban(self.n), kind)).fetchone()[0]

    def giro(self, eur=0, ctype="privat"):
        customer = self.conn.execute(
            "INSERT INTO bank.customer (name, customer_type) VALUES (%s, %s) RETURNING customer_id",
            (f"Kunde {self.n}", ctype)).fetchone()[0]
        account = self._account(customer, "giro")
        if eur:
            self.pay(self.extern, account, eur, kind="eroeffnung")
        return account

    def pay(self, src, dst, eur, at="2026-05-04 10:00+00", kind="ueberweisung", key=None):
        self.n += 1
        return self.conn.execute(
            "SELECT bank.transfer(%s, %s, %s, %s, '', %s, %s)",
            (key or f"t-{self.n}", src, dst, round(eur * 100), kind, at)).fetchone()[0]

    def balance(self, account):
        return self.conn.execute(
            "SELECT balance_cents FROM bank.account WHERE account_id = %s", (account,)).fetchone()[0]

    def alerts(self, regel):
        return {row[0] for row in self.conn.execute(
            "SELECT account_id FROM aml.v_alert WHERE regel = %s", (regel,))}


@pytest.fixture
def bank(conn):
    return Bank(conn)
