"""Integration tests for afterimage.protocol — full encode/decode pipeline."""
import os
import zlib
import pytest
from afterimage.crypto import CryptoLayer
from afterimage.fountain import LTEncoder, LTDecoder


PASSWORD = "test-password-xyz"


def _full_pipeline(data: bytes, loss_rate: float = 0.0) -> bytes:
    """
    Simulate the full TX pipeline (compress → encrypt → fountain encode)
    followed by the RX pipeline (fountain decode → decrypt → decompress),
    with optional packet loss.
    """
    import random
    rng = random.Random(0)

    # TX side
    compressed = zlib.compress(data, level=9)
    encrypted = CryptoLayer.encrypt(compressed, PASSWORD)
    encoder = LTEncoder(encrypted)

    # RX side
    decoder = LTDecoder()
    decoder.set_block_count(encoder.k)

    total = int(encoder.recommended_count() * (1 + loss_rate) * 2.0) + 30
    for _ in range(total):
        pkt = encoder.generate_droplet()
        if rng.random() >= loss_rate:
            if decoder.add_droplet(pkt):
                break

    recovered_enc = decoder.get_data()
    recovered_cmp = CryptoLayer.decrypt(recovered_enc, PASSWORD)
    return zlib.decompress(recovered_cmp)


class TestFullPipeline:
    def test_text_file(self):
        data = b"Hello, AFTERIMAGE!\n" * 50
        assert _full_pipeline(data) == data

    def test_binary_data(self):
        data = os.urandom(2048)
        assert _full_pipeline(data) == data

    def test_zero_bytes(self):
        assert _full_pipeline(b"") == b""

    def test_single_byte(self):
        assert _full_pipeline(b"\xff") == b"\xff"

    @pytest.mark.slow
    def test_50kb_no_loss(self):
        data = os.urandom(50 * 1024)
        assert _full_pipeline(data) == data

    @pytest.mark.slow
    def test_10kb_30pct_loss(self):
        data = os.urandom(10 * 1024)
        assert _full_pipeline(data, loss_rate=0.30) == data

    def test_wrong_password_raises(self):
        from afterimage.crypto import DecryptionError
        data = b"secret"
        compressed = zlib.compress(data)
        encrypted = CryptoLayer.encrypt(compressed, PASSWORD)
        encoder = LTEncoder(encrypted)
        decoder = LTDecoder()
        decoder.set_block_count(encoder.k)
        for _ in range(encoder.recommended_count() + 20):
            if decoder.add_droplet(encoder.generate_droplet()):
                break
        recovered_enc = decoder.get_data()
        with pytest.raises(DecryptionError):
            CryptoLayer.decrypt(recovered_enc, "wrong-password")

    def test_compression_reduces_size(self):
        # Highly compressible data
        data = b"AAAA" * 1000
        compressed = zlib.compress(data, level=9)
        assert len(compressed) < len(data)

    def test_incompressible_data_survives(self):
        # Already-random data won't compress; pipeline must still work
        data = os.urandom(1024)
        assert _full_pipeline(data) == data