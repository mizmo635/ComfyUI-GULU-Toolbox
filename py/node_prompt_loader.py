# -*- coding: utf-8 -*-
"""
节点：📝 批量提示词顺序导入（单文件模式）

一个文本文件里放多条**正面提示词**，用空行（或指定分隔符）隔开。
每轮工作流取一条，整轮跑完前端自动推进到下一条，最后一条跑完自动停。

复用了「📂 图像批量顺序导入」的循环推进机制：
    后端返回 ui.gulu_show（index / total / next / more）
    前端在 execution_success 之后把「循环位置」+1 并重新提交下一轮。

和图片批量的区别：
    · 不需要图片张量，直接输出字符串，内存占用可以忽略
    · 每轮都重新读文件（几十 KB），你在两轮之间改了文件，下一轮就生效
    · 空文件 / 只有注释的文件会报错，而不是默默输出空提示词（避免白白出废图）
"""

import os
import re

from .core import CATEGORY_LOADER

# 分隔符模式
SEP_MODES = ["空行", "---", "自定义"]

# 注释行前缀（整行以它开头就跳过）
COMMENT_PREFIX = "#"

# 解码顺序：utf-8-sig 先吃掉 BOM；gb18030 兜底中文 Windows 记事本存的 GBK
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")

DEFAULT_FILE = "G:/提示词/正面提示词.txt"


# ---------------------------------------------------------------- 读取与切分

def read_text(path):
    """读文本文件，自动识别编码。返回 (文本, 实际用的编码)。"""
    with open(path, "rb") as fp:
        raw = fp.read()
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), "utf-8(有损)"


