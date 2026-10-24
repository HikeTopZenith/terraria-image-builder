"""Terraria 原版贴图 .xnb → PNG 导出工具。

容器布局（对 765 个 Tiles_*.xnb 全量实测，与 XNA/FNA 的 XNB 口径一致）：
    [0..2]   b'XNB'
    [3]      平台标识 b'w'
    [4]      容器版本 5
    [5]      标志位 0x80 = LZX 压缩
    [6..9]   u32 文件总字节数（含 10 字节头）
    [10..13] u32 解压后字节数
    [14..]   帧序列：2 字节大端“本帧压缩字节数” + 该长度的 LZX 数据；
             末帧改用 3 字节头：0xFF + u16 大端“本帧解压字节数”，
             压缩数据长度取文件剩余全部字节
解压后 payload：
    7bit 读取器数 / 每项 7bit 长度 + UTF-8 类型名 / 7bit 共享资源数 /
    5 字节固定前缀 00 00 00 00 01 / Texture2D：int32 格式、宽、高、mip 数，
    随后每级 int32 数据长度 + 像素字节

用法：
    python tools/xnb_extract.py                      # 全量导出
    python tools/xnb_extract.py --ids 1,2,53         # 只导指定 tile
    python tools/xnb_extract.py --limit 10 --force   # 前 10 个，覆盖已有
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xnb_lzx import LzxDecoder, LzxError  # noqa: E402

CONTENT_DIR = Path("D:/Steam/steamapps/common/Terraria/Content/Images")
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "assets" / "tiles"
WINDOW_BITS = 16
HEADER_SIZE = 10
FRAME_SIZE = 0x8000
ROOT_PREFIX = b"\x00\x00\x00\x00\x01"

# XNA SurfaceFormat -> 每像素字节数
FORMAT_BPP = {0: 4, 1: 2, 2: 2, 3: 2, 4: 0, 5: 0, 6: 0, 12: 1}


class XnbError(Exception):
    """解码失败：消息里必须带文件名、偏移、期望值、实际值。"""


def _fail(path: Path, offset: int, expect: str, actual: str) -> None:
    raise XnbError(
        "解码失败 %s：偏移 %d 处期望 %s，实际 %s" % (path.name, offset, expect, actual)
    )


def decompress_payload(data: bytes, path: Path) -> bytes:
    """按帧循环做 LZX 解压，返回解压后的完整 payload。"""
    if data[:3] != b"XNB":
        _fail(path, 0, "魔数 b'XNB'", repr(data[:3]))
    if data[4] != 5:
        _fail(path, 4, "容器版本 5", str(data[4]))
    if not data[5] & 0x80:
        _fail(path, 5, "压缩标志 0x80", "0x%02x" % data[5])
    file_size, unpacked = struct.unpack_from("<II", data, 6)
    if file_size != len(data):
        _fail(path, 6, "文件字节数 %d" % len(data), str(file_size))

    dec = LzxDecoder(WINDOW_BITS)
    pos = HEADER_SIZE + 4
    total = 0
    while total < unpacked:
        if pos + 2 > len(data):
            _fail(path, pos, "帧头（2 字节）", "文件已结束")
        if data[pos] == 0xFF:
            if pos + 5 > len(data):
                _fail(path, pos, "5 字节转义帧头", "文件已结束")
            frame = (data[pos + 1] << 8) | data[pos + 2]
            block = (data[pos + 3] << 8) | data[pos + 4]
            pos += 5
        else:
            block = (data[pos] << 8) | data[pos + 1]
            pos += 2
            frame = FRAME_SIZE
        if total + frame > unpacked:
            frame = unpacked - total
        try:
            dec.decompress(data, pos, block, frame)
        except LzxError as exc:
            _fail(path, pos, "可解码的 LZX 数据", str(exc))
        pos += block
        total += frame
    return bytes(dec.out[:unpacked])


def _read_7bit(payload: bytes, pos: int, path: Path) -> tuple:
    value = 0
    shift = 0
    while True:
        if pos >= len(payload):
            _fail(path, pos, "7bit 变长整数", "payload 已结束")
        byte = payload[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos


def parse_texture(payload: bytes, path: Path) -> tuple:
    """解析 payload，返回 (格式, 宽, 高, [(mip宽, mip高, 像素字节)])。"""
    readers, pos = _read_7bit(payload, 0, path)
    names = []
    for _ in range(readers):
        length, pos = _read_7bit(payload, pos, path)
        names.append(payload[pos:pos + length].decode("utf-8", "replace"))
        pos += length
    if readers != 1 or "Texture2DReader" not in names[0]:
        _fail(path, 0, "唯一读取器 Texture2DReader", "读取器 %r" % (names,))
    _, pos = _read_7bit(payload, pos, path)

    prefix = payload[pos:pos + len(ROOT_PREFIX)]
    if prefix != ROOT_PREFIX:
        _fail(path, pos, "根对象前缀 %s" % ROOT_PREFIX.hex(" "), prefix.hex(" "))
    pos += len(ROOT_PREFIX)

    fmt, width, height, mips = struct.unpack_from("<iiii", payload, pos)
    pos += 16
    if fmt not in FORMAT_BPP:
        _fail(path, pos - 16, "已知 SurfaceFormat", str(fmt))
    if not (0 < width <= 8192 and 0 < height <= 8192):
        _fail(path, pos - 12, "宽高在 1..8192", "%dx%d" % (width, height))
    if not 1 <= mips <= 8:
        _fail(path, pos - 4, "mip 级数 1..8", str(mips))

    bpp = FORMAT_BPP[fmt]
    levels = []
    w, h = width, height
    for level in range(mips):
        size = struct.unpack_from("<i", payload, pos)[0]
        pos += 4
        if size != w * h * bpp:
            _fail(path, pos - 4, "mip%d 数据长度 %d" % (level, w * h * bpp), str(size))
        levels.append((w, h, payload[pos:pos + size]))
        pos += size
        w = max(1, w // 2)
        h = max(1, h // 2)
    if pos != len(payload):
        _fail(path, pos, "payload 在此结束（%d 字节）" % len(payload),
              "剩余 %d 字节" % (len(payload) - pos))
    return fmt, width, height, levels


def to_rgba(fmt: int, width: int, height: int, raw: bytes) -> np.ndarray:
    """把一级 mip 的像素字节转成 RGBA8 数组。"""
    if fmt == 0:
        return np.frombuffer(raw, np.uint8).reshape(height, width, 4).copy()
    if fmt == 12:
        alpha = np.frombuffer(raw, np.uint8).reshape(height, width)
        rgba = np.dstack([alpha, alpha, alpha, alpha])
        return rgba.astype(np.uint8)
    word = np.frombuffer(raw, "<u2").reshape(height, width).astype(np.uint32)
    if fmt == 1:  # Bgr565
        r = (word >> 11) & 0x1F
        g = (word >> 5) & 0x3F
        b = word & 0x1F
        out = np.dstack([r * 255 // 31, g * 255 // 63, b * 255 // 31,
                         np.full_like(r, 255)])
    elif fmt == 2:  # Bgra5551
        b = (word >> 10) & 0x1F
        g = (word >> 5) & 0x1F
        r = word & 0x1F
        a = (word >> 15) & 1
        out = np.dstack([r * 255 // 31, g * 255 // 31, b * 255 // 31, a * 255])
    else:  # Bgra4444
        b = (word >> 0) & 0xF
        g = (word >> 4) & 0xF
        r = (word >> 8) & 0xF
        a = (word >> 12) & 0xF
        out = np.dstack([r * 17, g * 17, b * 17, a * 17])
    return out.astype(np.uint8)


def export_one(tile_id: int, out_dir: Path, force: bool = False) -> str:
    """导出单个 tile；返回状态（'skip' / 'ok'）。"""
    src = CONTENT_DIR / ("Tiles_%d.xnb" % tile_id)
    dst = out_dir / ("Tiles_%d.png" % tile_id)
    if not src.exists():
        raise XnbError("源文件不存在：%s" % src)
    if dst.exists() and not force:
        return "skip"
    payload = decompress_payload(src.read_bytes(), src)
    fmt, width, height, levels = parse_texture(payload, src)
    mw, mh, raw = levels[0]
    Image.fromarray(to_rgba(fmt, mw, mh, raw), "RGBA").save(dst)
    return "ok"


def _tile_ids(arg: str | None, limit: int | None) -> list:
    if arg:
        ids = sorted({int(x) for x in arg.split(",") if x.strip()})
    else:
        ids = sorted(int(p.stem.split("_")[1])
                     for p in CONTENT_DIR.glob("Tiles_*.xnb")
                     if p.stem.split("_")[1].isdigit())
    return ids[:limit] if limit else ids


def main(argv: list = None) -> int:
    ap = argparse.ArgumentParser(description="Terraria xnb 贴图批量导出 PNG")
    ap.add_argument("--ids", help="逗号分隔的 tile id，如 1,2,53")
    ap.add_argument("--limit", type=int, help="最多导出前 N 个 tile")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的 PNG")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ids = _tile_ids(args.ids, args.limit)
    if not ids:
        print("没有可导出的 tile", file=sys.stderr)
        return 2

    done = skipped = 0
    failed = []
    for tile_id in ids:
        try:
            if export_one(tile_id, out_dir, args.force) == "ok":
                done += 1
            else:
                skipped += 1
        except XnbError as exc:
            failed.append(str(exc))
        except Exception as exc:  # 未预期异常也要带文件名
            failed.append("解码失败 Tiles_%d.xnb：%s: %s" % (tile_id, type(exc).__name__, exc))
    print("导出 %d 个，跳过 %d 个，失败 %d 个 -> %s" % (done, skipped, len(failed), out_dir))
    for msg in failed[:10]:
        print("  " + msg, file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

