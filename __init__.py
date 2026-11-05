# -*- coding: utf-8 -*-
"""
YSQ工具箱（ComfyUI-YSQ-Toolbox）
------------------------------------------------------------------
自用节点合集。目前包含：

    🖼️ 图像后处理导出   —— 压缩（PNG / JPG）+ 贴透明底水印 + 保存到指定目录
    📐 按边压缩         —— 按长边 / 短边等比压缩，可对齐到指定倍数，输出宽高
    📂 图像批量顺序导入 —— 逐张读图，每张跑完自动下一张，全部跑完自动结束
    🔁 LoRA 轮转         —— 登记多个 LoRA，一次运行逐个出图

仅依赖 ComfyUI 自带环境（Pillow / numpy / torch），无需额外安装。
"""

from .py.node_post import ImagePostExportCN
from .py.node_resize import ImageEdgeResizeCN
from .py.node_batch_loader import ImageBatchLoopLoader
from .py.node_lora_cycle import LoRACycleCN

NODE_CLASS_MAPPINGS = {
    # 注意：ImagePostExportCN 这个 ID 不要改，已有工作流引用了它
    "ImagePostExportCN": ImagePostExportCN,
    "ImageEdgeResizeCN": ImageEdgeResizeCN,
    "ImageBatchLoopLoader": ImageBatchLoopLoader,
    "LoRACycleCN": LoRACycleCN,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ImagePostExportCN": "🖼️ 图像后处理导出",
    "ImageEdgeResizeCN": "📐 按边压缩",
    "ImageBatchLoopLoader": "📂 图像批量顺序导入",
    "LoRACycleCN": "🔁 LoRA 轮转",
}

WEB_DIRECTORY = "./web"

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]

print("[YSQ工具箱] 已加载：图像后处理导出 / 按边压缩 / 图像批量顺序导入 / LoRA 轮转")
