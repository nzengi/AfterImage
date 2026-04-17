//! AirSign M-of-N multi-signature response envelope.
//!
//! After an air-gapped signer processes a [`MultiSignRequest`] it produces a
//! [`MultiSignResponse`].  The online machine inspects `complete` to decide
//! whether to advance to the next round or broadcast the final transaction.
//!
//! ## Wire format (JSON, AirSign envelope v2)
//!
//! ```json
//! {
//!   "version": 2,
//!   "nonce": "<hex-32-bytes>",
//!   "round": 1,
//!   "signer_pubkey": "<base58>",
//!   "signature_b64": "<base64-64-bytes>",
//!   "partial_sigs": [
//!     { "signer_pubkey": "<base58>", "signature_b64": "<base64>" }
//!   ],
//!   "signed_transaction_b64": "<base64-bincode>",
//!   "complete": false
//! }
//! ```

use serde::{Deserialize, Serialize};

use crate::multisig_request::PartialSig;

/// The response returned by one air-gapped signer during a multi-signature
/// round (AirSign envelope v2).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MultiSignResponse {
    /// AirSign envelope version — always `2` for multi-sig responses.
    pub version: u8,

    /// Echo of the session nonce from the corresponding [`MultiSignRequest`].
    pub nonce: String,

    /// The signing round this response belongs to (1-indexed).
    pub round: u8,

    /// Base58-encoded public key of the signer who produced this response.
    pub signer_pubkey: String,

    /// Base64-encoded 64-byte Ed25519 signature added in this round.
    pub signature_b64: String,

    /// All partial signatures collected so far, including the one just added.
    /// The online machine uses this as `partial_sigs` when building the next
    /// round's [`MultiSignRequest`].
    pub partial_sigs: Vec<PartialSig>,

    /// Bincode-serialised, fully (or partially) signed transaction,
    /// base64-encoded.  When `complete == true` the online machine can submit
    /// this directly to the cluster.
    pub signed_transaction_b64: String,

    /// `true` when `partial_sigs.len() >= threshold` — the transaction has
    /// enough signatures and can be broadcast immediately.
    pub complete: bool,
}

impl MultiSignResponse {
    /// Deserialise from JSON bytes.
    pub fn from_json(bytes: &[u8]) -> Result<Self, serde_json::Error> {
        serde_json::from_slice(bytes)
    }

    /// Serialise to compact JSON bytes.
    pub fn to_json(&self) -> Result<Vec<u8>, serde_json::Error> {
        serde_json::to_vec(self)
    }

    /// Decode the 64-byte Ed25519 signature added in this round.
    pub fn decode_signature(&self) -> Result<[u8; 64], Box<dyn std::error::Error>> {
        use base64::{engine::general_purpose::STANDARD, Engine};
        let raw = STANDARD.decode(&self.signature_b64)?;
        let arr: [u8; 64] = raw.try_into().map_err(|_| "signature must be 64 bytes")?;
        Ok(arr)
    }

    /// Decode the (partially or fully) signed transaction.
    pub fn decode_transaction(
        &self,
    ) -> Result<solana_sdk::transaction::Transaction, Box<dyn std::error::Error>> {
        use base64::{engine::general_purpose::STANDARD, Engine};
        let raw = STANDARD.decode(&self.signed_transaction_b64)?;
        let tx: solana_sdk::transaction::Transaction = bincode::deserialize(&raw)?;
        Ok(tx)
    }
}

// ─── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_response(round: u8, complete: bool) -> MultiSignResponse {
        MultiSignResponse {
            version: 2,
            nonce: "deadbeef".to_owned(),
            round,
            signer_pubkey: "PUBKEY0".to_owned(),
            signature_b64: "c2lnbmF0dXJlYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYQ==".to_owned(),
            partial_sigs: vec![PartialSig {
                signer_pubkey: "PUBKEY0".to_owned(),
                signature_b64: "c2ln".to_owned(),
            }],
            signed_transaction_b64: "AAAA".to_owned(),
            complete,
        }
    }

    #[test]
    fn json_roundtrip_incomplete() {
        let resp = make_response(1, false);
        let json = resp.to_json().unwrap();
        let decoded = MultiSignResponse::from_json(&json).unwrap();
        assert_eq!(decoded.round, 1);
        assert!(!decoded.complete);
        assert_eq!(decoded.partial_sigs.len(), 1);
    }

    #[test]
    fn json_roundtrip_complete() {
        let resp = make_response(2, true);
        let json = resp.to_json().unwrap();
        let decoded = MultiSignResponse::from_json(&json).unwrap();
        assert!(decoded.complete);
    }
}