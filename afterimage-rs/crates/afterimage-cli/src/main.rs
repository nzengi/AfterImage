//! afterimage — CLI binary
//! =======================
//! Air-gap file transfer via animated QR codes.
//!
//! ## Sub-commands
//!
//! ```text
//! afterimage send  <FILE> [--fps N] [--window-size PX]
//! afterimage recv  <OUTPUT> [--camera-index N]
//! afterimage bench <FILE>            # offline encode/decode benchmark
//! afterimage multisign init  <TX_BIN> --signers PK,PK,PK --threshold M [--out round1.json]
//! afterimage multisign sign  <REQUEST_JSON> --keypair keypair.json [--out resp.json]
//! afterimage multisign next  <RESPONSE_JSON> --request round1.json [--out round2.json]
//! ```
//!
//! `multisign` sub-commands are only available when compiled with
//! `--features solana`.

use std::path::PathBuf;

use clap::{Parser, Subcommand};
use indicatif::{ProgressBar, ProgressStyle};

use afterimage_core::session::{RecvSession, SendSession};

// ─── CLI definition ───────────────────────────────────────────────────────────

#[derive(Parser)]
#[command(
    name  = "afterimage",
    about = "Air-gap file transfer via animated QR codes (Rust v2)",
    version,
    propagate_version = true
)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Encrypt a file and transmit it as an animated QR stream.
    Send {
        /// File to transmit.
        file: PathBuf,

        /// Frames per second (default: 6).
        #[arg(long, default_value_t = 6)]
        fps: u32,

        /// Display window edge size in pixels (default: 600).
        #[arg(long, default_value_t = 600)]
        window_size: usize,

        /// Password (prompted securely if omitted).
        #[arg(long, env = "AFTERIMAGE_PASSWORD")]
        password: Option<String>,
    },

    /// Receive and decrypt a file from the camera QR stream.
    Recv {
        /// Output file path.
        output: PathBuf,

        /// Camera device index (default: 0).
        #[arg(long, default_value_t = 0)]
        camera_index: u32,

        /// Password (prompted securely if omitted).
        #[arg(long, env = "AFTERIMAGE_PASSWORD")]
        password: Option<String>,
    },

    /// Offline encode + decode benchmark (no camera/display required).
    Bench {
        /// File to benchmark.
        file: PathBuf,

        /// Password (default: "benchmark").
        #[arg(long, default_value = "benchmark")]
        password: String,
    },

    /// M-of-N multi-signature workflow (requires --features solana).
    #[cfg(feature = "solana")]
    Multisign {
        #[command(subcommand)]
        action: MultisignAction,
    },
}

/// Sub-commands for the `multisign` workflow.
#[cfg(feature = "solana")]
#[derive(Subcommand)]
enum MultisignAction {
    /// Initialise a new M-of-N session from an unsigned transaction binary.
    ///
    /// Reads a bincode-serialised `Transaction` from TX_BIN and writes a
    /// round-1 `MultiSignRequest` JSON to OUTPUT.
    Init {
        /// Bincode-serialised unsigned transaction.
        tx_bin: PathBuf,

        /// Comma-separated ordered list of signer public keys (base58).
        #[arg(long, value_delimiter = ',')]
        signers: Vec<String>,

        /// Minimum number of signatures required (M).
        #[arg(long)]
        threshold: u8,

        /// Human-readable description shown on the air-gapped screen.
        #[arg(long, default_value = "")]
        description: String,

        /// Solana cluster hint (mainnet-beta | devnet | testnet | localnet).
        #[arg(long, default_value = "mainnet-beta")]
        cluster: String,

        /// Output JSON path (default: round1.json).
        #[arg(long, default_value = "round1.json")]
        out: PathBuf,
    },

    /// Sign one round of a multi-sig session on the air-gapped machine.
    ///
    /// Reads a `MultiSignRequest` JSON from REQUEST_JSON, signs with the
    /// provided keypair, and writes a `MultiSignResponse` JSON to OUTPUT.
    Sign {
        /// MultiSignRequest JSON file (from `multisign init` or `multisign next`).
        request_json: PathBuf,

        /// Path to a JSON keypair file (Solana CLI format: `[u8; 64]` array).
        #[arg(long)]
        keypair: PathBuf,

        /// Output JSON path (default: response.json).
        #[arg(long, default_value = "response.json")]
        out: PathBuf,
    },

    /// Advance to the next signing round.
    ///
    /// Combines the previous `MultiSignResponse` with the original round-1
    /// request metadata to produce the next round's `MultiSignRequest` JSON.
    Next {
        /// MultiSignResponse JSON from the previous round.
        response_json: PathBuf,

        /// Original round-1 request JSON (for threshold / signers / metadata).
        #[arg(long)]
        request: PathBuf,

        /// Output JSON path (default: next_round.json).
        #[arg(long, default_value = "next_round.json")]
        out: PathBuf,
    },
}

