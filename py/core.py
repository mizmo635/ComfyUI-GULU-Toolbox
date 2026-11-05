# -*- coding: utf-8 -*-
"""
图像后处理工具箱 · 公共模块
------------------------------------------------------------------
只依赖 ComfyUI 自带环境（Pillow / numpy / torch），不用装任何额外依赖。

提供：
    · 张量 <-> PIL 互转
    · 水印图加载 / 缩放 / 透明度 / 九宫格定位 / 平铺
    · 按格式与压缩等级编码（PNG / JPG）
    · 保存目录解析、文件名去重、体积格式化
"""

import io
import os
import re
from datetime import datetime, timedelta

import numpy as np
import torch
from PIL import Image, ImageOps

# ---------------------------------------------------------------- 常量

CATEGORY = "YSQ工具箱/导出"
CATEGORY_RESIZE = "YSQ工具箱/图像"
CATEGORY_LOADER = "YSQ工具箱/导入"
CATEGORY_LORA = "YSQ工具箱/LoRA"
CATEGORY_POSE = "YSQ工具箱/循环"
CATEGORY_KREA = "YSQ工具箱/Krea2"

# 按边压缩：以哪条边为基准
EDGE_MODES = ["长边", "短边"]

# 按边压缩：原图没超过目标边长时，尺寸输出端口给什么值
FALLBACK_MODES = ["输出实际尺寸", "输出对齐后尺寸", "强制按目标边长计算"]

# 支持的保存格式（本插件只做 PNG / JPG 两种）
FORMATS = ["PNG", "JPG"]

# 水印来源
WM_SOURCES = ["关闭", "水印文件", "接入图像端口"]

# 水印摆放位置：九宫格 + 平铺
WM_POSITIONS = [
    "左上", "正上", "右上",
    "左中", "正中", "右中",
    "左下", "正下", "右下",
    "平铺",
]

# 保存位置
SAVE_MODES = ["输出目录（output）", "自定义绝对路径"]

# 落盘开关
SAVE_SWITCH = ["保存", "不保存"]

# 常见图片扩展名（用于列出 input 目录里的水印候选）
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")

# 文件名非法字符（保留 / 用于子目录分隔，单独处理）
INVALID_CHARS = re.compile(r'[\\:*?"<>|\r\n\t]+')


# ---------------------------------------------------------------- 张量 <-> PIL

def tensor_to_pil(t):
    """ComfyUI IMAGE 张量 -> PIL 图像。支持 [H,W,C] 与 [B,H,W,C]。"""
    if t is None:
        raise ValueError("没有可处理的图像")
    if hasattr(t, "detach"):
        t = t.detach().cpu()
    if t.dim() == 4:
        t = t[0]
    if t.dim() != 3:
        raise ValueError(f"无法识别的图像张量维度: {tuple(t.shape)}")
    arr = t.numpy()
    if arr.shape[2] == 1:
        arr = np.repeat(arr, 3, axis=2)
    elif arr.shape[2] > 3:
        arr = arr[:, :, :3]
    arr = np.clip(255.0 * arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def pil_to_tensor(img):
    """PIL 图像 -> ComfyUI 风格的 [H,W,3] float32 张量（0~1）。"""
    arr = np.array(img.convert("RGB"), dtype=np.float32) / 255.0
    return torch.from_numpy(arr.copy())


def load_pil(path):
    """
    从磁盘读图：自动纠正 EXIF 方向，透明通道合成到白底，统一返回 RGB。
    多帧图（GIF / TIFF）只取第一帧。
    """
    img = Image.open(path)
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    try:
        if getattr(img, "n_frames", 1) > 1:
            img.seek(0)
    except Exception:
        pass
    img.load()

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


# ---------------------------------------------------------------- 水印

def load_watermark(path):
    """
    读水印图，统一成 RGBA（保留透明通道）。
    支持带透明通道的 PNG；不带 alpha 的图整块贴上（建议用透明 PNG）。
    """
    if not path or not os.path.isfile(path):
        raise ValueError(f"水印文件不存在：{path}")
    img = Image.open(path)
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    try:
        if getattr(img, "n_frames", 1) > 1:
            img.seek(0)
    except Exception:
        pass
    img.load()
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    return img


def prepare_watermark(wm, base_size, width_pct, opacity, rotate):
    """按画面宽度比例缩放 + 旋转 + 调整整体透明度，返回可直接叠加的 RGBA 图。"""
    bw, bh = base_size
    pct = max(1, min(100, int(width_pct)))
    tw = max(1, int(round(bw * pct / 100.0)))
    th = max(1, int(round(wm.height * tw / float(wm.width))))
    wm = wm.resize((tw, th), Image.Resampling.LANCZOS)

    angle = int(rotate) % 360
    if angle:
        # expand=True 保证旋转后四角不被切掉
        wm = wm.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)

    op = max(0, min(100, int(opacity)))
    if op < 100:
        alpha = wm.getchannel("A").point(lambda v: int(v * op / 100.0))
        wm.putalpha(alpha)
    return wm


