#!/usr/bin/env python3
"""Evaluate memory_query recall quality against gold cases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable


try:
    from scripts import memory_query
    from scripts.memory_config import load_profile
except ImportError:  # pragma: no cover - used when executed as a script path.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import memory_query  # type: ignore
    from memory_config import load_profile  # type: ignore


DEFAULT_MEMORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_NAME = Path("codex_memory") / "memory_eval_cases.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=None, help="Gold eval cases JSON path.")
    parser.add_argument("--memory-root", type=Path, default=None, help="Memory archive root. Defaults to the selected profile.")
    parser.add_argument("--profile", default="default", help="Personal memory profile name.")
    parser.add_argument("--top-k", type=int, default=5, help="Top-k retrieval window used for scoring.")
    parser.add_argument("--json", action="store_true", help="Emit full JSON report.")
    parser.add_argument("--fail-under", type=float, default=None, help="Exit 1 if recall@k is below this threshold.")
    args = parser.parse_args(argv)

    if args.memory_root is None:
        memory_root = load_profile(args.profile, fallback_root=DEFAULT_MEMORY_ROOT).memory_root
    else:
        memory_root = args.memory_root.expanduser().resolve()
    cases_path = args.cases.expanduser().resolve() if args.cases else memory_root / DEFAULT_CASES_NAME
    cases = load_cases(cases_path)

    def retrieve(case: dict[str, Any]) -> list[dict[str, Any]]:
        payload = memory_query.build_payload(
            mode="recall",
            memory_root=memory_root,
            db_path=None,
            profile=args.profile,
            query=case.get("query", ""),
            cwd=case_cwd(case),
            limit=max(args.top_k, int(case.get("limit", args.top_k))),
            include_cwd_hints=True,
        )
        return payload["results"]

    report = evaluate_cases(cases, retrieve, args.top_k)
    report["cases_path"] = str(cases_path)
    report["memory_root"] = str(memory_root)
    report["profile"] = args.profile

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_report(report, args.top_k))

    if args.fail_under is not None and report["recall_at_k"] < args.fail_under:
        return 1
    return 0


def load_cases(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"eval cases not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid eval cases JSON: {path}: {exc}") from exc
    if not isinstance(data, list):
        raise SystemExit("eval cases JSON must be a list")
    for index, case in enumerate(data, 1):
        if not isinstance(case, dict):
            raise SystemExit(f"case #{index} must be an object")
        if not case.get("id"):
            raise SystemExit(f"case #{index} missing id")
        if not case.get("expected_any"):
            raise SystemExit(f"case {case['id']} missing expected_any")
    return data


def evaluate_cases(
    cases: list[dict[str, Any]],
    retrieve_results: Callable[[dict[str, Any]], list[dict[str, Any]]],
    top_k: int = 5,
) -> dict[str, Any]:
    scored_cases = []
    reciprocal_sum = 0.0
    hits = 0
    forbidden_hits = 0
    by_category: dict[str, dict[str, float]] = {}

    for case in cases:
        results = retrieve_results(case)[:top_k]
        rank = first_expected_rank(case.get("expected_any", []), results)
        forbidden = forbidden_matches(case.get("forbidden_any", []), results)
        hit = rank is not None
        if hit:
            hits += 1
            reciprocal_sum += 1 / rank
        if forbidden:
            forbidden_hits += 1

        category = str(case.get("category", "uncategorized"))
        category_row = by_category.setdefault(category, {"total": 0, "hits": 0, "mrr_sum": 0.0, "forbidden_hits": 0})
        category_row["total"] += 1
        category_row["hits"] += 1 if hit else 0
        category_row["mrr_sum"] += 1 / rank if rank else 0.0
        category_row["forbidden_hits"] += 1 if forbidden else 0

        scored_cases.append(
            {
                "id": case["id"],
                "category": category,
                "query": case.get("query", ""),
                "cwd": str(case_cwd(case)),
                "expected_any": case.get("expected_any", []),
                "forbidden_any": case.get("forbidden_any", []),
                "rank": rank,
                "hit": hit,
                "forbidden_matches": forbidden,
                "top_results": [result_digest(item) for item in results],
                "notes": case.get("notes", ""),
            }
        )

    total = len(cases)
    categories = {}
    for category, row in sorted(by_category.items()):
        total_for_category = int(row["total"])
        categories[category] = {
            "total": total_for_category,
            "hits": int(row["hits"]),
            "recall_at_k": row["hits"] / total_for_category if total_for_category else 0.0,
            "mrr": row["mrr_sum"] / total_for_category if total_for_category else 0.0,
            "forbidden_hits": int(row["forbidden_hits"]),
        }

    return {
        "total": total,
        "hits": hits,
        "recall_at_k": hits / total if total else 0.0,
        "mrr": reciprocal_sum / total if total else 0.0,
        "forbidden_hits": forbidden_hits,
        "categories": categories,
        "cases": scored_cases,
    }


def first_expected_rank(expected: list[str], results: list[dict[str, Any]]) -> int | None:
    for index, result in enumerate(results, 1):
        haystack = result_text(result)
        if any(term_matches(term, haystack) for term in expected):
            return index
    return None


def forbidden_matches(forbidden: list[str], results: list[dict[str, Any]]) -> list[str]:
    matches = []
    for term in forbidden:
        for result in results:
            if term_matches(term, result_text(result)):
                matches.append(term)
                break
    return matches


def term_matches(term: str, haystack: str) -> bool:
    return str(term).lower() in haystack.lower()


def result_text(result: dict[str, Any]) -> str:
    return " ".join(
        str(result.get(field, ""))
        for field in ("id", "type", "title", "summary", "source_path", "updated_at")
    )


def result_digest(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": result.get("id", ""),
        "type": result.get("type", ""),
        "title": result.get("title", ""),
        "source_path": result.get("source_path", ""),
    }


def case_cwd(case: dict[str, Any]) -> Path:
    cwd = case.get("cwd")
    if cwd:
        return Path(str(cwd)).expanduser()
    cwd_name = str(case.get("cwd_name") or case["id"])
    return Path("/tmp") / "memory-eval" / cwd_name


def render_report(report: dict[str, Any], top_k: int) -> str:
    lines = [
        "# Memory Query Eval Report",
        "",
        f"- Cases: {report['total']}",
        f"- Recall@{top_k}: {report['recall_at_k']:.3f} ({report['hits']}/{report['total']})",
        f"- MRR: {report['mrr']:.3f}",
        f"- Forbidden hits: {report['forbidden_hits']}",
        "",
        "## By Category",
        "",
        "| Category | Cases | Recall | MRR | Forbidden |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for category, item in report["categories"].items():
        lines.append(
            f"| {category} | {item['total']} | {item['recall_at_k']:.3f} | {item['mrr']:.3f} | {item['forbidden_hits']} |"
        )

    failures = [case for case in report["cases"] if not case["hit"] or case["forbidden_matches"]]
    lines += ["", "## Failures And Warnings", ""]
    if not failures:
        lines.append("_No misses or forbidden hits._")
    for case in failures:
        status = "MISS" if not case["hit"] else "FORBIDDEN"
        lines.append(f"- `{status}` `{case['id']}` query=`{case['query']}` expected={case['expected_any']}")
        if case["forbidden_matches"]:
            lines.append(f"  Forbidden matched: {case['forbidden_matches']}")
        top = ", ".join(f"{item['title']} [{item['type']}]" for item in case["top_results"][:3])
        lines.append(f"  Top results: {top or '(none)'}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
