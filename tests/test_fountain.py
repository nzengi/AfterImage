"""Tests for afterimage.fountain — LT Fountain Codes."""
import os
import pytest
from afterimage.fountain import LTEncoder, LTDecoder, RobustSoliton, BLOCK_SIZE


def _round_trip(data: bytes, loss_rate: float = 0.0) -> bytes:
    """Encode data, optionally drop frames, then decode."""
    encoder = LTEncoder(data)
    decoder = LTDecoder()
    decoder.set_block_count(encoder.k)

    needed = encoder.recommended_count()
    # Send extra headroom proportional to loss rate
    total = int(needed * (1 + loss_rate) * 1.5) + 20

    import random
    rng = random.Random(42)

    for _ in range(total):
        droplet = encoder.generate_droplet()
        if rng.random() >= loss_rate:
            if decoder.add_droplet(droplet):
                break

    return decoder.get_data()


class TestRobustSoliton:
    def test_cdf_ends_near_one(self):
        dist = RobustSoliton(100)
        assert abs(dist.cdf[-1] - 1.0) < 1e-9

    def test_sample_in_range(self):
        import numpy as np
        dist = RobustSoliton(50)
        rng = np.random.default_rng(0)
        for _ in range(200):
            d = dist.sample(rng)
            assert 1 <= d <= 50

    def test_k_equals_one(self):
        dist = RobustSoliton(1)
        import numpy as np
        rng = np.random.default_rng(0)
        assert dist.sample(rng) == 1

    def test_neighbours_deterministic(self):
        dist = RobustSoliton(20)
        assert dist.neighbours(7) == dist.neighbours(7)

    def test_neighbours_count_bounded(self):
        dist = RobustSoliton(20)
        for seed in range(50):
            nb = dist.neighbours(seed)
            assert 1 <= len(nb) <= 20
            assert all(0 <= i < 20 for i in nb)


class TestLTEncoder:
    def test_generates_bytes(self):
        enc = LTEncoder(b"hello world")
        pkt = enc.generate_droplet()
        assert isinstance(pkt, bytes)
        assert len(pkt) == 8 + BLOCK_SIZE  # HEADER + block

    def test_counter_increments(self):
        enc = LTEncoder(b"x" * 512)
        import struct
        s0 = struct.unpack(">IHH", enc.generate_droplet()[:8])[0]
        s1 = struct.unpack(">IHH", enc.generate_droplet()[:8])[0]
        assert s1 == s0 + 1

    def test_recommended_count_positive(self):
        enc = LTEncoder(b"a" * 1000)
        assert enc.recommended_count() > enc.k


class TestLTDecoder:
    def test_small_round_trip(self):
        data = b"AFTERIMAGE test payload 1234567890"
        assert _round_trip(data) == data

    def test_exact_block_boundary(self):
        data = os.urandom(BLOCK_SIZE * 4)
        assert _round_trip(data) == data

    def test_single_byte(self):
        assert _round_trip(b"\xAB") == b"\xAB"

    def test_empty_data(self):
        assert _round_trip(b"") == b""

    def test_1kb(self):
        data = os.urandom(1024)
        assert _round_trip(data) == data

    @pytest.mark.slow
    def test_10kb_with_20pct_loss(self):
        data = os.urandom(10 * 1024)
        assert _round_trip(data, loss_rate=0.20) == data

    @pytest.mark.slow
    def test_10kb_with_40pct_loss(self):
        data = os.urandom(10 * 1024)
        assert _round_trip(data, loss_rate=0.40) == data

    def test_duplicate_droplets_ignored(self):
        data = b"deduplicate me"
        encoder = LTEncoder(data)
        decoder = LTDecoder()
        decoder.set_block_count(encoder.k)

        pkt = encoder.generate_droplet()
        # Feed the same packet many times
        for _ in range(50):
            decoder.add_droplet(pkt)

        # Feed enough unique droplets to complete
        for _ in range(encoder.recommended_count() + 20):
            decoder.add_droplet(encoder.generate_droplet())

        assert decoder.get_data() == data

    def test_early_droplets_buffered(self):
        """Droplets arriving before set_block_count must be buffered."""
        data = b"early bird droplet"
        encoder = LTEncoder(data)
        decoder = LTDecoder()

        droplets = [encoder.generate_droplet()
                    for _ in range(encoder.recommended_count() + 20)]

        # Feed droplets BEFORE setting block count
        for pkt in droplets:
            decoder.add_droplet(pkt)

        # Now set block count — should replay buffered droplets
        decoder.set_block_count(encoder.k)

        assert decoder.get_data() == data

    def test_incomplete_raises(self):
        decoder = LTDecoder()
        decoder.set_block_count(10)
        with pytest.raises(RuntimeError, match="incomplete"):
            decoder.get_data()

    def test_progress_increases(self):
        data = os.urandom(2048)
        encoder = LTEncoder(data)
        decoder = LTDecoder()
        decoder.set_block_count(encoder.k)

        prev = 0.0
        for _ in range(encoder.recommended_count() + 10):
            decoder.add_droplet(encoder.generate_droplet())
            p = decoder.progress()
            assert p >= prev
            prev = p
            if decoder.is_complete():
                break