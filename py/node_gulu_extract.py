# -*- coding: utf-8 -*-
"""
节点：🏷️ GULU 提示词提取

只做一件事：从**单张图片文件**里把提示词读出来，输出一个字符串。

为什么必须读文件而不是读 IMAGE 张量：
    ComfyUI 在内存里传的是纯像素张量，图片里的文本元数据在加载那一刻就丢了，
    所以「提取提示词」只能回到磁盘上的原始文件去读。

读取优先级（命中就返回，读不到就输出空字符串，不打断链路）：
    1. text              —— 本插件「图像后处理导出」写进去的键，最准
    2. prompt            —— ComfyUI 工作流 JSON，按采样器的 positive 链路反查文本节点
    3. parameters        —— A1111 / WebUI 生成图
    4. comment / Comment —— JPG 的 COM 注释段
"""

import json
import os

from PIL import Image, ImageOps

from .core import CATEGORY_TEXT, TEXT_META_KEY, save_temp_preview


# 我们自己写入的键排在最前面
PREFERRED_KEYS = (TEXT_META_KEY, TEXT_META_KEY.upper())

# 判定「文本编码节点」的关键词（class_type 小写后含其一即认）
TEXT_NODE_HINTS = ("cliptextencode", "textencode", "text_encode", "promptencode", "文本编码")

# 路径最大追溯深度，防止环
MAX_DEPTH = 8


# ---------------------------------------------------------------- 读元数据

def _to_text(value):
    """字节 / 字符串 / 其它 → 字符串。JPG 的 COM 段常是 bytes。"""
    if value is None:
        return ""
    if isinstance(value, bytes):
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return value.decode(enc)
            except Exception:
                continue
        return value.decode("utf-8", "replace")
    return str(value)


def _repair_mojibake(text):
    """
    老文件（以及某些工具）把 UTF-8 写进了 PNG 的 tEXt 块，Pillow 会按 latin-1 读出来变乱码。
    这里按「latin-1 还原成字节再按 UTF-8 解码」救一次；不成立就原样返回（纯 ASCII / 真 latin-1 不受影响）。
    """
    if not text:
        return text
    try:
        fixed = text.encode("latin-1").decode("utf-8")
        return fixed if fixed != text else text
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def read_meta(path):
    """把图片里能读到的文本键值全取出来（PNG 的 tEXt / iTXt / zTXt、JPG 的 COM）。"""
    meta = {}
    with Image.open(path) as img:
        # PNG：Pillow 会把 tEXt / iTXt 解析进 .text
        try:
            for key, value in (getattr(img, "text", None) or {}).items():
                meta[str(key)] = _repair_mojibake(_to_text(value))
        except Exception:
            pass
        # JPG：COM 段在 info["comment"]；顺带兜底 info 里其它字符串项
        for key, value in (img.info or {}).items():
            name = str(key)
            if name in meta:
                continue
            if isinstance(value, (str, bytes)):
                meta[name] = _repair_mojibake(_to_text(value))
    return meta


# ---------------------------------------------------------------- 从工作流 JSON 取提示词

def _node_text(node):
    """从一个节点的 inputs 里挑出最像提示词的那个字符串。"""
    inputs = (node or {}).get("inputs") or {}
    best = ""
    for key, value in inputs.items():
        if not isinstance(value, str) or not value.strip():
            continue
        low = str(key).lower()
        if "text" in low or "prompt" in low:
            if len(value) > len(best):
                best = value
    if not best:
        # 兜底：某些自定义编码节点字段名不叫 text，取最长的那个字符串
        for value in inputs.values():
            if isinstance(value, str) and len(value) > len(best):
                best = value
    return best.strip()


def _walk_text(graph, node_id, depth=0, seen=None):
    """沿连线向上游追溯，找到第一个带文本的节点。"""
    seen = seen if seen is not None else set()
    key = str(node_id)
    if key in seen or depth > MAX_DEPTH:
        return ""
    seen.add(key)

    node = graph.get(key)
    if not isinstance(node, dict):
        return ""

    text = _node_text(node)
    if text:
        return text

    for value in (node.get("inputs") or {}).values():
        if isinstance(value, (list, tuple)) and value and isinstance(value[0], (str, int)):
            found = _walk_text(graph, value[0], depth + 1, seen)
            if found:
                return found
    return ""


def positive_from_workflow(raw):
    """从 ComfyUI 的 prompt JSON 里取正向提示词。"""
    try:
        graph = json.loads(raw)
    except Exception:
        return ""
    if not isinstance(graph, dict):
        return ""

    # 首选：采样器的 positive 输入 —— 这才是真正的正向链路，不会混进负向
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") or {}
        link = inputs.get("positive")
        if isinstance(link, (list, tuple)) and link:
            found = _walk_text(graph, link[0])
            if found:
                return found

    # 次选：所有文本编码节点里最长的一段（通常正向比负向长）
    candidates = []
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        cls = str(node.get("class_type") or "").lower()
        if any(hint in cls for hint in TEXT_NODE_HINTS):
            text = _node_text(node)
            if text:
                candidates.append(text)
    if candidates:
        return max(candidates, key=len)
    return ""


