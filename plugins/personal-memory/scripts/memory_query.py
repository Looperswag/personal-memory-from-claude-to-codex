#!/usr/bin/env python3
"""Query a profile-based local Claude memory archive from any working directory."""

from __future__ import annotations

import argparse
import configparser
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

try:
    from scripts.memory_config import ProfileError, load_profile
except ImportError:  # pragma: no cover - used when executed as a script path.
    from memory_config import ProfileError, load_profile  # type: ignore


DEFAULT_MEMORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_NAME = "memory_graph.db"
ALIAS_PATH = Path("codex_memory") / "project_aliases.json"
MAX_SUMMARY_CHARS = 260
CANDIDATE_LIMIT = 50
RAW_HISTORY_TERMS = {"conversation", "conversations", "history", "raw", "全量会话", "会话", "聊天记录"}
PROFILE_TERMS = {
    "profile",
    "background",
    "career",
    "naoh",
    "imperial",
    "bytedance",
    "capcut",
    "anker",
    "pm",
    "part",
    "个人",
    "画像",
    "背景",
    "履历",
    "经历",
    "风格",
    "公众号",
}


class QueryError(Exception):
    pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="Search the memory graph with explicit keywords.")
    add_common_args(search)
    search.add_argument("query", nargs="?", default="", help="Keywords to search.")

    recall = subparsers.add_parser("recall", help="Recall relevant memory using query plus current directory hints.")
    add_common_args(recall)
    recall.add_argument("query", nargs="*", help="Optional task/query text.")

    args = parser.parse_args(argv)
    try:
        if args.command == "search":
            payload = build_payload(
                mode="search",
                memory_root=args.memory_root,
                db_path=args.db,
                profile=args.profile,
                query=args.query,
                cwd=args.cwd,
                limit=args.limit,
                candidate_limit=args.candidate_limit,
                include_cwd_hints=False,
            )
        else:
            payload = build_payload(
                mode="recall",
                memory_root=args.memory_root,
                db_path=args.db,
                profile=args.profile,
                query=" ".join(args.query),
                cwd=args.cwd,
                limit=args.limit,
                candidate_limit=args.candidate_limit,
                include_cwd_hints=True,
            )
    except (ProfileError, QueryError) as exc:
        print(f"memory-query: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(payload))
    return 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--memory-root",
        type=Path,
        default=None,
        help=f"Memory archive root. Defaults to {DEFAULT_MEMORY_ROOT}.",
    )
    parser.add_argument("--db", type=Path, default=None, help="SQLite memory graph path. Defaults to memory_root/memory_graph.db.")
    parser.add_argument("--profile", default="default", help="Personal memory profile name.")
    parser.add_argument("--cwd", type=Path, default=Path.cwd(), help="Project directory used for recall hints.")
    parser.add_argument("--limit", type=int, default=6, help="Maximum number of memory nodes to return.")
    parser.add_argument("--candidate-limit", type=int, default=CANDIDATE_LIMIT, help="Number of FTS candidates to rerank.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def build_payload(
    *,
    mode: str,
    memory_root: Path | None,
    db_path: Path | None,
    profile: str = "default",
    query: str,
    cwd: Path,
    limit: int,
    candidate_limit: int = CANDIDATE_LIMIT,
    include_cwd_hints: bool,
) -> dict[str, Any]:
    fallback_root = Path.cwd() if (Path.cwd() / DEFAULT_DB_NAME).exists() else DEFAULT_MEMORY_ROOT
    loaded_profile = load_profile(profile, memory_root=memory_root, db_path=db_path, fallback_root=fallback_root)
    memory_root = loaded_profile.memory_root
    db_path = loaded_profile.db_path
    cwd = cwd.expanduser().resolve()
    if limit < 1:
        raise QueryError("--limit must be at least 1")
    if candidate_limit < limit:
        candidate_limit = limit
    if not db_path.exists():
        rebuild = (
            "PYTHONPATH={root} python3 -c 'from pathlib import Path; "
            "from memory_graph.indexer import MemoryGraphIndexer; "
            "root=Path(\"{root}\"); "
            "MemoryGraphIndexer(root, Path(\"{db_path}\")).rebuild(\"manual\")'"
        ).format(root=memory_root, db_path=db_path)
        raise QueryError(
            f"database not found at {db_path}. Rebuild it with: {rebuild}"
        )

    aliases = load_aliases(memory_root)
    hints = infer_hints(cwd, aliases) if include_cwd_hints else []
    combined_query = " ".join(part for part in [query.strip(), " ".join(hints)] if part).strip()
    candidates = search_nodes(db_path, combined_query, candidate_limit)
    ranked = rerank_nodes(candidates, query.strip(), hints, aliases, memory_root, limit)
    return {
        "mode": mode,
        "profile": loaded_profile.name,
        "memory_root": str(memory_root),
        "db_path": str(db_path),
        "cwd": str(cwd),
        "query": query.strip(),
        "hints": hints,
        "effective_query": combined_query,
        "results": ranked["results"],
        "suppressed_results": ranked["suppressed_results"],
        "diagnostics": {
            "candidate_count": len(candidates),
            "candidate_limit": candidate_limit,
            "allow_broad_memory": allow_broad_memory(query),
            "profile_config": str(loaded_profile.config_path) if loaded_profile.config_path else "",
            "overflow_count": ranked["overflow_count"],
        },
    }