// ─── Entry point ─────────────────────────────────────────────────────────────

fn main() {
    let cli = Cli::parse();

    match cli.command {
        Commands::Send {
            file,
            fps,
            window_size,
            password,
        } => cmd_send(file, fps, window_size, password),

        Commands::Recv {
            output,
            camera_index,
            password,
        } => cmd_recv(output, camera_index, password),

        Commands::Bench { file, password } => cmd_bench(file, password),

        #[cfg(feature = "solana")]
        Commands::Multisign { action } => cmd_multisign(action),
    }
}

// ─── send ─────────────────────────────────────────────────────────────────────

fn cmd_send(file: PathBuf, fps: u32, window_size: usize, password: Option<String>) {
    let data = std::fs::read(&file).unwrap_or_else(|e| {
        eprintln!("error: cannot read {:?}: {e}", file);
        std::process::exit(1);
    });

    let password = resolve_password(password, "Encryption password: ");

    let filename = file
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("data.bin");

    let mut session = SendSession::new(&data, filename, &password).unwrap_or_else(|e| {
        eprintln!("error: {e}");
        std::process::exit(1);
    });

    let frame_ms = 1000 / fps.max(1);
    let recommended = session.recommended_droplet_count();

    eprintln!(
        "[afterimage] send: {} bytes | ~{} droplets recommended | {fps} fps",
        data.len(),
        recommended
    );

    #[cfg(feature = "display")]
    {
        use afterimage_optical::display::QrDisplay;

        let mut disp = QrDisplay::new("AfterImage — Transmitting", window_size)
            .unwrap_or_else(|e| {
                eprintln!("error opening window: {e}");
                std::process::exit(1);
            });
        disp.frame_ms = frame_ms as u64;

        let count = disp.run_session(&mut session);
        eprintln!("[afterimage] sent {count} frames");
    }

    #[cfg(not(feature = "display"))]
    {
        use afterimage_optical::qr::encode_qr;
        eprintln!("[afterimage] display feature not enabled — saving QR PNGs instead");
        let mut i = 0usize;
        while let Some(frame) = session.next_frame() {
            let qr = encode_qr(&frame).unwrap();
            qr.save_png(&format!("frame_{i:05}.png")).unwrap();
            i += 1;
        }
        eprintln!("[afterimage] saved {i} QR PNG files");
    }
}

// ─── recv ─────────────────────────────────────────────────────────────────────

fn cmd_recv(output: PathBuf, camera_index: u32, password: Option<String>) {
    let password = resolve_password(password, "Decryption password: ");

    eprintln!("[afterimage] recv: waiting for QR stream on camera {camera_index}…");

    #[cfg(feature = "camera")]
    {
        use afterimage_optical::camera::CameraReceiver;

        let mut rx = CameraReceiver::open(camera_index, &password).unwrap_or_else(|e| {
            eprintln!("error: {e}");
            std::process::exit(1);
        });

        let data = rx.receive().unwrap_or_else(|e| {
            eprintln!("error: {e}");
            std::process::exit(1);
        });

        std::fs::write(&output, &data).unwrap_or_else(|e| {
            eprintln!("error writing {:?}: {e}", output);
            std::process::exit(1);
        });

        eprintln!(
            "[afterimage] recv: wrote {} bytes to {:?}",
            data.len(),
            output
        );
    }

    #[cfg(not(feature = "camera"))]
    {
        eprintln!("error: camera feature not enabled; rebuild with --features camera");
        std::process::exit(1);
    }
}

// ─── bench ────────────────────────────────────────────────────────────────────

