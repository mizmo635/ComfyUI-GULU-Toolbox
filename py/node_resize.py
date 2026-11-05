# -*- coding: utf-8 -*-
"""
节点：📐 按边压缩

按长边或短边等比压缩图片，可选把宽高对齐到指定倍数，
输出压缩后的图像，以及压缩后的宽度和高度。

规则：
    · 只缩不放 —— 选定的那条边不大于目标边长时，图片完全不缩放
    · 缩放后按「对齐倍数」向下取整，保证不会越过目标值
    · 未缩放时，宽高输出端口可选给「实际尺寸」或「对齐后尺寸」（此时图像仍保持原样）
"""

import torch

from .core import (
    CATEGORY_RESIZE, EDGE_MODES, FALLBACK_MODES,
    align_down, calc_edge_resize, calc_forced_size, pil_to_tensor,
    resize_image, tensor_to_pil,
)


class ImageEdgeResizeCN:
    """📐 按边压缩：长边 / 短边 + 目标边长 + 对齐倍数"""

    CATEGORY = CATEGORY_RESIZE
    FUNCTION = "run"
    RETURN_TYPES = ("IMAGE", "INT", "INT")
    RETURN_NAMES = ("图像", "宽度", "高度")
    OUTPUT_NODE = True
    DESCRIPTION = (
        "按长边或短边等比压缩图片，可选把宽高对齐到 8/16/32/64 等倍数，"
        "输出压缩后的图像与宽高，节点下方直接显示计算结果。原图不大于设定值时不会放大。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE", {
                    "tooltip": "要压缩的图像（支持批量，逐张按同一套规则处理）。",
                }),
                "基准边": (EDGE_MODES, {
                    "default": "长边",
                    "tooltip": "长边：按画面较长的那条边缩放（竖图就按高度算）；短边：按较短的那条边缩放。",
                }),
                "目标边长": ("INT", {
                    "default": 1024, "min": 8, "max": 16384, "step": 8,
                    "tooltip": "希望基准边压缩到的像素值。只缩不放 —— 原图这条边比它小就保持原样。",
                }),
                "对齐倍数": ("INT", {
                    "default": 8, "min": 0, "max": 256, "step": 1,
                    "tooltip": "把宽高各自向下取整到这个倍数（常用 8 / 16 / 32 / 64）。填 0 或 1 = 不对齐。",
                }),
                "未缩放时尺寸": (FALLBACK_MODES, {
                    "default": "输出实际尺寸",
                    "tooltip": "原图没超过目标边长时不会缩放，这里决定右侧「宽度 / 高度」端口输出什么值：\n· 输出实际尺寸 —— 原图是多少就多少\n· 输出对齐后尺寸 —— 原图宽高各自向下对齐到倍数\n· 强制按目标边长计算 —— 不管原图多小，都按「目标边长 + 对齐倍数」算出宽高\n三种都只改数值，图像本身保持原样。",
                }),
            },
        }

    def run(self, image, 基准边, 目标边长, 对齐倍数, 未缩放时尺寸):
        if image is None or len(image) == 0:
            raise ValueError("没有输入图像")

        # 兼容偶发的 3 维单图张量（标准是 [B,H,W,C]）
        if hasattr(image, "dim") and image.dim() == 3:
            image = image.unsqueeze(0)

        outs = []
        out_w = out_h = 0
        src_size = (0, 0)
        scaled_any = False

        for i in range(len(image)):
            pil = tensor_to_pil(image[i])
            w, h = pil.size
            if i == 0:
                src_size = (w, h)

            tw, th, scaled = calc_edge_resize(w, h, 基准边, 目标边长, 对齐倍数)

            if scaled:
                pil = resize_image(pil, tw, th)
                ow, oh = tw, th
                scaled_any = True
            else:
                # 不缩放：图像原封不动，只决定尺寸端口给什么值
                if 未缩放时尺寸 == "输出对齐后尺寸":
                    ow, oh = align_down(w, h, 对齐倍数)
                elif 未缩放时尺寸 == "强制按目标边长计算":
                    ow, oh = calc_forced_size(w, h, 基准边, 目标边长, 对齐倍数)
                else:
                    ow, oh = w, h

            if i == 0:
                out_w, out_h = ow, oh
            outs.append(pil_to_tensor(pil))

        if len(outs) > 1:
            try:
                out_tensor = torch.stack(outs, dim=0)
            except Exception:
                # 批量里尺寸不一致时无法合成一个张量，只往下传第一张
                print("[YSQ工具箱] 按边压缩：批量内图像尺寸不一致，只输出第一张")
                out_tensor = outs[0].unsqueeze(0)
        else:
            out_tensor = outs[0].unsqueeze(0)

        n = len(image)
        state = "已缩放" if scaled_any else "未缩放(原图未超过目标边长)"
        print(
            f"[YSQ工具箱] 按边压缩：{src_size[0]}×{src_size[1]} -> {out_w}×{out_h}"
            f" | {state} | 基准{基准边} {目标边长} | 对齐 {对齐倍数} | {n} 张"
        )

        return {
            "ui": {
                "ysq_resize": [{
                    "width": int(out_w),
                    "height": int(out_h),
                    "src_width": src_size[0],
                    "src_height": src_size[1],
                    "scaled": bool(scaled_any),
                    "count": n,
                    "edge": 基准边,
                    "target": int(目标边长),
                    "align": int(对齐倍数),
                    "mode": 未缩放时尺寸,
                }],
            },
            "result": (out_tensor, int(out_w), int(out_h)),
        }
