"""
afterimage.protocol
===================
High-level transmit / receive orchestration.

Protocol frame types
--------------------
METADATA frame (recognised by the 4-byte magic prefix ``b'AFTI'``):

    magic    (4 B)  – b'AFTI'
    version  (1 B)  – uint8, currently 1
    k        (4 B)  – uint32 BE, number of source blocks
    orig_len (4 B)  – uint32 BE, original file size in bytes
    filename (64 B) – UTF-8, NUL-padded

Total: 77 bytes.  Sent every METADATA_INTERVAL droplets so a late receiver
can synchronise without waiting from the start.

DATA frame (everything that does NOT start with b'AFTI'):

    LT droplet as produced by ``LTEncoder.generate_droplet()``.

Security notes
--------------
* The filename is transmitted in cleartext inside the METADATA frame.
  Operators who require filename anonymity should pass an empty string or a
  decoy name.
* Elapsed time and droplet counters are printed to stdout, not rendered
  onto the QR image, to avoid metadata leakage through screen recordings.
"""

from __future__ import annotations

import os
import struct
import sys
import time
import zlib
from typing import Optional

import cv2
import numpy as np

from .crypto import CryptoLayer, DecryptionError
from .fountain import LTDecoder, LTEncoder
from .optical import QRGenerator, QRScanner, TARGET_FPS

__all__ = ["AfterImage"]

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

MAGIC: bytes = b"AFTI"
VERSION: int = 1

# Metadata packet layout offsets
_META_MAGIC = slice(0, 4)
_META_VER = 4
_META_K = slice(5, 9)
_META_ORIG = slice(9, 13)
_META_FNAME = slice(13, 77)
META_SIZE: int = 77

METADATA_INTERVAL: int = 50   # send one METADATA frame every N droplets


# ---------------------------------------------------------------------------
# AfterImage orchestrator
# ---------------------------------------------------------------------------

