from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from app.document_loader import DocumentPart, display_path


def is_gpu_device(device: str) -> bool:
    """PaddleOCR-VL 只接受显式指定的 CUDA GPU 设备。"""
    return re.fullmatch(r"gpu(?::\d+)?", device) is not None


class PaddleOCRTextBackend:
    """使用轻量 PP-OCRv5 mobile 模型提取按页纯文本。"""

    def __init__(self, *, device: str = "cpu", pipeline: Optional[Any] = None) -> None:
        self.device = device
        self._pipeline = pipeline

    @property
    def name(self) -> str:
        return "paddleocr-v5-mobile"

    def _get_pipeline(self) -> Any:
        if self._pipeline is None:
            try:
                from paddleocr import PaddleOCR
            except ImportError as exc:
                raise RuntimeError(
                    "未安装 PaddleOCR。请使用 Python 3.12 创建 .venv-ocr，"
                    "并安装 requirements-ocr.txt。"
                ) from exc
            self._pipeline = PaddleOCR(
                device=self.device,
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="PP-OCRv5_mobile_rec",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
        return self._pipeline

    def extract(self, path: Path) -> list[DocumentPart]:
        parts: list[DocumentPart] = []
        results = self._get_pipeline().predict(input=str(path))
        for page_number, result in enumerate(results, start=1):
            payload = getattr(result, "json", None)
            if not isinstance(payload, dict):
                continue
            data = payload.get("res", payload)
            texts = data.get("rec_texts", []) if isinstance(data, dict) else []
            text = "\n".join(
                item.strip() for item in texts if isinstance(item, str) and item.strip()
            )
            if not text:
                continue
            parts.append(
                DocumentPart(
                    source=display_path(path),
                    locator=f"第 {page_number} 页（OCR）",
                    text=text,
                )
            )
        return parts


class PaddleOCRVLBackend:
    """使用 PaddleOCR-VL 将扫描 PDF 或图片转换为按页 Markdown。"""

    def __init__(
        self,
        *,
        pipeline_version: str = "v1.6",
        device: str = "cpu",
        pipeline: Optional[Any] = None,
    ) -> None:
        if not is_gpu_device(device):
            raise ValueError(
                "安全限制：PaddleOCR-VL 只允许 --ocr-device gpu 或 gpu:编号；"
                "CPU 推理可能耗尽内存。无 GPU 时请改用 --ocr-backend paddleocr。"
            )
        self.pipeline_version = pipeline_version
        self.device = device
        self._pipeline = pipeline

    @property
    def name(self) -> str:
        return f"paddleocr-vl-{self.pipeline_version}"

    def _get_pipeline(self) -> Any:
        if self._pipeline is None:
            try:
                import paddle
            except ImportError as exc:
                raise RuntimeError(
                    "未安装 PaddlePaddle GPU 版。请按 docs/OCR部署手册.md "
                    "在独立 .venv-ocr 环境中安装。"
                ) from exc
            if not paddle.device.is_compiled_with_cuda():
                raise RuntimeError(
                    "当前 PaddlePaddle 不是 CUDA GPU 版，已停止加载 PaddleOCR-VL，"
                    "避免意外回退到 CPU。"
                )
            gpu_count = paddle.device.cuda.device_count()
            gpu_index = int(self.device.split(":", 1)[1]) if ":" in self.device else 0
            if gpu_index >= gpu_count:
                raise RuntimeError(
                    f"请求使用 GPU {gpu_index}，但 PaddlePaddle 只检测到 {gpu_count} 张 GPU；"
                    "已停止加载 PaddleOCR-VL。"
                )
            try:
                from paddleocr import PaddleOCRVL
            except ImportError as exc:
                raise RuntimeError(
                    "未安装 PaddleOCR-VL。请使用 Python 3.12 创建 .venv-ocr，"
                    "并安装 requirements-ocr.txt。"
                ) from exc
            self._pipeline = PaddleOCRVL(
                pipeline_version=self.pipeline_version,
                device=self.device,
            )
        return self._pipeline

    def extract(self, path: Path) -> list[DocumentPart]:
        parts: list[DocumentPart] = []
        results = self._get_pipeline().predict(input=str(path))
        for page_number, result in enumerate(results, start=1):
            markdown = getattr(result, "markdown", None)
            if not isinstance(markdown, dict):
                continue
            text = markdown.get("markdown_texts") or markdown.get("text") or ""
            if not isinstance(text, str) or not text.strip():
                continue
            parts.append(
                DocumentPart(
                    source=display_path(path),
                    locator=f"第 {page_number} 页（OCR）",
                    text=text.strip(),
                )
            )
        return parts
