"""Deutsche IBAN erzeugen und prüfen (ISO 13616, Modulo 97)."""

BLZ_DEMO = "30099999"  # frei erfundene Bankleitzahl für dieses Projekt


def _mod97(text: str) -> int:
    digits = "".join(str(int(ch, 36)) for ch in text)  # A = 10 ... Z = 35
    return int(digits) % 97


def make_iban(account_number: int, blz: str = BLZ_DEMO) -> str:
    bban = f"{blz}{account_number:010d}"
    check = 98 - _mod97(bban + "DE00")
    return f"DE{check:02d}{bban}"


def is_valid(iban: str) -> bool:
    if len(iban) < 15 or not iban.isalnum() or not iban.isupper():
        return False
    return _mod97(iban[4:] + iban[:4]) == 1
