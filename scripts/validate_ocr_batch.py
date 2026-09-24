from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from app.document_preprocessing import discover_documents, sha256_file


OCR_PAGE_HEADING = re.compile(r"^## 第\s+(\d+)\s+页（OCR）\s*$", re.MULTILINE)
IMAGE_REFERENCE = re.compile(
    r"(?:!\[[^\]]*\]\([^)]*\)|<img\b)",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class OCRValidationRecord:
    source: str
    sha256: str
    manifest_status: str
    parser: str
    output: Optional[str]
    passed: bool
    total_pages: int
    ocr_pages: list[int]
    confirmed_blank_pages: list[int]
    uncovered_pages: list[int]
    duplicate_page_labels: list[int]
    out_of_range_page_labels: list[int]
    text_characters: int
    html_tables: int
    image_references: int
    issues: list[str]
    manual_review: str = "pending"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="核对 OCR 批次的页码覆盖、空白页、输出结构和处理台账。"
    )
    parser.add_argument("sources", nargs="+", type=Path, help="原始 PDF 文件或目录")
    parser.add_argument(
        "--manifest",
        required=True,
        action="append",
        type=Path,
        help="预处理生成的 JSONL 台账；合并多个批次时可重复传入",
    )
    parser.add_argument(
        "--report",
        required=True,
        type=Path,
        help="供人工查看的 Markdown 验收报告",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        help="机器可读 JSON 报告；默认与 Markdown 报告同名、后缀为 .json",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="自动检查有失败项时返回状态码 1",
    )
    parser.add_argument(
        "--ocr-only",
        action="store_true",
        help=(
            "只检查台账中状态为 ready 且解析器名称包含 paddleocr 的 PDF；"
            "用于从完整资料目录中排除原生文本 PDF 和重复副本"
        ),
    )
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} 第 {line_number} 行不是有效 JSON：{exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path} 第 {line_number} 行必须是 JSON 对象")
            records.append(value)
    return records


def extract_ocr_page_numbers(markdown: str) -> list[int]:
    return [int(value) for value in OCR_PAGE_HEADING.findall(markdown)]


def _pdf_page_count(path: Path) -> int:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("缺少 pypdf；请在 .venv-ocr 中运行本命令。") from exc
    return len(PdfReader(str(path), strict=False).pages)


def _is_exact_white_pdf_page(document: Any, page_number: int) -> bool:
    page = document[page_number - 1]
    bitmap = None
    try:
        bitmap = page.render(scale=0.25)
        grayscale = bitmap.to_pil().convert("L")
        return grayscale.getextrema() == (255, 255)
    finally:
        if bitmap is not None and hasattr(bitmap, "close"):
            bitmap.close()
        page.close()


def detect_confirmed_blank_pages(path: Path, candidates: Sequence[int]) -> list[int]:
    """Return only pages whose low-resolution rendering is completely white.

    The deliberately conservative rule prevents a faint scan from being accepted
    as blank.  Pages that cannot be proven blank remain uncovered and fail the
    automatic check until a person reviews them.
    """
    if not candidates:
        return []
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError(
            "存在未输出页面，但缺少 pypdfium2，无法确认其是否为空白页；"
            "请在安装 requirements-ocr.txt 的 .venv-ocr 中运行。"
        ) from exc

    document = pdfium.PdfDocument(str(path))
    try:
        return [
            page_number
            for page_number in candidates
            if _is_exact_white_pdf_page(document, page_number)
        ]
    finally:
        document.close()


