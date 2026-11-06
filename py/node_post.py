# -*- coding: utf-8 -*-
"""
节点主体：🖼️ 图像后处理导出

一个节点干完三件事：
    ① 压缩：PNG / JPG 二选一，PNG 有压缩等级、JPG 有质量档
    ② 水印：按九宫格 / 平铺自动贴水印图，可调大小、透明度、旋转、边距
    ③ 保存：落 output 指定子目录，或任意绝对路径
"""

import os
from datetime import datetime

import folder_paths
import torch

from .core import (
    CATEGORY, FORMATS, IMAGE_EXTS, PNG_META_MODES, SAVE_MODES, SAVE_SWITCH, TEXT_META_KEY,
    WM_POSITIONS, WM_SOURCES,
    apply_watermark, encode_image, ext_of, human_size, limit_long_edge, list_watermark_files,
    load_watermark, pil_to_tensor, resolve_save_dir, tensor_to_pil, timed_filepath,
)
from .meta import build_meta, embed


def _input_dir():
    try:
        return folder_paths.get_input_directory()
    except Exception:
        return ""


def _watermark_choices():
    """水印候选列表（相对 input 目录的路径）。"""
    files = list_watermark_files(_input_dir())
    # 常用文件名始终保留在列表里，避免「工作流里的旧值不在列表中」导致提交失败
    if "watermarks/logo.png" not in files:
        files.append("watermarks/logo.png")
    return files


def _wm_exists(rel):
    """列表里的相对路径是否真能落到磁盘上。"""
    if not rel or rel.startswith("（"):
        return False
    path = rel if os.path.isabs(rel) else os.path.join(_input_dir(), rel.replace("/", os.sep))
    return os.path.isfile(path)


def _watermark_default(files):
    """默认选中项：watermarks 目录里真实存在的一张，其次 input 里真实存在的第一张。"""
    real = [f for f in files if not f.startswith("（")]
    for f in real:
        if f.startswith("watermarks/") and _wm_exists(f):
            return f
    for f in real:
        if _wm_exists(f):
            return f
    return "watermarks/logo.png"


