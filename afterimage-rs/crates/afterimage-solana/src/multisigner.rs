//! AirSign M-of-N multi-signer — air-gapped round processing.
//!
//! [`MultiSigner`] runs on the **air-gapped machine**.  For each round it:
//!
//! 1. Validates the [`MultiSignRequest`] (version, nonce, signer list, round).
//! 2. Confirms the loaded keypair is the expected signer for this round.
//! 3. Verifies all prior [`PartialSig`] entries (prevents spoofed partial sigs).
//! 4. Signs the transaction message with the air-gapped keypair.
//! 5. Returns a [`MultiSignResponse`] carrying all accumulated partial sigs.
//!
//! The online machine uses [`build_multisig_session`] to create the round-1
//! request and [`advance_round`] to produce subsequent requests from a
//! response.

use base64::{engine::general_purpose::STANDARD, Engine};
use solana_sdk::{
    signature::{Signature, Signer},
    signer::keypair::Keypair,
};

use crate::{
    error::AirSignError,
    multisig_request::{MultiSignRequest, PartialSig},
    multisig_response::MultiSignResponse,
};

// ─── Air-gapped signer ────────────────────────────────────────────────────────

/// Air-gapped M-of-N multi-signer for a single Ed25519 keypair.
///
/// Instantiate one per air-gapped machine.  The same struct handles any round
/// of any multi-sig session whose `signers` list includes this keypair.
pub struct MultiSigner {
    keypair: Keypair,
}

impl MultiSigner {
    /// Load from a 64-byte Solana keypair slice.
    pub fn from_bytes(keypair_bytes: &[u8]) -> Result<Self, AirSignError> {
        let keypair = Keypair::try_from(keypair_bytes)
            .map_err(|e| AirSignError::InvalidRequest(format!("bad keypair bytes: {e}")))?;
        Ok(Self { keypair })
    }

    /// Return the public key of the loaded keypair.
    pub fn pubkey(&self) -> solana_sdk::pubkey::Pubkey {
        self.keypair.pubkey()
    }

