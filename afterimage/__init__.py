"""
AFTERIMAGE
==========
Optical air-gap data exfiltration via QR stream with LT Fountain Codes.

Public API
----------
>>> from afterimage import AfterImage
>>> ai = AfterImage(password="…")
>>> ai.tx("secret.zip")          # transmitter
>>> ai.rx("recovered.zip")       # receiver

Submodules
----------
afterimage.crypto    – ChaCha20-Poly1305 encryption layer
afterimage.fountain  – LT Fountain Code encoder / decoder
afterimage.optical   – QR generation and camera scanning
afterimage.protocol  – High-level TX / RX orchestration
afterimage.cli       – Command-line entry point
"""

from .crypto import CryptoLayer, DecryptionError
from .fountain import LTEncoder, LTDecoder, RobustSoliton

__version__ = "1.0.0"
__author__ = "AFTERIMAGE Contributors"
__license__ = "AGPL-3.0-or-later"

# AfterImage, QRGenerator and QRScanner are imported lazily so that
# importing afterimage.crypto / afterimage.fountain in tests does NOT
# require cv2 / opencv to be installed.

def __getattr__(name: str):  # type: ignore[no-untyped-def]
    if name == "AfterImage":
        from .protocol import AfterImage
        return AfterImage
    if name == "QRGenerator":
        from .optical import QRGenerator
        return QRGenerator
    if name == "QRScanner":
        from .optical import QRScanner
        return QRScanner
    raise AttributeError(f"module 'afterimage' has no attribute {name!r}")

__all__ = [
    "AfterImage",
    "CryptoLayer",
    "DecryptionError",
    "LTEncoder",
    "LTDecoder",
    "RobustSoliton",
    "QRGenerator",
    "QRScanner",
    "__version__",
]