def load_aliases(memory_root: Path) -> dict[str, list[str]]:
    path = memory_root / ALIAS_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    aliases: dict[str, list[str]] = {}
    if not isinstance(data, dict):
        return aliases
    for title, values in data.items():
        if isinstance(values, list):
            clean = [str(value).strip() for value in values if str(value).strip()]
            if clean:
                aliases[str(title)] = clean
    return aliases


def infer_hints(cwd: Path, aliases: dict[str, list[str]]) -> list[str]:
    raw_parts = [cwd.name, *git_remote_tokens(cwd)]
    raw = " ".join(raw_parts)
    normalized = normalize(raw)
    tokens = tokenise(raw)
    hints: list[str] = []
    for token in [cwd.name, *tokens]:
        add_unique(hints, token)
    for title, terms in aliases.items():
        searchable_terms = [title, *terms]
        if any(alias_matches(term, normalized, tokens) for term in searchable_terms):
            add_unique(hints, title)
            for term in terms:
                add_unique(hints, term)
    return hints[:24]


def git_remote_tokens(cwd: Path) -> list[str]:
    config_path = find_git_config(cwd)
    if not config_path:
        return []
    parser = configparser.ConfigParser()
    try:
        parser.read(config_path)
    except configparser.Error:
        return []
    tokens: list[str] = []
    for section in parser.sections():
        if not section.startswith("remote "):
            continue
        url = parser.get(section, "url", fallback="")
        tokens.extend(tokenise(url))
    return tokens


def find_git_config(cwd: Path) -> Path | None:
    for parent in [cwd, *cwd.parents]:
        config = parent / ".git" / "config"
        if config.exists():
            return config
    return None


def alias_matches(term: str, normalized: str, tokens: list[str]) -> bool:
    term_norm = normalize(term)
    if not term_norm:
        return False
    if term_norm in normalized:
        return True
    term_tokens = tokenise(term)
    return bool(term_tokens and all(token in tokens for token in term_tokens))