fn cmd_bench(file: PathBuf, password: String) {
    let data = std::fs::read(&file).unwrap_or_else(|e| {
        eprintln!("error: cannot read {:?}: {e}", file);
        std::process::exit(1);
    });

    let size = data.len();
    eprintln!("[bench] file size: {size} bytes");

    // ── Encode phase ──────────────────────────────────────────────────────
    let t0 = std::time::Instant::now();
    let mut send = SendSession::new(&data, "bench.bin", &password).unwrap_or_else(|e| {
        eprintln!("error: {e}");
        std::process::exit(1);
    });

    let recommended = send.recommended_droplet_count();
    let limit = (recommended * 3) as u32 + 200;
    send.set_limit(limit);

    let frames: Vec<Vec<u8>> = std::iter::from_fn(|| send.next_frame()).collect();
    let encode_ms = t0.elapsed().as_millis();
    eprintln!(
        "[bench] encoded {} frames in {encode_ms} ms ({:.1} MB/s)",
        frames.len(),
        size as f64 / 1e6 / (encode_ms as f64 / 1000.0).max(0.001)
    );

    // ── Decode phase ──────────────────────────────────────────────────────
    let pb = ProgressBar::new(frames.len() as u64);
    pb.set_style(
        ProgressStyle::with_template("[bench] decoding {bar:40} {pos}/{len} frames")
            .unwrap(),
    );

    let t1 = std::time::Instant::now();
    let mut recv = RecvSession::new(&password);
    for frame in &frames {
        pb.inc(1);
        if recv.ingest_frame(frame).unwrap() {
            break;
        }
    }
    pb.finish_and_clear();

    let decode_ms = t1.elapsed().as_millis();

    if recv.is_complete() {
        let recovered = recv.get_data().unwrap();
        if recovered == data {
            eprintln!(
                "[bench] ✓ roundtrip OK in {decode_ms} ms ({:.1} MB/s)",
                size as f64 / 1e6 / (decode_ms as f64 / 1000.0).max(0.001)
            );
        } else {
            eprintln!("[bench] ✗ data mismatch after roundtrip!");
            std::process::exit(2);
        }
    } else {
        eprintln!(
            "[bench] ✗ decoding incomplete after {} frames (progress={:.1}%)",
            frames.len(),
            recv.progress() * 100.0
        );
        std::process::exit(2);
    }
}

// ─── multisign ────────────────────────────────────────────────────────────────

#[cfg(feature = "solana")]
fn cmd_multisign(action: MultisignAction) {
    match action {
        MultisignAction::Init {
            tx_bin,
            signers,
            threshold,
            description,
            cluster,
            out,
        } => cmd_multisign_init(tx_bin, signers, threshold, description, cluster, out),

        MultisignAction::Sign {
            request_json,
            keypair,
            out,
        } => cmd_multisign_sign(request_json, keypair, out),

        MultisignAction::Next {
            response_json,
            request,
            out,
        } => cmd_multisign_next(response_json, request, out),
    }
}

/// `afterimage multisign init` — build a round-1 request from an unsigned tx.
#[cfg(feature = "solana")]
fn cmd_multisign_init(
    tx_bin: PathBuf,
    signer_strs: Vec<String>,
    threshold: u8,
    description: String,
    cluster: String,
    out: PathBuf,
) {
    use afterimage_solana::build_multisig_session;

    let tx_raw = std::fs::read(&tx_bin).unwrap_or_else(|e| {
        eprintln!("error: cannot read {:?}: {e}", tx_bin);
        std::process::exit(1);
    });

    // Parse signer public keys
    let signers: Vec<solana_sdk::pubkey::Pubkey> = signer_strs
        .iter()
        .map(|s| {
            s.parse().unwrap_or_else(|e| {
                eprintln!("error: invalid pubkey {s:?}: {e}");
                std::process::exit(1);
            })
        })
        .collect();

    // Deserialise the unsigned transaction
    let tx: solana_sdk::transaction::Transaction = bincode::deserialize(&tx_raw)
        .unwrap_or_else(|e| {
            eprintln!("error: failed to deserialise transaction: {e}");
            std::process::exit(1);
        });

    let req = build_multisig_session(&tx, &signers, threshold, &description, &cluster)
        .unwrap_or_else(|e| {
            eprintln!("error: {e}");
            std::process::exit(1);
        });

    let json = req.to_json().unwrap_or_else(|e| {
        eprintln!("error: JSON serialise: {e}");
        std::process::exit(1);
    });
    std::fs::write(&out, &json).unwrap_or_else(|e| {
        eprintln!("error: write {:?}: {e}", out);
        std::process::exit(1);
    });

    eprintln!(
        "[multisign] init: threshold={}/{}, {} signers → {:?}",
        threshold,
        signers.len(),
        signers.len(),
        out
    );
    eprintln!("  Round 1 signer: {}", signers[0]);
    eprintln!("  Transmit {:?} via: afterimage send {:?}", out, out);
}

