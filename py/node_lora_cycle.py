# -*- coding: utf-8 -*-
"""
节点：🔁 LoRA 轮转

登记一组人物 LoRA，一次运行里逐轮切换：
    第 1 轮 → LoRA A → 整条工作流跑一遍
    第 2 轮 → LoRA B → 整条工作流跑一遍
    ...
出图数量 = LoRA 数量。提示词和种子都由你自己的工作流决定（种子设成 fixed 就每张一致）。

名单格式（一行一个，支持 # 注释）：
    角色甲:0.85
    角色乙.safetensors:1.0
    角色丙              ← 不写强度就用「默认强度」
"""

import json
import os

import comfy.sd
import comfy.utils
import folder_paths

from .core import CATEGORY_LORA

# LoRA 权重缓存：每轮换一个 LoRA，不缓存的话每次都要重读几秒
_LORA_CACHE = {}
_LORA_CACHE_MAX = 12

DEFAULT_LIST = "[]"


# ---------------------------------------------------------------- 名单解析

def _to_float(text):
    try:
        return float(str(text).strip())
    except Exception:
        return None


def parse_items(text):
    """
    解析前端面板写回的 JSON 清单 -> [(名字, 启用, 强度)]
    格式：[{"lora": "路径", "on": true, "strength": 0.8}, ...]
    旧版的文本名单（一行一个）也能读，自动降级。
    """
    raw = (text or "").strip()
    if not raw:
        return []

    if raw.startswith("["):
        try:
            data = json.loads(raw)
        except Exception as e:
            raise ValueError(f"LoRA 清单不是合法 JSON：{e}")
        out = []
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("lora") or "").strip()
                if not name:
                    continue
                out.append((
                    name,
                    bool(item.get("on", True)),
                    float(item.get("strength", 1.0)),
                ))
        return out

    # 兼容旧的文本写法
    return [(n, True, s if s is not None else 1.0) for n, s in parse_entries(raw)]


def parse_entries(text):
    """
    解析旧的文本名单 -> [(名字, 强度或 None)]
    支持 `名字:0.8`、`名字：0.8`（全角冒号）、`名字,0.8`、纯 `名字`。
    """
    entries = []
    raw = (text or "").replace("\r", "").replace("：", ":")
    for line in raw.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        name, strength = line, None
        for sep in (":", ","):
            if sep in line:
                head, tail = line.rsplit(sep, 1)
                val = _to_float(tail)
                if val is not None:
                    name, strength = head.strip(), val
                    break
        if name:
            entries.append((name, strength))
    return entries


def _norm(x):
    """统一分隔符 + 转小写，用于比较（get_filename_list 在 Windows 上给的是反斜杠）。"""
    return str(x).replace("\\", "/").lower()