def _anchor_pos(position, canvas, wm, margin_x, margin_y):
    """九宫格 -> 左上角坐标。"""
    cw, ch = canvas
    ww, wh = wm.size
    mx, my = int(margin_x), int(margin_y)

    left = mx
    center_x = (cw - ww) // 2
    right = cw - ww - mx
    top = my
    center_y = (ch - wh) // 2
    bottom = ch - wh - my

    table = {
        "左上": (left, top),
        "正上": (center_x, top),
        "右上": (right, top),
        "左中": (left, center_y),
        "正中": (center_x, center_y),
        "右中": (right, center_y),
        "左下": (left, bottom),
        "正下": (center_x, bottom),
        "右下": (right, bottom),
    }
    return table.get(position, (right, bottom))


def apply_watermark(base, wm, position, margin_x=32, margin_y=32, gap=48, width_pct=15,
                    opacity=100, rotate=0):
    """
    把水印合成到画面上，返回新的 RGB 图像。
    平铺时会以 (水印宽 + 间距) 为步长在整幅画面上重复。
    """
    if wm is None:
        return base

    canvas = base.convert("RGBA")
    tile = prepare_watermark(wm, canvas.size, width_pct, opacity, rotate)

    if position == "平铺":
        cw, ch = canvas.size
        tw, th = tile.size
        step_x = max(1, tw + int(gap))
        step_y = max(1, th + int(gap))
        for y in range(0, ch, step_y):
            for x in range(0, cw, step_x):
                canvas.alpha_composite(tile, dest=(x, y))
    else:
        x, y = _anchor_pos(position, canvas.size, tile, margin_x, margin_y)
        canvas.alpha_composite(tile, dest=(x, y))

    return canvas.convert("RGB")


# ---------------------------------------------------------------- 缩放

def limit_long_edge(img, max_edge):
    """限制最长边（0 / 负数 = 不缩放），始终保持宽高比。"""
    n = int(max_edge or 0)
    if n <= 0:
        return img
    w, h = img.size
    if max(w, h) <= n:
        return img
    s = n / float(max(w, h))
    tw = max(1, int(round(w * s)))
    th = max(1, int(round(h * s)))
    return img.resize((tw, th), Image.Resampling.LANCZOS)


def resize_image(img, w, h):
    """缩放到指定尺寸（LANCZOS）。尺寸没变就原样返回，省一次重采样。"""
    w, h = max(1, int(w)), max(1, int(h))
    if (w, h) == img.size:
        return img
    return img.resize((w, h), Image.Resampling.LANCZOS)