def search_nodes(db_path: Path, query: str, limit: int) -> list[dict[str, Any]]:
    terms = tokenise(query)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if terms:
            expr = " OR ".join(f'"{term}"' for term in terms)
            try:
                rows = conn.execute(
                    """
                    select nodes.*, bm25(nodes_fts) as rank
                    from nodes_fts
                    join nodes on nodes.id = nodes_fts.id
                    where nodes_fts match ? and nodes.type != 'topic'
                    order by rank, nodes.type = 'file', nodes.updated_at desc, nodes.title
                    limit ?
                    """,
                    (expr, limit),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                raise QueryError(f"SQLite FTS query failed for {query!r}: {exc}") from exc
        else:
            rows = conn.execute(
                """
                select nodes.*, 0.0 as rank
                from nodes
                where type in ('profile', 'project', 'design', 'skill', 'doc')
                order by updated_at desc, type, title
                limit ?
                """,
                (limit,),
            ).fetchall()
    return [row_to_result(row) for row in rows]


def rerank_nodes(
    candidates: list[dict[str, Any]],
    query: str,
    hints: list[str],
    aliases: dict[str, list[str]],
    memory_root: Path,
    limit: int,
) -> dict[str, Any]:
    allow_broad = allow_broad_memory(query)
    scored = [score_result(item, query, hints, aliases, memory_root, allow_broad) for item in candidates]
    scored.sort(key=lambda item: (-item["score"], item.get("rank", 0), item["title"]))

    results = []
    suppressed = []
    overflow_count = 0
    for item in scored:
        if is_suppressed_result(item, allow_broad):
            suppressed.append(item)
            continue
        if len(results) < limit:
            results.append(item)
        else:
            overflow_count += 1
    return {
        "results": results,
        "suppressed_results": suppressed[: max(limit, 6)],
        "overflow_count": overflow_count,
    }


def score_result(
    item: dict[str, Any],
    query: str,
    hints: list[str],
    aliases: dict[str, list[str]],
    memory_root: Path,
    allow_broad: bool = False,
) -> dict[str, Any]:
    text = result_text(item)
    source = item.get("source_path", "")
    title = item.get("title", "")
    node_type = item.get("type", "")
    score = max(0.0, -float(item.get("rank") or 0.0))
    reasons: list[str] = []

    type_weight = type_score(item)
    score += type_weight
    if type_weight:
        reasons.append(f"type:{node_type}+{type_weight:g}")

    for phrase in phrase_terms(query):
        if phrase and phrase in text.lower():
            boost = 42 if "-" in phrase else 10
            score += boost
            reasons.append(f"query:{phrase}+{boost}")

    query_tokens = tokenise(query)
    for token in query_tokens:
        if term_matches(token, title) or term_matches(token, source):
            score += 8
            reasons.append(f"title_path:{token}+8")

    for hint in hints:
        if not hint:
            continue
        if term_matches(hint, title):
            score += 28
            reasons.append(f"hint_title:{hint}+28")
        elif term_matches(hint, source):
            score += 18
            reasons.append(f"hint_path:{hint}+18")

    for alias_title, alias_terms in aliases.items():
        alias_values = [alias_title, *alias_terms]
        if any(term_matches(value, " ".join(hints)) for value in alias_values):
            if term_matches(alias_title, text):
                score += 70
                reasons.append(f"alias_project:{alias_title}+70")
            for value in alias_terms:
                if term_matches(value, title) or term_matches(value, source):
                    score += 20
                    reasons.append(f"alias_term:{value}+20")

    if node_type == "skill" and any(term_matches(phrase, text) for phrase in phrase_terms(query)):
        score += 220
        reasons.append("skill_exact+220")

    if is_profile_result(item):
        profile_matches = sum(1 for token in query_tokens if term_matches(token, text))
        if profile_query(query) or profile_matches >= 2:
            score += 92
            reasons.append("profile_intent+92")
        else:
            score -= 14
            reasons.append("profile_generic-14")
    if is_project_markdown(item):
        score += 34
        reasons.append("project_markdown+34")
    if is_broad_memory(item) and allow_broad:
        score += 24
        reasons.append("broad_memory_allowed+24")
    elif is_broad_memory(item):
        score -= 120
        reasons.append("broad_memory-120")
    if is_generic_design(item):
        score -= 55
        reasons.append("generic_design-55")

    enriched = dict(item)
    enriched["score"] = round(score, 3)
    enriched["reasons"] = dedupe(reasons)
    enriched["read_next"] = read_next(enriched, memory_root)
    return enriched


def type_score(item: dict[str, Any]) -> float:
    source = item.get("source_path", "")
    node_type = item.get("type", "")
    if node_type == "project":
        return 44
    if node_type == "skill":
        return 58
    if node_type == "profile":
        return 24
    if node_type == "doc":
        return 18
    if node_type == "file" and source.startswith("codex_memory/projects/"):
        return 40
    if node_type == "file" and source == "codex_memory/skills.md":
        return 28
    if node_type == "file":
        return 8
    return 0


def is_suppressed_result(item: dict[str, Any], allow_broad: bool) -> bool:
    return not allow_broad and (is_broad_memory(item) or is_generic_design(item))


def is_broad_memory(item: dict[str, Any]) -> bool:
    return item.get("source_path") in {"codex_memory/conversations.md", "codex_memory/projects/INDEX.md"} or item.get("id") == "project:INDEX"


def is_generic_design(item: dict[str, Any]) -> bool:
    return item.get("type") == "design" and item.get("title") in {"Chat", ""}


def is_profile_result(item: dict[str, Any]) -> bool:
    return item.get("type") == "profile" or item.get("source_path") == "codex_memory/profile.md"


def is_project_markdown(item: dict[str, Any]) -> bool:
    return item.get("type") == "file" and str(item.get("source_path", "")).startswith("codex_memory/projects/")


def profile_query(query: str) -> bool:
    lowered = query.lower()
    return any(term in lowered for term in PROFILE_TERMS)


def allow_broad_memory(query: str) -> bool:
    lowered = query.lower()
    return any(term in lowered for term in RAW_HISTORY_TERMS)


def phrase_terms(query: str) -> list[str]:
    return dedupe(
        term.lower()
        for term in re.findall(r"[\w\u4e00-\u9fff-]+", query or "")
        if len(term.strip()) >= 2
    )


def result_text(result: dict[str, Any]) -> str:
    return " ".join(
        str(result.get(field, ""))
        for field in ("id", "type", "title", "summary", "source_path", "updated_at")
    )


def read_next(item: dict[str, Any], memory_root: Path) -> str:
    source = item.get("source_path") or ""
    if source:
        return str((memory_root / source).resolve())
    return item.get("id", "")


def row_to_result(row: sqlite3.Row) -> dict[str, Any]:
    summary = re.sub(r"\s+", " ", row["summary"] or "").strip()
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[: MAX_SUMMARY_CHARS - 3].rstrip() + "..."
    try:
        meta = json.loads(row["meta_json"] or "{}")
    except json.JSONDecodeError:
        meta = {}
    return {
        "id": row["id"],
        "type": row["type"],
        "title": row["title"],
        "summary": summary,
        "source_path": row["source_path"],
        "updated_at": row["updated_at"],
        "size": row["size"],
        "meta": meta if isinstance(meta, dict) else {},
        "rank": row["rank"],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Personal Memory Recall",
        "",
        f"- Memory root: `{payload['memory_root']}`",
        f"- CWD: `{payload['cwd']}`",
        f"- Query: `{payload['effective_query'] or '(latest memory)'}`",
    ]
    if payload["hints"]:
        lines.append(f"- CWD hints: `{', '.join(payload['hints'])}`")
    lines += ["", "## Results", ""]
    if not payload["results"]:
        lines.append("_No memory nodes matched. Try a more specific query or rebuild the memory graph._")
        return "\n".join(lines)
    for index, item in enumerate(payload["results"], 1):
        source = f" Source: `{item['source_path']}`." if item["source_path"] else ""
        score = f" score={item.get('score')}" if "score" in item else ""
        lines.append(f"{index}. **{item['title']}** `{item['type']}`")
        lines.append(f"   {item['summary'] or '_No summary._'}{source}{score}")
        if item.get("read_next"):
            lines.append(f"   Read next: `{item['read_next']}`")
    lines += [
        "",
        "Use these results as contextual evidence only. Repository instructions and the user's explicit prompt still take precedence.",
    ]
    return "\n".join(lines)


def tokenise(text: str) -> list[str]:
    tokens = []
    for chunk in re.findall(r"[\w\u4e00-\u9fff]+", text or ""):
        lowered = chunk.lower()
        if len(lowered) >= 2 or re.search(r"[\u4e00-\u9fff]", lowered):
            tokens.append(lowered)
    return dedupe(tokens)


def normalize(text: str) -> str:
    return " ".join(tokenise(text))


def term_matches(term: str, text: str) -> bool:
    return str(term or "").lower() in str(text or "").lower()


def add_unique(values: list[str], value: str) -> None:
    value = str(value or "").strip()
    if value and value not in values:
        values.append(value)


def dedupe(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