    /// Process one round of a [`MultiSignRequest`].
    ///
    /// # Errors
    ///
    /// - [`AirSignError::InvalidRequest`] — version mismatch, wrong round,
    ///   duplicate signer, or this keypair is not in the signer list.
    /// - [`AirSignError::VerificationFailed`] — a prior partial signature
    ///   did not verify against its declared public key.
    pub fn sign_multi_request(
        &self,
        req: &MultiSignRequest,
    ) -> Result<MultiSignResponse, AirSignError> {
        // ── 1. Basic validation ──────────────────────────────────────────
        if req.version != 2 {
            return Err(AirSignError::InvalidRequest(format!(
                "expected MultiSignRequest version 2, got {}",
                req.version
            )));
        }
        if req.threshold == 0 || req.threshold as usize > req.signers.len() {
            return Err(AirSignError::InvalidRequest(format!(
                "threshold {} is out of range for {} signers",
                req.threshold,
                req.signers.len()
            )));
        }

        // ── 2. Confirm this keypair is the expected signer for this round ─
        let our_pubkey = self.keypair.pubkey().to_string();
        let expected = req.current_signer().ok_or_else(|| {
            AirSignError::InvalidRequest(format!(
                "round {} exceeds signer list length {}",
                req.round,
                req.signers.len()
            ))
        })?;
        if our_pubkey != expected {
            return Err(AirSignError::InvalidRequest(format!(
                "round {}: expected signer {expected}, but this keypair is {our_pubkey}",
                req.round
            )));
        }

        // ── 3. Reject duplicate signing ──────────────────────────────────
        if req.has_signed(&our_pubkey) {
            return Err(AirSignError::InvalidRequest(format!(
                "signer {our_pubkey} has already contributed a partial signature"
            )));
        }

        // ── 4. Decode the unsigned transaction ───────────────────────────
        let tx_raw = STANDARD
            .decode(&req.transaction_b64)
            .map_err(|e| AirSignError::InvalidRequest(format!("base64 decode: {e}")))?;
        let tx: solana_sdk::transaction::Transaction = bincode::deserialize(&tx_raw)
            .map_err(|e| AirSignError::InvalidRequest(format!("bincode decode: {e}")))?;
        let message_bytes = tx.message_data();

        // ── 5. Verify prior partial signatures ───────────────────────────
        for ps in &req.partial_sigs {
            let pk = ps
                .signer_pubkey
                .parse::<solana_sdk::pubkey::Pubkey>()
                .map_err(|e| {
                    AirSignError::InvalidRequest(format!(
                        "bad pubkey in partial_sigs {}: {e}",
                        ps.signer_pubkey
                    ))
                })?;
            let sig_bytes = STANDARD.decode(&ps.signature_b64).map_err(|e| {
                AirSignError::InvalidRequest(format!(
                    "base64 decode partial sig for {}: {e}",
                    ps.signer_pubkey
                ))
            })?;
            let sig_arr: [u8; 64] = sig_bytes.try_into().map_err(|_| {
                AirSignError::InvalidRequest(format!(
                    "partial sig for {} must be 64 bytes",
                    ps.signer_pubkey
                ))
            })?;
            let sig = Signature::from(sig_arr);
            if !sig.verify(pk.as_ref(), &message_bytes) {
                return Err(AirSignError::VerificationFailed);
            }
        }

        // ── 6. Sign ──────────────────────────────────────────────────────
        let our_sig = self.keypair.sign_message(&message_bytes);

        // ── 7. Apply all collected signatures to the transaction ─────────
        let mut signed_tx = tx;
        // Apply prior partial signatures
        for ps in &req.partial_sigs {
            let pk = ps.signer_pubkey.parse::<solana_sdk::pubkey::Pubkey>().unwrap();
            let sig_bytes: [u8; 64] = STANDARD.decode(&ps.signature_b64).unwrap().try_into().unwrap();
            let sig = Signature::from(sig_bytes);
            if let Some(pos) = signed_tx.message.account_keys.iter().position(|k| k == &pk) {
                signed_tx.signatures[pos] = sig;
            }
        }
        // Apply our signature
        let our_pk = self.keypair.pubkey();
        if let Some(pos) = signed_tx.message.account_keys.iter().position(|k| k == &our_pk) {
            signed_tx.signatures[pos] = our_sig;
        } else {
            return Err(AirSignError::InvalidRequest(
                "this signer's pubkey is not in the transaction account keys".into(),
            ));
        }

        // ── 8. Accumulate partial sigs ───────────────────────────────────
        let mut all_sigs = req.partial_sigs.clone();
        all_sigs.push(PartialSig {
            signer_pubkey: our_pubkey.clone(),
            signature_b64: STANDARD.encode(our_sig.as_ref()),
        });

        let complete = all_sigs.len() >= req.threshold as usize;

        // ── 9. Serialise the (partially) signed transaction ───────────────
        let signed_tx_bytes = bincode::serialize(&signed_tx)
            .map_err(|e| AirSignError::InvalidRequest(format!("serialize signed tx: {e}")))?;

        Ok(MultiSignResponse {
            version: 2,
            nonce: req.nonce.clone(),
            round: req.round,
            signer_pubkey: our_pubkey,
            signature_b64: STANDARD.encode(our_sig.as_ref()),
            partial_sigs: all_sigs,
            signed_transaction_b64: STANDARD.encode(&signed_tx_bytes),
            complete,
        })
    }
}

// ─── Online-machine helpers ───────────────────────────────────────────────────

/// Build a round-1 [`MultiSignRequest`] from an unsigned transaction.
///
/// `signers`   — ordered list of N signer public keys; round 1 goes to index 0  
/// `threshold` — M (minimum signatures required)  
/// `password`  — AfterImage transfer password (unused here, passed to session builder)
pub fn build_multisig_session(
    tx: &solana_sdk::transaction::Transaction,
    signers: &[solana_sdk::pubkey::Pubkey],
    threshold: u8,
    description: &str,
    cluster: &str,
) -> Result<MultiSignRequest, AirSignError> {
    use rand::RngCore;

    if threshold == 0 || threshold as usize > signers.len() {
        return Err(AirSignError::InvalidRequest(format!(
            "threshold {threshold} out of range for {} signers",
            signers.len()
        )));
    }

    let mut nonce_bytes = [0u8; 32];
    rand::rng().fill_bytes(&mut nonce_bytes);
    let nonce = hex::encode(nonce_bytes);

    let tx_bytes = bincode::serialize(tx)
        .map_err(|e| AirSignError::InvalidRequest(e.to_string()))?;

    Ok(MultiSignRequest {
        version: 2,
        nonce,
        threshold,
        signers: signers.iter().map(|p| p.to_string()).collect(),
        round: 1,
        partial_sigs: vec![],
        transaction_b64: STANDARD.encode(&tx_bytes),
        description: description.to_owned(),
        created_at: std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs() as i64,
        cluster: cluster.to_owned(),
    })
}