/// `afterimage multisign sign` — air-gapped signer processes one round.
#[cfg(feature = "solana")]
fn cmd_multisign_sign(request_json: PathBuf, keypair_path: PathBuf, out: PathBuf) {
    use afterimage_solana::{MultiSignRequest, MultiSigner};

    let req_bytes = std::fs::read(&request_json).unwrap_or_else(|e| {
        eprintln!("error: cannot read {:?}: {e}", request_json);
        std::process::exit(1);
    });
    let req = MultiSignRequest::from_json(&req_bytes).unwrap_or_else(|e| {
        eprintln!("error: invalid request JSON: {e}");
        std::process::exit(1);
    });

    // Load keypair from Solana CLI JSON format ([u8; 64] array)
    let kp_bytes = load_keypair_json(&keypair_path);
    let signer = MultiSigner::from_bytes(&kp_bytes).unwrap_or_else(|e| {
        eprintln!("error: {e}");
        std::process::exit(1);
    });

    eprintln!(
        "[multisign] sign: round={}, signer={}",
        req.round,
        signer.pubkey()
    );
    eprintln!("  Description : {}", req.description);
    eprintln!("  Threshold   : {}/{}", req.threshold, req.signers.len());
    eprintln!("  Prior sigs  : {}", req.partial_sigs.len());

    let resp = signer.sign_multi_request(&req).unwrap_or_else(|e| {
        eprintln!("error: signing failed: {e}");
        std::process::exit(1);
    });

    let json = resp.to_json().unwrap_or_else(|e| {
        eprintln!("error: JSON serialise: {e}");
        std::process::exit(1);
    });
    std::fs::write(&out, &json).unwrap_or_else(|e| {
        eprintln!("error: write {:?}: {e}", out);
        std::process::exit(1);
    });

    if resp.complete {
        eprintln!("[multisign] ✓ threshold met ({}/{}) — session COMPLETE", resp.partial_sigs.len(), req.threshold);
        eprintln!("  Send {:?} to online machine for broadcast.", out);
    } else {
        eprintln!("[multisign] round {} signed ({}/{} sigs collected)", resp.round, resp.partial_sigs.len(), req.threshold);
        eprintln!("  Transmit {:?} to online machine, then run: afterimage multisign next", out);
    }
}

/// `afterimage multisign next` — online machine advances to the next round.
#[cfg(feature = "solana")]
fn cmd_multisign_next(response_json: PathBuf, original_req_path: PathBuf, out: PathBuf) {
    use afterimage_solana::{advance_round_from, MultiSignRequest, MultiSignResponse};

    let resp_bytes = std::fs::read(&response_json).unwrap_or_else(|e| {
        eprintln!("error: cannot read {:?}: {e}", response_json);
        std::process::exit(1);
    });
    let resp = MultiSignResponse::from_json(&resp_bytes).unwrap_or_else(|e| {
        eprintln!("error: invalid response JSON: {e}");
        std::process::exit(1);
    });

    if resp.complete {
        eprintln!("[multisign] session already complete — no more rounds needed.");
        eprintln!("  Broadcast the transaction from {:?}", response_json);
        return;
    }

    let req_bytes = std::fs::read(&original_req_path).unwrap_or_else(|e| {
        eprintln!("error: cannot read {:?}: {e}", original_req_path);
        std::process::exit(1);
    });
    let original_req = MultiSignRequest::from_json(&req_bytes).unwrap_or_else(|e| {
        eprintln!("error: invalid request JSON: {e}");
        std::process::exit(1);
    });

    let next_req = advance_round_from(&resp, &original_req).unwrap_or_else(|| {
        eprintln!("[multisign] advance_round_from returned None (session complete)");
        std::process::exit(0);
    });

    let json = next_req.to_json().unwrap_or_else(|e| {
        eprintln!("error: JSON serialise: {e}");
        std::process::exit(1);
    });
    std::fs::write(&out, &json).unwrap_or_else(|e| {
        eprintln!("error: write {:?}: {e}", out);
        std::process::exit(1);
    });

    eprintln!(
        "[multisign] next: round {} → {:?}",
        next_req.round, out
    );
    let next_signer = next_req.current_signer().unwrap_or("?");
    eprintln!("  Next signer : {next_signer}");
    eprintln!("  Transmit {:?} via: afterimage send {:?}", out, out);
}

/// Load a Solana CLI keypair JSON file (`[u8; 64]` array) → raw bytes.
#[cfg(feature = "solana")]
fn load_keypair_json(path: &PathBuf) -> Vec<u8> {
    let raw = std::fs::read_to_string(path).unwrap_or_else(|e| {
        eprintln!("error: cannot read keypair {:?}: {e}", path);
        std::process::exit(1);
    });
    let values: Vec<u8> = serde_json::from_str(&raw).unwrap_or_else(|e| {
        eprintln!("error: invalid keypair JSON {:?}: {e}", path);
        std::process::exit(1);
    });
    values
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

fn resolve_password(opt: Option<String>, prompt: &str) -> String {
    if let Some(p) = opt {
        return p;
    }
    rpassword::prompt_password(prompt).unwrap_or_else(|e| {
        eprintln!("error reading password: {e}");
        std::process::exit(1);
    })
}