def split_entries(text, sep_mode="空行", custom_sep="",
                  skip_comments=True, merge_lines=True):
    """
    把一整份文本切成若干条提示词。

    sep_mode：
        空行   —— 一个或多个空行都算一次分隔（最稳，提示词里不会出现这种结构）
        ---    —— 单独一行三个及以上短横线（Markdown 分隔线习惯）
        自定义 —— 用 custom_sep 原样切
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]

    if skip_comments:
        kept = []
        for line in text.split("\n"):
            # 整行注释：替换成空行，而不是直接删掉 —— 这样它天然充当分隔，
            # 不会把注释行上下两条提示词粘成一条
            if line.lstrip().startswith(COMMENT_PREFIX):
                kept.append("")
                continue
            kept.append(line)
        text = "\n".join(kept)

    mode = str(sep_mode or "空行")
    if mode == "---":
        parts = re.split(r"(?m)^[ \t]*-{3,}[ \t]*$", text)
    elif mode == "自定义":
        sep = str(custom_sep or "")
        parts = text.split(sep) if sep else [text]
    else:
        parts = re.split(r"\n[ \t]*\n", text)

    entries = []
    for part in parts:
        s = part.strip()
        if not s:
            continue
        if merge_lines:
            # 一个文件 = 一句提示词：把条目内部的换行压成空格
            s = " ".join(line.strip() for line in s.split("\n") if line.strip())
        else:
            s = "\n".join(line.rstrip() for line in s.split("\n")).strip()
        if s:
            entries.append(s)
    return entries


def collect_entries(path, sep_mode, custom_sep, skip_comments, merge_lines):
    """读文件 + 切分，返回 (条目列表, 编码)。"""
    text, enc = read_text(path)
    return split_entries(text, sep_mode, custom_sep, skip_comments, merge_lines), enc


# ---------------------------------------------------------------- 节点

class PromptBatchLoopLoader:
    """📝 批量提示词顺序导入：一轮一条，跑完自动停"""

    CATEGORY = CATEGORY_LOADER
    FUNCTION = "run"
    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("提示词", "文件名", "序号")
    OUTPUT_NODE = True
    DESCRIPTION = (
        "一个文本文件里放多条正面提示词（默认空行分隔），每轮工作流取一条，"
        "整轮跑完自动推进下一条，跑完自动停。和「📂 图像批量顺序导入」配对使用即可按顺序配图配词。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "提示词文件": ("STRING", {
                    "default": DEFAULT_FILE,
                    "multiline": False,
                    "tooltip": "要读取的文本文件（.txt / .md 都行）。文件里每一条提示词之间用空行隔开。",
                }),
                "分隔符": (SEP_MODES, {
                    "default": "空行",
                    "tooltip": "空行：一条提示词和另一条之间空一行（推荐，提示词本身不可能是空行）。"
                               "---：单独一行三个短横线分隔。自定义：用下面那栏的字符串切。",
                }),
                "自定义分隔符": ("STRING", {
                    "default": "|||",
                    "multiline": False,
                    "tooltip": "仅「分隔符 = 自定义」时生效。会按这个字符串原样切分，注意别用逗号 / 分号 / 竖线（它们是提示词语法的一部分）。",
                }),
                "跳过注释行": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "打开后，整行以 # 开头的行会被忽略，方便在文件里写备注。行内的 # 不受影响。",
                }),
                "合并条目内换行": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "打开后，一条提示词内部的换行会被压成空格（保证「一条 = 一句」）。关掉则保留换行原样输出。",
                }),
                "起始索引": ("INT", {
                    "default": 0, "min": 0, "max": 999999, "step": 1,
                    "tooltip": "从第几条开始（0 = 第一条）。跑到一半中断了，下次从这里接着跑。",
                }),
                "加载数量": ("INT", {
                    "default": 0, "min": 0, "max": 999999, "step": 1,
                    "tooltip": "这一批最多处理多少条。0 = 全部。",
                }),
            },
            "optional": {
                "循环位置": ("INT", {
                    "default": 0, "min": 0, "max": 999999, "step": 1,
                    "tooltip": "内部参数：每轮由前端自动 +1，不要手动填。想指定从第几条开始请用上面的「起始索引」。",
                }),
            },
        }

    # ---------------------------------------------------------------- 执行

    def run(self, 提示词文件, 分隔符, 自定义分隔符, 跳过注释行, 合并条目内换行,
            起始索引, 加载数量, 循环位置=0):

        path = os.path.expanduser(str(提示词文件 or "").strip().strip('"').strip("'"))
        if not path:
            raise ValueError("还没有填「提示词文件」")
        if not os.path.isfile(path):
            raise ValueError(f"提示词文件不存在：{path}")

        entries, enc = collect_entries(path, 分隔符, 自定义分隔符, 跳过注释行, 合并条目内换行)
        if not entries:
            raise ValueError(
                f"这个文件里没有解析出任何提示词：{path}\n"
                f"→ 检查「分隔符」设置对不对；如果文件只有注释行或全空，也会是 0 条。"
            )

        start = max(0, int(起始索引))
        entries = entries[start:]
        limit = int(加载数量)
        if limit > 0:
            entries = entries[:limit]

        total = len(entries)
        if total == 0:
            raise ValueError(f"起始索引 {start} 已经超过文件里的条目数，这一批没有任何提示词。")

        # 越界防御：宁可停，也不要绕回第一条 —— 否则和另一个批量节点长度不一致时会无限出图
        idx = int(循环位置 or 0)
        forced_stop = False
        if idx < 0 or idx >= total:
            print(f"[GULU工具箱] 提示词循环位置 {idx} 超出范围 0~{total - 1}，按最后一条处理并结束")
            idx = max(0, total - 1)
            forced_stop = True

        prompt = entries[idx]
        name = os.path.basename(path)
        more = (not forced_stop) and (idx + 1 < total)

        # 面板预览：压成一行、截断
        preview = " ".join(prompt.split())
        if len(preview) > 90:
            preview = preview[:90] + "…"

        print(f"[GULU工具箱] 提示词导入 {idx + 1}/{total}：{name}（{enc}） {len(prompt)} 字 "
              f"{'→ 自动继续下一条' if more else '→ 本批最后一条，结束'}")
        print(f"[GULU工具箱] 当前提示词：{preview}")

        return {
            "ui": {"gulu_show": [{
                "mode": "loop",
                "kind": "prompt",
                "name": name,
                "path": path,
                "index": idx,
                "total": total,
                "next": idx + 1,
                "more": more,
                "preview": preview,
                "text": prompt,          # 完整内容，给节点面板滚动显示
                "chars": len(prompt),
                "encoding": enc,
            }]},
            "result": (prompt, name, idx),
        }
