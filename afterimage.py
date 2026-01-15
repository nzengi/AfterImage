#!/usr/bin/env python3
"""
AFTERIMAGE - Optical Air-Gap Data Exfiltration Protocol
========================================================
Secure, unidirectional file transfer via QR stream with LT Fountain Codes.

Usage:
    Transmitter: python afterimage.py --tx <file> --password <pass>
    Receiver:    python afterimage.py --rx <output> --password <pass> [--camera <idx>]

Dependencies: numpy, opencv-python, pyzbar, cryptography, qrcode, Pillow
"""

import argparse
import hashlib
import os
import secrets
import struct
import sys
import time
import zlib
from dataclasses import dataclass, field
from typing import Optional, Set, List, Tuple

import cv2
import numpy as np
import qrcode
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
try:
    from pyzbar import pyzbar
    PYZBAR_AVAILABLE = True
except ImportError:
    PYZBAR_AVAILABLE = False

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

BLOCK_SIZE = 256          # Bytes per source block
OVERHEAD_FACTOR = 1.05    # 5% overhead for LT decoding
QR_VERSION = 22           # QR version (20-25 range)
QR_ERROR_CORRECTION = qrcode.constants.ERROR_CORRECT_L
TARGET_FPS = 20
NONCE_SIZE = 12
TAG_SIZE = 16
SALT_SIZE = 16
HEADER_SIZE = 8           # 4B seed + 2B degree + 2B reserved

# Robust Soliton Distribution parameters
C_PARAM = 0.1
DELTA_PARAM = 0.5

# ═══════════════════════════════════════════════════════════════════════════════
# CRYPTO LAYER
# ═══════════════════════════════════════════════════════════════════════════════

class CryptoLayer:
    """ChaCha20-Poly1305 encryption with password-derived key."""
    
    ITERATIONS = 100_000
    
    @staticmethod
    def derive_key(password: str, salt: bytes) -> bytes:
        """Derive 32-byte key from password using PBKDF2-SHA256."""
        return hashlib.pbkdf2_hmac(
            'sha256',
            password.encode('utf-8'),
            salt,
            CryptoLayer.ITERATIONS,
            dklen=32
        )
    
    @staticmethod
    def encrypt(data: bytes, password: str) -> bytes:
        """Encrypt data. Returns: salt || nonce || ciphertext || tag."""
        salt = secrets.token_bytes(SALT_SIZE)
        key = CryptoLayer.derive_key(password, salt)
        nonce = secrets.token_bytes(NONCE_SIZE)
        cipher = ChaCha20Poly1305(key)
        ciphertext = cipher.encrypt(nonce, data, None)
        return salt + nonce + ciphertext
    
    @staticmethod
    def decrypt(data: bytes, password: str) -> bytes:
        """Decrypt data. Expects: salt || nonce || ciphertext || tag."""
        salt = data[:SALT_SIZE]
        nonce = data[SALT_SIZE:SALT_SIZE + NONCE_SIZE]
        ciphertext = data[SALT_SIZE + NONCE_SIZE:]
        key = CryptoLayer.derive_key(password, salt)
        cipher = ChaCha20Poly1305(key)
        return cipher.decrypt(nonce, ciphertext, None)

# ═══════════════════════════════════════════════════════════════════════════════
# LT FOUNTAIN CODES (Pure NumPy Implementation)
# ═══════════════════════════════════════════════════════════════════════════════