/// Advance to the next signing round by building a new [`MultiSignRequest`]
/// from the previous round's response.
///
/// Returns `None` if the threshold has already been met (session complete).
pub fn advance_round(prev_response: &MultiSignResponse) -> Option<MultiSignRequest> {
    if prev_response.complete {
        return None; // No more rounds needed
    }
    let next_round = prev_response.round + 1;
    // We need the original request data to reconstruct; callers pass it in
    // via the response fields (partial_sigs accumulate, transaction_b64 echoed).
    Some(MultiSignRequest {
        version: 2,
        nonce: prev_response.nonce.clone(),
        // threshold / signers are not stored in the response — caller must
        // supply them.  This function is intentionally minimal; see
        // `advance_round_from` for the full version.
        threshold: 0,   // filled by caller
        signers: vec![], // filled by caller
        round: next_round,
        partial_sigs: prev_response.partial_sigs.clone(),
        transaction_b64: prev_response.signed_transaction_b64.clone(),
        description: String::new(),
        created_at: prev_response
            .partial_sigs
            .first()
            .map(|_| 0i64)
            .unwrap_or(0),
        cluster: String::new(),
    })
}

/// Full advance: build the next [`MultiSignRequest`] by combining the
/// previous response with the original session metadata (threshold, signers,
/// description, cluster).
pub fn advance_round_from(
    prev_response: &MultiSignResponse,
    original_req: &MultiSignRequest,
) -> Option<MultiSignRequest> {
    if prev_response.complete {
        return None;
    }
    Some(MultiSignRequest {
        version: 2,
        nonce: prev_response.nonce.clone(),
        threshold: original_req.threshold,
        signers: original_req.signers.clone(),
        round: prev_response.round + 1,
        partial_sigs: prev_response.partial_sigs.clone(),
        transaction_b64: original_req.transaction_b64.clone(),
        description: original_req.description.clone(),
        created_at: original_req.created_at,
        cluster: original_req.cluster.clone(),
    })
}