class ImagePostExportCN:
    """图像后处理导出：压缩 / 水印 / 保存"""

    CATEGORY = CATEGORY
    FUNCTION = "run"
    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("处理后的图像", "保存路径", "分辨率信息")
    OUTPUT_NODE = True
    DESCRIPTION = (
        "一步做完图片后处理：按 PNG / JPG 压缩保存、自动贴透明底水印、可指定保存目录。"
        "处理后的图像可以从左侧输出端口继续接下游节点。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        wm_files = _watermark_choices()
        wm_default = _watermark_default(wm_files)
        return {
            "required": {
                "image": ("IMAGE", {"tooltip": "要处理的图像（支持批量，会逐张处理并保存）。"}),

                # ---------------- ① 压缩 ----------------
                "保存格式": (FORMATS, {
                    "default": "PNG",
                    "tooltip": "PNG：无损，体积较大，可把工作流一起写进图片；JPG：有损，体积小，用质量档控制画质。",
                }),
                "JPG质量": ("INT", {
                    "default": 92, "min": 1, "max": 100, "step": 1, "display": "slider",
                    "tooltip": "仅保存格式为 JPG 时生效。90~95 画质与体积比较平衡；低于 70 会出现明显压缩痕迹。",
                }),
                "PNG压缩等级": ("INT", {
                    "default": 6, "min": 0, "max": 9, "step": 1, "display": "slider",
                    "tooltip": "仅保存格式为 PNG 时生效。0 最快但文件最大，9 最慢但文件最小；PNG 压缩是无损的，画面完全不变。",
                }),
                "PNG写入工作流": (PNG_META_MODES, {
                    "default": "写入",
                    "tooltip": "仅保存格式为 PNG 时生效。写入：把完整工作流写进图片，拖回 ComfyUI 能还原整条链路（与官方保存节点一致）。"
                               "不写入：不写任何元数据。只写提示词：只把 text 端口接进来的文字写进图片，不写工作流 JSON（体积更小）。",
                }),
                "最长边限制": ("INT", {
                    "default": 0, "min": 0, "max": 16384, "step": 64,
                    "tooltip": "等比缩小到不超过这个像素值，用来顺带压体积。0 = 保持原始分辨率。",
                }),

                # ---------------- ② 水印 ----------------
                "水印来源": (WM_SOURCES, {
                    "default": "关闭",
                    "tooltip": "关闭：不加水印。水印文件：用你准备好的透明底 PNG（放进 ComfyUI/input/watermarks 目录，会自动出现在下面的列表里）。接入图像端口：用右侧 watermark 端口接进来的图。",
                }),
                "水印文件": (wm_files, {
                    "default": wm_default,
                    "tooltip": "水印图路径（相对 ComfyUI 的 input 目录）。推荐把你那张透明底 PNG 放到 input/watermarks/ 目录，重载工作流后就会出现在列表最前面。",
                }),
                "水印位置": (WM_POSITIONS, {
                    "default": "右下",
                    "tooltip": "九宫格任选一格（左上 / 正上 / 右上 / 左中 / 正中 / 右中 / 左下 / 正下 / 右下）；选「平铺」则铺满整幅画面，配合旋转做版权水印。",
                }),
                "水印宽度": ("INT", {
                    "default": 15, "min": 1, "max": 100, "step": 1, "display": "slider",
                    "tooltip": "水印宽度占画面宽度的百分比，高度自动等比。平铺模式下它决定单个水印的大小。",
                }),
                "不透明度": ("INT", {
                    "default": 100, "min": 0, "max": 100, "step": 1, "display": "slider",
                    "tooltip": "水印整体透明度。想低调一点给 60~80。",
                }),
                "旋转角度": ("INT", {
                    "default": 0, "min": 0, "max": 359, "step": 1,
                    "tooltip": "逆时针旋转角度。平铺时常配 30~45 度做斜向水印。",
                }),
                "水印边距X": ("INT", {
                    "default": 32, "min": 0, "max": 4096, "step": 1,
                    "tooltip": "距画面左 / 右边缘的像素距离（平铺模式不使用）。",
                }),
                "水印边距Y": ("INT", {
                    "default": 32, "min": 0, "max": 4096, "step": 1,
                    "tooltip": "距画面上 / 下边缘的像素距离（平铺模式不使用）。",
                }),
                "水印间距": ("INT", {
                    "default": 48, "min": 0, "max": 4096, "step": 1,
                    "tooltip": "仅「平铺」模式生效：相邻水印之间的像素间隔。",
                }),

                # ---------------- ③ 保存 ----------------
                "保存位置": (SAVE_MODES, {
                    "default": "输出目录（output）",
                    "tooltip": "输出目录：存到 ComfyUI 的 output 下，可以在下方填子文件夹。自定义绝对路径：存到硬盘任意位置，例如 G:/出图/成品。",
                }),
                "子文件夹": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "tooltip": "保存位置为「输出目录」时生效。相对 output 的子目录，可以写多级，例如 项目A/第一版。留空 = 直接存 output 根目录。",
                }),
                "自定义路径": ("STRING", {
                    "default": "G:/图像后处理输出",
                    "multiline": False,
                    "tooltip": "保存位置为「自定义绝对路径」时生效。填完整路径，目录不存在会自动创建。",
                }),
                "文件名前缀": ("STRING", {
                    "default": "图像后处理",
                    "multiline": False,
                    "tooltip": "保存文件名 = 前缀_年月日时分秒.扩展名，例如 图像后处理_20261017195938.png。后缀就是纯数字时间，同一秒存多张时秒数自动顺延，绝不覆盖已有文件。",
                }),
                "落盘开关": (SAVE_SWITCH, {
                    "default": "保存",
                    "tooltip": "保存：把图片写到磁盘。不保存：只把处理后的图像从输出端口往下传，不写任何文件（调参数时省得留垃圾）。",
                }),
            },
            "optional": {
                "watermark": ("IMAGE", {
                    "tooltip": "水印来源选「接入图像端口」时，从这里接水印图（支持上游节点生成的图）。带透明通道的图最好。",
                }),
                "text": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "tooltip": "要写进图片里的文本（提示词等）。可以从上游文本节点接进来，也可以直接手填。"
                               "节点一定等这一轮的 image 真正到齐后才执行，所以文字先到还是图片先到都不会写错轮次；"
                               "留空 = 不写文本。配合下面的「PNG写入工作流 = 只写提示词」可以只存提示词、不存整条工作流。",
                }),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    # ---------------------------------------------------------------- 内部工具

    @staticmethod
    def _resolve_watermark(水印来源, 水印文件, watermark):
        """返回 (PIL.RGBA 水印图 或 None, 提示信息)"""
        if 水印来源 == "关闭":
            return None, ""
        if 水印来源 == "接入图像端口":
            if watermark is None:
                raise ValueError("水印来源选了「接入图像端口」，但没有接 watermark 图像输入")
            wm = tensor_to_pil(watermark).convert("RGBA")
            return wm, "水印来源：图像端口"
        # 水印文件
        rel = str(水印文件 or "").strip()
        if not rel or rel.startswith("（"):
            raise ValueError("还没有可用的水印文件，请把水印图放进 ComfyUI/input/watermarks 目录后重新选择")
        path = rel if os.path.isabs(rel) else os.path.join(_input_dir(), rel.replace("/", os.sep))
        wm = load_watermark(path)
        return wm, f"水印文件：{rel}"

    # ---------------------------------------------------------------- 执行

    def run(self, image, 保存格式, JPG质量, PNG压缩等级, PNG写入工作流, 最长边限制,
            水印来源, 水印文件, 水印位置, 水印宽度, 不透明度, 旋转角度,
            水印边距X, 水印边距Y, 水印间距,
            保存位置, 子文件夹, 自定义路径, 文件名前缀, 落盘开关,
            watermark=None, text=None, prompt=None, extra_pnginfo=None):

        if image is None or len(image) == 0:
            raise ValueError("没有输入图像")

        # 兼容偶发的 3 维单图张量（标准是 [B,H,W,C]）
        if hasattr(image, "dim") and image.dim() == 3:
            image = image.unsqueeze(0)

        fmt = 保存格式
        ext = ext_of(fmt)

        wm, wm_note = self._resolve_watermark(水印来源, 水印文件, watermark)

        # ---------------- 文字：只在本轮图像真的到齐后才写 ----------------
        # 时序说明：ComfyUI 是依赖驱动的，节点必须等 image（以及已接线的 text）
        # 全部就绪才会被调用一次；批量导入循环里每一轮同样是「一次完整调用」，
        # 所以这里拿到的 text 必然是当前这一轮的值 —— 文字先到或图片先到都不会串轮。
        txt = str(text or "").strip()

        # 元数据只整理一次
        meta = {}
        if fmt == "PNG":
            if PNG写入工作流 == "写入":
                meta = build_meta(prompt, extra_pnginfo)
                if txt:
                    meta[TEXT_META_KEY] = txt
            elif PNG写入工作流 == "只写提示词":
                if txt:
                    meta = {TEXT_META_KEY: txt}
        elif txt:
            # JPG 没有工作流开关：接了文字就直接写进 COM 注释段
            meta = {TEXT_META_KEY: txt}

        # 保存目录（仅保存时才需要）
        do_save = 落盘开关 == "保存"
        save_dir = ""
        if do_save:
            save_dir = resolve_save_dir(
                保存位置, 子文件夹, 自定义路径, folder_paths.get_output_directory()
            )
        prefix = str(文件名前缀 or "").strip() or "图像后处理"
        # 前缀里的非法字符清掉，避免 Windows 直接拒绝写文件
        prefix = "".join("_" if c in '\\/:*?"<>|' else c for c in prefix).strip(" .") or "图像后处理"

        outs = []
        paths = []
        infos = []
        stamp = datetime.now()
        saved_bytes = 0
        batch_n = len(image)

        for i in range(batch_n):
            pil = tensor_to_pil(image[i])
            pil = limit_long_edge(pil, 最长边限制)
            if wm is not None:
                pil = apply_watermark(
                    pil, wm, 水印位置,
                    margin_x=水印边距X, margin_y=水印边距Y, gap=水印间距,
                    width_pct=水印宽度, opacity=不透明度, rotate=旋转角度,
                )
            if pil.mode != "RGB":
                pil = pil.convert("RGB")

            data = encode_image(pil, fmt, jpg_quality=JPG质量, png_level=PNG压缩等级)
            if meta:
                data, _ok = embed(data, ext, meta)

            saved_bytes += len(data)
            w, h = pil.size
            infos.append(f"{w} × {h}")

            if do_save:
                path, stamp = timed_filepath(save_dir, prefix, ext, stamp)
                with open(path, "wb") as f:
                    f.write(data)
                paths.append(path)
            else:
                paths.append("（未写入文件）")

            outs.append(pil_to_tensor(pil))

        # 尺寸一致就整批输出，不一致只输出第一张（ComfyUI 的图像张量要求同尺寸）
        if len(outs) > 1:
            try:
                out_tensor = torch.stack(outs, dim=0)
            except Exception:
                out_tensor = outs[0].unsqueeze(0)
        else:
            out_tensor = outs[0].unsqueeze(0)

        if batch_n > 1:
            dim_text = f"{batch_n} 张 ｜ " + " ".join(sorted(set(infos)))
        else:
            dim_text = infos[0]

        log_lines = [f"图像后处理：{dim_text} ｜ {fmt} ｜ {human_size(saved_bytes)}"]
        if wm is not None:
            log_lines.append(
                f"水印：{wm_note} / 位置 {水印位置} / 宽度 {水印宽度}% / 不透明 {不透明度}%"
            )
        if do_save:
            log_lines.append(f"已保存到：{save_dir}")
        else:
            log_lines.append("未写入文件（只输出处理后的图像）")

        if TEXT_META_KEY in meta:
            log_lines.append(f"已嵌入提示词：{len(txt)} 字（键名 {TEXT_META_KEY}）")
        elif fmt == "PNG" and PNG写入工作流 == "只写提示词":
            log_lines.append("只写提示词：text 输入是空的，本次没有写入任何元数据")
        elif txt and fmt == "PNG" and PNG写入工作流 == "不写入":
            log_lines.append("提示词未嵌入（PNG写入工作流 = 不写入）")
        for line in log_lines:
            print("[GULU工具箱] " + line)

        return {
            "result": (out_tensor, "\n".join(paths), dim_text),
        }
