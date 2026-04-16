# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.x     | ✅ Yes    |

## Threat Model

AFTERIMAGE provides **confidentiality** and **integrity** for data
transmitted across an optical (QR stream) channel.

### What AFTERIMAGE protects against

- **Passive interception**: An adversary who captures the full QR stream
  obtains only authenticated ciphertext. Without the password, the
  plaintext is computationally inaccessible (ChaCha20-Poly1305 + PBKDF2).
- **Bitflip / tampering**: Poly1305 authentication detects any modification
  to the ciphertext. Decryption will fail entirely on a single altered bit.
- **Replay isolation**: There is no network socket to probe. The channel
  is unidirectional and ephemeral.

### What AFTERIMAGE does NOT protect against

- **Password compromise**: If an adversary learns your password, all past
  and future transmissions are compromised. Use a strong, unique passphrase.
- **Screen recording metadata**: The transmitter window is visible on screen.
  An adversary recording the screen may correlate transmission timing with
  other side-channels. Status information is printed to stdout only — it is
  NOT rendered on the QR image — but the window title and timing remain
  visible.
- **Filename disclosure**: The filename is transmitted in cleartext inside
  the METADATA frame. Pass a decoy name if anonymity is required.
- **Physical access to the transmitting machine**: AFTERIMAGE cannot protect
  against keyloggers, compromised hardware, or an adversary with physical
  access to the air-gapped system.
- **Password in environment variables**: Using `AFTERIMAGE_PASSWORD` is
  safer than a CLI argument but the variable may still appear in process
  listings or shell init files if not handled carefully.

## Reporting a Vulnerability

Please report security vulnerabilities **privately** before public disclosure.

**Contact:** nzengi@proton.me  
**PGP:** Available on request — include "PGP key request" in the subject line.  
**Subject line format:** `[SECURITY] <brief description>`

We aim to:
- Acknowledge receipt within **48 hours**
- Provide an initial assessment within **7 days**
- Release a patch within **30 days** for critical issues

Please do **not** open a public GitHub issue for security vulnerabilities.

## Cryptographic Primitives

| Primitive | Purpose | Library |
|-----------|---------|---------|
| ChaCha20-Poly1305 | Authenticated encryption (AEAD) | `cryptography` (BoringSSL/OpenSSL) |
| PBKDF2-SHA256 | Password-based key derivation | Python stdlib `hashlib` |
| `secrets.token_bytes` | Salt and nonce generation | Python stdlib `secrets` (OS CSPRNG) |

Key derivation uses **600,000 PBKDF2 iterations** (NIST SP 800-132 guidance).
This value is a compile-time constant in `afterimage/crypto.py` and can be
increased for higher-security deployments.