"""Item images from the installed Tibia client's own sprite sheets.

Sheets are listed in ``assets/catalog-content.json`` (type ``sprite``) and
stored as ``*.bmp.lzma``: a few zero bytes, the marker ``70 0A FA 80 24``, a
7-bit encoded size, a classic LZMA header (properties byte, dictionary size,
8-byte size) and a raw LZMA1 stream. Inside is a 384×384 32-bit BMP holding a
grid of 32×32, 32×64, 64×32 or 64×64 sprites (``spritetype`` 0–3).

Images are produced on this computer from the user's own client and cached in
the app data folder; none are bundled with the app. Animated items become
animated PNGs (APNG), which the app window plays like a GIF. Everything here
uses the standard library only.
"""

import bisect
import json
import lzma
import struct
import threading
import zlib
from collections import OrderedDict
from pathlib import Path

SHEET_SIZE = 384
CELL_SIZES = {0: (32, 32), 1: (32, 64), 2: (64, 32), 3: (64, 64)}
_MARKER = bytes([0x70, 0x0A, 0xFA, 0x80, 0x24])


class SpriteError(Exception):
    pass


def decode_sheet(blob: bytes) -> bytes:
    """Return the BMP inside one ``.bmp.lzma`` sprite sheet."""
    pos = 0
    while pos < len(blob) and blob[pos] == 0:
        pos += 1
    if blob[pos:pos + 5] != _MARKER:
        raise SpriteError("Unknown sprite sheet header")
    pos += 5
    while blob[pos] & 0x80:  # 7-bit encoded size (not needed)
        pos += 1
    pos += 1
    props = blob[pos]
    dict_size = struct.unpack_from("<I", blob, pos + 1)[0]
    pos += 5 + 8
    lc, lp, pb = props % 9, (props // 9) % 5, props // 45
    try:
        decompressor = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[
            {"id": lzma.FILTER_LZMA1, "dict_size": dict_size, "lc": lc, "lp": lp, "pb": pb}])
        bmp = decompressor.decompress(blob[pos:])
    except lzma.LZMAError as e:
        raise SpriteError(f"Sprite sheet could not be decompressed: {e}") from None
    if bmp[:2] != b"BM":
        raise SpriteError("Sprite sheet is not a BMP image")
    return bmp


def _channel_shift(mask: int) -> int:
    return (mask & -mask).bit_length() - 1 if mask else 0


def crop_cell(bmp: bytes, sprite_type: int, index: int) -> tuple[int, int, bytearray]:
    """Cut one sprite out of a decoded sheet as (width, height, RGBA bytes)."""
    if sprite_type not in CELL_SIZES:
        raise SpriteError(f"Unknown sprite type {sprite_type}")
    offset = struct.unpack_from("<I", bmp, 10)[0]
    width, height, _planes, bpp = struct.unpack_from("<iiHH", bmp, 18)
    if (width, abs(height), bpp) != (SHEET_SIZE, SHEET_SIZE, 32):
        raise SpriteError("Unexpected sprite sheet layout")
    compression = struct.unpack_from("<I", bmp, 30)[0]
    masks = struct.unpack_from("<IIII", bmp, 54) if compression == 3 else (0xFF0000, 0xFF00, 0xFF, 0xFF000000)
    w, h = CELL_SIZES[sprite_type]
    cols = SHEET_SIZE // w
    x0, y0 = (index % cols) * w, (index // cols) * h
    if y0 + h > SHEET_SIZE:
        raise SpriteError("Sprite index outside its sheet")
    bottom_up = height > 0
    stride = SHEET_SIZE * 4
    out = bytearray(w * h * 4)
    standard = masks == (0xFF0000, 0xFF00, 0xFF, 0xFF000000)  # stored as B, G, R, A bytes
    shifts = [_channel_shift(m) for m in masks]
    for y in range(h):
        row = (SHEET_SIZE - 1 - (y0 + y)) if bottom_up else (y0 + y)
        start = offset + row * stride + x0 * 4
        src = bmp[start:start + w * 4]
        dst = y * w * 4
        if standard:
            out[dst + 0:dst + w * 4:4] = src[2::4]
            out[dst + 1:dst + w * 4:4] = src[1::4]
            out[dst + 2:dst + w * 4:4] = src[0::4]
            out[dst + 3:dst + w * 4:4] = src[3::4]
        else:
            for x in range(w):
                v = struct.unpack_from("<I", src, x * 4)[0]
                for c in range(4):
                    out[dst + x * 4 + c] = ((v & masks[c]) >> shifts[c]) & 0xFF if masks[c] else 255
    return w, h, out


def content_box(frames: list[tuple[int, int, bytearray]]) -> tuple[int, int, int, int] | None:
    """Smallest box (x0, y0, x1, y1) holding every non-transparent pixel of all frames."""
    x0 = y0 = 10 ** 9
    x1 = y1 = -1
    for w, h, px in frames:
        alpha = px[3::4]
        for y in range(h):
            row = alpha[y * w:(y + 1) * w]
            if not any(row):
                continue
            first = next(i for i, a in enumerate(row) if a)
            last = w - 1 - next(i for i, a in enumerate(reversed(row)) if a)
            x0, x1, y0, y1 = min(x0, first), max(x1, last), min(y0, y), max(y1, y)
    return None if x1 < 0 else (x0, y0, x1 + 1, y1 + 1)


def crop(frame: tuple[int, int, bytearray], box: tuple[int, int, int, int]) -> bytes:
    w, _h, px = frame
    x0, y0, x1, y1 = box
    return b"".join(bytes(px[(y * w + x0) * 4:(y * w + x1) * 4]) for y in range(y0, y1))


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _idat(w: int, h: int, rgba: bytes) -> bytes:
    rows = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))
    return zlib.compress(rows, 9)


