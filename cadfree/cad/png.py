"""Minimal 8-bit PNG encode/decode. Stdlib zlib only — no Pillow required.

Used for silhouette snapshots. Photos in JPEG/WebP still need Pillow; that
path names the missing install instead of inventing pixels.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

_SIG = b"\x89PNG\r\n\x1a\n"


def write_gray_png(path: Path, pixels: np.ndarray) -> None:
    """Write an 8-bit grayscale PNG. `pixels` is HxW uint8."""
    arr = np.asarray(pixels, dtype=np.uint8)
    if arr.ndim != 2:
        raise ValueError("write_gray_png expects a 2-D array")
    h, w = arr.shape
    raw = b"".join(b"\x00" + arr[y].tobytes() for y in range(h))
    compressed = zlib.compress(raw, 9)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    path = Path(path)
    path.write_bytes(_SIG + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b""))


def read_png(data: bytes) -> np.ndarray:
    """Return HxW float luminance in [0, 1]. Supports 8-bit gray/RGB/RGBA."""
    if not data.startswith(_SIG):
        raise ValueError("not a PNG")
    off = 8
    width = height = bit_depth = color_type = None
    idat = bytearray()
    while off + 12 <= len(data):
        length = struct.unpack_from(">I", data, off)[0]
        tag = data[off + 4 : off + 8]
        payload = data[off + 8 : off + 8 + length]
        off += 12 + length
        if tag == b"IHDR":
            width, height, bit_depth, color_type, compression, filt, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            if compression != 0 or filt != 0 or interlace != 0:
                raise ValueError("PNG compression/filter/interlace not supported")
            if bit_depth != 8:
                raise ValueError("only 8-bit PNG is supported without Pillow")
            if color_type not in (0, 2, 4, 6):
                raise ValueError(f"PNG color type {color_type} is not supported")
        elif tag == b"IDAT":
            idat.extend(payload)
        elif tag == b"IEND":
            break
    if width is None or height is None or color_type is None:
        raise ValueError("PNG missing IHDR")
    raw = zlib.decompress(bytes(idat))
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    stride = width * channels
    rows = []
    prev = bytearray(stride)
    cursor = 0
    for _ in range(height):
        if cursor + 1 + stride > len(raw):
            raise ValueError("truncated PNG scanline")
        ftype = raw[cursor]
        scan = bytearray(raw[cursor + 1 : cursor + 1 + stride])
        cursor += 1 + stride
        recon = _unfilter(ftype, scan, prev, channels)
        rows.append(recon)
        prev = recon
    img = np.frombuffer(b"".join(rows), dtype=np.uint8).reshape(height, width, channels)
    if channels in (1, 2):
        lum = img[:, :, 0]
    else:
        rgb = img[:, :, :3].astype(np.float32)
        lum = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    return np.clip(lum.astype(np.float32) / 255.0, 0.0, 1.0)


def _chunk(tag: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)


def _unfilter(ftype: int, scan: bytearray, prev: bytearray, bpp: int) -> bytearray:
    out = bytearray(len(scan))
    if ftype == 0:
        return bytearray(scan)
    for i, val in enumerate(scan):
        a = out[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        if ftype == 1:
            out[i] = (val + a) & 255
        elif ftype == 2:
            out[i] = (val + b) & 255
        elif ftype == 3:
            out[i] = (val + ((a + b) // 2)) & 255
        elif ftype == 4:
            out[i] = (val + _paeth(a, b, c)) & 255
        else:
            raise ValueError(f"unsupported PNG filter {ftype}")
    return out


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c
