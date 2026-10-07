-- Transaktionsmonitoring: drei erklärbare Regeln zur Erkennung von Geldwäsche-Mustern.
-- Alle Schwellenwerte stehen in aml.param und sind Annahmen dieses Projekts, keine Bankvorgaben.

CREATE SCHEMA aml;

CREATE TABLE aml.param (
    name        TEXT PRIMARY KEY,
    value       NUMERIC NOT NULL,
    description TEXT NOT NULL
);

INSERT INTO aml.param VALUES
 ('bar_schwelle_cents',    1000000, 'Bargeld-Schwelle: ab 10.000 EUR verlangen Banken einen Herkunftsnachweis'),
 ('smurf_nah_anteil',      0.8,     'Einzahlung gilt als "knapp unter der Schwelle" ab diesem Anteil (80 %)'),
 ('smurf_fenster_tage',    7,       'Zeitfenster für Smurfing in Tagen'),
 ('smurf_min_anzahl',      3,       'Mindestzahl knapper Bareinzahlungen im Fenster'),
 ('smurf_nur_privat',      0,       '1 = Regel gilt nur für Privatkunden (Geschäfte zahlen regelmäßig Bargeld ein)'),
 ('durchlauf_min_cents',   500000,  'Mindest-Eingang pro Tag, ab dem ein Durchlauf geprüft wird (5.000 EUR)'),
 ('durchlauf_stunden',     48,      'Geld muss innerhalb so vieler Stunden weitergeleitet sein'),
 ('durchlauf_min_quote',   0.9,     'Mindestens dieser Anteil des Eingangs wird weitergeleitet'),
 ('durchlauf_max_quote',   1.05,    'Höchstens dieser Anteil (sonst ist es eine normale größere Zahlung)'),
 ('durchlauf_min_tage',    3,       'An so vielen Tagen muss das Muster auftreten'),
 ('kreis_min_cents',       300000,  'Nur Überweisungen ab diesem Betrag zählen als Kante (3.000 EUR)'),
 ('kreis_max_stunden',     72,      'Maximaler Abstand zwischen zwei Stationen im Kreis'),
 ('kreis_min_quote',       0.9,     'Betrag darf je Station um höchstens 10 % sinken'),
 ('kreis_max_laenge',      5,       'Maximale Zahl der Konten im Kreis (Minimum ist 3)');

CREATE FUNCTION aml.p(p_name TEXT) RETURNS NUMERIC
LANGUAGE sql STABLE AS $$ SELECT value FROM aml.param WHERE name = p_name $$;

CREATE FUNCTION aml.eur(p_cents NUMERIC) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$ SELECT translate(to_char(p_cents / 100.0, 'FM999,999,990.00'), ',.', '.,') $$;

-- Regel 1: Smurfing (Structuring).
-- Mehrere Bareinzahlungen knapp unter der Schwelle innerhalb weniger Tage auf dasselbe Konto.
CREATE VIEW aml.v_alert_smurfing AS
WITH near AS (
    SELECT t.to_account AS account_id, t.booked_at, t.amount_cents
    FROM bank.v_transfer t
    JOIN bank.account a  ON a.account_id = t.to_account
    JOIN bank.customer c ON c.customer_id = a.customer_id
    WHERE t.kind = 'bareinzahlung'
      AND t.amount_cents >= aml.p('bar_schwelle_cents') * aml.p('smurf_nah_anteil')
      AND t.amount_cents <  aml.p('bar_schwelle_cents')
      AND (aml.p('smurf_nur_privat') = 0 OR c.customer_type = 'privat')
), windowed AS (
    SELECT n1.account_id, n1.booked_at,
           count(*)             AS n,
           sum(n2.amount_cents) AS sum_cents
    FROM near n1
    JOIN near n2 ON n2.account_id = n1.account_id
                AND n2.booked_at <= n1.booked_at
                AND n2.booked_at >  n1.booked_at - make_interval(days => aml.p('smurf_fenster_tage')::INT)
    GROUP BY n1.account_id, n1.booked_at
)
SELECT DISTINCT ON (account_id)
       'smurfing'::TEXT AS regel,
       account_id,
       booked_at AS zeitpunkt,
       format('%s Bareinzahlungen knapp unter %s EUR innerhalb von %s Tagen (Summe %s EUR)',
              n, aml.eur(aml.p('bar_schwelle_cents')), aml.p('smurf_fenster_tage')::INT,
              aml.eur(sum_cents)) AS begruendung
FROM windowed
WHERE n >= aml.p('smurf_min_anzahl')
ORDER BY account_id, n DESC, sum_cents DESC;

