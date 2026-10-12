#!/usr/bin/env python3
"""JARVIS.app용 간단 HUD 아이콘 PNG 생성 (의존성 없음)."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def write_png(path: Path, size: int = 512) -> None:
    """아크 리액터 스타일 원형 아이콘 (진한 배경 + 시안 링)."""
    cx = cy = (size - 1) / 2.0
    r_outer = size * 0.38
    r_mid = size * 0.28
    r_core = size * 0.12
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            dx, dy = x - cx, y - cy
            d = (dx * dx + dy * dy) ** 0.5
            if d <= r_core:
                r, g, b = 180, 240, 255
            elif d <= r_mid:
                t = (d - r_core) / max(r_mid - r_core, 1)
                r = int(20 + t * 10)
                g = int(180 - t * 40)
                b = int(255 - t * 30)
            elif d <= r_outer:
                t = (d - r_mid) / max(r_outer - r_mid, 1)
                r = int(8 + t * 12)
                g = int(140 - t * 80)
                b = int(220 - t * 100)
            elif d <= r_outer + 3:
                r, g, b = 0, 200, 255
            else:
                r, g, b = 12, 18, 32
            row.extend((r, g, b))
        rows.append(bytes(row))
    raw = b"".join(rows)
    compressed = zlib.compress(raw, 9)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n"
    png += _png_chunk(b"IHDR", ihdr)
    png += _png_chunk(b"IDAT", compressed)
    png += _png_chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def build_icns(png_path: Path, icns_path: Path) -> bool:
    import shutil
    import subprocess
    import tempfile

    iconset = Path(tempfile.mkdtemp(suffix=".iconset"))
    try:
        sizes = [16, 32, 64, 128, 256, 512]
        for s in sizes:
            out = iconset / f"icon_{s}x{s}.png"
            subprocess.run(
                ["sips", "-z", str(s), str(s), str(png_path), "--out", str(out)],
                check=True, capture_output=True,
            )
            if s <= 32:
                out2 = iconset / f"icon_{s}x{s}@2x.png"
                subprocess.run(
                    ["sips", "-z", str(s * 2), str(s * 2), str(png_path), "--out", str(out2)],
                    check=True, capture_output=True,
                )
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(icns_path)],
            check=True, capture_output=True,
        )
        return icns_path.is_file()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    finally:
        shutil.rmtree(iconset, ignore_errors=True)


if __name__ == "__main__":
    import sys

    root = Path(__file__).resolve().parent.parent
    assets = root / "assets"
    png = assets / "jarvis_icon_512.png"
    icns = assets / "AppIcon.icns"
    write_png(png)
    if build_icns(png, icns):
        print(f"✓ {icns}")
        sys.exit(0)
    print("iconutil/sips 실패 — PNG만 생성됨", file=sys.stderr)
    sys.exit(1)