def encode_png(w: int, h: int, frames: list[bytes], durations: list[int] | None = None) -> bytes:
    """A PNG, or an animated PNG (APNG) when there is more than one frame."""
    head = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    if len(frames) == 1:
        return head + _chunk(b"IDAT", _idat(w, h, frames[0])) + _chunk(b"IEND", b"")
    durations = durations or [100] * len(frames)
    out = [head, _chunk(b"acTL", struct.pack(">II", len(frames), 0))]
    seq = 0
    for i, (frame, ms) in enumerate(zip(frames, durations)):
        out.append(_chunk(b"fcTL", struct.pack(">IIIIIHHBB", seq, w, h, 0, 0, min(ms, 65535), 1000, 1, 0)))
        seq += 1
        data = _idat(w, h, frame)
        if i == 0:
            out.append(_chunk(b"IDAT", data))
        else:
            out.append(_chunk(b"fdAT", struct.pack(">I", seq) + data))
            seq += 1
    out.append(_chunk(b"IEND", b""))
    return b"".join(out)


class SpriteSheets:
    """Find and decode the sheet holding a sprite ID; keeps recently used sheets in memory."""

    def __init__(self, assets_dir: Path, keep: int = 24):
        self.assets_dir = Path(assets_dir)
        try:
            entries = json.loads((self.assets_dir / "catalog-content.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise SpriteError(f"Client asset catalog unavailable: {e}") from None
        sheets = sorted((e["firstspriteid"], e["lastspriteid"], e.get("spritetype", 0), e["file"])
                        for e in entries if isinstance(e, dict) and e.get("type") == "sprite")
        self._sheets = sheets
        self._firsts = [s[0] for s in sheets]
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._keep = keep
        self._lock = threading.Lock()

    def cell(self, sprite_id: int) -> tuple[int, int, bytearray]:
        k = bisect.bisect_right(self._firsts, sprite_id) - 1
        if k < 0 or not (self._sheets[k][0] <= sprite_id <= self._sheets[k][1]):
            raise SpriteError(f"Sprite {sprite_id} is not in any sheet")
        first, _last, sprite_type, name = self._sheets[k]
        with self._lock:
            bmp = self._cache.get(name)
            if bmp is None:
                try:
                    bmp = decode_sheet((self.assets_dir / name).read_bytes())
                except OSError as e:
                    raise SpriteError(f"Sprite sheet missing: {e}") from None
                self._cache[name] = bmp
                while len(self._cache) > self._keep:
                    self._cache.popitem(last=False)
            else:
                self._cache.move_to_end(name)
        return crop_cell(bmp, sprite_type, sprite_id - first)


def render_item(sheets: SpriteSheets, sprite: dict) -> bytes | None:
    """PNG (or APNG) bytes for an item's ``sprite`` record, trimmed to its visible pixels."""
    ids = sprite.get("ids") or []
    if not ids:
        return None
    frames = [sheets.cell(i) for i in ids]
    sizes = {(w, h) for w, h, _ in frames}
    if len(sizes) > 1:  # animation frames should share a size; fall back to the first frame
        frames = frames[:1]
    box = content_box(frames)
    if box is None:
        return None
    w, h = box[2] - box[0], box[3] - box[1]
    durations = sprite.get("durations") if len(frames) > 1 else None
    return encode_png(w, h, [crop(f, box) for f in frames], durations)


class ItemImages:
    """Item images on demand, cached on disk per client version."""

    def __init__(self, package_dir: Path, cache_root: Path, version_tag: str):
        self.sheets = SpriteSheets(Path(package_dir) / "assets")
        safe_tag = "".join(c for c in version_tag if c.isalnum() or c in "._-")[:40] or "unknown"
        self.cache_dir = Path(cache_root) / safe_tag
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def image(self, client_id: int, sprite: dict | None) -> bytes | None:
        path = self.cache_dir / f"{client_id}.png"
        try:
            return path.read_bytes()
        except FileNotFoundError:
            pass
        if not sprite:
            return None
        data = render_item(self.sheets, sprite)
        if data:
            tmp = path.with_name(f"{client_id}.{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        return data
