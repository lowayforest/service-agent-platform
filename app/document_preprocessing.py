from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Protocol, Sequence
from zipfile import BadZipFile, ZipFile

from app.document_loader import DocumentPart, load_document


AUDITABLE_SUFFIXES = {".doc", ".docx", ".md", ".pdf", ".txt", ".xlsx"}
NATIVE_SUFFIXES = {".docx", ".md", ".pdf", ".txt", ".xlsx"}
OCR_IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


@dataclass(frozen=True)
class DocumentAudit:
    source: str
    suffix: str
    size_bytes: int
    sha256: str
    classification: str
    supported: bool
    page_count: Optional[int] = None
    sampled_pages: Optional[int] = None
    text_pages: Optional[int] = None
    low_text_pages: Optional[int] = None
    extracted_characters: Optional[int] = None
    embedded_images: Optional[int] = None
    error: Optional[str] = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PreprocessResult:
    source: str
    status: str
    parser: str
    output: Optional[str]
    parts: int
    text_characters: int
    warnings: List[str]

    def as_dict(self) -> dict:
        return asdict(self)


class OCRBackend(Protocol):
    """OCR 后端约定；实际实现放在独立 OCR 环境中。"""

    name: str

    def extract(self, path: Path) -> List[DocumentPart]:
        """从扫描 PDF 或图片型文档中返回带页码/位置的文本。"""


def display_path(path: Path, base: Optional[Path] = None) -> str:
    resolved = path.resolve()
    reference = (base or Path.cwd()).resolve()
    try:
        return resolved.relative_to(reference).as_posix()
    except ValueError:
        return resolved.name


def discover_documents(inputs: Iterable[Path]) -> List[Path]:
    files: List[Path] = []
    for item in inputs:
        path = item.expanduser()
        if path.is_file():
            if not path.name.startswith("."):
                files.append(path)
            continue
        if path.is_dir():
            files.extend(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file()
                and not any(part.startswith(".") for part in candidate.relative_to(path).parts)
            )
    return sorted(set(files))


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def classify_pdf_page_lengths(lengths: Sequence[int], minimum_text_characters: int) -> str:
    if not lengths or all(length < minimum_text_characters for length in lengths):
        return "scan"
    if all(length >= minimum_text_characters for length in lengths):
        return "text"
    return "mixed"


def _audit_pdf(
    path: Path,
    page_limit: int,
    minimum_text_characters: int,
) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(str(path), strict=False)
    page_count = len(reader.pages)
    sample_count = page_count if page_limit <= 0 else min(page_limit, page_count)
    lengths: List[int] = []
    for page in reader.pages[:sample_count]:
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        lengths.append(len(text))
    low_pages = sum(length < minimum_text_characters for length in lengths)
    return {
        "classification": classify_pdf_page_lengths(lengths, minimum_text_characters),
        "page_count": page_count,
        "sampled_pages": sample_count,
        "text_pages": sample_count - low_pages,
        "low_text_pages": low_pages,
        "extracted_characters": sum(lengths),
    }


def count_docx_images(path: Path) -> int:
    try:
        with ZipFile(path) as archive:
            return sum(
                name.startswith("word/media/") and not name.endswith("/")
                for name in archive.namelist()
            )
    except (BadZipFile, OSError):
        return 0


def audit_document(
    path: Path,
    *,
    base: Optional[Path] = None,
    pdf_page_limit: int = 5,
    minimum_text_characters: int = 30,
) -> DocumentAudit:
    suffix = path.suffix.lower()
    common = {
        "source": display_path(path, base),
        "suffix": suffix or "[no extension]",
        "size_bytes": 0,
        "sha256": "",
    }
    try:
        common["size_bytes"] = path.stat().st_size
        common["sha256"] = sha256_file(path)
        if suffix == ".pdf":
            return DocumentAudit(supported=True, **common, **_audit_pdf(
                path, pdf_page_limit, minimum_text_characters
            ))
        if suffix == ".doc":
            return DocumentAudit(
                classification="legacy_doc", supported=False, **common
            )
        if suffix == ".docx":
            image_count = count_docx_images(path)
            return DocumentAudit(
                classification="docx_with_images" if image_count else "native_text",
                supported=True,
                embedded_images=image_count,
                **common,
            )
        if suffix in NATIVE_SUFFIXES:
            return DocumentAudit(
                classification="native_text", supported=True, **common
            )
        return DocumentAudit(
            classification="unsupported", supported=False, **common
        )
    except Exception as exc:
        return DocumentAudit(
            classification="error",
            supported=suffix in AUDITABLE_SUFFIXES,
            error=str(exc),
            **common,
        )