-- Regel 2: Durchlaufkonto.
-- Ein Konto erhält an einem Tag viel Geld und leitet fast alles kurz danach weiter, und das wiederholt.
CREATE VIEW aml.v_alert_durchlauf AS
WITH inbound AS (
    SELECT t.to_account AS account_id,
           (t.booked_at AT TIME ZONE 'Europe/Berlin')::DATE AS tag,
           min(t.booked_at)    AS first_in,
           sum(t.amount_cents) AS in_cents
    FROM bank.v_transfer t
    JOIN bank.account a ON a.account_id = t.to_account AND a.account_type = 'giro'
    WHERE t.kind = 'ueberweisung'
    GROUP BY 1, 2
    HAVING sum(t.amount_cents) >= aml.p('durchlauf_min_cents')
), days AS (
    SELECT i.*,
           (SELECT coalesce(sum(o.amount_cents), 0)
            FROM bank.v_transfer o
            WHERE o.from_account = i.account_id
              AND o.kind = 'ueberweisung'
              AND o.booked_at >  i.first_in
              AND o.booked_at <= i.first_in + make_interval(hours => aml.p('durchlauf_stunden')::INT)
           ) AS out_cents
    FROM inbound i
), hits AS (
    SELECT * FROM days
    WHERE out_cents >= in_cents * aml.p('durchlauf_min_quote')
      AND out_cents <= in_cents * aml.p('durchlauf_max_quote')
)
SELECT 'durchlauf'::TEXT AS regel,
       account_id,
       max(first_in) AS zeitpunkt,
       format('An %s Tagen Eingänge von zusammen %s EUR, davon %s %% innerhalb von %s Stunden weitergeleitet',
              count(*), aml.eur(sum(in_cents)), round(100.0 * sum(out_cents) / sum(in_cents)),
              aml.p('durchlauf_stunden')::INT) AS begruendung
FROM hits
GROUP BY account_id
HAVING count(*) >= aml.p('durchlauf_min_tage');

-- Regel 3: Kreisüberweisung (Round-Tripping).
-- Geld wandert A -> B -> C -> ... -> A, in zeitlicher Reihenfolge und mit fast gleichem Betrag.
-- Gesucht wird mit einer rekursiven CTE auf dem Graphen der großen Überweisungen.
CREATE VIEW aml.v_kreis AS
WITH RECURSIVE edge AS (
    SELECT t.booking_id, t.booked_at, t.from_account, t.to_account, t.amount_cents
    FROM bank.v_transfer t
    JOIN bank.account af ON af.account_id = t.from_account AND af.account_type = 'giro'
    JOIN bank.account at ON at.account_id = t.to_account   AND at.account_type = 'giro'
    WHERE t.kind = 'ueberweisung'
      AND t.amount_cents >= aml.p('kreis_min_cents')
), walk AS (
    SELECT e.from_account AS start_account,
           e.to_account   AS current_account,
           ARRAY[e.from_account, e.to_account] AS path,
           e.booked_at    AS started_at,
           e.booked_at    AS last_at,
           e.amount_cents AS first_cents,
           e.amount_cents AS last_cents,
           1 AS hops
    FROM edge e
    UNION ALL
    SELECT w.start_account, e.to_account, w.path || e.to_account,
           w.started_at, e.booked_at, w.first_cents, e.amount_cents, w.hops + 1
    FROM walk w
    JOIN edge e ON e.from_account = w.current_account
    WHERE w.current_account <> w.start_account                    -- Kreis noch nicht geschlossen
      AND w.hops < aml.p('kreis_max_laenge')
      AND e.booked_at >  w.last_at
      AND e.booked_at <= w.last_at + make_interval(hours => aml.p('kreis_max_stunden')::INT)
      AND e.amount_cents <= w.last_cents
      AND e.amount_cents >= w.last_cents * aml.p('kreis_min_quote')
      AND (e.to_account = w.start_account OR e.to_account <> ALL (w.path))
)
SELECT start_account, path, hops, started_at, last_at AS closed_at, first_cents, last_cents
FROM walk
WHERE current_account = start_account AND hops >= 3;

CREATE VIEW aml.v_alert_kreis AS
SELECT DISTINCT ON (member.account_id)
       'kreis'::TEXT AS regel,
       member.account_id,
       k.closed_at AS zeitpunkt,
       format('Kreisüberweisung über %s Konten (%s), Startbetrag %s EUR, Rückfluss %s EUR nach %s Stunden',
              k.hops, array_to_string(k.path, ' -> '), aml.eur(k.first_cents), aml.eur(k.last_cents),
              round(extract(epoch FROM k.closed_at - k.started_at) / 3600)) AS begruendung
FROM aml.v_kreis k
CROSS JOIN LATERAL unnest(k.path[1:k.hops]) AS member(account_id)
ORDER BY member.account_id, k.closed_at;

CREATE VIEW aml.v_alert AS
SELECT * FROM aml.v_alert_smurfing
UNION ALL SELECT * FROM aml.v_alert_durchlauf
UNION ALL SELECT * FROM aml.v_alert_kreis;

-- Nur für die Bewertung: Welche Konten sind in der Simulation wirklich verdächtig?
-- Die Regeln oben lesen diese Tabelle nie.
CREATE SCHEMA eval;
CREATE TABLE eval.truth (
    account_id BIGINT NOT NULL REFERENCES bank.account,
    muster     TEXT NOT NULL CHECK (muster IN ('smurfing', 'durchlauf', 'kreis')),
    variante   TEXT NOT NULL,          -- 'offensichtlich' oder eine getarnte Variante
    PRIMARY KEY (account_id, muster)
);
