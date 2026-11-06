# -*- coding: utf-8 -*-
"""
节点：📂 图像批量顺序导入

两种用法，接哪个端口就自动走哪种模式：

    ① 接「图像」端口      → 顺序模式：一轮工作流读一张，跑完自动推进到下一张，
                            全部跑完自动停。任何时刻内存里只有当前这一张（低占用）。
    ② 接「全部图像」端口  → 一次模式：直接把整个目录读成一个 batch 一次性输出，
                            不再循环（适合喂给批处理节点）。

分辨率 / 文件名 / 第 x/y 张 这些信息不走端口，直接显示在节点预览图的下方。
"""

import os

import folder_paths
import torch

from .core import CATEGORY_LOADER, load_pil, pil_to_tensor, save_temp_preview

# 排序方式
SORT_MODES = [
    "文件名升序",
    "文件名降序",
    "修改时间升序",
    "修改时间降序",
]

# 默认扩展名：与 ImageToolkit-CN 的批量加载保持一致（11 种）
DEFAULT_EXTS = ".png,.jpg,.jpeg,.webp,.bmp,.gif,.tif,.tiff,.jfif,.ppm,.pgm"

# 输出槽位
PORT_IMAGE = 0
PORT_BATCH = 1


# ---------------------------------------------------------------- 目录扫描

def _parse_exts(raw):
    """" .png,.jpg " -> (".png", ".jpg")，空则用默认图片扩展名。"""
    text = (raw or "").replace("，", ",").replace(";", ",")
    items = [x.strip().lower() for x in text.split(",") if x.strip()]
    items = [x if x.startswith(".") else "." + x for x in items]
    if not items:
        items = [x.strip() for x in DEFAULT_EXTS.split(",")]
    return tuple(items)


def collect_images(directory, recursive=False, exts=DEFAULT_EXTS, sort_mode="文件名升序"):
    """按条件收集图片，返回绝对路径列表。"""
    directory = os.path.expanduser(str(directory or "").strip().strip('"'))
    if not directory:
        raise ValueError("还没有填「图片目录」")
    if not os.path.isdir(directory):
        raise ValueError(f"目录不存在：{directory}")

    allow = _parse_exts(exts)
    found = []

    if recursive:
        for root, _dirs, files in os.walk(directory):
            for fn in files:
                if fn.lower().endswith(allow):
                    found.append(os.path.join(root, fn))
    else:
        for entry in os.scandir(directory):
            if entry.is_file() and entry.name.lower().endswith(allow):
                found.append(entry.path)

    if sort_mode == "文件名降序":
        found.sort(key=lambda p: os.path.basename(p).lower(), reverse=True)
    elif sort_mode == "修改时间升序":
        found.sort(key=lambda p: os.path.getmtime(p))
    elif sort_mode == "修改时间降序":
        found.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    else:
        found.sort(key=lambda p: os.path.basename(p).lower())

    return found


def _used_ports(prompt, unique_id):
    """这个节点有哪些输出槽位被下游引用了。"""
    used = set()
    if not prompt or unique_id is None:
        return used
    uid = str(unique_id)
    for node in prompt.values():
        for value in (node.get("inputs") or {}).values():
            if (isinstance(value, list) and len(value) == 2
                    and isinstance(value[0], str) and value[0] == uid
                    and isinstance(value[1], (int, float))):
                used.add(int(value[1]))
    return used


# ---------------------------------------------------------------- 预览

def save_preview(pil):
    """缩一张小图写进 temp 目录给节点预览用，返回文件名。失败返回 None。"""
    return save_temp_preview(pil, prefix="gulu_load")


# ---------------------------------------------------------------- 节点

