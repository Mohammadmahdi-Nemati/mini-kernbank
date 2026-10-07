"""Tests für die Geldwäsche-Regeln mit kleinen, von Hand gebauten Beispielen."""

from kernbank import generator, iban


def test_smurfing_drei_knappe_einzahlungen_in_einer_woche(bank):
    smurfer, normal, weit_auseinander = bank.giro(), bank.giro(), bank.giro()
    for day in (4, 6, 8):
        bank.pay(bank.kasse, smurfer, 9500, at=f"2026-05-{day:02d} 10:00+00", kind="bareinzahlung")
        bank.pay(bank.kasse, normal, 2000, at=f"2026-05-{day:02d} 10:00+00", kind="bareinzahlung")
    for day in (1, 11, 21):                                   # knapp, aber nie drei in sieben Tagen
        bank.pay(bank.kasse, weit_auseinander, 9500, at=f"2026-05-{day:02d} 10:00+00", kind="bareinzahlung")
    assert bank.alerts("smurfing") == {smurfer}


def test_smurfing_einzahlung_ueber_der_schwelle_zaehlt_nicht(bank):
    konto = bank.giro()
    for day in (4, 5, 6):
        bank.pay(bank.kasse, konto, 12000, at=f"2026-05-{day:02d} 10:00+00", kind="bareinzahlung")
    assert bank.alerts("smurfing") == set()


def test_durchlaufkonto_wird_erkannt_sparer_mit_zwei_tagen_nicht(bank):
    mule, sparer, normal = bank.giro(), bank.giro(), bank.giro(1000)
    for day in (4, 11, 18):
        bank.pay(bank.extern, mule, 8000, at=f"2026-05-{day:02d} 09:00+00")
        bank.pay(mule, bank.extern, 7600, at=f"2026-05-{day:02d} 15:00+00")       # 95 % nach 6 Stunden
        bank.pay(bank.extern, normal, 8000, at=f"2026-05-{day:02d} 09:00+00")
        bank.pay(normal, bank.extern, 2500, at=f"2026-05-{day:02d} 15:00+00")     # nur 31 % weiter
    for day in (4, 11):
        bank.pay(bank.extern, sparer, 6000, at=f"2026-05-{day:02d} 09:00+00")
        bank.pay(sparer, bank.extern, 5700, at=f"2026-05-{day:02d} 15:00+00")
    assert bank.alerts("durchlauf") == {mule}


def test_kreis_ueber_drei_konten_wird_erkannt(bank):
    a, b, c = bank.giro(10000), bank.giro(), bank.giro()
    bank.pay(a, b, 9000, at="2026-05-04 10:00+00")
    bank.pay(b, c, 8800, at="2026-05-05 10:00+00")
    bank.pay(c, a, 8600, at="2026-05-06 10:00+00")
    assert bank.alerts("kreis") == {a, b, c}
    path = bank.conn.execute("SELECT path FROM aml.v_kreis").fetchall()
    assert path == [([a, b, c, a],)]                          # genau einmal, nicht dreimal rotiert


def test_kein_kreis_bei_darlehen_kette_oder_falscher_reihenfolge(bank):
    a, b, c, d = bank.giro(50000), bank.giro(50000), bank.giro(50000), bank.giro(50000)
    bank.pay(a, b, 5000, at="2026-05-04 10:00+00")            # Darlehen hin und zurück: nur 2 Konten
    bank.pay(b, a, 5000, at="2026-05-05 10:00+00")
    bank.pay(a, c, 9000, at="2026-05-10 10:00+00")            # Kette ohne Rückfluss
    bank.pay(c, d, 8800, at="2026-05-11 10:00+00")
    bank.pay(d, b, 7000, at="2026-05-20 10:00+00")            # Kreis d -> b -> c -> d, aber zeitlich rückwärts
    bank.pay(b, c, 7000, at="2026-05-19 10:00+00")
    bank.pay(c, d, 7000, at="2026-05-18 10:00+00")
    assert bank.alerts("kreis") == set()


def test_storniertes_geld_loest_keinen_alarm_aus(bank):
    a, b, c = bank.giro(10000), bank.giro(), bank.giro()
    bank.pay(a, b, 9000, at="2026-05-04 10:00+00")
    bank.pay(b, c, 8800, at="2026-05-05 10:00+00")
    last = bank.pay(c, a, 8600, at="2026-05-06 10:00+00")
    bank.conn.execute("SELECT bank.reverse('fehlbuchung', %s)", (last,))
    assert bank.alerts("kreis") == set()


def test_simulation_ist_wiederholbar_und_ausgeglichen():
    rows_a = generator.finalize(generator.build_world(seed=7))
    rows_b = generator.finalize(generator.build_world(seed=7))
    assert rows_a == rows_b
    assert all(cents > 0 and src != dst for _, _, src, dst, cents, _ in rows_a)
    assert iban.is_valid(iban.make_iban(123))
