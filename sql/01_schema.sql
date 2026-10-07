-- Mini-Kernbank: Schema für ein Kontobuch (Ledger) nach dem Prinzip der doppelten Buchführung.
-- Alle Beträge sind ganze Cent (BIGINT), nie Gleitkommazahlen.

DROP SCHEMA IF EXISTS eval CASCADE;
DROP SCHEMA IF EXISTS aml CASCADE;
DROP SCHEMA IF EXISTS bank CASCADE;
CREATE SCHEMA bank;

-- IBAN-Prüfung nach ISO 13616 (Modulo 97): Die ersten vier Zeichen wandern ans Ende,
-- Buchstaben werden zu Zahlen (A = 10 ... Z = 35), der Rest bei Division durch 97 muss 1 sein.
CREATE FUNCTION bank.iban_valid(p_iban TEXT) RETURNS BOOLEAN
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    v_moved  TEXT;
    v_digits TEXT := '';
    v_char   TEXT;
BEGIN
    IF p_iban IS NULL OR p_iban !~ '^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$' THEN
        RETURN FALSE;
    END IF;
    v_moved := substr(p_iban, 5) || substr(p_iban, 1, 4);
    FOR i IN 1 .. length(v_moved) LOOP
        v_char := substr(v_moved, i, 1);
        IF v_char BETWEEN '0' AND '9' THEN
            v_digits := v_digits || v_char;
        ELSE
            v_digits := v_digits || (ascii(v_char) - 55)::TEXT;
        END IF;
    END LOOP;
    RETURN v_digits::NUMERIC % 97 = 1;
END $$;

CREATE TABLE bank.customer (
    customer_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          TEXT NOT NULL,
    customer_type TEXT NOT NULL CHECK (customer_type IN ('privat', 'geschaeft'))
);

-- 'giro'   = Kundenkonto, darf nicht ins Minus.
-- 'intern' = Verrechnungskonto der Bank (z. B. Kasse, Fremdbanken), gehört keinem Kunden.
CREATE TABLE bank.account (
    account_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id   BIGINT REFERENCES bank.customer,
    iban          TEXT NOT NULL UNIQUE CHECK (bank.iban_valid(iban)),
    account_type  TEXT NOT NULL CHECK (account_type IN ('giro', 'intern')),
    label         TEXT NOT NULL DEFAULT '',
    balance_cents BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT giro_hat_kunden CHECK ((account_type = 'giro') = (customer_id IS NOT NULL)),
    CONSTRAINT kein_dispo CHECK (account_type = 'intern' OR balance_cents >= 0)
);

-- Eine Buchung ist der Kopf, die Buchungszeilen (entry) sind Soll und Haben.
-- idempotency_key: Dieselbe Anfrage (z. B. Doppelklick) wird nur einmal gebucht.
CREATE TABLE bank.booking (
    booking_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key     TEXT NOT NULL UNIQUE,
    kind                TEXT NOT NULL CHECK (kind IN
                            ('eroeffnung', 'ueberweisung', 'bareinzahlung', 'barauszahlung', 'storno')),
    purpose             TEXT NOT NULL DEFAULT '',
    booked_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    reverses_booking_id BIGINT UNIQUE REFERENCES bank.booking,   -- UNIQUE: nur ein Storno je Buchung
    CONSTRAINT storno_hat_bezug CHECK ((kind = 'storno') = (reverses_booking_id IS NOT NULL))
);

CREATE TABLE bank.entry (
    booking_id   BIGINT   NOT NULL REFERENCES bank.booking,
    line_no      SMALLINT NOT NULL,
    account_id   BIGINT   NOT NULL REFERENCES bank.account,
    amount_cents BIGINT   NOT NULL CHECK (amount_cents <> 0),    -- negativ = Abgang, positiv = Zugang
    PRIMARY KEY (booking_id, line_no)
);

CREATE INDEX idx_entry_account ON bank.entry (account_id);
CREATE INDEX idx_booking_booked_at ON bank.booking (booked_at);

-- Regel 1: Jede Buchung muss ausgeglichen sein (Summe der Zeilen = 0, mindestens 2 Zeilen).
-- Geprüft wird erst beim COMMIT (DEFERRED), weil die Zeilen nacheinander eingefügt werden.
CREATE FUNCTION bank.check_balanced() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_sum BIGINT;
    v_n   INT;
BEGIN
    SELECT coalesce(sum(amount_cents), 0), count(*) INTO v_sum, v_n
    FROM bank.entry WHERE booking_id = NEW.booking_id;
    IF v_sum <> 0 OR v_n < 2 THEN
        RAISE EXCEPTION 'Buchung % ist nicht ausgeglichen (Summe % Cent, % Zeilen)',
            NEW.booking_id, v_sum, v_n USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER entry_balanced AFTER INSERT ON bank.entry
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bank.check_balanced();
CREATE CONSTRAINT TRIGGER booking_has_entries AFTER INSERT ON bank.booking
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION bank.check_balanced();

-- Regel 2: Buchungen sind unveränderlich (revisionssicher). Fehler werden per Storno korrigiert.
CREATE FUNCTION bank.forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Buchungen sind unveränderlich: % auf % ist nicht erlaubt (Storno verwenden)',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
END $$;

CREATE TRIGGER entry_immutable BEFORE UPDATE OR DELETE ON bank.entry
    FOR EACH ROW EXECUTE FUNCTION bank.forbid_change();
CREATE TRIGGER booking_immutable BEFORE UPDATE OR DELETE ON bank.booking
    FOR EACH ROW EXECUTE FUNCTION bank.forbid_change();
CREATE TRIGGER entry_no_truncate BEFORE TRUNCATE ON bank.entry
    FOR EACH STATEMENT EXECUTE FUNCTION bank.forbid_change();
CREATE TRIGGER booking_no_truncate BEFORE TRUNCATE ON bank.booking
    FOR EACH STATEMENT EXECUTE FUNCTION bank.forbid_change();

-- Abstimmung: Der gespeicherte Kontostand muss immer der Summe der Buchungszeilen entsprechen.
CREATE VIEW bank.v_reconciliation AS
SELECT a.account_id,
       a.balance_cents,
       coalesce(sum(e.amount_cents), 0)::BIGINT                   AS entries_cents,
       (a.balance_cents - coalesce(sum(e.amount_cents), 0))::BIGINT AS diff_cents
FROM bank.account a
LEFT JOIN bank.entry e USING (account_id)
GROUP BY a.account_id, a.balance_cents;

-- Zahlungen als "von -> an" (jede Buchung in diesem Projekt hat genau zwei Zeilen).
-- Stornos und stornierte Buchungen sind ausgeblendet.
CREATE VIEW bank.v_transfer AS
SELECT b.booking_id, b.booked_at, b.kind, b.purpose,
       d.account_id AS from_account,
       c.account_id AS to_account,
       c.amount_cents
FROM bank.booking b
JOIN bank.entry d ON d.booking_id = b.booking_id AND d.amount_cents < 0
JOIN bank.entry c ON c.booking_id = b.booking_id AND c.amount_cents > 0
WHERE b.kind <> 'storno'
  AND NOT EXISTS (SELECT 1 FROM bank.booking s WHERE s.reverses_booking_id = b.booking_id);
