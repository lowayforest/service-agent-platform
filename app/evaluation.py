from __future__ import annotations

import json
import statistics
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(text.split())


def load_cases(path: Path) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    seen_ids = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} 不是有效 JSON：{exc}") from exc
            case_id = case.get("id")
            question = case.get("question")
            if not isinstance(case_id, str) or not case_id.strip():
                raise ValueError(f"{path}:{line_number} 缺少非空字符串 id")
            if case_id in seen_ids:
                raise ValueError(f"{path}:{line_number} id 重复：{case_id}")
            if not isinstance(question, str) or not question.strip():
                raise ValueError(f"{path}:{line_number} 缺少非空字符串 question")
            seen_ids.add(case_id)
            cases.append(case)
    if not cases:
        raise ValueError(f"评测文件为空：{path}")
    return cases


def load_case_files(paths: Iterable[Path]) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    seen_ids = set()
    for path in paths:
        for case in load_cases(path):
            case_id = case["id"]
            if case_id in seen_ids:
                raise ValueError(f"多个评测文件之间 id 重复：{case_id}")
            seen_ids.add(case_id)
            cases.append(case)
    return cases


def evaluate_response(case: Dict[str, Any], response: Dict[str, Any]) -> List[str]:
    failures: List[str] = []
    answer = str(response.get("answer", ""))
    normalized_answer = normalize_text(answer)
    sources = response.get("sources")
    if not isinstance(sources, list):
        failures.append("响应中的 sources 不是列表")
        sources = []

    for keyword in case.get("answer_contains_all", []):
        if normalize_text(keyword) not in normalized_answer:
            failures.append(f"答案缺少关键词：{keyword}")

    source_text = "\n".join(
        f"{source.get('source', '')} {source.get('locator', '')}"
        for source in sources
        if isinstance(source, dict)
    )
    normalized_sources = normalize_text(source_text)
    for keyword in case.get("source_contains_all", []):
        if normalize_text(keyword) not in normalized_sources:
            failures.append(f"来源缺少关键词：{keyword}")

    if case.get("expect_no_sources") is True and sources:
        failures.append(f"预期来源为空，实际返回 {len(sources)} 条")
    if case.get("expect_sources") is True and not sources:
        failures.append("预期至少一个来源，实际为空")

    if "expected_blocked_realtime" in case:
        expected = bool(case["expected_blocked_realtime"])
        actual = bool(response.get("blocked_realtime"))
        if actual != expected:
            failures.append(
                f"blocked_realtime 预期为 {expected}，实际为 {actual}"
            )
    return failures


class EvaluationClient:
    def __init__(self, base_url: str, timeout: float = 600) -> None:
        self.endpoint = f"{base_url.rstrip('/')}/api/chat"
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def ask(self, question: str, top_k: int | None = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"question": question}
        if top_k is not None:
            payload["top_k"] = top_k
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"API 返回 HTTP {exc.code}：{detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"无法连接评测 API：{exc.reason}") from exc


def run_cases(
    cases: Iterable[Dict[str, Any]], client: EvaluationClient
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        response: Dict[str, Any] = {}
        try:
            response = client.ask(case["question"], case.get("top_k"))
            failures = evaluate_response(case, response)
        except Exception as exc:
            failures = [str(exc)]
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        results.append(
            {
                "id": case["id"],
                "category": case.get("category", "uncategorized"),
                "question": case["question"],
                "passed": not failures,
                "failures": failures,
                "latency_ms": latency_ms,
                "response": response,
            }
        )

    category_totals = Counter(result["category"] for result in results)
    category_passed = Counter(
        result["category"] for result in results if result["passed"]
    )
    latencies = [result["latency_ms"] for result in results]
    passed = sum(result["passed"] for result in results)
    summary = {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results), 4) if results else 0.0,
        "latency_ms": {
            "average": round(statistics.fmean(latencies), 1) if latencies else 0.0,
            "median": round(statistics.median(latencies), 1) if latencies else 0.0,
            "maximum": max(latencies, default=0.0),
        },
        "categories": {
            category: {
                "passed": category_passed[category],
                "total": total,
            }
            for category, total in sorted(category_totals.items())
        },
    }
    return results, summary


def write_jsonl(path: Path, records: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    temporary.replace(path)


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
