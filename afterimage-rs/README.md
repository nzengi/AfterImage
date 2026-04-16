# AfterImage — Rust v2

**Air-gap file transfer via animated QR codes**

AfterImage encodes any file into a stream of QR codes that can be transmitted
across an air-gap (no network, no USB) and decoded on the receiving machine via
its camera.  All data is encrypted end-to-end; a screen recording cannot
reveal the payload.

---

## Architecture

```
afterimage-rs/
├── crates/
│   ├── afterimage-core      # Crypto + fountain codes + protocol (no_std-compatible)
│   ├── afterimage-optical   # QR encode/decode, camera capture, minifb display
│   ├── afterimage-cli       # `afterimage` binary (send / recv / bench)
│   ├── afterimage-solana    # AirSign — air-gap Solana transaction signing
│   └── afterimage-wasm      # WebAssembly bindings (wasm-pack / browser)
└── tests/
    └── integration_test.rs  # End-to-end roundtrip tests
```

### Data flow

```
Send side                            Receive side
─────────                            ────────────
plaintext                            
  │ zlib compress                    
  ▼                                  
compressed bytes                     
  │ Argon2id key-derive              
  │ ChaCha20-Poly1305 encrypt        
  ▼                                  
ciphertext                           
  │ LT (Luby Transform) fountain     
  │ encode → droplets                
  ▼                                  
QR frames ──── optical channel ────► QR frames
(animated window / display)          (camera capture)
                                        │ QR decode → droplets
                                        │ LT decode → ciphertext
                                        │ ChaCha20-Poly1305 decrypt
                                        │ zlib decompress
                                        ▼
                                     plaintext ✓
```

---

## Crates

### `afterimage-core`

The cryptographic and fountain-code heart of the library.

| Module | Purpose |
|---|---|
| `crypto` | Argon2id KDF, ChaCha20-Poly1305 AEAD, zlib compress/decompress |
| `fountain` | LT encoder / decoder (Luby Transform erasure codes) |
| `protocol` | Binary frame format (METADATA + DATA frames) |
| `session` | High-level `SendSession` / `RecvSession` |
| `error` | `AfterImageError` enum |

### `afterimage-optical`

QR code generation (via `qrcode`) and decoding (via `rxing`), camera
capture (via `nokhwa`), and animated window display (via `minifb`).

Features: `display` (default on), `camera` (default on).

### `afterimage-cli`

The `afterimage` binary.

```
USAGE:
    afterimage send  <FILE> [--fps N] [--window-size PX] [--password P]
    afterimage recv  <OUTPUT> [--camera-index N] [--password P]
    afterimage bench <FILE> [--password P]
```

Set `AFTERIMAGE_PASSWORD` env-var instead of using `--password`.

### `afterimage-solana` — AirSign

Air-gap Solana transaction signing protocol.

```
Online machine                       Air-gapped signer
──────────────                       ─────────────────
build unsigned tx                    
  │ build_send_session()             
  ▼                                  
QR stream ──────────────────────────► SignRequest (JSON)
                                        │ AirSigner::sign_request()
                                        │ Ed25519 sign
                                        ▼
QR stream ◄────────────────────────── SignResponse (JSON)
  │ decode_transaction()             
  ▼                                  
submit to cluster ✓                  
```

### `afterimage-wasm`

Browser-ready WASM bindings.  Build with:

```bash
wasm-pack build crates/afterimage-wasm --target web --release
```

JavaScript API:

```js
import init, { WasmSendSession, WasmRecvSession, recommended_frames }
    from './afterimage_wasm.js';
await init();

// Sender
const session = new WasmSendSession(uint8Data, "file.bin", "password");
while (session.has_next()) {
    const frame = session.next_frame();
    renderQrCode(frame);
    await sleep(150);
}

// Receiver
const rx = new WasmRecvSession("password");
rx.ingest_frame(decodedQrPayload);
if (rx.is_complete()) {
    const data = rx.get_data();   // Uint8Array
}
```

---

## Building

### Prerequisites

```bash
# Rust toolchain
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# WASM target (optional)
rustup target add wasm32-unknown-unknown
cargo install wasm-pack
```

### CLI binary

```bash
cd afterimage-rs
cargo build --release -p afterimage
./target/release/afterimage --help
```

### Run all tests

```bash
cd afterimage-rs
cargo test --workspace
```

### WASM package

```bash
cd afterimage-rs
wasm-pack build crates/afterimage-wasm --target web --release
```

---

## Security

| Property | Mechanism |
|---|---|
| Confidentiality | ChaCha20-Poly1305 AEAD |
| Key derivation | Argon2id (m=64 MiB, t=3, p=4) |
| Integrity | Poly1305 MAC per ciphertext |
| Replay protection (AirSign) | 32-byte random nonce per request |
| Eavesdrop-resistant QR stream | Entire payload encrypted before encoding |

**No key material ever crosses the air-gap.**  The password is typed on each
machine independently.  A QR stream video recording leaks nothing without
the password.

---

## Protocol versions

| Version | KDF | AEAD | Notes |
|---|---|---|---|
| v1 | PBKDF2-SHA256 | AES-256-GCM | Python original — read-only compat |
| v2 | Argon2id | ChaCha20-Poly1305 | Current default |

The receiver auto-detects the version from the METADATA frame.

---

## License

AGPL-3.0-or-later — see [LICENSE](../LICENSE).