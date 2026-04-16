"""Tests for afterimage.crypto — CryptoLayer."""
import pytest
from afterimage.crypto import CryptoLayer, DecryptionError, SALT_SIZE, NONCE_SIZE


PLAINTEXT = b"The quick brown fox jumps over the lazy dog"
PASSWORD = "correct-horse-battery-staple"


class TestDeriveKey:
    def test_returns_32_bytes(self):
        import os
        salt = os.urandom(SALT_SIZE)
        key = CryptoLayer.derive_key(PASSWORD, salt)
        assert len(key) == 32

    def test_deterministic(self):
        import os
        salt = os.urandom(SALT_SIZE)
        assert CryptoLayer.derive_key(PASSWORD, salt) == CryptoLayer.derive_key(PASSWORD, salt)

    def test_different_salts_give_different_keys(self):
        import os
        k1 = CryptoLayer.derive_key(PASSWORD, os.urandom(SALT_SIZE))
        k2 = CryptoLayer.derive_key(PASSWORD, os.urandom(SALT_SIZE))
        assert k1 != k2

    def test_empty_password_raises(self):
        import os
        with pytest.raises(ValueError):
            CryptoLayer.derive_key("", os.urandom(SALT_SIZE))

    def test_wrong_salt_size_raises(self):
        with pytest.raises(ValueError):
            CryptoLayer.derive_key(PASSWORD, b"tooshort")


class TestEncryptDecrypt:
    def test_round_trip(self):
        blob = CryptoLayer.encrypt(PLAINTEXT, PASSWORD)
        assert CryptoLayer.decrypt(blob, PASSWORD) == PLAINTEXT

    def test_empty_plaintext(self):
        blob = CryptoLayer.encrypt(b"", PASSWORD)
        assert CryptoLayer.decrypt(blob, PASSWORD) == b""

    def test_large_plaintext(self):
        data = bytes(range(256)) * 400   # 102 400 bytes
        blob = CryptoLayer.encrypt(data, PASSWORD)
        assert CryptoLayer.decrypt(blob, PASSWORD) == data

    def test_different_ciphertexts_same_input(self):
        b1 = CryptoLayer.encrypt(PLAINTEXT, PASSWORD)
        b2 = CryptoLayer.encrypt(PLAINTEXT, PASSWORD)
        assert b1 != b2  # fresh salt + nonce each time

    def test_wrong_password_raises(self):
        blob = CryptoLayer.encrypt(PLAINTEXT, PASSWORD)
        with pytest.raises(DecryptionError):
            CryptoLayer.decrypt(blob, "wrong-password")

    def test_tampered_ciphertext_raises(self):
        blob = bytearray(CryptoLayer.encrypt(PLAINTEXT, PASSWORD))
        blob[-1] ^= 0xFF   # flip last byte of tag
        with pytest.raises(DecryptionError):
            CryptoLayer.decrypt(bytes(blob), PASSWORD)

    def test_truncated_blob_raises(self):
        with pytest.raises(ValueError):
            CryptoLayer.decrypt(b"\x00" * 10, PASSWORD)

    def test_blob_structure(self):
        blob = CryptoLayer.encrypt(PLAINTEXT, PASSWORD)
        # salt + nonce + ciphertext + 16-byte tag
        assert len(blob) == SALT_SIZE + NONCE_SIZE + len(PLAINTEXT) + 16