def positive_from_a1111(raw):
    """A1111 / WebUI 格式：`Negative prompt:` 之前那段就是正向提示词。"""
    text = str(raw).replace("\r\n", "\n")
    cut = text.find("Negative prompt:")
    if cut >= 0:
        return text[:cut].strip()

    # 没有 Negative prompt 时，砍掉结尾那几行参数（Steps: / Sampler: ...）
    lines = text.split("\n")
    while lines and any(tag in lines[-1] for tag in ("Steps:", "Sampler:", "CFG scale:")):
        lines.pop()
    return "\n".join(lines).strip()


# ---------------------------------------------------------------- 主流程

def _split_comment(meta):
    """
    JPG 的 COM 段里本插件写的是 `key=value` 形式（多键会分多段，但 Pillow 只返回第一段）。
    把单键的值拆出来，当成普通键参与后面的优先级匹配。
    """
    for raw_key in ("comment", "Comment"):
        raw = (meta.get(raw_key) or "").strip()
        if not raw:
            continue
        head, sep, tail = raw.partition("=")
        head = head.strip()
        if sep and head and len(head) <= 32 and "\n" not in head:
            meta.setdefault(head, tail)


def extract(path):
    """返回 (提示词, 来源说明)。"""
    meta = read_meta(path)
    _split_comment(meta)

    # 1. 本插件写入的 text 键
    for key in PREFERRED_KEYS:
        value = (meta.get(key) or "").strip()
        if value:
            return value, f"键 {key}"

    # 2. ComfyUI 工作流 JSON（官方保存节点 / 本插件「写入」模式）
    raw = (meta.get("prompt") or "").strip()
    if raw:
        value = positive_from_workflow(raw)
        if value:
            return value, "工作流 JSON（positive 链路）"

    # 3. A1111 / WebUI
    raw = (meta.get("parameters") or "").strip()
    if raw:
        value = positive_from_a1111(raw)
        if value:
            return value, "A1111 parameters"

    # 4. JPG 的 COM 注释段，或其它可能塞了工作流 JSON 的键
    for key in ("comment", "Comment", "workflow", "Description", "description"):
        raw = (meta.get(key) or "").strip()
        if not raw:
            continue
        value = positive_from_workflow(raw)
        if value:
            return value, f"键 {key}（工作流 JSON）"
        return raw, f"键 {key}"

    return "", "没有找到提示词"


def resolve_path(raw):
    """把用户给的路径字符串变成真实存在的文件；找不到返回空串（不抛错，免得打断批量链路）。"""
    lines = [p.strip().strip('"').strip("'") for p in str(raw or "").replace("\r", "").split("\n")]
    lines = [p for p in lines if p]
    if not lines:
        return ""

    for item in lines:
        if os.path.isfile(item):
            return item

    # 相对路径兜底：相对 ComfyUI 根目录
    base = ""
    try:
        import folder_paths
        base = getattr(folder_paths, "base_path", "") or ""
    except Exception:
        base = ""

    if base:
        for item in lines:
            if os.path.isabs(item):
                continue
            joined = os.path.join(base, item.replace("/", os.sep))
            if os.path.isfile(joined):
                return joined

    return ""


class GuluPromptExtractCN:
    """🏷️ GULU 提示词提取：单张图片 → 一个提示词字符串"""

    CATEGORY = CATEGORY_TEXT
    FUNCTION = "run"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("提示词",)
    OUTPUT_NODE = True
    DESCRIPTION = (
        "从单张图片的元数据里读出提示词，只有一个字符串输出。"
        "支持本插件保存的图、ComfyUI 工作流图、A1111 生成的图；读不到就输出空字符串。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "图片路径": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "tooltip": "要读取的图片文件路径。可以接上游「图像后处理导出」的「保存路径」输出，"
                               "也可以直接粘 Windows 路径。多行时只取第一个真实存在的文件。",
                }),
            },
        }

    @staticmethod
    def _preview(path):
        """缩一张预览图进 temp 目录，让前端像官方 LoadImage 那样把这张图直接显示在节点上。"""
        try:
            with Image.open(path) as im:
                name = save_temp_preview(ImageOps.exif_transpose(im), prefix="gulu_extract")
            if name:
                return {"images": [{"filename": name, "subfolder": "", "type": "temp"}]}
        except Exception as e:
            print(f"[GULU工具箱] 预览图生成失败（不影响提取）：{e}")
        return {}

    def run(self, 图片路径):
        path = resolve_path(图片路径)
        if not path:
            raw = str(图片路径 or "").strip()
            if raw:
                head = raw.splitlines()[0] if raw.splitlines() else raw
                print(f"[GULU工具箱] GULU 提取：找不到图片文件，已输出空字符串 → {head}")
            else:
                print("[GULU工具箱] GULU 提取：没有填图片路径，已输出空字符串")
            return {"ui": {}, "result": ("",)}

        try:
            prompt, source = extract(path)
        except Exception as e:
            print(f"[GULU工具箱] GULU 提取失败（{os.path.basename(path)}）：{e}")
            return {"ui": self._preview(path), "result": ("",)}

        print(f"[GULU工具箱] GULU 提取：{os.path.basename(path)} → {source}，{len(prompt)} 字")
        return {"ui": self._preview(path), "result": (prompt,)}
