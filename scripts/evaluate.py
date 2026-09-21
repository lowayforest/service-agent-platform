from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation import EvaluationClient, load_cases, run_cases, write_json, write_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="调用 RAG API 执行 JSONL 评测集。")
    parser.add_argument("cases", type=Path, help="JSONL 评测文件")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="RAG API 根地址，默认 http://127.0.0.1:8000",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/evals/latest-results.jsonl"),
        help="逐题结果输出位置",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("data/evals/latest-summary.json"),
        help="汇总结果输出位置",
    )
    parser.add_argument("--timeout", type=float, default=600, help="单题超时秒数")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.timeout <= 0:
        raise SystemExit("--timeout 必须大于 0")
    try:
        cases = load_cases(args.cases)
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    client = EvaluationClient(args.base_url, args.timeout)
    results, summary = run_cases(cases, client)
    write_jsonl(args.output, results)
    write_json(args.summary, summary)

    for result in results:
        state = "PASS" if result["passed"] else "FAIL"
        print(f"[{state}] {result['id']} ({result['latency_ms']} ms)")
        for failure in result["failures"]:
            print(f"  - {failure}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"逐题结果：{args.output}")
    print(f"汇总结果：{args.summary}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
