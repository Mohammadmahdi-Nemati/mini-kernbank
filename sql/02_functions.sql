-- Sichere Buchungsfunktionen. Jede Funktion läuft in der Transaktion des Aufrufers.

-- Überweisung von p_from an p_to.
--   * Idempotent: Derselbe p_key bucht nur einmal und liefert dieselbe booking_id zurück.
--   * Zeilensperren (FOR UPDATE) in fester Reihenfolge der account_id: verhindert Lost Updates
--     und Deadlocks, wenn zwei Überweisungen dieselben Konten in umgekehrter Richtung betreffen.
--   * Kundenkonten dürfen nicht ins Minus (SQLSTATE KB001).
CREATE FUNCTION bank.transfer(
    p_key          TEXT,
    p_from         BIGINT,
    p_to           BIGINT,
    p_amount_cents BIGINT,
    p_purpose      TEXT DEFAULT '',
    p_kind         TEXT DEFAULT 'ueberweisung',
    p_at           TIMESTAMPTZ DEFAULT now()
) RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE
    v_id      BIGINT;
    v_balance BIGINT;
    v_type    TEXT;
    v_locked  INT;
BEGIN
    IF p_amount_cents IS NULL OR p_amount_cents <= 0 THEN
        RAISE EXCEPTION 'Betrag muss positiv sein' USING ERRCODE = 'KB002';
    END IF;
    IF p_from = p_to THEN
        RAISE EXCEPTION 'Auftraggeber und Empfänger sind identisch' USING ERRCODE = 'KB002';
    END IF;
    IF p_kind = 'storno' THEN
        RAISE EXCEPTION 'Stornos nur über bank.reverse()' USING ERRCODE = 'KB002';
    END IF;

    INSERT INTO bank.booking (idempotency_key, kind, purpose, booked_at)
    VALUES (p_key, p_kind, p_purpose, p_at)
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING booking_id INTO v_id;

    IF v_id IS NULL THEN           -- Schlüssel schon bekannt: nichts tun, alte Buchung zurückgeben
        SELECT booking_id INTO v_id FROM bank.booking WHERE idempotency_key = p_key;
        RETURN v_id;
    END IF;

    SELECT count(*) INTO v_locked FROM (
        SELECT 1 FROM bank.account WHERE account_id IN (p_from, p_to)
        ORDER BY account_id FOR UPDATE
    ) locked;
    IF v_locked <> 2 THEN
        RAISE EXCEPTION 'Konto nicht gefunden' USING ERRCODE = 'KB003';
    END IF;

    SELECT balance_cents, account_type INTO v_balance, v_type
    FROM bank.account WHERE account_id = p_from;
    IF v_type = 'giro' AND v_balance < p_amount_cents THEN
        RAISE EXCEPTION 'Deckung nicht ausreichend (Konto %, Stand % Cent, Betrag % Cent)',
            p_from, v_balance, p_amount_cents USING ERRCODE = 'KB001';
    END IF;

    UPDATE bank.account SET balance_cents = balance_cents - p_amount_cents WHERE account_id = p_from;
    UPDATE bank.account SET balance_cents = balance_cents + p_amount_cents WHERE account_id = p_to;

    INSERT INTO bank.entry (booking_id, line_no, account_id, amount_cents)
    VALUES (v_id, 1, p_from, -p_amount_cents),
           (v_id, 2, p_to,    p_amount_cents);
    RETURN v_id;
END $$;

-- Storno: bucht dieselben Zeilen mit umgekehrtem Vorzeichen. Die alte Buchung bleibt stehen.
-- Jede Buchung kann nur einmal storniert werden, ein Storno selbst gar nicht.
CREATE FUNCTION bank.reverse(p_key TEXT, p_booking_id BIGINT, p_at TIMESTAMPTZ DEFAULT now())
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE
    v_id   BIGINT;
    v_kind TEXT;
BEGIN
    SELECT booking_id INTO v_id FROM bank.booking WHERE idempotency_key = p_key;
    IF v_id IS NOT NULL THEN
        RETURN v_id;
    END IF;

    SELECT kind INTO v_kind FROM bank.booking WHERE booking_id = p_booking_id;
    IF v_kind IS NULL THEN
        RAISE EXCEPTION 'Buchung % nicht gefunden', p_booking_id USING ERRCODE = 'KB003';
    END IF;
    IF v_kind = 'storno' THEN
        RAISE EXCEPTION 'Ein Storno kann nicht storniert werden' USING ERRCODE = 'KB004';
    END IF;

    PERFORM 1 FROM bank.account
    WHERE account_id IN (SELECT account_id FROM bank.entry WHERE booking_id = p_booking_id)
    ORDER BY account_id FOR UPDATE;

    BEGIN
        INSERT INTO bank.booking (idempotency_key, kind, purpose, booked_at, reverses_booking_id)
        VALUES (p_key, 'storno', 'Storno von Buchung ' || p_booking_id, p_at, p_booking_id)
        RETURNING booking_id INTO v_id;
    EXCEPTION WHEN unique_violation THEN
        RAISE EXCEPTION 'Buchung % ist bereits storniert', p_booking_id USING ERRCODE = 'KB004';
    END;

    INSERT INTO bank.entry (booking_id, line_no, account_id, amount_cents)
    SELECT v_id, line_no, account_id, -amount_cents FROM bank.entry WHERE booking_id = p_booking_id;

    UPDATE bank.account a SET balance_cents = a.balance_cents - e.amount_cents
    FROM bank.entry e WHERE e.booking_id = p_booking_id AND e.account_id = a.account_id;
    RETURN v_id;
END $$;
