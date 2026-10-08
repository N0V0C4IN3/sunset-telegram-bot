"""Coordinates are Fernet ciphertext at rest; a silent break here loses every
stored location and forces every subscriber to re-share."""

from dataclasses import dataclass

import pytest
from cryptography.fernet import InvalidToken

from app.services.location_crypto import LocationCrypto


@dataclass
class FakeSettings:
    location_encryption_key: str


def crypto(key: str = "a-stable-secret-key") -> LocationCrypto:
    return LocationCrypto(FakeSettings(location_encryption_key=key))


@pytest.mark.parametrize("coordinate", [50.4501, 30.5234, -33.8688, 0.0, -0.000001, 179.999999, -89.5])
def test_a_coordinate_survives_a_round_trip(coordinate):
    box = crypto()
    assert box.decrypt_coordinate(box.encrypt_coordinate(coordinate)) == pytest.approx(coordinate, abs=1e-6)


def test_the_ciphertext_does_not_contain_the_coordinate():
    encrypted = crypto().encrypt_coordinate(50.4501)
    assert "50.4501" not in encrypted


def test_the_same_coordinate_encrypts_differently_each_time():
    # Fernet embeds a timestamp and IV, so identical input must not produce
    # identical ciphertext — otherwise stored rows leak which users share a location.
    box = crypto()
    assert box.encrypt_coordinate(50.4501) != box.encrypt_coordinate(50.4501)


def test_a_value_encrypted_under_one_key_does_not_decrypt_under_another():
    encrypted = crypto("the-original-key").encrypt_coordinate(50.4501)
    with pytest.raises(InvalidToken):
        crypto("a-different-key").decrypt_coordinate(encrypted)


def test_the_same_key_always_derives_the_same_box():
    encrypted = crypto("shared-key").encrypt_coordinate(50.4501)
    assert crypto("shared-key").decrypt_coordinate(encrypted) == pytest.approx(50.4501)


def test_coordinates_are_stored_at_six_decimal_places():
    # Roughly 11cm of precision: enough for weather, and a deliberate cap on how
    # precisely a stored location pins someone down.
    box = crypto()
    assert box.decrypt_coordinate(box.encrypt_coordinate(50.45012345)) == pytest.approx(50.450123, abs=1e-6)


def test_tampered_ciphertext_is_rejected_rather_than_silently_wrong():
    box = crypto()
    encrypted = box.encrypt_coordinate(50.4501)
    tampered = encrypted[:-4] + ("AAAA" if not encrypted.endswith("AAAA") else "BBBB")
    with pytest.raises(InvalidToken):
        box.decrypt_coordinate(tampered)
