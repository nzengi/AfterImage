"""
afterimage.optical
==================
QR code generation (transmitter) and camera-based scanning (receiver).

Design decisions
----------------
* ``QRGenerator`` uses the ``segno`` library when available (3-5× faster than
  the pure-Python ``qrcode`` package) and falls back to ``qrcode`` otherwise.
* ``QRScanner`` tries ``pyzbar`` first (faster, handles more edge cases) and
  falls back to OpenCV's built-in detector.
* Camera parameters are tuned for low-latency capture: buffer size 1,
  explicit resolution, and explicit FPS target.
* Status overlays are drawn in a separate compositing step so the raw QR
  image is never mutated — important for reproducible testing.
"""

from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np

__all__ = ["QRGenerator", "QRScanner"]

# ---------------------------------------------------------------------------
# QR parameters
# ---------------------------------------------------------------------------

QR_VERSION: int = 22
# ERROR_CORRECT_M (~15 % recovery) balances density and optical resilience.
# The original code used ERROR_CORRECT_L (7 %) which is too fragile for
# handheld cameras at a distance.
try:
    import qrcode
    _QR_EC = qrcode.constants.ERROR_CORRECT_M
except ImportError:
    _QR_EC = None  # handled below

TARGET_FPS: int = 20
CAMERA_WIDTH: int = 1280
CAMERA_HEIGHT: int = 720


# ---------------------------------------------------------------------------
# QR Generator
# ---------------------------------------------------------------------------

class QRGenerator:
    """
    Encode arbitrary bytes into a QR code image (numpy RGB array).

    Tries ``segno`` first, falls back to ``qrcode``.

    Parameters
    ----------
    version:
        QR symbol version (1–40).  Version 22 holds ~1000 bytes at EC level M.
    box_size:
        Pixel size of each QR module.
    border:
        Quiet-zone width in modules (spec requires ≥ 4; 2 is workable for
        full-screen display).
    """

    def __init__(
        self,
        version: int = QR_VERSION,
        box_size: int = 10,
        border: int = 2,
    ) -> None:
        self.version = version
        self.box_size = box_size
        self.border = border
        self._backend = self._detect_backend()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_backend() -> str:
        try:
            import segno  # noqa: F401
            return "segno"
        except ImportError:
            pass
        try:
            import qrcode  # noqa: F401
            return "qrcode"
        except ImportError:
            raise ImportError(
                "No QR generation library found. "
                "Install with: pip install segno  (recommended) or pip install qrcode[pil]"
            )

    def _generate_segno(self, data: bytes) -> np.ndarray:
        import segno
        qr = segno.make(
            data,
            version=self.version,
            error="m",
            mode="byte",
        )
        # segno can render to an in-memory PIL image
        from io import BytesIO
        from PIL import Image

        buf = BytesIO()
        qr.save(
            buf,
            kind="png",
            scale=self.box_size,
            border=self.border,
            dark="black",
            light="white",
        )
        buf.seek(0)
        img = Image.open(buf).convert("RGB")
        return np.array(img)

    def _generate_qrcode(self, data: bytes) -> np.ndarray:
        import qrcode as _qrcode

        qr = _qrcode.QRCode(
            version=self.version,
            error_correction=_QR_EC,
            box_size=self.box_size,
            border=self.border,
        )
        qr.add_data(data)
        qr.make(fit=False)
        img = qr.make_image(fill_color="black", back_color="white")
        return np.array(img.convert("RGB"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, data: bytes) -> np.ndarray:
        """
        Encode *data* and return a uint8 RGB numpy array.

        Parameters
        ----------
        data:
            Raw bytes to encode (must fit within QR version capacity).

        Returns
        -------
        np.ndarray
            Shape (H, W, 3), dtype uint8, RGB colour order.
        """
        if self._backend == "segno":
            return self._generate_segno(data)
        return self._generate_qrcode(data)


# ---------------------------------------------------------------------------
# QR Scanner
# ---------------------------------------------------------------------------

class QRScanner:
    """
    Live camera feed QR code scanner.

    Parameters
    ----------
    camera_idx:
        OpenCV camera index (0 = default device).
    """

    def __init__(self, camera_idx: int = 0) -> None:
        self.camera_idx = camera_idx
        self._cap: Optional[cv2.VideoCapture] = None
        self._cv_detector = cv2.QRCodeDetector()
        self._use_pyzbar: bool = self._probe_pyzbar()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _probe_pyzbar() -> bool:
        """Return True if pyzbar + libzbar are both functional."""
        try:
            from pyzbar import pyzbar  # noqa: F401

            # Do a real decode call to catch "libzbar.so not found" at import
            # time on Linux where pyzbar installs but libzbar is missing.
            pyzbar.decode(np.zeros((32, 32, 3), dtype=np.uint8))
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "QRScanner":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> bool:
        """
        Open the camera.

        Returns
        -------
        bool
            ``True`` on success.
        """
        cap = cv2.VideoCapture(self.camera_idx)
        if not cap.isOpened():
            return False
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, 30)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # minimise latency
        self._cap = cap
        return True

    def close(self) -> None:
        """Release the camera."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    # ------------------------------------------------------------------
    # Frame operations
    # ------------------------------------------------------------------

    def read_frame(self) -> Tuple[bool, Optional[np.ndarray]]:
        """
        Capture one frame.

        Returns
        -------
        (success, frame)
            *frame* is None when *success* is False.
        """
        if self._cap is None:
            return False, None
        ret, frame = self._cap.read()
        if not ret:
            return False, None
        return True, frame

    def decode_qr(self, frame: np.ndarray) -> Optional[bytes]:
        """
        Attempt to decode a QR code from *frame*.

        Tries pyzbar first (if available), then OpenCV's detector.

        Parameters
        ----------
        frame:
            BGR uint8 numpy array (as returned by OpenCV).

        Returns
        -------
        bytes or None
            Decoded payload, or ``None`` if no QR code was found.
        """
        if self._use_pyzbar:
            from pyzbar import pyzbar

            decoded = pyzbar.decode(frame)
            for obj in decoded:
                if obj.type == "QRCODE":
                    return bytes(obj.data)

        # OpenCV fallback
        data, _points, _ = self._cv_detector.detectAndDecode(frame)
        if data:
            # OpenCV returns a str; use latin-1 to preserve all byte values
            if isinstance(data, str):
                return data.encode("latin-1")
            return bytes(data)

        return None