# AFTERIMAGE

> *"The only way to keep a secret is to never have one."*
> — But what if the medium itself forgets?

**Optical air-gap data exfiltration via QR stream with LT Fountain Codes.**

[![License: AGPL-3.0-or-later](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)

---

## The Problem

They watch the wires. They own the spectrum. Every packet you send traverses
infrastructure you do not control, logged by entities you will never meet,
parsed by algorithms you cannot audit.

TCP/IP was a dream of openness. It became a panopticon.

Bluetooth whispers, but its handshake screams. RF is a broadcast to anyone
with an ear. Even "end-to-end encryption" relies on *their* relays, *their*
timestamps, *their* metadata.

The network is compromised. Not by a bug, but by design.
The architecture itself is the vulnerability.

---

## The Solution

**Light leaves no trace.**

AFTERIMAGE transmits files across an air-gap using only visible light — a
stream of QR codes displayed on one screen and recorded by a camera on the
other side. No network. No Bluetooth. No USB. No log.

Pipeline: `compress (zlib) → encrypt (ChaCha20-Poly1305) → fountain-code (LT) → QR stream`

- **Resilience**: LT Fountain Codes tolerate 30–50 % frame loss. Order is
  irrelevant. A camera that shakes, blurs, or misses frames still recovers
  the file once *enough* droplets are captured.
- **Confidentiality**: The password never leaves your mind. Key derivation is
  local and ephemeral. No key exchange, no certificate authority, no third party.
- **Ephemerality**: Once transmission ends, the channel vanishes. There is no
  session to hijack, no socket to probe.

---

## Quick Start

### Install

```bash
pip install afterimage                  # core (segno QR backend)
pip install "afterimage[pyzbar]"        # + faster QR scanning
pip install "afterimage[dev]"           # + dev/test tools
```

### Requirements

| Dependency | Purpose |
|---|---|
| `numpy` | Fountain-code XOR arithmetic |
| `opencv-python` | Camera capture, QR fallback decoder, display |
| `cryptography` | ChaCha20-Poly1305, PBKDF2 |
| `segno` | Fast QR generation (recommended) |
| `Pillow` | Image conversion for segno |
| `pyzbar` *(optional)* | Faster QR scanning (requires `libzbar`) |

### Transmit a file

```bash
python -m afterimage --tx secret.zip
# Password: <enter passphrase — not echoed>
# Confirm password: <enter again>
```

A fullscreen QR stream starts. Point a camera at the screen from the
receiving machine.

### Receive a file

```bash
python -m afterimage --rx recovered.zip
# Password: <enter passphrase>
```

The receiver scans the QR stream and reconstructs the file once enough
droplets have been captured. Press **q** to abort.

### Non-interactive mode (scripts / CI)

```bash
export AFTERIMAGE_PASSWORD="your-passphrase"
python -m afterimage --tx secret.zip
```

The environment variable is read once and immediately scrubbed from
`os.environ`.

> **Security note:** Never pass the password as `--password` on the command
> line. It will appear in `ps aux`, shell history, and system audit logs.

---

## Architecture

```
afterimage/
├── crypto.py    — ChaCha20-Poly1305 AEAD, PBKDF2-SHA256 key derivation
├── fountain.py  — LT Fountain Code encoder / decoder (Robust Soliton)
├── optical.py   — QR generation (segno/qrcode) + camera scanning (pyzbar/cv2)
├── protocol.py  — TX / RX orchestration, wire-format, METADATA frames
└── cli.py       — Secure CLI entry point (getpass, no --password flag)
```

### Cryptographic pipeline

```
plaintext
  │  zlib compress (level 9)
  │  ChaCha20-Poly1305 encrypt  ← PBKDF2-SHA256(password, random_salt, 600k iter)
  │  LT Fountain encode          ← Robust Soliton Distribution
  ▼
QR stream  ──[optical channel]──▶  camera
  │
  │  LT Fountain decode          ← belief propagation, inverted index
  │  ChaCha20-Poly1305 decrypt
  │  zlib decompress
  ▼
plaintext
```

---

## Security

Read [SECURITY.md](SECURITY.md) for the full threat model, responsible
disclosure policy, and cryptographic primitive details.

**Key properties:**

- AEAD authentication: any tampered bit causes decryption to fail entirely.
- Fresh random salt + nonce per encryption call — same password + plaintext
  never produces the same ciphertext.
- 600,000 PBKDF2 iterations (NIST SP 800-132); adjustable in `crypto.py`.
- Filename transmitted in cleartext in METADATA frames — use a decoy name
  if anonymity is required.

---

## Development

```bash
git clone https://github.com/nzengi/AfterImage.git
cd afterimage
pip install -e ".[dev]"

# Run fast tests
pytest -m "not slow and not camera"

# Run all tests with coverage
pytest --cov=afterimage --cov-report=term-missing

# Lint + format
ruff check --fix afterimage/ tests/
ruff format afterimage/ tests/
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for full guidelines.

---

## Licensing

AFTERIMAGE is dual-licensed:

**Open source (AGPL-3.0-or-later)**
Free for individuals, security researchers, journalists, academics, and
open-source projects. If you modify and distribute AFTERIMAGE (including
as a network service), you must publish your modifications under the same
license.

**Commercial license**
Required for use in closed-source or proprietary products, or in any context
where AGPL-3.0 terms are incompatible with your requirements.

Commercial licensees receive:
- Use in proprietary products without AGPL disclosure obligations
- Priority security patch notifications
- Direct engineering support

**Contact:** nzengi@proton.me
**Subject:** Commercial License Inquiry

---

## Philosophy

We do not build tools for the lawful. We do not build tools for the lawless.
We build tools for the *autonomous* — those who believe that the ability to
communicate privately is not a privilege granted by the state, but a natural
right that predates the state.

*"Privacy is necessary for an open society in the electronic age."*
— Eric Hughes, *A Cypherpunk's Manifesto*, 1993

---

**AFTERIMAGE** — *When the screen goes dark, the data survives.*