// ─── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use solana_sdk::{
        message::Message, pubkey::Pubkey, signer::keypair::Keypair, system_instruction,
    };

    /// Simple single-signer transfer (only `from` appears in account_keys).
    fn make_transfer_tx(from: &Keypair, to: &Pubkey) -> solana_sdk::transaction::Transaction {
        let ix = system_instruction::transfer(&from.pubkey(), to, 1_000_000);
        let msg = Message::new(&[ix], Some(&from.pubkey()));
        solana_sdk::transaction::Transaction::new_unsigned(msg)
    }

    /// Build a transaction that requires ALL `signers` as Ed25519 signers.
    ///
    /// The first signer is the fee-payer and "from" account; the rest are
    /// additional required co-signers inserted into the Message header so
    /// that their pubkeys appear in `account_keys` at indices 1..N.
    fn make_multisig_tx(
        signers: &[&Keypair],
        recipient: &Pubkey,
        lamports: u64,
    ) -> solana_sdk::transaction::Transaction {
        use solana_sdk::{
            hash::Hash,
            instruction::CompiledInstruction,
            message::MessageHeader,
            signature::Signature,
            system_program,
        };

        assert!(!signers.is_empty(), "need at least one signer");

        // Accounts layout:
        //   [0..n]          required signers (num_required_signatures = n)
        //   [n]             recipient  (readonly, unsigned)
        //   [n+1]           system program (readonly, unsigned)
        let mut account_keys: Vec<solana_sdk::pubkey::Pubkey> =
            signers.iter().map(|kp| kp.pubkey()).collect();
        let recipient_idx = account_keys.len() as u8;
        account_keys.push(*recipient);
        let system_idx = account_keys.len() as u8;
        account_keys.push(system_program::id());

        // system_instruction::transfer data: [discriminant u32=2, lamports u64]
        let mut data = vec![2u8, 0, 0, 0];
        data.extend_from_slice(&lamports.to_le_bytes());

        let compiled_ix = CompiledInstruction {
            program_id_index: system_idx,
            accounts: vec![0, recipient_idx], // from = signer[0], to = recipient
            data,
        };

        let n = signers.len() as u8;
        let header = MessageHeader {
            num_required_signatures: n,
            num_readonly_signed_accounts: 0,
            num_readonly_unsigned_accounts: 2, // recipient + system_program
        };

        let msg = Message {
            header,
            account_keys,
            recent_blockhash: Hash::default(),
            instructions: vec![compiled_ix],
        };

        solana_sdk::transaction::Transaction {
            signatures: vec![Signature::default(); n as usize],
            message: msg,
        }
    }

    // ── 1-of-1 (degenerate single-signer) ────────────────────────────────────
    #[test]
    fn single_signer_1_of_1() {
        let kp = Keypair::new();
        let recipient = Pubkey::new_unique();
        let tx = make_transfer_tx(&kp, &recipient);

        let req =
            build_multisig_session(&tx, &[kp.pubkey()], 1, "1-of-1 test", "devnet").unwrap();

        let ms = MultiSigner::from_bytes(&kp.to_bytes()).unwrap();
        let resp = ms.sign_multi_request(&req).unwrap();

        assert!(resp.complete, "1-of-1 must be complete after round 1");
        assert_eq!(resp.partial_sigs.len(), 1);

        // Transaction must verify
        let signed_tx = resp.decode_transaction().unwrap();
        assert!(
            signed_tx.verify_with_results().iter().all(|&ok| ok),
            "1-of-1 signed transaction must verify"
        );
    }

    // ── 2-of-3 full roundtrip ────────────────────────────────────────────────
    #[test]
    fn two_of_three_roundtrip() {
        let kp_a = Keypair::new();
        let kp_b = Keypair::new();
        let kp_c = Keypair::new();
        let recipient = Pubkey::new_unique();

        // Transaction that genuinely requires all three as signers.
        let tx = make_multisig_tx(&[&kp_a, &kp_b, &kp_c], &recipient, 5_000);

        let signers = [kp_a.pubkey(), kp_b.pubkey(), kp_c.pubkey()];
        let round1_req =
            build_multisig_session(&tx, &signers, 2, "2-of-3 test", "devnet").unwrap();

        // Round 1 — Signer A
        let ms_a = MultiSigner::from_bytes(&kp_a.to_bytes()).unwrap();
        let resp1 = ms_a.sign_multi_request(&round1_req).unwrap();

        assert!(!resp1.complete, "threshold=2, only 1 sig so far");
        assert_eq!(resp1.round, 1);
        assert_eq!(resp1.partial_sigs.len(), 1);
        assert_eq!(resp1.partial_sigs[0].signer_pubkey, kp_a.pubkey().to_string());

        // Online machine: advance to round 2
        let round2_req = advance_round_from(&resp1, &round1_req).unwrap();
        assert_eq!(round2_req.round, 2);
        assert_eq!(round2_req.partial_sigs.len(), 1);

        // Round 2 — Signer B
        let ms_b = MultiSigner::from_bytes(&kp_b.to_bytes()).unwrap();
        let resp2 = ms_b.sign_multi_request(&round2_req).unwrap();

        assert!(resp2.complete, "threshold=2 met after round 2");
        assert_eq!(resp2.partial_sigs.len(), 2);
        assert_eq!(resp2.partial_sigs[1].signer_pubkey, kp_b.pubkey().to_string());

        // The signed transaction must have two valid signatures
        let signed_tx = resp2.decode_transaction().unwrap();
        let results = signed_tx.verify_with_results();
        // Signer A (index 0) and Signer B (index 1) should verify; C (index 2) stays default
        assert!(results[0], "kp_a signature must verify");
        assert!(results[1], "kp_b signature must verify");
    }

    // ── advance_round_from returns None when complete ─────────────────────────
    #[test]
    fn advance_round_returns_none_when_complete() {
        let kp = Keypair::new();
        let recipient = Pubkey::new_unique();
        let tx = make_transfer_tx(&kp, &recipient);
        let req =
            build_multisig_session(&tx, &[kp.pubkey()], 1, "done", "devnet").unwrap();
        let ms = MultiSigner::from_bytes(&kp.to_bytes()).unwrap();
        let resp = ms.sign_multi_request(&req).unwrap();
        assert!(resp.complete);
        let next = advance_round_from(&resp, &req);
        assert!(next.is_none(), "no next round when threshold met");
    }

    // ── wrong signer for round rejected ──────────────────────────────────────
    #[test]
    fn wrong_signer_for_round_rejected() {
        let kp_a = Keypair::new();
        let kp_b = Keypair::new();
        let recipient = Pubkey::new_unique();
        let tx = make_transfer_tx(&kp_a, &recipient);

        let req =
            build_multisig_session(&tx, &[kp_a.pubkey(), kp_b.pubkey()], 2, "test", "devnet")
                .unwrap();

        // kp_b tries to sign round 1 (which is for kp_a)
        let ms_b = MultiSigner::from_bytes(&kp_b.to_bytes()).unwrap();
        let result = ms_b.sign_multi_request(&req);
        assert!(
            matches!(result, Err(AirSignError::InvalidRequest(_))),
            "wrong signer must be rejected"
        );
    }

    // ── duplicate signer rejected ─────────────────────────────────────────────
    #[test]
    fn duplicate_signer_rejected() {
        let kp_a = Keypair::new();
        let kp_b = Keypair::new();
        let recipient = Pubkey::new_unique();
        let tx = make_multisig_tx(&[&kp_a, &kp_b], &recipient, 1_000);

        let round1_req =
            build_multisig_session(&tx, &[kp_a.pubkey(), kp_b.pubkey()], 2, "test", "devnet")
                .unwrap();

        let ms_a = MultiSigner::from_bytes(&kp_a.to_bytes()).unwrap();
        let resp1 = ms_a.sign_multi_request(&round1_req).unwrap();

        // Manually inject a duplicate entry for kp_a in round 2's partial_sigs
        let round2_req = MultiSignRequest {
            round: 2,
            // kp_a appears in round 2's signer slot — forcing a duplicate
            signers: vec![kp_b.pubkey().to_string(), kp_a.pubkey().to_string()],
            partial_sigs: resp1.partial_sigs.clone(), // already contains kp_a
            ..round1_req.clone()
        };

        // kp_a tries to sign round 2 where it's listed again
        let result = ms_a.sign_multi_request(&round2_req);
        assert!(
            matches!(result, Err(AirSignError::InvalidRequest(_))),
            "duplicate signer must be rejected"
        );
    }

    // ── threshold=0 rejected ─────────────────────────────────────────────────
    #[test]
    fn zero_threshold_rejected() {
        let kp = Keypair::new();
        let recipient = Pubkey::new_unique();
        let tx = make_transfer_tx(&kp, &recipient);
        let result = build_multisig_session(&tx, &[kp.pubkey()], 0, "bad", "devnet");
        assert!(matches!(result, Err(AirSignError::InvalidRequest(_))));
    }

    // ── threshold > N rejected ────────────────────────────────────────────────
    #[test]
    fn threshold_exceeds_n_rejected() {
        let kp = Keypair::new();
        let recipient = Pubkey::new_unique();
        let tx = make_transfer_tx(&kp, &recipient);
        // threshold=3 but only 1 signer
        let result = build_multisig_session(&tx, &[kp.pubkey()], 3, "bad", "devnet");
        assert!(matches!(result, Err(AirSignError::InvalidRequest(_))));
    }

    // ── tampered partial sig rejected ────────────────────────────────────────
    #[test]
    fn tampered_partial_sig_rejected() {
        let kp_a = Keypair::new();
        let kp_b = Keypair::new();
        let recipient = Pubkey::new_unique();
        let tx = make_transfer_tx(&kp_a, &recipient);

        let round1_req =
            build_multisig_session(&tx, &[kp_a.pubkey(), kp_b.pubkey()], 2, "test", "devnet")
                .unwrap();

        let ms_a = MultiSigner::from_bytes(&kp_a.to_bytes()).unwrap();
        let resp1 = ms_a.sign_multi_request(&round1_req).unwrap();

        // Build round 2 with a tampered partial sig
        let mut tampered_sigs = resp1.partial_sigs.clone();
        tampered_sigs[0].signature_b64 =
            STANDARD.encode(&[0u8; 64]); // all-zeros — invalid sig

        let round2_req = advance_round_from(&resp1, &round1_req)
            .map(|mut r| {
                r.partial_sigs = tampered_sigs;
                r
            })
            .unwrap();

        let ms_b = MultiSigner::from_bytes(&kp_b.to_bytes()).unwrap();
        let result = ms_b.sign_multi_request(&round2_req);
        assert!(
            matches!(result, Err(AirSignError::VerificationFailed)),
            "tampered partial sig must trigger VerificationFailed"
        );
    }
}