class AfterImage:
    """
    Optical air-gap data exfiltration protocol.

    Parameters
    ----------
    password:
        Encryption passphrase.  Must be provided by the caller (e.g. via
        ``getpass.getpass()``); never pass it as a CLI argument.
    """

    def __init__(self, password: str) -> None:
        if not password:
            raise ValueError("password must not be empty")
        self._password = password
        self._qr = QRGenerator()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_metadata(k: int, original_size: int, filename: str) -> bytes:
        """Serialise a METADATA frame."""
        fname_bytes = filename.encode("utf-8")[:64].ljust(64, b"\x00")
        return struct.pack(">4sBII", MAGIC, VERSION, k, original_size) + fname_bytes

    @staticmethod
    def _parse_metadata(frame: bytes) -> Optional[tuple]:
        """
        Parse a METADATA frame.

        Returns
        -------
        (version, k, original_size, filename) or None if parsing fails.
        """
        if len(frame) < META_SIZE:
            return None
        if frame[:4] != MAGIC:
            return None
        version = frame[4]
        k, original_size = struct.unpack(">II", frame[5:13])
        filename = frame[13:77].rstrip(b"\x00").decode("utf-8", errors="replace")
        return version, k, original_size, filename

    @staticmethod
    def _draw_overlay(
        base: np.ndarray,
        text_lines: list,
        progress: Optional[float] = None,
    ) -> np.ndarray:
        """
        Composite status text (and optional progress bar) onto *base*.

        A fresh copy is returned; *base* is never mutated.
        """
        img = base.copy()
        y = 30
        for line in text_lines:
            cv2.putText(
                img, line, (10, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 128, 0), 2,
                cv2.LINE_AA,
            )
            y += 28

        if progress is not None:
            bx, by, bw, bh = 50, 50, 400, 25
            cv2.rectangle(img, (bx, by), (bx + bw, by + bh), (40, 40, 40), -1)
            cv2.rectangle(
                img,
                (bx, by),
                (bx + int(bw * progress), by + bh),
                (0, 220, 0), -1,
            )
            cv2.rectangle(img, (bx, by), (bx + bw, by + bh), (200, 200, 200), 1)

        return img

    # ------------------------------------------------------------------
    # Transmitter
    # ------------------------------------------------------------------

    def tx(self, filepath: str) -> None:
        """
        Transmit *filepath* as a continuous QR stream.

        The window is fullscreen.  Press **q** to stop transmitting.

        Parameters
        ----------
        filepath:
            Path to the file to send.
        """
        _bar = "═" * 60
        print(f"\n{_bar}")
        print("  AFTERIMAGE  ▶  TRANSMITTER")
        print(f"{_bar}\n")

        # ── Load ──────────────────────────────────────────────────────
        if not os.path.isfile(filepath):
            print(f"[!] File not found: {filepath}", file=sys.stderr)
            return

        with open(filepath, "rb") as fh:
            raw = fh.read()

        original_size = len(raw)
        print(f"[*] File         : {filepath}")
        print(f"[*] Original size: {original_size:,} B")

        # ── Compress ──────────────────────────────────────────────────
        compressed = zlib.compress(raw, level=9)
        ratio = 100 * len(compressed) / original_size
        print(f"[*] Compressed   : {len(compressed):,} B  ({ratio:.1f} %)")

        # ── Encrypt ───────────────────────────────────────────────────
        print("[*] Encrypting … (this may take a moment — PBKDF2 key stretch)")
        encrypted = CryptoLayer.encrypt(compressed, self._password)
        print(f"[*] Encrypted    : {len(encrypted):,} B")

        # ── Fountain encoder ──────────────────────────────────────────
        encoder = LTEncoder(encrypted)
        k = encoder.k
        recommended = encoder.recommended_count()
        print(f"[*] Source blocks: {k}")
        print(f"[*] Recommended  : {recommended} droplets")

        # ── Metadata packet (sent periodically) ───────────────────────
        meta_packet = self._build_metadata(
            k, original_size, os.path.basename(filepath)
        )

        # ── Display ───────────────────────────────────────────────────
        print(f"\n[*] Streaming at {TARGET_FPS} FPS  |  press 'q' to stop\n")
        cv2.namedWindow("AFTERIMAGE TX", cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(
            "AFTERIMAGE TX", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN
        )

        frame_delay_ms = max(1, int(1000 / TARGET_FPS))
        droplet_idx = 0
        start = time.time()

        try:
            while True:
                if droplet_idx % METADATA_INTERVAL == 0:
                    packet = meta_packet
                    ptype = "META"
                else:
                    packet = encoder.generate_droplet()
                    ptype = "DATA"

                qr_img = self._qr.generate(packet)

                # Status lines (printed to stdout, NOT burned into QR image
                # to avoid metadata leakage through screen recordings)
                elapsed = time.time() - start
                overlay = self._draw_overlay(
                    qr_img,
                    [f"AFTERIMAGE TX  {ptype}#{droplet_idx}  {elapsed:.0f}s"],
                )

                cv2.imshow("AFTERIMAGE TX", overlay)
                key = cv2.waitKey(frame_delay_ms) & 0xFF
                if key == ord("q"):
                    break

                droplet_idx += 1

                if droplet_idx % 100 == 0:
                    pct = droplet_idx / recommended * 100
                    print(
                        f"[TX] {droplet_idx:>5} droplets sent "
                        f"({pct:.0f}% of recommended)"
                    )

        finally:
            cv2.destroyAllWindows()

        print(f"\n[*] Transmission ended — {droplet_idx} droplets sent.")

    # ------------------------------------------------------------------
    # Receiver
    # ------------------------------------------------------------------

    def rx(self, output_path: str, camera_idx: int = 0) -> bool:
        """
        Receive a file from a QR stream captured by the camera.

        Parameters
        ----------
        output_path:
            Where to write the reconstructed file.
        camera_idx:
            OpenCV camera index (default 0).

        Returns
        -------
        bool
            ``True`` on successful reconstruction and save.
        """
        _bar = "═" * 60
        print(f"\n{_bar}")
        print("  AFTERIMAGE  ◀  RECEIVER")
        print(f"{_bar}\n")

        scanner = QRScanner(camera_idx)
        decoder = LTDecoder()

        if not scanner.open():
            print(f"[!] Cannot open camera {camera_idx}", file=sys.stderr)
            return False

        print(f"[*] Camera {camera_idx} ready")
        print("[*] Waiting for AFTERIMAGE stream …  press 'q' to abort\n")

        cv2.namedWindow("AFTERIMAGE RX", cv2.WINDOW_NORMAL)

        meta_ok = False
        k = 0
        original_size = 0
        filename = ""
        droplets_rx = 0
        start: Optional[float] = None
        last_qr = time.time()

        try:
            with scanner:
                while True:
                    ret, frame = scanner.read_frame()
                    if not ret or frame is None:
                        continue

                    qr_data = scanner.decode_qr(frame)
                    display = frame.copy()

                    if qr_data:
                        last_qr = time.time()

                        if qr_data[:4] == MAGIC:
                            parsed = self._parse_metadata(qr_data)
                            if parsed and not meta_ok:
                                ver, k, original_size, filename = parsed
                                if ver != VERSION:
                                    print(
                                        f"[!] Protocol version mismatch "
                                        f"(got {ver}, expected {VERSION})",
                                        file=sys.stderr,
                                    )
                                    continue
                                decoder.set_block_count(k)
                                meta_ok = True
                                start = time.time()
                                print(f"[*] Stream detected!")
                                print(f"[*] Filename      : {filename}")
                                print(
                                    f"[*] Original size : {original_size:,} B"
                                )
                                print(f"[*] Source blocks : {k}\n")

                        elif meta_ok:
                            complete = decoder.add_droplet(qr_data)
                            droplets_rx += 1

                            if droplets_rx % 50 == 0:
                                pct = decoder.progress() * 100
                                print(
                                    f"[RX] {droplets_rx:>5} droplets  "
                                    f"{pct:.1f}% decoded"
                                )

                            if complete:
                                break

                    # ── Overlay ───────────────────────────────────────
                    if meta_ok:
                        prog = decoder.progress()
                        display = self._draw_overlay(
                            display,
                            [
                                f"AFTERIMAGE RX  {prog*100:.1f}%  "
                                f"{droplets_rx} droplets",
                                f"File: {filename}",
                            ],
                            progress=prog,
                        )
                    else:
                        age = time.time() - last_qr
                        colour = (0, 220, 0) if age < 0.5 else (0, 0, 220)
                        cv2.circle(
                            display,
                            (display.shape[1] - 40, 40), 15, colour, -1
                        )
                        cv2.putText(
                            display, "AFTERIMAGE RX  scanning …",
                            (10, 40), cv2.FONT_HERSHEY_SIMPLEX,
                            0.7, (0, 200, 255), 2, cv2.LINE_AA,
                        )

                    cv2.imshow("AFTERIMAGE RX", display)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        print("[*] Aborted by user.")
                        return False

        finally:
            cv2.destroyAllWindows()

        # ── Reconstruct ───────────────────────────────────────────────
        elapsed = (time.time() - start) if start else 0.0
        print(
            f"\n[*] Capture complete — {droplets_rx} droplets in {elapsed:.1f} s"
        )

        try:
            encrypted = decoder.get_data()
            print(f"[*] Encrypted payload : {len(encrypted):,} B")

            print("[*] Decrypting …")
            compressed = CryptoLayer.decrypt(encrypted, self._password)
            print(f"[*] Compressed payload: {len(compressed):,} B")

            print("[*] Decompressing …")
            recovered = zlib.decompress(compressed)
            print(f"[*] Recovered size    : {len(recovered):,} B")

            if len(recovered) != original_size:
                print(
                    f"[!] Size mismatch: expected {original_size:,} B, "
                    f"got {len(recovered):,} B",
                    file=sys.stderr,
                )

            with open(output_path, "wb") as fh:
                fh.write(recovered)

            print(f"\n[✓] Saved → {output_path}")
            return True

        except DecryptionError:
            # Deliberately minimal message — don't leak internal state
            print("[!] Decryption failed: wrong password or corrupted stream.",
                  file=sys.stderr)
            return False
        except Exception as exc:
            print(f"[!] Reconstruction failed: {exc}", file=sys.stderr)
            return False