class RobustSoliton:
    """Robust Soliton Distribution for LT codes."""
    
    def __init__(self, k: int, c: float = C_PARAM, delta: float = DELTA_PARAM):
        self.k = k
        self.c = c
        self.delta = delta
        self.probs = self._compute_distribution()
        self.cdf = np.cumsum(self.probs)
    
    def _compute_distribution(self) -> np.ndarray:
        """Compute the Robust Soliton probability distribution."""
        k = self.k
        R = self.c * np.log(k / self.delta) * np.sqrt(k)
        
        # Ideal Soliton
        rho = np.zeros(k + 1)
        rho[1] = 1.0 / k
        for d in range(2, k + 1):
            rho[d] = 1.0 / (d * (d - 1))
        
        # Tau component
        tau = np.zeros(k + 1)
        threshold = int(k / R) if R > 0 else k
        for d in range(1, min(threshold, k) + 1):
            tau[d] = R / (d * k)
        if threshold <= k and threshold > 0:
            tau[threshold] = R * np.log(R / self.delta) / k
        
        # Combine and normalize
        mu = rho + tau
        mu = mu / np.sum(mu)
        return mu
    
    def sample(self, rng: np.random.Generator) -> int:
        """Sample a degree from the distribution."""
        u = rng.random()
        return max(1, int(np.searchsorted(self.cdf, u)))


@dataclass
class Droplet:
    """A single encoded droplet (packet)."""
    seed: int
    degree: int
    data: bytes
    neighbors: Set[int] = field(default_factory=set)


class LTEncoder:
    """LT Fountain Code Encoder using vectorized NumPy operations."""
    
    def __init__(self, data: bytes, block_size: int = BLOCK_SIZE):
        self.block_size = block_size
        self.blocks = self._split_blocks(data)
        self.k = len(self.blocks)
        self.distribution = RobustSoliton(self.k)
        self.droplet_count = 0
    
    def _split_blocks(self, data: bytes) -> np.ndarray:
        """Split data into fixed-size blocks, pad last block if needed."""
        # Prepend original length for reconstruction
        length_prefix = struct.pack('>I', len(data))
        data = length_prefix + data
        
        # Pad to block boundary
        padding_needed = (self.block_size - len(data) % self.block_size) % self.block_size
        data += b'\x00' * padding_needed
        
        # Convert to numpy array of blocks
        arr = np.frombuffer(data, dtype=np.uint8)
        return arr.reshape(-1, self.block_size)
    
    def generate_droplet(self) -> bytes:
        """Generate a single encoded droplet."""
        seed = self.droplet_count
        self.droplet_count += 1
        
        # Use seed for reproducible neighbor selection
        rng = np.random.default_rng(seed)
        degree = self.distribution.sample(rng)
        degree = min(degree, self.k)
        
        # Select random neighbors
        neighbors = rng.choice(self.k, size=degree, replace=False)
        
        # XOR selected blocks
        encoded = np.zeros(self.block_size, dtype=np.uint8)
        for idx in neighbors:
            encoded ^= self.blocks[idx]
        
        # Pack: seed (4B) + degree (2B) + reserved (2B) + data
        header = struct.pack('>IHH', seed, degree, 0)
        return header + encoded.tobytes()
    
    def get_recommended_count(self) -> int:
        """Get recommended number of droplets for successful decoding."""
        return int(self.k * OVERHEAD_FACTOR) + 10


