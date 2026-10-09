import json
import lzma
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from tibia_loot_manager.sources import sprites

SIZE = sprites.SHEET_SIZE


def make_bmp(paint) -> bytes:
    """A 384×384 bottom-up 32-bit BMP (BGRA, BITFIELDS) like the client's sheets. paint(x, y) -> (r, g, b, a)."""
    rows = []
    for y in range(SIZE - 1, -1, -1):  # bottom-up
        rows.append(b"".join(bytes((b, g, r, a)) for r, g, b, a in (paint(x, y) for x in range(SIZE))))
    pixels = b"".join(rows)
    dib = struct.pack("<IiiHHIIiiII", 108, SIZE, SIZE, 1, 32, 3, len(pixels), 2835, 2835, 0, 0)
    dib += struct.pack("<IIII", 0xFF0000, 0xFF00, 0xFF, 0xFF000000) + b"\x00" * 52
    header = b"BM" + struct.pack("<IHHI", 14 + len(dib) + len(pixels), 0, 0, 14 + len(dib))
    return header + dib + pixels


def pack_sheet(bmp: bytes) -> bytes:
    """Wrap a BMP the way the client stores sprite sheets (CIP header + raw LZMA1)."""
    data = lzma.compress(bmp, format=lzma.FORMAT_RAW,
                         filters=[{"id": lzma.FILTER_LZMA1, "dict_size": 1 << 20, "lc": 3, "lp": 0, "pb": 2}])
    size, varint = len(data), b""
    while True:
        byte = size & 0x7F
        size >>= 7
        varint += bytes([byte | (0x80 if size else 0)])
        if not size:
            break
    return (b"\x00" * 3 + bytes([0x70, 0x0A, 0xFA, 0x80, 0x24]) + varint + bytes([0x5D])
            + struct.pack("<I", 1 << 20) + struct.pack("<Q", len(data)) + data)


def red_block(cell_x0, colour=(255, 0, 0, 255)):
    """Paint a 4×3 block at (10..13, 5..7) inside the 32×32 cell starting at x = cell_x0."""
    def paint(x, y):
        return colour if cell_x0 + 10 <= x < cell_x0 + 14 and 5 <= y < 8 else (0, 0, 0, 0)
    return paint


def read_png(data: bytes) -> dict:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, []
    while pos < len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        assert struct.unpack_from(">I", data, pos + 8 + length)[0] == zlib.crc32(kind + body)
        chunks.append((kind, body))
        pos += 12 + length
    w, h = struct.unpack(">II", chunks[0][1][:8])
    return {"w": w, "h": h, "kinds": [k for k, _ in chunks], "chunks": chunks}


class SheetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        assets = Path(self.tmp.name) / "assets"
        assets.mkdir()
        # sheet A: sprite 100 empty, 101 red block, 102 the same block in green (an animation frame)
        def paint(x, y):
            if 32 <= x < 64:
                return red_block(32)(x, y)
            if 64 <= x < 96:
                return red_block(64, (0, 255, 0, 255))(x, y)
            return (0, 0, 0, 0)
        (assets / "a.bmp.lzma").write_bytes(pack_sheet(make_bmp(paint)))
        (assets / "catalog-content.json").write_text(json.dumps([
            {"type": "sprite", "file": "a.bmp.lzma", "spritetype": 0, "firstspriteid": 100, "lastspriteid": 243}]))
        self.root = Path(self.tmp.name)
        self.sheets = sprites.SpriteSheets(assets)

    def tearDown(self):
        self.tmp.cleanup()

    def test_crop_reads_bgra_bottom_up(self):
        w, h, px = self.sheets.cell(101)
        self.assertEqual((w, h), (32, 32))
        i = (5 * 32 + 10) * 4
        self.assertEqual(tuple(px[i:i + 4]), (255, 0, 0, 255))
        self.assertEqual(sprites.content_box([(w, h, px)]), (10, 5, 14, 8))

    def test_png_is_trimmed_to_visible_pixels(self):
        png = read_png(sprites.render_item(self.sheets, {"ids": [101], "durations": []}))
        self.assertEqual((png["w"], png["h"]), (4, 3))
        rows = zlib.decompress(dict(png["chunks"])[b"IDAT"])
        self.assertEqual(rows[:5], b"\x00\xff\x00\x00\xff")  # filter byte, then a red opaque pixel
        self.assertNotIn(b"acTL", png["kinds"])

    def test_animation_becomes_apng(self):
        png = read_png(sprites.render_item(self.sheets, {"ids": [101, 102], "durations": [150, 150]}))
        actl = dict(png["chunks"])[b"acTL"]
        self.assertEqual(struct.unpack(">II", actl), (2, 0))  # two frames, loop forever
        self.assertEqual(png["kinds"].count(b"fcTL"), 2)
        self.assertEqual(png["kinds"].count(b"fdAT"), 1)

    def test_empty_sprite_and_unknown_ids(self):
        self.assertIsNone(sprites.render_item(self.sheets, {"ids": [100]}))
        with self.assertRaises(sprites.SpriteError):
            self.sheets.cell(5000)

    def test_bad_header_rejected(self):
        with self.assertRaises(sprites.SpriteError):
            sprites.decode_sheet(b"\x00\x00not a sheet")

    def test_concurrent_requests_for_one_image(self):
        import threading
        images = sprites.ItemImages(self.root, self.root / "cache", "v2")
        results = []
        threads = [threading.Thread(target=lambda: results.append(images.image(8, {"ids": [101]}))) for _ in range(16)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(results), 16)
        self.assertTrue(all(results))  # regression: concurrent renders used to fail on Windows
        self.assertEqual(len(set(results)), 1)
        self.assertEqual([p.name for p in (self.root / "cache" / "v2").iterdir()], ["8.png"])

    def test_disk_cache(self):
        images = sprites.ItemImages(self.root, self.root / "cache", "v1")
        first = images.image(7, {"ids": [101]})
        self.assertTrue((self.root / "cache" / "v1" / "7.png").is_file())
        self.assertEqual(images.image(7, None), first)  # served from disk without the sprite record


if __name__ == "__main__":
    unittest.main()