def _match_manifest_record(
    source: Path,
    records: Sequence[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    source_posix = source.as_posix()
    resolved_posix = source.resolve().as_posix()

    def path_matches(record: dict[str, Any]) -> bool:
        record_source = str(record.get("source", "")).replace("\\", "/")
        if record_source.startswith("./"):
            record_source = record_source[2:]
        record_source_path = str(record.get("source_path", "")).replace("\\", "/")
        return bool(
            record_source
            and (
                record_source in {source_posix, resolved_posix, source.name}
                or resolved_posix.endswith("/" + record_source)
                or record_source_path == resolved_posix
            )
        )

    exact = [record for record in records if path_matches(record)]
    if len(exact) == 1:
        return exact[0]

    by_name = [
        record
        for record in records
        if Path(str(record.get("source", ""))).name == source.name
    ]
    return by_name[0] if len(by_name) == 1 else None


def _is_ocr_manifest_record(record: Optional[dict[str, Any]]) -> bool:
    if not record or record.get("status") != "ready":
        return False
    return "paddleocr" in str(record.get("parser", "")).lower()


def select_ocr_sources(
    sources: Sequence[Path],
    manifest_records: Sequence[dict[str, Any]],
) -> list[Path]:
    return [
        source
        for source in sources
        if _is_ocr_manifest_record(_match_manifest_record(source, manifest_records))
    ]


def _resolve_output(record: Optional[dict[str, Any]], cwd: Path) -> Optional[Path]:
    if not record or not record.get("output"):
        return None
    output = Path(str(record["output"])).expanduser()
    return output if output.is_absolute() else cwd / output


def validate_document(
    source: Path,
    manifest_record: Optional[dict[str, Any]],
    *,
    cwd: Optional[Path] = None,
) -> OCRValidationRecord:
    reference = (cwd or Path.cwd()).resolve()
    issues: list[str] = []
    status = str(manifest_record.get("status", "missing")) if manifest_record else "missing"
    parser = str(manifest_record.get("parser", "unknown")) if manifest_record else "unknown"
    output_path = _resolve_output(manifest_record, reference)
    total_pages = 0
    markdown = ""

    if manifest_record is None:
        issues.append("处理台账中没有找到唯一对应记录。")
    elif status != "ready":
        issues.append(f"处理状态不是 ready，而是 {status}。")
    if manifest_record and manifest_record.get("warnings"):
        issues.append("处理台账包含警告：" + "；".join(map(str, manifest_record["warnings"])))

    try:
        total_pages = _pdf_page_count(source)
    except Exception as exc:
        issues.append(f"无法读取原 PDF 页数：{exc}")

    if output_path is None:
        issues.append("处理台账未记录输出文件。")
    elif not output_path.is_file():
        issues.append(f"输出文件不存在：{output_path}")
    else:
        try:
            markdown = output_path.read_text(encoding="utf-8")
        except Exception as exc:
            issues.append(f"无法读取输出 Markdown：{exc}")

    page_labels = extract_ocr_page_numbers(markdown)
    label_counts = Counter(page_labels)
    duplicate_labels = sorted(page for page, count in label_counts.items() if count > 1)
    unique_pages = sorted(label_counts)
    out_of_range = [page for page in unique_pages if page < 1 or page > total_pages]
    if duplicate_labels:
        issues.append("输出存在重复页码：" + format_page_ranges(duplicate_labels))
    if out_of_range:
        issues.append("输出存在越界页码：" + format_page_ranges(out_of_range))
    if page_labels != sorted(page_labels):
        issues.append("输出页码顺序不是严格递增。")

    expected_pages = set(range(1, total_pages + 1))
    candidate_missing = sorted(expected_pages - set(unique_pages))
    confirmed_blank: list[int] = []
    if candidate_missing:
        try:
            confirmed_blank = detect_confirmed_blank_pages(source, candidate_missing)
        except Exception as exc:
            issues.append(str(exc))
    uncovered = sorted(set(candidate_missing) - set(confirmed_blank))
    if uncovered:
        issues.append(
            "以下页面既无 OCR 输出，也未被确认为纯白空白页："
            + format_page_ranges(uncovered)
        )
    if "\ufffd" in markdown:
        issues.append("输出包含 Unicode 替换字符 �，疑似存在乱码。")
    if not page_labels:
        issues.append("输出中没有找到“第 N 页（OCR）”页级标题。")

    output_label: Optional[str] = None
    if output_path is not None:
        try:
            output_label = output_path.resolve().relative_to(reference).as_posix()
        except ValueError:
            output_label = str(output_path.resolve())

    return OCRValidationRecord(
        source=str(source.resolve()),
        sha256=sha256_file(source),
        manifest_status=status,
        parser=parser,
        output=output_label,
        passed=not issues,
        total_pages=total_pages,
        ocr_pages=unique_pages,
        confirmed_blank_pages=confirmed_blank,
        uncovered_pages=uncovered,
        duplicate_page_labels=duplicate_labels,
        out_of_range_page_labels=out_of_range,
        text_characters=int(manifest_record.get("text_characters", 0)) if manifest_record else 0,
        html_tables=len(re.findall(r"<table\b", markdown, flags=re.IGNORECASE)),
        image_references=len(IMAGE_REFERENCE.findall(markdown)),
        issues=issues,
    )


def format_page_ranges(pages: Iterable[int]) -> str:
    ordered = sorted(set(pages))
    if not ordered:
        return "—"
    ranges: list[str] = []
    start = previous = ordered[0]
    for page in ordered[1:]:
        if page == previous + 1:
            previous = page
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = page
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(ranges)


def summarize(records: Sequence[OCRValidationRecord]) -> dict[str, int]:
    return {
        "documents": len(records),
        "automatic_passed": sum(record.passed for record in records),
        "automatic_failed": sum(not record.passed for record in records),
        "pdf_pages": sum(record.total_pages for record in records),
        "ocr_content_pages": sum(len(record.ocr_pages) for record in records),
        "confirmed_blank_pages": sum(len(record.confirmed_blank_pages) for record in records),
        "text_characters": sum(record.text_characters for record in records),
        "html_tables": sum(record.html_tables for record in records),
        "image_references": sum(record.image_references for record in records),
        "manual_review_pending": len(records),
    }


def render_markdown_report(
    records: Sequence[OCRValidationRecord],
    summary: dict[str, int],
) -> str:
    lines = [
        "# OCR 批次验收报告",
        "",
        "> 自动检查只能确认文件、页码覆盖和基础结构；关键数字、表格行列、专业术语仍需人工核对。",
        "",
        "## 自动检查汇总",
        "",
        "| 指标 | 数量 |",
        "| --- | ---: |",
        f"| 文档 | {summary['documents']} |",
        f"| 自动通过 | {summary['automatic_passed']} |",
        f"| 自动失败 | {summary['automatic_failed']} |",
        f"| PDF 原始页 | {summary['pdf_pages']} |",
        f"| 有内容 OCR 页 | {summary['ocr_content_pages']} |",
        f"| 确认纯白空白页 | {summary['confirmed_blank_pages']} |",
        f"| 提取字符 | {summary['text_characters']} |",
        f"| HTML 表格 | {summary['html_tables']} |",
        f"| 图片引用 | {summary['image_references']} |",
        "",
        "## 文档明细",
        "",
        "| 结果 | 文件 | 原始页 | OCR 页 | 空白页 | 表格 | 图片 | 字符 |",
        "| --- | --- | ---: | ---: | --- | ---: | ---: | ---: |",
    ]
    for record in records:
        filename = Path(record.source).name.replace("|", "\\|")
        result = "通过" if record.passed else "失败"
        lines.append(
            f"| {result} | {filename} | {record.total_pages} | {len(record.ocr_pages)} | "
            f"{format_page_ranges(record.confirmed_blank_pages)} | {record.html_tables} | "
            f"{record.image_references} | {record.text_characters} |"
        )

    lines.extend(["", "## 自动检查问题", ""])
    failed = [record for record in records if record.issues]
    if not failed:
        lines.append("未发现自动检查问题。")
        lines.append("")
    else:
        for record in failed:
            lines.append(f"### {Path(record.source).name}")
            lines.append("")
            lines.extend(f"- {issue}" for issue in record.issues)
            lines.append("")

    lines.extend(
        [
            "## 人工抽查清单",
            "",
            "每份 OCR 文档至少核对首页、数字密集页和表格或图片密集页，并在单独的业务验收台账中记录：",
            "",
            "- 标准号、日期、航段名、单位和关键数值是否与原件一致；",
            "- 标题层级、段落顺序以及跨页内容是否正确；",
            "- 表格行列、合并单元格和月份对应关系是否正确；",
            "- 图片、印章、生僻字和低清区域是否需要人工修订；",
            "- 文件版本、有效状态、公开范围和审核人是否已经确认。",
            "",
            "人工验收完成前，不得将本报告中的“自动通过”等同于可发布或可替换正式索引。",
            "",
        ]
    )
    return "\n".join(lines)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    args = parse_args()
    try:
        manifest_records = [
            record
            for manifest in args.manifest
            for record in read_jsonl(manifest)
        ]
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    sources = [
        path
        for path in discover_documents(args.sources)
        if path.suffix.lower() == ".pdf"
    ]
    if args.ocr_only:
        sources = select_ocr_sources(sources, manifest_records)
    if not sources:
        if args.ocr_only:
            print("没有找到台账状态为 ready 且由 PaddleOCR 生成的 PDF。")
        else:
            print("没有找到 PDF 文件。")
        return 2

    validation_records = [
        validate_document(source, _match_manifest_record(source, manifest_records))
        for source in sources
    ]
    batch_summary = summarize(validation_records)
    json_report = args.json_report or args.report.with_suffix(".json")
    _write_text_atomic(args.report, render_markdown_report(validation_records, batch_summary))
    _write_text_atomic(
        json_report,
        json.dumps(
            {
                "summary": batch_summary,
                "documents": [record.as_dict() for record in validation_records],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )

    print(json.dumps(batch_summary, ensure_ascii=False, indent=2))
    print(f"Markdown 报告：{args.report}")
    print(f"JSON 报告：{json_report}")
    return 1 if args.strict and batch_summary["automatic_failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