class LTDecoder:
    """LT Fountain Code Decoder with belief propagation."""
    
    def __init__(self, block_size: int = BLOCK_SIZE):
        self.block_size = block_size
        self.k: Optional[int] = None
        self.blocks: Optional[np.ndarray] = None
        self.decoded: Optional[np.ndarray] = None
        self.droplets: List[Droplet] = []
        self.processed_seeds: Set[int] = set()
    
    def add_droplet(self, packet: bytes) -> bool:
        """Add a droplet and attempt decoding. Returns True if fully decoded."""
        if len(packet) < HEADER_SIZE + self.block_size:
            return False
        
        # Parse header
        seed, degree, _ = struct.unpack('>IHH', packet[:HEADER_SIZE])
        data = packet[HEADER_SIZE:HEADER_SIZE + self.block_size]
        
        # Skip duplicate droplets
        if seed in self.processed_seeds:
            return self.is_complete()
        self.processed_seeds.add(seed)
        
        # Reconstruct neighbors from seed
        rng = np.random.default_rng(seed)
        
        # We need to know k to determine neighbors - infer from degree 1 droplets
        if self.k is None:
            # Store droplet for later processing
            droplet = Droplet(seed=seed, degree=degree, data=data)
            self.droplets.append(droplet)
            return False
        
        # Generate neighbors
        sampled_degree = self.blocks.shape[0]  # Need distribution sample
        rng_temp = np.random.default_rng(seed)
        dist = RobustSoliton(self.k)
        actual_degree = dist.sample(rng_temp)
        actual_degree = min(actual_degree, self.k)
        
        rng2 = np.random.default_rng(seed)
        _ = dist.sample(rng2)  # Consume same random value
        neighbors = set(rng2.choice(self.k, size=actual_degree, replace=False).tolist())
        
        droplet = Droplet(seed=seed, degree=degree, data=data, neighbors=neighbors)
        self._process_droplet(droplet)
        
        return self.is_complete()
    
    def set_block_count(self, k: int):
        """Set the number of source blocks (received from metadata)."""
        self.k = k
        self.blocks = np.zeros((k, self.block_size), dtype=np.uint8)
        self.decoded = np.zeros(k, dtype=bool)
        
        # Reprocess stored droplets
        dist = RobustSoliton(self.k)
        for droplet in self.droplets:
            rng = np.random.default_rng(droplet.seed)
            degree = dist.sample(rng)
            degree = min(degree, self.k)
            neighbors = set(rng.choice(self.k, size=degree, replace=False).tolist())
            droplet.neighbors = neighbors
            self._process_droplet(droplet)
        self.droplets.clear()
    
    def _process_droplet(self, droplet: Droplet):
        """Process a droplet using belief propagation."""
        data_arr = np.frombuffer(droplet.data, dtype=np.uint8).copy()
        neighbors = droplet.neighbors.copy()
        
        # Remove already decoded neighbors and XOR their data
        decoded_neighbors = neighbors & set(np.where(self.decoded)[0])
        for idx in decoded_neighbors:
            data_arr ^= self.blocks[idx]
            neighbors.discard(idx)
        
        if len(neighbors) == 0:
            # All neighbors already decoded, droplet is redundant
            return
        elif len(neighbors) == 1:
            # Can decode this block immediately
            idx = neighbors.pop()
            self.blocks[idx] = data_arr
            self.decoded[idx] = True
            self._propagate(idx)
        else:
            # Store for later resolution
            droplet.neighbors = neighbors
            droplet.data = data_arr.tobytes()
            self.droplets.append(droplet)
    
    def _propagate(self, decoded_idx: int):
        """Propagate a newly decoded block through pending droplets."""
        changed = True
        while changed:
            changed = False
            remaining = []
            for droplet in self.droplets:
                if decoded_idx in droplet.neighbors:
                    data_arr = np.frombuffer(droplet.data, dtype=np.uint8).copy()
                    data_arr ^= self.blocks[decoded_idx]
                    droplet.neighbors.discard(decoded_idx)
                    droplet.data = data_arr.tobytes()
                
                if len(droplet.neighbors) == 0:
                    continue  # Fully resolved, discard
                elif len(droplet.neighbors) == 1:
                    idx = droplet.neighbors.pop()
                    self.blocks[idx] = np.frombuffer(droplet.data, dtype=np.uint8)
                    self.decoded[idx] = True
                    decoded_idx = idx
                    changed = True
                else:
                    remaining.append(droplet)
            self.droplets = remaining
    
    def is_complete(self) -> bool:
        """Check if all blocks have been decoded."""
        if self.decoded is None:
            return False
        return bool(np.all(self.decoded))
    
    def get_progress(self) -> float:
        """Get decoding progress as a fraction."""
        if self.decoded is None:
            return 0.0
        return float(np.sum(self.decoded)) / len(self.decoded)
    
    def get_data(self) -> bytes:
        """Reconstruct the original data."""
        if not self.is_complete():
            raise ValueError("Decoding not complete")
        
        raw = self.blocks.tobytes()
        original_length = struct.unpack('>I', raw[:4])[0]
        return raw[4:4 + original_length]

