from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.evaluation import (
    evaluate_response,
    load_case_files,
    load_cases,
    normalize_text,
    run_cases,
)


class FakeEvaluationClient:
    def __init__(self, responses):
        self.responses = iter(responses)

    def ask(self, question, top_k=None):
        return next(self.responses)


class EvaluationTests(unittest.TestCase):
    def test_normalization_ignores_width_case_and_whitespace(self) -> None:
        self.assertEqual(normalize_text("Ａ B\nＣ"), "abc")

    def test_checks_answer_source_and_realtime_boundary(self) -> None:
        case = {
            "answer_contains_all": ["2021年3月1日"],
            "source_contains_all": ["长江保护法"],
            "expect_sources": True,
            "expected_blocked_realtime": False,
        }
        response = {
            "answer": "自 2021 年 3 月 1 日起施行。",
            "sources": [{"source": "中华人民共和国长江保护法.docx", "locator": "正文"}],
            "blocked_realtime": False,
        }
        self.assertEqual(evaluate_response(case, response), [])

    def test_reports_unexpected_sources_and_missing_keyword(self) -> None:
        case = {
            "answer_contains_all": ["无法确认"],
            "expect_no_sources": True,
        }
        response = {
            "answer": "可能是张三。",
            "sources": [{"source": "无关资料.md", "locator": "正文"}],
            "blocked_realtime": False,
        }
        failures = evaluate_response(case, response)
        self.assertTrue(any("答案缺少关键词" in item for item in failures))
        self.assertTrue(any("预期来源为空" in item for item in failures))

    def test_accepts_any_configured_source_keyword(self) -> None:
        case = {"source_contains_any": ["实际标准.pdf", "标准目录.xlsx"]}
        response = {
            "answer": "标准名称",
            "sources": [{"source": "标准目录.xlsx", "locator": "工作表"}],
        }
        self.assertEqual(evaluate_response(case, response), [])

    def test_load_cases_rejects_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_text(
                '\n'.join([
                    json.dumps({"id": "same", "question": "问题一"}, ensure_ascii=False),
                    json.dumps({"id": "same", "question": "问题二"}, ensure_ascii=False),
                ]),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "id 重复"):
                load_cases(path)

    def test_load_case_files_rejects_ids_repeated_across_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.jsonl"
            second = root / "second.jsonl"
            payload = json.dumps({"id": "same", "question": "问题"}, ensure_ascii=False)
            first.write_text(payload, encoding="utf-8")
            second.write_text(payload, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "多个评测文件之间 id 重复"):
                load_case_files([first, second])

    def test_run_cases_summarizes_categories(self) -> None:
        cases = [
            {"id": "one", "category": "fact", "question": "问题", "answer_contains_all": ["正确"]},
            {"id": "two", "category": "boundary", "question": "问题", "expect_no_sources": True},
        ]
        client = FakeEvaluationClient([
            {"answer": "正确", "sources": [], "blocked_realtime": False},
            {"answer": "拒答", "sources": [], "blocked_realtime": True},
        ])

        results, summary = run_cases(cases, client)

        self.assertTrue(all(result["passed"] for result in results))
        self.assertEqual(summary["passed"], 2)
        self.assertEqual(summary["categories"]["fact"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
