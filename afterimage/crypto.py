"""
afterimage.crypto
=================
ChaCha20-Poly1305 authenticated encryption with password-derived keys.

Security design:
- Passwords are NEVER accepted as CLI arguments; callers must use getpass or
  pass pre-validated strings.
- Key derivation uses PBKDF2-SHA256 with 600 000 iterations (NIST SP 800-132
  recommendation as of 2023; adjustable via PBKDF2_ITERATIONS).
- Each encrypt() call generates a fresh random salt and nonce, so the same
  password + plaintext pair always produces different ciphertext.
- Wire format: salt (16 B) || nonce (12 B) || ciphertext+tag (N+16 B)
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Final

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.exceptions import InvalidTag

__all__ = ["CryptoLayer", "DecryptionError"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SALT_SIZE: Final[int] = 16   # bytes
NONCE_SIZE: Final[int] = 12  # bytes (ChaCha20 standard)
KEY_SIZE: Final[int] = 32    # bytes (256-bit)

# Increase this constant to raise the brute-force cost on new deployments.
# Existing ciphertext is not affected; the iteration count is NOT stored in
# the wire format, so both sides must agree out-of-band.
PBKDF2_ITERATIONS: Final[int] = 600_000


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class DecryptionError(ValueError):
    """Raised when decryption fails (wrong password or tampered data)."""


# ---------------------------------------------------------------------------
# CryptoLayer
# ---------------------------------------------------------------------------

class CryptoLayer:
    """
    Stateless helper for symmetric encryption / decryption.

    All methods are static so callers don't need to hold an instance.
    """

    @staticmethod
    def derive_key(password: str, salt: bytes) -> bytes:
        """
        Derive a 256-bit key from *password* and *salt* using PBKDF2-SHA256.

        Parameters
        ----------
        password:
            UTF-8 passphrase supplied by the operator.
        salt:
            Cryptographically random 16-byte value.

        Returns
        -------
        bytes
            32-byte derived key, suitable for ChaCha20-Poly1305.
        """
        if not isinstance(password, str) or not password:
            raise ValueError("password must be a non-empty string")
        if len(salt) != SALT_SIZE:
            raise ValueError(f"salt must be exactly {SALT_SIZE} bytes")

        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            PBKDF2_ITERATIONS,
            dklen=KEY_SIZE,
        )

    @staticmethod
    def encrypt(data: bytes, password: str) -> bytes:
        """
        Compress-then-encrypt *data* with a fresh salt and nonce.

        Wire format
        -----------
        ``salt (16 B) || nonce (12 B) || ciphertext+tag (len(data)+16 B)``

        Parameters
        ----------
        data:
            Plaintext bytes to protect.
        password:
            Operator-supplied passphrase.

        Returns
        -------
        bytes
            Authenticated ciphertext blob.
        """
        salt = secrets.token_bytes(SALT_SIZE)
        nonce = secrets.token_bytes(NONCE_SIZE)
        key = CryptoLayer.derive_key(password, salt)
        cipher = ChaCha20Poly1305(key)
        # additional_data=None — no AAD in this protocol version
        ciphertext = cipher.encrypt(nonce, data, None)
        return salt + nonce + ciphertext

    @staticmethod
    def decrypt(blob: bytes, password: str) -> bytes:
        """
        Authenticate and decrypt a blob produced by :meth:`encrypt`.

        Parameters
        ----------
        blob:
            Raw bytes from the wire (salt || nonce || ciphertext+tag).
        password:
            Operator-supplied passphrase.

        Returns
        -------
        bytes
            Recovered plaintext.

        Raises
        ------
        DecryptionError
            If the password is wrong or the ciphertext has been tampered with.
        ValueError
            If the blob is too short to contain salt + nonce + tag.
        """
        min_len = SALT_SIZE + NONCE_SIZE + 16  # 16 = Poly1305 tag
        if len(blob) < min_len:
            raise ValueError(
                f"blob too short: need at least {min_len} bytes, got {len(blob)}"
            )

        salt = blob[:SALT_SIZE]
        nonce = blob[SALT_SIZE : SALT_SIZE + NONCE_SIZE]
        ciphertext = blob[SALT_SIZE + NONCE_SIZE :]
        key = CryptoLayer.derive_key(password, salt)
        cipher = ChaCha20Poly1305(key)

        try:
            return cipher.decrypt(nonce, ciphertext, None)
        except InvalidTag as exc:
            # Deliberately vague — don't leak whether it was the password
            # or data integrity that failed.
            raise DecryptionError(
                "Decryption failed: wrong password or corrupted data."
            ) from exc