# ═══════════════════════════════════════════════════════════════════════════════
# QR CODE LAYER
# ═══════════════════════════════════════════════════════════════════════════════

class QRGenerator:
    """High-performance QR code generator for streaming."""
    
    def __init__(self, version: int = QR_VERSION, box_size: int = 10, border: int = 2):
        self.version = version
        self.box_size = box_size
        self.border = border
    
    def generate(self, data: bytes) -> np.ndarray:
        """Generate a QR code image as numpy array."""
        qr = qrcode.QRCode(
            version=self.version,
            error_correction=QR_ERROR_CORRECTION,
            box_size=self.box_size,
            border=self.border
        )
        qr.add_data(data)
        qr.make(fit=False)
        
        img = qr.make_image(fill_color="black", back_color="white")
        return np.array(img.convert('RGB'))


class QRScanner:
    """Camera-based QR code scanner with multiple backends."""
    
    def __init__(self, camera_idx: int = 0):
        self.camera_idx = camera_idx
        self.cap: Optional[cv2.VideoCapture] = None
        self.detector = cv2.QRCodeDetector()
        self.use_pyzbar = PYZBAR_AVAILABLE
    
    def open(self) -> bool:
        """Open the camera."""
        self.cap = cv2.VideoCapture(self.camera_idx)
        if not self.cap.isOpened():
            return False
        # Optimize for speed
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        if self.use_pyzbar:
            # Test if libzbar is actually functional
            try:
                pyzbar.decode(np.zeros((100, 100, 3), dtype=np.uint8))
            except Exception:
                print("[!] pyzbar found but libzbar is missing. Falling back to OpenCV detector.")
                self.use_pyzbar = False
        
        return True
    
    def read_frame(self) -> Tuple[bool, Optional[np.ndarray]]:
        """Read a frame from the camera."""
        if self.cap is None:
            return False, None
        return self.cap.read()
    
    def decode_qr(self, frame: np.ndarray) -> Optional[bytes]:
        """Decode QR code from frame using available backends."""
        if self.use_pyzbar:
            decoded = pyzbar.decode(frame)
            for obj in decoded:
                if obj.type == 'QRCODE':
                    return obj.data
        
        # Fallback to OpenCV
        data, points, _ = self.detector.detectAndDecode(frame)
        if data:
            if isinstance(data, str):
                return data.encode('latin1') # QR data can be binary
            return data
            
        return None
    
    def close(self):
        """Release the camera."""
        if self.cap is not None:
            self.cap.release()
            self.cap = None

