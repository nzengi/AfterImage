//! AirSign M-of-N multi-signature request envelope.
//!
//! The online machine builds a [`MultiSignRequest`] for each signing round.
//! Round 1 carries no prior signatures.  Each subsequent round includes the
//! `PartialSig` entries collected from previous signers, so any air-gapped
//! machine receiving the request can verify the chain of partial signatures
//! before adding its own.
//!
//! ## Wire format (JSON, AirSign envelope v2)
//!
//! ```json
//! {
//!   "version": 2,
//!   "nonce": "<hex-32-bytes>",
//!   "threshold": 2,
//!   "signers": ["<base58>", "<base58>", "<base58>"],
//!   "round": 1,
//!   "partial_sigs": [],
//!   "transaction_b64": "<base64-bincode>",
//!   "description": "Treasury withdrawal — 10 SOL",
//!   "created_at": 1714000000,
//!   "cluster": "mainnet-beta"
//! }
//! ```

use serde::{Deserialize, Serialize};

/// A single partial Ed25519 signature collected from one signer during a
/// multi-signature round.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PartialSig {
    /// Base58-encoded public key of the signer who produced this signature.
    pub signer_pubkey: String,

    /// Base64-encoded 64-byte Ed25519 signature over the *original* (unsigned)
    /// transaction message bytes.
    pub signature_b64: String,
}

/// An M-of-N multi-signature request (AirSign envelope v2).
///
/// The online machine creates one of these for each signing round and
/// transmits it as an encrypted AfterImage QR stream.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MultiSignRequest {
    /// AirSign envelope version — always `2` for multi-sig requests.
    pub version: u8,

    /// Random 32-byte nonce (hex-encoded) shared across all rounds of the
    /// same multi-sig session.  Prevents replay attacks.
    pub nonce: String,

    /// Minimum number of signatures required to authorise the transaction
    /// (`M` in M-of-N).
    pub threshold: u8,

    /// Ordered list of all `N` expected signer public keys (base58-encoded).
    /// The `round`-th signer (1-indexed) in this list is the intended
    /// recipient of the current request.
    pub signers: Vec<String>,

    /// Current signing round, 1-indexed.
    /// Round 1 goes to `signers[0]`, round 2 to `signers[1]`, etc.
    pub round: u8,

    /// Partial signatures gathered from previous rounds (rounds 1..(round-1)).
    /// Empty for round 1.
    pub partial_sigs: Vec<PartialSig>,

    /// Bincode-serialised `solana_sdk::transaction::Transaction` (unsigned).
    /// Base64-encoded for JSON transport.  The same bytes are used in every
    /// round — signers sign the *message* bytes, not a modified transaction.
    pub transaction_b64: String,

    /// Human-readable description shown on the air-gapped machine's screen.
    pub description: String,

    /// Unix timestamp (seconds) when this session was initiated.
    pub created_at: i64,

    /// Optional cluster hint (e.g. "mainnet-beta", "devnet").
    #[serde(default)]
    pub cluster: String,
}

impl MultiSignRequest {
    /// Deserialise from JSON bytes.
    pub fn from_json(bytes: &[u8]) -> Result<Self, serde_json::Error> {
        serde_json::from_slice(bytes)
    }

    /// Serialise to compact JSON bytes.
    pub fn to_json(&self) -> Result<Vec<u8>, serde_json::Error> {
        serde_json::to_vec(self)
    }

    /// Decode the embedded (unsigned) transaction.
    pub fn decode_transaction(
        &self,
    ) -> Result<solana_sdk::transaction::Transaction, Box<dyn std::error::Error>> {
        use base64::{engine::general_purpose::STANDARD, Engine};
        let raw = STANDARD.decode(&self.transaction_b64)?;
        let tx: solana_sdk::transaction::Transaction = bincode::deserialize(&raw)?;
        Ok(tx)
    }

    /// The public key of the signer expected in the current round (0-indexed
    /// into `signers` by `round - 1`).
    ///
    /// Returns `None` if `round` is out of range.
    pub fn current_signer(&self) -> Option<&str> {
        let idx = self.round.checked_sub(1)? as usize;
        self.signers.get(idx).map(String::as_str)
    }

    /// Returns `true` if `partial_sigs` already contains a signature from
    /// `pubkey`.
    pub fn has_signed(&self, pubkey: &str) -> bool {
        self.partial_sigs.iter().any(|p| p.signer_pubkey == pubkey)
    }

    /// Returns `true` if the number of collected partial signatures has
    /// reached the threshold.
    pub fn threshold_met(&self) -> bool {
        self.partial_sigs.len() >= self.threshold as usize
    }

    /// Total number of expected signers (`N`).
    pub fn n(&self) -> usize {
        self.signers.len()
    }
}

// ─── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_request(threshold: u8, n: usize, round: u8) -> MultiSignRequest {
        MultiSignRequest {
            version: 2,
            nonce: "aabbccdd".to_owned(),
            threshold,
            signers: (0..n).map(|i| format!("PUBKEY{i}")).collect(),
            round,
            partial_sigs: vec![],
            transaction_b64: "AAAA".to_owned(),
            description: "test".to_owned(),
            created_at: 0,
            cluster: "devnet".to_owned(),
        }
    }

    #[test]
    fn current_signer_round1() {
        let req = make_request(2, 3, 1);
        assert_eq!(req.current_signer(), Some("PUBKEY0"));
    }

    #[test]
    fn current_signer_round3() {
        let req = make_request(2, 3, 3);
        assert_eq!(req.current_signer(), Some("PUBKEY2"));
    }

    #[test]
    fn current_signer_out_of_range() {
        let req = make_request(2, 3, 4);
        assert_eq!(req.current_signer(), None);
    }

    #[test]
    fn threshold_met_false_initially() {
        let req = make_request(2, 3, 1);
        assert!(!req.threshold_met());
    }

    #[test]
    fn threshold_met_after_enough_sigs() {
        let mut req = make_request(2, 3, 3);
        req.partial_sigs.push(PartialSig {
            signer_pubkey: "A".to_owned(),
            signature_b64: "sig_a".to_owned(),
        });
        req.partial_sigs.push(PartialSig {
            signer_pubkey: "B".to_owned(),
            signature_b64: "sig_b".to_owned(),
        });
        assert!(req.threshold_met());
    }

    #[test]
    fn has_signed_detects_duplicate() {
        let mut req = make_request(2, 3, 2);
        req.partial_sigs.push(PartialSig {
            signer_pubkey: "PUBKEY0".to_owned(),
            signature_b64: "sig".to_owned(),
        });
        assert!(req.has_signed("PUBKEY0"));
        assert!(!req.has_signed("PUBKEY1"));
    }

    #[test]
    fn json_roundtrip() {
        let req = make_request(2, 3, 1);
        let json = req.to_json().unwrap();
        let decoded = MultiSignRequest::from_json(&json).unwrap();
        assert_eq!(decoded.threshold, 2);
        assert_eq!(decoded.signers.len(), 3);
        assert_eq!(decoded.round, 1);
    }
}