def summarize_audits(records: Sequence[DocumentAudit]) -> dict:
    classifications = Counter(record.classification for record in records)
    suffixes = Counter(record.suffix for record in records)
    return {
        "documents": len(records),
        "classifications": dict(sorted(classifications.items())),
        "suffixes": dict(sorted(suffixes.items())),
        "total_size_bytes": sum(record.size_bytes for record in records),
        "pdf_pages": sum(record.page_count or 0 for record in records),
        "errors": sum(record.error is not None for record in records),
    }


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


class LegacyDocConverter:
    def __init__(self, executable: Optional[str] = None) -> None:
        self.executable = (
            executable
            if executable is not None
            else shutil.which("soffice") or shutil.which("libreoffice")
        )

    @property
    def available(self) -> bool:
        return bool(self.executable)

    def convert_to_docx(self, source: Path, output_dir: Path) -> Path:
        if not self.executable:
            raise RuntimeError("未找到 LibreOffice/soffice，无法转换旧版 .doc 文件。")
        output_dir.mkdir(parents=True, exist_ok=True)
        process = subprocess.run(
            [
                self.executable,
                "--headless",
                "--convert-to",
                "docx",
                "--outdir",
                str(output_dir),
                str(source),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        converted = output_dir / f"{source.stem}.docx"
        if process.returncode != 0 or not converted.exists():
            detail = (process.stderr or process.stdout).strip()
            raise RuntimeError(f"DOC 转换失败：{detail or '未生成 DOCX 文件'}")
        return converted


def parts_to_markdown(source: str, parts: Sequence[DocumentPart]) -> str:
    sections = [f"# {Path(source).name}", "", f"> 原始来源：`{source}`", ""]
    for part in parts:
        sections.extend([f"## {part.locator}", "", part.text.strip(), ""])
    return "\n".join(sections).rstrip() + "\n"


def output_path_for(source: Path, output_dir: Path, base: Optional[Path] = None) -> Path:
    reference = (base or Path.cwd()).resolve()
    resolved = source.resolve()
    try:
        relative = resolved.relative_to(reference)
    except ValueError:
        relative = Path(source.name)
    return output_dir / relative.parent / f"{relative.name}.md"


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def extract_docx_image_parts(
    source: Path,
    ocr_backend: OCRBackend,
) -> tuple[List[DocumentPart], List[str]]:
    parts: List[DocumentPart] = []
    warnings: List[str] = []
    with ZipFile(source) as archive, tempfile.TemporaryDirectory(
        prefix="service-agent-docx-images-"
    ) as temporary:
        image_names = sorted(
            name
            for name in archive.namelist()
            if name.startswith("word/media/") and not name.endswith("/")
        )
        for image_number, image_name in enumerate(image_names, start=1):
            suffix = Path(image_name).suffix.lower()
            if suffix not in OCR_IMAGE_SUFFIXES:
                warnings.append(
                    f"内嵌图片 {image_number} 的格式 {suffix or '未知'} 暂不支持 OCR。"
                )
                continue
            image_path = Path(temporary) / f"image-{image_number}{suffix}"
            image_path.write_bytes(archive.read(image_name))
            try:
                image_parts = ocr_backend.extract(image_path)
            except Exception as exc:
                warnings.append(f"内嵌图片 {image_number} OCR 失败：{exc}")
                continue
            if not image_parts:
                warnings.append(f"内嵌图片 {image_number} 未识别到文字。")
                continue
            for part in image_parts:
                parts.append(
                    DocumentPart(
                        source=display_path(source),
                        locator=f"内嵌图片 {image_number} / {part.locator}",
                        text=part.text,
                    )
                )
    return parts, warnings


def preprocess_document(
    source: Path,
    output_dir: Path,
    *,
    base: Optional[Path] = None,
    audit: Optional[DocumentAudit] = None,
    converter: Optional[LegacyDocConverter] = None,
    ocr_backend: Optional[OCRBackend] = None,
) -> PreprocessResult:
    record = audit or audit_document(source, base=base, pdf_page_limit=0)
    warnings: List[str] = []
    parser = "native"
    parts: List[DocumentPart] = []

    if record.classification in {"unsupported", "error"}:
        return PreprocessResult(
            source=record.source,
            status=record.classification,
            parser="none",
            output=None,
            parts=0,
            text_characters=0,
            warnings=[record.error] if record.error else ["文件格式暂不支持。"],
        )

    try:
        path_to_load = source
        if source.suffix.lower() == ".doc":
            active_converter = converter or LegacyDocConverter()
            if not active_converter.available:
                return PreprocessResult(
                    source=record.source,
                    status="needs_conversion",
                    parser="none",
                    output=None,
                    parts=0,
                    text_characters=0,
                    warnings=["需要先使用 LibreOffice 将 .doc 转换为 .docx。"],
                )
            with tempfile.TemporaryDirectory(prefix="service-agent-doc-") as temporary:
                path_to_load = active_converter.convert_to_docx(source, Path(temporary))
                parts = load_document(path_to_load)
            parser = "libreoffice+python-docx"
        elif source.suffix.lower() == ".pdf" and record.classification in {"scan", "mixed"}:
            if ocr_backend is not None:
                parts = ocr_backend.extract(source)
                parser = ocr_backend.name
            else:
                if record.classification == "scan":
                    return PreprocessResult(
                        source=record.source,
                        status="needs_ocr",
                        parser="none",
                        output=None,
                        parts=0,
                        text_characters=0,
                        warnings=[
                            f"检测到 {record.low_text_pages or 0}/{record.sampled_pages or 0} "
                            "个低文本页面，需要 OCR。"
                        ],
                    )
                parts = load_document(source)
                warnings.append(
                    f"检测到 {record.low_text_pages or 0}/{record.sampled_pages or 0} 个低文本页面，需要 OCR。"
                )
        else:
            parts = load_document(path_to_load)
            parser = {
                ".docx": "python-docx",
                ".pdf": "pypdf",
                ".xlsx": "openpyxl",
            }.get(source.suffix.lower(), "text")
    except Exception as exc:
        return PreprocessResult(
            source=record.source,
            status="error",
            parser=parser,
            output=None,
            parts=0,
            text_characters=0,
            warnings=[str(exc)],
        )

    if record.embedded_images:
        if ocr_backend is None:
            warnings.append(f"DOCX 包含 {record.embedded_images} 个内嵌图片，图片文字尚需 OCR。")
        else:
            try:
                image_parts, image_warnings = extract_docx_image_parts(source, ocr_backend)
                parts.extend(image_parts)
                warnings.extend(image_warnings)
                parser = f"{parser}+{ocr_backend.name}"
            except Exception as exc:
                warnings.append(f"DOCX 内嵌图片提取失败：{exc}")

    if not parts:
        return PreprocessResult(
            source=record.source,
            status="empty",
            parser=parser,
            output=None,
            parts=0,
            text_characters=0,
            warnings=warnings or ["未提取到可用文本。"],
        )

    destination = output_path_for(source, output_dir, base)
    _write_text_atomic(destination, parts_to_markdown(record.source, parts))
    status = "ready"
    if warnings:
        status = "partial_needs_image_ocr" if record.embedded_images else "partial_needs_ocr"
    return PreprocessResult(
        source=record.source,
        status=status,
        parser=parser,
        output=display_path(destination, base),
        parts=len(parts),
        text_characters=sum(len(part.text) for part in parts),
        warnings=warnings,
    )