# ═══════════════════════════════════════════════════════════════════════════════
# AFTERIMAGE MAIN CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class AfterImage:
    """
    Optical Air-Gap Data Exfiltration Protocol.
    
    Transmits files securely over visible light (QR stream) with
    fountain code FEC for resilience against frame drops.
    """
    
    MAGIC = b'AFTI'  # Protocol magic bytes
    VERSION = 1
    
    def __init__(self, password: str):
        self.password = password
        self.qr_gen = QRGenerator()
    
    def tx(self, filepath: str):
        """Transmit a file as a QR stream."""
        print(f"\n{'═' * 60}")
        print("  AFTERIMAGE TRANSMITTER")
        print(f"{'═' * 60}\n")
        
        # Load and prepare data
        print(f"[*] Loading file: {filepath}")
        with open(filepath, 'rb') as f:
            raw_data = f.read()
        
        original_size = len(raw_data)
        print(f"[*] Original size: {original_size:,} bytes")
        
        # Compress
        print("[*] Compressing...")
        compressed = zlib.compress(raw_data, level=9)
        print(f"[*] Compressed size: {len(compressed):,} bytes ({100*len(compressed)/original_size:.1f}%)")
        
        # Encrypt
        print("[*] Encrypting...")
        encrypted = CryptoLayer.encrypt(compressed, self.password)
        print(f"[*] Encrypted size: {len(encrypted):,} bytes")
        
        # Create fountain encoder
        encoder = LTEncoder(encrypted)
        k = encoder.k
        recommended = encoder.get_recommended_count()
        
        print(f"[*] Source blocks: {k}")
        print(f"[*] Recommended droplets: {recommended}")
        
        # Prepare metadata packet (first QR)
        filename = os.path.basename(filepath)
        metadata = struct.pack('>4sBII', self.MAGIC, self.VERSION, k, original_size)
        metadata += filename.encode('utf-8')[:64].ljust(64, b'\x00')
        
        print(f"\n[*] Starting transmission at {TARGET_FPS} FPS")
        print("[*] Press 'q' to quit\n")
        
        # Create fullscreen window
        cv2.namedWindow('AFTERIMAGE TX', cv2.WINDOW_NORMAL)
        cv2.setWindowProperty('AFTERIMAGE TX', cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        
        frame_delay = int(1000 / TARGET_FPS)
        droplet_idx = 0
        start_time = time.time()
        
        try:
            while True:
                # Alternate between metadata and droplets
                if droplet_idx % 50 == 0:
                    # Send metadata periodically
                    packet = metadata
                    packet_type = "META"
                else:
                    packet = encoder.generate_droplet()
                    packet_type = "DROP"
                
                # Generate QR
                qr_img = self.qr_gen.generate(packet)
                
                # Add status overlay
                elapsed = time.time() - start_time
                status = f"AFTERIMAGE TX | {packet_type} #{droplet_idx} | {elapsed:.1f}s"
                cv2.putText(qr_img, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 
                           0.7, (0, 100, 0), 2)
                
                cv2.imshow('AFTERIMAGE TX', qr_img)
                
                key = cv2.waitKey(frame_delay) & 0xFF
                if key == ord('q'):
                    break
                
                droplet_idx += 1
                
                # Progress indicator
                if droplet_idx % 100 == 0:
                    print(f"[TX] Sent {droplet_idx} droplets ({droplet_idx/recommended*100:.0f}% of recommended)")
        
        finally:
            cv2.destroyAllWindows()
            print(f"\n[*] Transmission ended. Sent {droplet_idx} droplets.")
    
    def rx(self, output_path: str, camera_idx: int = 0):
        """Receive a file from QR stream."""
        print(f"\n{'═' * 60}")
        print("  AFTERIMAGE RECEIVER")
        print(f"{'═' * 60}\n")
        
        scanner = QRScanner(camera_idx)
        decoder = LTDecoder()
        
        if not scanner.open():
            print(f"[!] Failed to open camera {camera_idx}")
            return False
        
        print(f"[*] Camera {camera_idx} opened")
        print("[*] Waiting for AFTERIMAGE stream...")
        print("[*] Press 'q' to quit\n")
        
        # Create viewfinder window
        cv2.namedWindow('AFTERIMAGE RX', cv2.WINDOW_NORMAL)
        
        metadata_received = False
        k = 0
        original_size = 0
        filename = ""
        droplets_received = 0
        start_time = None
        last_decode_time = time.time()
        
        try:
            while True:
                ret, frame = scanner.read_frame()
                if not ret:
                    continue
                
                # Try to decode QR
                qr_data = scanner.decode_qr(frame)
                
                # Create display frame
                display = frame.copy()
                
                if qr_data:
                    last_decode_time = time.time()
                    
                    # Check if metadata packet
                    if qr_data[:4] == self.MAGIC:
                        if not metadata_received:
                            # Parse metadata
                            magic, version, k_val, orig_size = struct.unpack('>4sBII', qr_data[:13])
                            fname = qr_data[13:77].rstrip(b'\x00').decode('utf-8')
                            
                            if version != self.VERSION:
                                print(f"[!] Version mismatch: {version}")
                                continue
                            
                            k = k_val
                            original_size = orig_size
                            filename = fname
                            decoder.set_block_count(k)
                            metadata_received = True
                            start_time = time.time()
                            
                            print(f"[*] Stream detected!")
                            print(f"[*] File: {filename}")
                            print(f"[*] Original size: {original_size:,} bytes")
                            print(f"[*] Source blocks: {k}\n")
                    
                    elif metadata_received:
                        # Process droplet
                        complete = decoder.add_droplet(qr_data)
                        droplets_received += 1
                        
                        if complete:
                            break
                
                # Draw status overlay
                if metadata_received:
                    progress = decoder.get_progress()
                    bar_width = 400
                    bar_height = 30
                    x, y = 50, 50
                    
                    # Background
                    cv2.rectangle(display, (x, y), (x + bar_width, y + bar_height), (40, 40, 40), -1)
                    # Progress
                    cv2.rectangle(display, (x, y), (x + int(bar_width * progress), y + bar_height), (0, 255, 0), -1)
                    # Border
                    cv2.rectangle(display, (x, y), (x + bar_width, y + bar_height), (255, 255, 255), 2)
                    
                    # Text
                    status = f"AFTERIMAGE RX | {progress*100:.1f}% | {droplets_received} droplets"
                    cv2.putText(display, status, (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                    cv2.putText(display, f"File: {filename}", (x, y + bar_height + 25), 
                               cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
                else:
                    # Waiting state
                    cv2.putText(display, "AFTERIMAGE RX | Scanning...", (50, 50), 
                               cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)
                    
                    # Signal indicator
                    signal_age = time.time() - last_decode_time
                    if signal_age < 0.5:
                        cv2.circle(display, (frame.shape[1] - 50, 50), 20, (0, 255, 0), -1)
                    else:
                        cv2.circle(display, (frame.shape[1] - 50, 50), 20, (0, 0, 255), -1)
                
                cv2.imshow('AFTERIMAGE RX', display)
                
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    print("[*] Aborted by user")
                    return False
        
        finally:
            scanner.close()
            cv2.destroyAllWindows()
        
        # Decode successful
        elapsed = time.time() - start_time if start_time else 0
        print(f"\n[*] Decoding complete!")
        print(f"[*] Received {droplets_received} droplets in {elapsed:.1f}s")
        
        # Reconstruct data
        print("[*] Reconstructing file...")
        try:
            encrypted = decoder.get_data()
            print(f"[*] Encrypted size: {len(encrypted):,} bytes")
            
            # Decrypt
            print("[*] Decrypting...")
            compressed = CryptoLayer.decrypt(encrypted, self.password)
            print(f"[*] Compressed size: {len(compressed):,} bytes")
            
            # Decompress
            print("[*] Decompressing...")
            raw_data = zlib.decompress(compressed)
            print(f"[*] Final size: {len(raw_data):,} bytes")
            
            # Verify size
            if len(raw_data) != original_size:
                print(f"[!] Size mismatch! Expected {original_size}, got {len(raw_data)}")
            
            # Save
            with open(output_path, 'wb') as f:
                f.write(raw_data)
            
            print(f"\n[✓] File saved to: {output_path}")
            return True
            
        except Exception as e:
            print(f"[!] Reconstruction failed: {e}")
            return False

# ═══════════════════════════════════════════════════════════════════════════════
# CLI INTERFACE
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='AFTERIMAGE - Optical Air-Gap Data Exfiltration Protocol',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Transmit:  python afterimage.py --tx secret.zip --password hunter2
  Receive:   python afterimage.py --rx output.zip --password hunter2 --camera 0
        """
    )
    
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--tx', metavar='FILE', help='Transmit file (sender mode)')
    mode.add_argument('--rx', metavar='OUTPUT', help='Receive to file (receiver mode)')
    
    parser.add_argument('--password', '-p', required=True, help='Encryption password')
    parser.add_argument('--camera', '-c', type=int, default=0, help='Camera index (default: 0)')
    
    args = parser.parse_args()
    
    afterimage = AfterImage(args.password)
    
    if args.tx:
        if not os.path.isfile(args.tx):
            print(f"[!] File not found: {args.tx}")
            sys.exit(1)
        afterimage.tx(args.tx)
    else:
        success = afterimage.rx(args.rx, args.camera)
        sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
