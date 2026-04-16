# Changelog

All notable changes to AFTERIMAGE are documented here.
This project follows [Semantic Versioning](https://semver.org/).

---

## [1.0.0] — 2024-01-01

### Added
- Initial public release.
- `afterimage.crypto` — ChaCha20-Poly1305 AEAD with PBKDF2-SHA256 key
  derivation (600 000 iterations).
- `afterimage.fountain` — LT Fountain Code encoder/decoder with Robust
  Soliton Distribution and belief-propagation decoding.
- `afterimage.optical` — QR code generation (`segno` / `qrcode` backends)
  and camera-based scanning (`pyzbar` / OpenCV backends).
- `afterimage.protocol` — High-level `AfterImage.tx()` / `AfterImage.rx()`
  orchestration with periodic METADATA frame injection.
- `afterimage.cli` — Secure CLI entry point using `getpass` (no `--password`
  argument; passwords are never exposed in process listings or shell history).
- Dual license: AGPL-3.0-or-later (open source) + commercial license option.
- Full test suite: `tests/test_crypto.py`, `tests/test_fountain.py`,
  `tests/test_protocol.py`.
- `pyproject.toml` with PEP 621 metadata, `ruff`, `mypy`, `pytest`, and
  `coverage` configuration.

### Security
- Removed `--password` CLI argument (shell history / `ps aux` leak).
- Upgraded PBKDF2 iterations from 100 000 to 600 000.
- Decoder no longer trusts the `degree` header field; neighbours are always
  re-derived deterministically from the seed (fixes header/decoder divergence
  bug in v0.x).
- Status overlays printed to stdout only — not burned into QR images — to
  prevent metadata leakage through screen recordings.
- `DecryptionError` message is deliberately vague to avoid oracle attacks.

### Performance
- `LTDecoder._propagate` rewritten with an inverted index: O(degree) per
  resolved block instead of O(n × pending_droplets).
- `RobustSoliton` instantiated once per encoder/decoder session (was
  re-created per droplet in v0.x).
- QR generation now defaults to `segno` (3-5× faster than `qrcode`).
- QR error-correction level upgraded from L (7 %) to M (15 %) for better
  resilience to optical noise, camera shake, and partial obstructions.

---

## [0.1.0] — Initial prototype

- Single-file `afterimage.py` proof-of-concept.