def align_down(w, h, align):
    """
    把宽高各自向下取整到 align 的倍数（不放大）。
    align <= 1 表示不对齐，原样返回。
    """
    a = int(align or 0)
    w, h = int(w), int(h)
    if a <= 1:
        return w, h
    return max(a, (w // a) * a), max(a, (h // a) * a)


def calc_edge_resize(w, h, edge_mode, target, align):
    """
    按「长边 / 短边」压缩尺寸。

    返回 (目标宽, 目标高, 是否缩放)：
        · 该边大于目标边长  -> 等比缩到目标边长，再按 align 向下对齐，scaled=True
        · 该边不大于目标边长 -> 完全不放大，原样返回，scaled=False
    """
    w, h = int(w), int(h)
    target = max(1, int(target))
    cur = max(w, h) if edge_mode == "长边" else min(w, h)

    if cur <= target:
        return w, h, False

    s = target / float(cur)
    nw = max(1, int(round(w * s)))
    nh = max(1, int(round(h * s)))
    nw, nh = align_down(nw, nh, align)
    return nw, nh, True


def calc_forced_size(w, h, edge_mode, target, align):
    """
    不管原图多大，都按「目标边长 + 对齐倍数」算出宽高。
    只用来给尺寸端口提供"如果压到目标边长会是多大"的数值，不动图像。
    """
    w, h = int(w), int(h)
    target = max(1, int(target))
    cur = max(w, h) if edge_mode == "长边" else min(w, h)
    if cur <= 0:
        return w, h

    s = target / float(cur)
    nw = max(1, int(round(w * s)))
    nh = max(1, int(round(h * s)))
    return align_down(nw, nh, align)


# ---------------------------------------------------------------- 编码

def encode_image(img, fmt, jpg_quality=92, png_level=6):
    """
    按格式编码为字节流。
        JPG -> quality 1~100，optimize + progressive（网页追图更友好）
        PNG -> compress_level 0~9（0 最快最大，9 最慢最小），无损
    """
    buf = io.BytesIO()
    fmt = (fmt or "PNG").upper()

    if fmt == "JPG":
        q = max(1, min(100, int(jpg_quality)))
        # JPEG 不支持透明：去 alpha，透明区域垫白
        if img.mode in ("RGBA", "LA"):
            rgba = img.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=q, optimize=True, progressive=True)
    else:
        lv = max(0, min(9, int(png_level)))
        if img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
            img = img.convert("RGB")
        img.save(buf, format="PNG", compress_level=lv)

    return buf.getvalue()


def ext_of(fmt):
    return "jpg" if (fmt or "").upper() == "JPG" else "png"


# ---------------------------------------------------------------- 路径与文件名

def clean_name(name, fallback="图像后处理"):
    """清理文件名里的非法字符（保留 / 交给 clean_subdir 处理）。"""
    s = INVALID_CHARS.sub("_", str(name or "")).strip().strip(".")
    return s or fallback


def clean_subdir(subdir):
    """把 'a/b/c' 逐段清理，返回安全的相对路径（空字符串表示根目录）。"""
    raw = str(subdir or "").replace("\\", "/").strip()
    parts = [clean_name(p, "") for p in raw.split("/")]
    parts = [p for p in parts if p]
    return "/".join(parts)


def resolve_save_dir(save_mode, subdir, custom_path, default_dir):
    """解析最终保存目录并创建。"""
    if str(save_mode).startswith("自定义"):
        base = os.path.expanduser(str(custom_path or "").strip().strip('"'))
        if not base:
            base = default_dir
        # 允许用户写到不存在的盘 / 目录，明确报错比默默换地方好
        base = os.path.abspath(base)
    else:
        base = default_dir

    sub = clean_subdir(subdir)
    target = os.path.join(base, sub.replace("/", os.sep)) if sub else base
    os.makedirs(target, exist_ok=True)
    return target


def timed_filepath(folder, prefix, ext, start_at=None):
    """
    生成 `前缀_年月日时分秒.扩展名`，例如 图像后处理_20261017195938.png。

    · 后缀是纯数字的 YYYYMMDDHHMMSS（14 位），不用序号
    · 同一秒内要存多张（或者撞上已有文件）时，秒数自动往后顺延一秒，
      保证每张都唯一、且后缀仍然是纯数字
    返回 (路径, 这一张实际用的时间)，下一张把返回的时间传回来即可接着排。
    """
    t = start_at or datetime.now()
    for _ in range(3600):
        path = os.path.join(folder, f"{prefix}_{t.strftime('%Y%m%d%H%M%S')}.{ext}")
        if not os.path.exists(path):
            return path, t
        t += timedelta(seconds=1)
    # 一秒一格排满一小时还撞（几乎不可能）：退回带毫秒的写法兜底
    path = os.path.join(folder, f"{prefix}_{t.strftime('%Y%m%d%H%M%S%f')[:20]}.{ext}")
    return path, t


def human_size(num_bytes):
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / 1024 / 1024:.2f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes} B"


def list_watermark_files(input_dir, limit=80):
    """
    列出可用的水印候选（相对 input 目录的路径）。

    规则（保证下拉列表干净好用）：
        · input/watermarks/ 里有图 -> 全部列出（按修改时间倒序）+ input 根目录最近 30 张兜底
        · 没有 watermarks 目录    -> 列出 input 根目录最近 limit 张
    """
    if not input_dir or not os.path.isdir(input_dir):
        return ["（把透明 PNG 放进 input/watermarks 目录）"]

    def scan(dirpath, recursive, cap=None):
        found = []
        if not os.path.isdir(dirpath):
            return found
        if recursive:
            walker = os.walk(dirpath)
            for root, _dirs, files in walker:
                for fn in files:
                    if fn.lower().endswith(IMAGE_EXTS):
                        full = os.path.join(root, fn)
                        try:
                            mt = os.path.getmtime(full)
                        except OSError:
                            mt = 0
                        found.append((mt, os.path.relpath(full, input_dir).replace("\\", "/")))
        else:
            try:
                for entry in os.scandir(dirpath):
                    if entry.is_file() and entry.name.lower().endswith(IMAGE_EXTS):
                        try:
                            mt = entry.stat().st_mtime
                        except OSError:
                            mt = 0
                        found.append((mt, entry.name))
            except OSError:
                pass
        found.sort(key=lambda x: -x[0])
        return found[:cap] if cap else found

    wm_dir = os.path.join(input_dir, "watermarks")
    dedicated = scan(wm_dir, recursive=True)
    if dedicated:
        extra = scan(input_dir, recursive=False, cap=30)
        merged = [rel for _mt, rel in dedicated] + [rel for _mt, rel in extra]
    else:
        merged = [rel for _mt, rel in scan(input_dir, recursive=False, cap=limit)]

    if not merged:
        return ["（把透明 PNG 放进 input/watermarks 目录）"]
    return merged
