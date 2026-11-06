# -*- coding: utf-8 -*-
"""
工作流信息写入
------------------------------------------------------------------
PNG：写标准 tEXt 块（与 ComfyUI 官方 SaveImage 一致，图片拖回 ComfyUI 可还原工作流）
JPG：写 COM 注释段（体积小、不影响画质，但不是所有看图软件都显示）

全部是字节级注入，不重新编码像素，画质零损失。
"""

import json
import struct
import zlib


def build_meta(prompt, extra_pnginfo):
    """整理成 ComfyUI 兼容的键值对。"""
    meta = {}
    try:
        if extra_pnginfo:
            for key, value in extra_pnginfo.items():
                meta[str(key)] = json.dumps(value)
        if prompt is not None:
            meta["prompt"] = json.dumps(prompt)
    except Exception as e:
        print(f"[GULU工具箱] 工作流信息整理失败（已忽略）：{e}")
    return meta


def embed(data, ext, meta):
    """把元数据写进已编码好的图片字节；失败时原样返回。"""
    if not meta or not data:
        return data, False
    e = (ext or "").lower().lstrip(".")
    try:
        if e == "png":
            return _png(data, meta), True
        if e in ("jpg", "jpeg"):
            return _jpeg(data, meta), True
    except Exception as ex:
        print(f"[GULU工具箱] 写入工作流信息失败（{e}，已按原样保存）：{ex}")
    return data, False


# ------------------------------------------------------------------ PNG

def _text_chunk(key, text):
    """
    生成一个文本块。

    PNG 规范里 tEXt 只允许 latin-1，中文写进去会被 Pillow 按 latin-1 读成乱码。
    所以按内容自动选：纯 ASCII 走 tEXt（兼容老流程），含中文走 iTXt（UTF-8，规范做法）。
    两种块 Pillow / ComfyUI / 常见看图工具都能读。
    """
    k = key.encode("latin-1", "replace")
    try:
        body = k + b"\x00" + text.encode("latin-1")
        chunk_type = b"tEXt"
    except UnicodeEncodeError:
        # iTXt: keyword \0 | 压缩标志 | 压缩方法 | 语言标签 \0 | 译名 \0 | UTF-8 正文
        body = k + b"\x00" + b"\x00\x00" + b"\x00" + b"\x00" + text.encode("utf-8")
        chunk_type = b"iTXt"
    crc = zlib.crc32(chunk_type + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + chunk_type + body + struct.pack(">I", crc)


def _png(data, meta):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return data
    blocks = b"".join(_text_chunk(k, v) for k, v in meta.items())

    # 插到第一个 IDAT 之前 —— 惯例位置，各种解析器都认
    pos = 8
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        if data[pos + 4:pos + 8] == b"IDAT":
            break
        pos += 12 + length
    else:
        pos = data.rfind(b"\x00\x00\x00\x00IEND")
        if pos < 0:
            return data
    return data[:pos] + blocks + data[pos:]


# ------------------------------------------------------------------ JPG

def _jpeg(data, meta):
    if not data.startswith(b"\xff\xd8"):
        return data
    out = bytearray(data[:2])
    for key, value in meta.items():
        buf = f"{key}={value}".encode("utf-8", "replace")
        for i in range(0, len(buf), 65531):   # 单段上限，超长拆段
            part = buf[i:i + 65531]
            out += b"\xff\xfe" + struct.pack(">H", len(part) + 2) + part
    out += data[2:]
    return bytes(out)