class ImageBatchLoopLoader:
    """📂 图像批量顺序导入：一轮一张，跑完自动停"""

    CATEGORY = CATEGORY_LOADER
    FUNCTION = "run"
    RETURN_TYPES = ("IMAGE", "IMAGE")
    RETURN_NAMES = ("图像", "全部图像")
    OUTPUT_NODE = True
    DESCRIPTION = (
        "接「图像」端口 = 一轮读一张、处理完自动下一张、全部跑完自动停（低占用）；"
        "接「全部图像」端口 = 一次性把整个目录读成一个 batch 输出（不循环）。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "图片目录": ("STRING", {
                    "default": "G:/图片素材",
                    "multiline": False,
                    "tooltip": "要导入的文件夹。可以直接粘贴资源管理器的路径。",
                }),
                "包含子文件夹": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "打开后会连子文件夹里的图一起导入。",
                }),
                "图片扩展名": ("STRING", {
                    "default": DEFAULT_EXTS,
                    "multiline": False,
                    "tooltip": "逗号分隔的扩展名白名单，默认 11 种常见图片格式。留空 = 用默认。",
                }),
                "排序方式": (SORT_MODES, {
                    "default": "文件名升序",
                    "tooltip": "决定处理顺序。文件名排序不区分大小写，数字按字符顺序（img_2 会排在 img_10 前面）。",
                }),
                "起始索引": ("INT", {
                    "default": 0, "min": 0, "max": 999999, "step": 1,
                    "tooltip": "从第几张开始（0 = 第一张）。批量跑到一半中断了，下次从这里接着跑。",
                }),
                "加载数量": ("INT", {
                    "default": 0, "min": 0, "max": 999999, "step": 1,
                    "tooltip": "这一批最多处理多少张。0 = 全部。",
                }),
            },
            "optional": {
                "循环位置": ("INT", {
                    "default": 0, "min": 0, "max": 999999, "step": 1,
                    "tooltip": "内部参数：每轮由前端自动 +1，不要手动填。想指定从第几张开始请用上面的「起始索引」。",
                }),
            },
            "hidden": {
                "prompt": "PROMPT",
                "unique_id": "UNIQUE_ID",
            },
        }

    # ---------------------------------------------------------------- 一次性 batch

    def _run_batch(self, files, totals_info):
        """把整个目录读成一个 batch。尺寸不一致就报错（batch 张量必须同尺寸）。"""
        tensors = []
        first_size = None
        for p in files:
            pil = load_pil(p)
            if first_size is None:
                first_size = pil.size
            elif pil.size != first_size:
                raise ValueError(
                    f"目录里的图片尺寸不一致，无法合成一个 batch："
                    f"第一张 {first_size[0]}×{first_size[1]}，"
                    f"但 {os.path.basename(p)} 是 {pil.width}×{pil.height}。\n"
                    f"→ 要么改用「图像」端口逐张处理，要么先用「📐 按边压缩」统一尺寸。"
                )
            tensors.append(pil_to_tensor(pil).unsqueeze(0))

        out = torch.cat(tensors, dim=0)
        preview = save_preview(load_pil(files[0]))
        print(f"[GULU工具箱] 批量导入（一次模式）：{len(files)} 张 {first_size[0]}×{first_size[1]}")

        return {
            "ui": {"gulu_show": [{
                "filename": preview or "",
                "width": first_size[0],
                "height": first_size[1],
                "name": os.path.basename(files[0]),
                "index": 0,
                "total": len(files),
                "more": False,
                "mode": "batch",
            }]},
            "result": (out[:1], out),
        }

    # ---------------------------------------------------------------- 执行

    def run(self, 图片目录, 包含子文件夹, 图片扩展名, 排序方式, 起始索引, 加载数量,
            循环位置=0, prompt=None, unique_id=None):

        files = collect_images(图片目录, 包含子文件夹, 图片扩展名, 排序方式)

        start = max(0, int(起始索引))
        files = files[start:]
        limit = int(加载数量)
        if limit > 0:
            files = files[:limit]

        total = len(files)
        if total == 0:
            raise ValueError(f"目录里没有符合条件的图片：{图片目录}")

        # 接「全部图像」端口 -> 走一次性模式，不循环
        if PORT_BATCH in _used_ports(prompt, unique_id):
            return self._run_batch(files, total)

        # 接「图像」端口 -> 顺序模式
        # 越界防御：宁可停，也不绕回第一张 —— 否则和另一个批量节点长度不一致时会被推超、无限出图
        idx = int(循环位置 or 0)
        forced_stop = False
        if idx < 0 or idx >= total:
            print(f"[GULU工具箱] 循环位置 {idx} 超出本批张数 {total}，按最后一张处理并结束")
            idx = max(0, total - 1)
            forced_stop = True

        path = files[idx]
        pil = load_pil(path)
        tensor = pil_to_tensor(pil).unsqueeze(0)
        name = os.path.basename(path)
        more = (not forced_stop) and (idx + 1 < total)

        tail = "→ 自动继续下一张" if more else "→ 本批最后一张，结束"
        print(f"[GULU工具箱] 批量导入 {idx + 1}/{total}：{name}  "
              f"({pil.width}×{pil.height})  {tail}")

        return {
            "ui": {"gulu_show": [{
                "filename": save_preview(pil) or "",
                "width": pil.width,
                "height": pil.height,
                "name": name,
                "index": idx,
                "total": total,
                "next": idx + 1,
                "more": more,
                "mode": "loop",
            }]},
            "result": (tensor, tensor),
        }