def resolve_lora(name):
    """把用户写的名字解析成 folder_paths 里的规范 LoRA 名。"""
    raw = str(name or "").strip().replace("\\", "/")
    if not raw:
        raise ValueError("LoRA 名字是空的")

    # 直接给了绝对路径
    if os.path.isabs(raw) and os.path.isfile(raw):
        base = folder_paths.get_filename_list("loras")
        for item in base:
            if os.path.normpath(folder_paths.get_full_path("loras", item)) == os.path.normpath(raw):
                return item
        raise ValueError(f"这个 LoRA 不在 ComfyUI 的 loras 目录里：{raw}")

    available = folder_paths.get_filename_list("loras")
    low = _norm(raw)

    for item in available:
        if _norm(item) == low:
            return item

    for ext in (".safetensors", ".pt", ".ckpt", ".bin"):
        for item in available:
            if _norm(item) == low + ext:
                return item

    base = low.split("/")[-1]
    hits = [i for i in available if _norm(i).split("/")[-1] == base]
    if not hits:
        stem = os.path.splitext(base)[0]
        hits = [i for i in available if _norm(i).split("/")[-1].startswith(stem)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        print(f"[GULU工具箱] LoRA「{name}」匹配到多个，用第一个：{hits[0]}（候选：{', '.join(hits[:4])}）")
        return hits[0]

    raise ValueError(
        f"找不到 LoRA：{name}\n"
        f"→ 写全文件名（含 .safetensors）或只写文件名部分；"
        f"当前 loras 目录共 {len(available)} 个"
    )


def _load_lora(lora_name):
    """带缓存的 LoRA 读取，避免每轮重新解包。"""
    path = folder_paths.get_full_path_or_raise("loras", lora_name)
    hit = _LORA_CACHE.get(path)
    if hit is not None:
        return hit
    lora, metadata = comfy.utils.load_torch_file(path, safe_load=True, return_metadata=True)
    if len(_LORA_CACHE) >= _LORA_CACHE_MAX:
        _LORA_CACHE.pop(next(iter(_LORA_CACHE)))
    _LORA_CACHE[path] = (lora, metadata)
    return lora, metadata


# ---------------------------------------------------------------- 节点

class LoRACycleCN:
    """🔁 LoRA 轮转：一次运行，逐个 LoRA 出图"""

    CATEGORY = CATEGORY_LORA
    FUNCTION = "run"
    RETURN_TYPES = ("MODEL", "STRING")
    RETURN_NAMES = ("模型", "当前LoRA")
    OUTPUT_NODE = True
    DESCRIPTION = (
        "登记多个 LoRA，一次运行逐轮切换：每轮把当前 LoRA 应用到模型上，"
        "整条工作流跑一遍。出图数量 = 名单里的 LoRA 数量。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL", {"tooltip": "基础模型（接 Checkpoint / UNet 加载器）。"}),
                "LoRA清单": ("STRING", {
                    "default": DEFAULT_LIST,
                    "multiline": False,
                    "tooltip": "由节点上的面板自动维护（JSON），不用手改。用面板里的下拉添加 LoRA、开关决定是否参与轮转、左右箭头调强度。",
                }),
                "默认强度": ("FLOAT", {
                    "default": 1.0, "min": -10.0, "max": 10.0, "step": 0.01,
                    "tooltip": "名单里没写强度时用这个值。",
                }),
            },
            "optional": {
                "循环位置": ("INT", {
                    "default": 0, "min": 0, "max": 9999, "step": 1,
                    "tooltip": "内部参数：每轮由前端自动 +1，不要手填。",
                }),
            },
        }

    def run(self, model, LoRA清单, 默认强度, 循环位置=0):
        items = parse_items(LoRA清单)
        if not items:
            raise ValueError("LoRA 清单是空的 —— 在节点面板里点下拉选一个 LoRA 加进来")

        # 只轮转「开关打开」的
        enabled = [it for it in items if it[1]]
        if not enabled:
            raise ValueError("清单里的 LoRA 全部关掉了，至少打开一个开关")

        total = len(enabled)
        idx = int(循环位置 or 0)
        if idx < 0 or idx >= total:
            print(f"[GULU工具箱] LoRA 轮转位置 {idx} 超出启用数 {total}，回到第一个")
            idx = 0

        name, _on, strength = enabled[idx]
        if strength is None:
            strength = float(默认强度)

        resolved = resolve_lora(name)
        lora, metadata = _load_lora(resolved)
        # 只作用于扩散模型（不碰 CLIP / text encoder）
        model_out, _ = comfy.sd.load_lora_for_models(
            model, None, lora, float(strength), 0.0, lora_metadata=metadata
        )

        more = idx + 1 < total
        print(f"[GULU工具箱] LoRA 轮转 {idx + 1}/{total}：{resolved}  强度 {strength}"
              f"  {'→ 自动继续下一个' if more else '→ 全部完成'}")

        return {
            "ui": {"gulu_show": [{
                "mode": "loop",
                "index": idx,
                "total": total,
                "next": idx + 1,
                "more": more,
                "name": os.path.basename(resolved),
                "lora": resolved,
                "strength": float(strength),
            }]},
            "result": (model_out, resolved),
        }
