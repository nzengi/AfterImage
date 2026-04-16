"""
afterimage.fountain
===================
LT Fountain Code encoder and decoder using the Robust Soliton Distribution.

Performance improvements over the original monolithic script
------------------------------------------------------------
* ``RobustSoliton`` is instantiated once per encoder/decoder and cached.
* ``LTDecoder._propagate`` uses an inverted index (block → droplet list) so
  propagation is O(degree) per newly decoded block instead of O(n·droplets).
* ``np.frombuffer`` copies are taken only when mutation is needed.
* Droplet neighbour sets are stored as Python ``set`` objects for O(1) discard.

Wire format per droplet
-----------------------
``seed (4 B, uint32 BE) || degree (2 B, uint16 BE) || reserved (2 B) || data (BLOCK_SIZE B)``

The *degree* field in the header is informational only (kept for debugging).
The decoder always re-derives the actual neighbours deterministically from the
seed, eliminating the original bug where the header degree and the recomputed
degree could diverge.
"""

from __future__ import annotations

import struct
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

import numpy as np

__all__ = [
    "BLOCK_SIZE",
    "HEADER_SIZE",
    "OVERHEAD_FACTOR",
    "RobustSoliton",
    "Droplet",
    "LTEncoder",
    "LTDecoder",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BLOCK_SIZE: int = 256       # bytes per source block
HEADER_SIZE: int = 8        # 4 B seed + 2 B degree + 2 B reserved
OVERHEAD_FACTOR: float = 1.05   # 5 % above k for recommended droplet count

# Robust Soliton parameters — tuned for moderate loss rates (~40 %)
C_PARAM: float = 0.1
DELTA_PARAM: float = 0.5


# ---------------------------------------------------------------------------
# Robust Soliton Distribution
# ---------------------------------------------------------------------------

class RobustSoliton:
    """
    Precomputed Robust Soliton Distribution for LT codes.

    Parameters
    ----------
    k:
        Number of source blocks.
    c:
        Tuning constant (affects ripple size). Default: 0.1
    delta:
        Failure probability bound. Default: 0.5
    """

    __slots__ = ("k", "c", "delta", "cdf")

    def __init__(self, k: int, c: float = C_PARAM, delta: float = DELTA_PARAM) -> None:
        if k < 1:
            raise ValueError("k must be >= 1")
        self.k = k
        self.c = c
        self.delta = delta
        self.cdf = self._build_cdf()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_cdf(self) -> np.ndarray:
        k = self.k
        R = self.c * np.log(k / self.delta) * np.sqrt(k)

        # Ideal Soliton
        rho = np.zeros(k + 1, dtype=np.float64)
        rho[1] = 1.0 / k
        for d in range(2, k + 1):
            rho[d] = 1.0 / (d * (d - 1))

        # Tau (ripple boost) component
        tau = np.zeros(k + 1, dtype=np.float64)
        threshold = max(1, int(k / R)) if R > 0 else k
        threshold = min(threshold, k)
        for d in range(1, threshold + 1):
            tau[d] = R / (d * k)
        if 0 < threshold <= k:
            tau[threshold] += R * np.log(R / self.delta) / k

        mu = rho + tau
        total = mu.sum()
        if total <= 0:
            mu[1] = 1.0
            total = 1.0
        mu /= total
        return np.cumsum(mu)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sample(self, rng: np.random.Generator) -> int:
        """Draw a degree value from the distribution."""
        u = rng.random()
        degree = int(np.searchsorted(self.cdf, u))
        return max(1, min(degree, self.k))

    def neighbours(self, seed: int) -> Set[int]:
        """
        Deterministically derive the neighbour set for a given *seed*.

        This is the canonical method used by both encoder and decoder so that
        the header *degree* field is never trusted for reconstruction.
        """
        rng = np.random.default_rng(seed)
        degree = self.sample(rng)
        return set(int(i) for i in rng.choice(self.k, size=degree, replace=False))


# ---------------------------------------------------------------------------
# Droplet dataclass
# ---------------------------------------------------------------------------

@dataclass
class Droplet:
    """A single fountain-coded packet, partially XOR-reduced."""
    seed: int
    data: bytearray                    # mutable buffer; XOR-reduced in place
    neighbors: Set[int] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------

class LTEncoder:
    """
    Rateless LT Fountain Code encoder.

    Parameters
    ----------
    data:
        Raw plaintext (or ciphertext) bytes to encode.
    block_size:
        Size of each source block in bytes.
    """

    def __init__(self, data: bytes, block_size: int = BLOCK_SIZE) -> None:
        self.block_size = block_size
        self.blocks: np.ndarray = self._split(data)
        self.k: int = len(self.blocks)
        self._dist = RobustSoliton(self.k)
        self._counter: int = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _split(self, data: bytes) -> np.ndarray:
        """Prepend length prefix, pad to block boundary, reshape."""
        header = struct.pack(">I", len(data))
        padded = header + data
        remainder = len(padded) % self.block_size
        if remainder:
            padded += b"\x00" * (self.block_size - remainder)
        arr = np.frombuffer(padded, dtype=np.uint8)
        return arr.reshape(-1, self.block_size).copy()   # writeable copy

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_droplet(self) -> bytes:
        """
        Generate the next encoded droplet.

        Returns
        -------
        bytes
            ``HEADER_SIZE + block_size`` bytes ready to embed in a QR code.
        """
        seed = self._counter
        self._counter += 1

        neighbors = self._dist.neighbours(seed)
        encoded = np.zeros(self.block_size, dtype=np.uint8)
        for idx in neighbors:
            encoded ^= self.blocks[idx]

        # degree in header = len(neighbors); purely informational
        header = struct.pack(">IHH", seed, len(neighbors), 0)
        return header + encoded.tobytes()

    def recommended_count(self) -> int:
        """Minimum recommended droplets for reliable decoding."""
        return int(self.k * OVERHEAD_FACTOR) + 10


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------

class LTDecoder:
    """
    Belief-propagation decoder for LT Fountain Codes.

    Usage
    -----
    1. Call :meth:`set_block_count` once the metadata packet is received.
    2. Feed droplets via :meth:`add_droplet`.
    3. Poll :meth:`is_complete`; when ``True``, call :meth:`get_data`.
    """

    def __init__(self, block_size: int = BLOCK_SIZE) -> None:
        self.block_size = block_size
        self._k: Optional[int] = None
        self._dist: Optional[RobustSoliton] = None
        self._blocks: Optional[np.ndarray] = None
        self._decoded: Optional[np.ndarray] = None   # bool mask

        # Droplets waiting for neighbours to be decoded
        self._pending: List[Droplet] = []
        # Inverted index: block_idx -> list of Droplet objects that still
        # reference it.  Enables O(neighbours) propagation instead of O(n).
        self._inv_index: Dict[int, List[Droplet]] = defaultdict(list)
        self._seen_seeds: Set[int] = set()

        # Buffer for packets that arrive before set_block_count()
        self._early: List[bytes] = []

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_block_count(self, k: int) -> None:
        """
        Initialise the decoder for *k* source blocks.

        Must be called exactly once, before or after droplets arrive
        (early-arrival droplets are buffered and replayed automatically).
        """
        if self._k is not None:
            return  # idempotent
        self._k = k
        self._dist = RobustSoliton(k)
        self._blocks = np.zeros((k, self.block_size), dtype=np.uint8)
        self._decoded = np.zeros(k, dtype=bool)

        # Replay buffered early droplets
        buffered = self._early
        self._early = []
        for raw in buffered:
            self._ingest(raw)

    # ------------------------------------------------------------------
    # Droplet ingestion
    # ------------------------------------------------------------------

    def add_droplet(self, packet: bytes) -> bool:
        """
        Feed a raw packet (header + payload) to the decoder.

        Parameters
        ----------
        packet:
            Bytes as decoded from the QR frame.

        Returns
        -------
        bool
            ``True`` if all source blocks have been recovered.
        """
        if self._k is None:
            # Haven't received metadata yet — buffer the packet
            self._early.append(packet)
            return False
        return self._ingest(packet)

    def _ingest(self, packet: bytes) -> bool:
        """Parse a packet and run belief propagation."""
        min_len = HEADER_SIZE + self.block_size
        if len(packet) < min_len:
            return self.is_complete()

        seed, _deg_hdr, _reserved = struct.unpack(">IHH", packet[:HEADER_SIZE])
        payload = bytearray(packet[HEADER_SIZE : HEADER_SIZE + self.block_size])

        if seed in self._seen_seeds:
            return self.is_complete()
        self._seen_seeds.add(seed)

        # Re-derive neighbours from seed (canonical; ignores _deg_hdr)
        neighbors: Set[int] = self._dist.neighbours(seed)  # type: ignore[union-attr]

        # XOR out already-decoded neighbours immediately
        for idx in list(neighbors):
            if self._decoded[idx]:  # type: ignore[index]
                payload_arr = np.frombuffer(payload, dtype=np.uint8)
                result = payload_arr ^ self._blocks[idx]  # type: ignore[index]
                payload[:] = result.tobytes()
                neighbors.discard(idx)

        if not neighbors:
            # Redundant droplet
            return self.is_complete()

        droplet = Droplet(seed=seed, data=payload, neighbors=neighbors)

        if len(neighbors) == 1:
            idx = next(iter(neighbors))
            self._resolve(idx, payload)
            # No need to add to pending/inv_index
        else:
            # Register in inverted index for fast propagation
            for idx in neighbors:
                self._inv_index[idx].append(droplet)
            self._pending.append(droplet)

        return self.is_complete()

    # ------------------------------------------------------------------
    # Belief propagation helpers
    # ------------------------------------------------------------------

    def _resolve(self, block_idx: int, data: bytearray) -> None:
        """Mark *block_idx* as decoded and propagate through the graph."""
        if self._decoded[block_idx]:  # type: ignore[index]
            return
        self._blocks[block_idx] = np.frombuffer(data, dtype=np.uint8)  # type: ignore[index]
        self._decoded[block_idx] = True  # type: ignore[index]
        self._propagate(block_idx)

    def _propagate(self, newly_decoded: int) -> None:
        """
        Reduce all droplets that reference *newly_decoded*.

        Uses the inverted index so only affected droplets are touched.
        """
        queue = [newly_decoded]
        while queue:
            idx = queue.pop()
            affected = self._inv_index.pop(idx, [])
            for droplet in affected:
                if idx not in droplet.neighbors:
                    continue  # already removed in a previous pass

                # XOR out the newly decoded block
                block_data = self._blocks[idx]  # type: ignore[index]
                arr = np.frombuffer(droplet.data, dtype=np.uint8)
                result = arr ^ block_data
                droplet.data[:] = result.tobytes()
                droplet.neighbors.discard(idx)

                if len(droplet.neighbors) == 0:
                    # Redundant; do nothing
                    pass
                elif len(droplet.neighbors) == 1:
                    next_idx = next(iter(droplet.neighbors))
                    if not self._decoded[next_idx]:  # type: ignore[index]
                        self._resolve(next_idx, droplet.data)
                        queue.append(next_idx)
                else:
                    # Re-register remaining neighbours
                    for remaining in droplet.neighbors:
                        self._inv_index[remaining].append(droplet)

    # ------------------------------------------------------------------
    # Status / output
    # ------------------------------------------------------------------

    def is_complete(self) -> bool:
        """Return ``True`` if all source blocks have been decoded."""
        if self._decoded is None:
            return False
        return bool(np.all(self._decoded))

    def progress(self) -> float:
        """Return fraction of source blocks decoded (0.0 – 1.0)."""
        if self._decoded is None:
            return 0.0
        return float(np.sum(self._decoded)) / self._k  # type: ignore[operator]

    def get_data(self) -> bytes:
        """
        Reconstruct and return the original data.

        Raises
        ------
        RuntimeError
            If decoding is not yet complete.
        """
        if not self.is_complete():
            raise RuntimeError(
                f"Decoding incomplete: {self.progress()*100:.1f}% recovered"
            )
        raw = self._blocks.tobytes()  # type: ignore[union-attr]
        original_length = struct.unpack(">I", raw[:4])[0]
        return raw[4 : 4 + original_length]