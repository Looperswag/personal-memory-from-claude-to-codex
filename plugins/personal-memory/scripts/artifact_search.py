#!/usr/bin/env python3
"""Find deep conversation artifacts in a local personal-memory archive.

This module is intentionally independent from the existing SQLite node graph.
The current graph is project/file summary oriented; this tool drills into the
raw Claude `conversations.json` export and returns precise artifact pointers.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_PROFILE = "default"
DEFAULT_LIMIT = 5
DEFAULT_CANDIDATE_LIMIT = 120
DEFAULT_MAX_TEXT_CHARS = 20000

ARTIFACT_TOOL_NAMES = {"create_file", "str_replace_based_edit_tool", "replace_file"}
RAW_HISTORY_FILENAMES = {"conversations.json"}

EXPANSIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ai导购", ("天猫", "淘宝", "千问", "导购", "电商")),
    ("商品改写", ("query rewrite", "rewrite_query", "L2 改写", "改写模块")),
    ("改写", ("rewrite_query", "query rewrite", "L2", "改写模块")),
    ("prompt", ("提示词", "system prompt", "系统提示词", "file_text")),
    ("提示词", ("prompt", "system prompt", "系统提示词", "file_text")),
    ("商品id", ("item_id", "anchored_item", "商品 ID", "id 锚定")),
    ("item_id", ("商品ID", "anchored_item", "relevant_item")),
    ("记忆检索", ("Memory Retriever", "memory_context", "relevant_item")),
    ("memory retriever", ("记忆检索器", "memory_context", "anchored_item")),
    ("artifact", ("create_file", "file_text", "outputs")),
    ("文件", ("create_file", "file_text", "filename")),
)


@dataclass(frozen=True)
class SearchConfig:
    query: str
    memory_root: Path
    limit: int = DEFAULT_LIMIT
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT
    project_hint: str = ""
    include_full_text: bool = True
    include_messages: bool = True
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS


@dataclass
class Candidate:
    kind: str
    conversation_uuid: str
    conversation_title: str
    conversation_created_at: str
    conversation_updated_at: str
    message_index: int
    message_uuid: str
    message_sender: str
    message_created_at: str
    content_index: int | None
    tool_name: str
    artifact_path: str
    field_path: str
    text: str
    visible_context: str = ""
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def pointer(self) -> dict[str, Any]:
        return {
            "conversation_uuid": self.conversation_uuid,
            "message_index": self.message_index,
            "message_uuid": self.message_uuid,
            "content_index": self.content_index,
            "field_path": self.field_path,
        }


def find_memory_artifacts(
    *,
    query: str,
    memory_root: Path | str,
    limit: int = DEFAULT_LIMIT,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    project_hint: str = "",
    include_full_text: bool = True,
    include_messages: bool = True,
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
) -> dict[str, Any]:
    """Return ranked raw conversation artifacts matching a query."""

    root = Path(memory_root).expanduser().resolve()
    if not query.strip():
        raise ValueError("query is required")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if candidate_limit < limit:
        candidate_limit = limit

    config = SearchConfig(
        query=query,
        memory_root=root,
        limit=limit,
        candidate_limit=candidate_limit,
        project_hint=project_hint,
        include_full_text=include_full_text,
        include_messages=include_messages,
        max_text_chars=max_text_chars,
    )
    conversations_path = root / "conversations.json"
    conversations = load_conversations(conversations_path)
    terms = expanded_terms(query)
    candidates = collect_candidates(conversations, include_messages=include_messages)
    rough = [(rough_score_candidate(candidate, terms, config), candidate) for candidate in candidates]
    rough = [(score, candidate) for score, candidate in rough if score > 0]
    rough.sort(key=lambda item: (-item[0], item[1].conversation_updated_at, item[1].message_index))
    candidates_to_score = [candidate for _, candidate in rough[:candidate_limit]]
    scored = [score_candidate(candidate, terms, config) for candidate in candidates_to_score]
    scored = [candidate for candidate in scored if candidate.score > 0]
    scored.sort(key=lambda item: (-item.score, item.conversation_updated_at, item.message_index))

    top = scored[:limit]
    return {
        "mode": "artifact_search",
        "query": query,
        "expanded_terms": terms[:60],
        "memory_root": str(root),
        "conversations_path": str(conversations_path),
        "candidate_count": len(candidates),
        "prefiltered_count": len(rough),
        "scored_count": len(candidates_to_score),
        "matched_count": len(scored),
        "candidate_limit": candidate_limit,
        "results": [candidate_to_result(candidate, config) for candidate in top],
    }


@lru_cache(maxsize=4)
def _load_conversations_cached(path_text: str, mtime_ns: int, size: int) -> tuple[dict[str, Any], ...]:
    path = Path(path_text)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array")
    return tuple(item for item in data if isinstance(item, dict))


def load_conversations(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.exists():
        raise FileNotFoundError(f"conversations.json not found at {path}")
    stat = path.stat()
    return _load_conversations_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def collect_candidates(conversations: tuple[dict[str, Any], ...], *, include_messages: bool) -> list[Candidate]:
    candidates: list[Candidate] = []
    for conversation in conversations:
        title = str(conversation.get("name") or conversation.get("title") or "")
        conv_uuid = str(conversation.get("uuid") or "")
        conv_created = str(conversation.get("created_at") or "")
        conv_updated = str(conversation.get("updated_at") or "")
        messages = conversation.get("chat_messages") or []
        if not isinstance(messages, list):
            continue
        for message_index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            visible_text = visible_message_text(message)
            if include_messages and visible_text:
                candidates.append(
                    Candidate(
                        kind="message",
                        conversation_uuid=conv_uuid,
                        conversation_title=title,
                        conversation_created_at=conv_created,
                        conversation_updated_at=conv_updated,
                        message_index=message_index,
                        message_uuid=str(message.get("uuid") or ""),
                        message_sender=str(message.get("sender") or message.get("role") or ""),
                        message_created_at=str(message.get("created_at") or ""),
                        content_index=None,
                        tool_name="",
                        artifact_path="",
                        field_path="content.text",
                        text=visible_text,
                        visible_context=visible_text[:1000],
                    )
                )
            candidates.extend(artifact_candidates(conversation, message, message_index, visible_text))
    return candidates


def visible_message_text(message: dict[str, Any]) -> str:
    """Return only user-visible text blocks, excluding thinking/internal traces."""

    parts: list[str] = []
    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
    if not parts:
        # Some exports store only a flat text field. Use it as fallback, but
        # artifacts from structured content are preferred and scored higher.
        text = message.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n\n".join(part.strip() for part in parts if part.strip())


def artifact_candidates(
    conversation: dict[str, Any],
    message: dict[str, Any],
    message_index: int,
    visible_text: str,
) -> list[Candidate]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    title = str(conversation.get("name") or conversation.get("title") or "")
    conv_uuid = str(conversation.get("uuid") or "")
    out: list[Candidate] = []
    for content_index, block in enumerate(content):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tool_name = str(block.get("name") or "")
        tool_input = block.get("input") or {}
        if not isinstance(tool_input, dict):
            continue
        for field_path, value in iter_artifact_text_fields(tool_input):
            path = artifact_path(tool_input)
            out.append(
                Candidate(
                    kind="artifact",
                    conversation_uuid=conv_uuid,
                    conversation_title=title,
                    conversation_created_at=str(conversation.get("created_at") or ""),
                    conversation_updated_at=str(conversation.get("updated_at") or ""),
                    message_index=message_index,
                    message_uuid=str(message.get("uuid") or ""),
                    message_sender=str(message.get("sender") or message.get("role") or ""),
                    message_created_at=str(message.get("created_at") or ""),
                    content_index=content_index,
                    tool_name=tool_name,
                    artifact_path=path,
                    field_path=f"content[{content_index}].tool_use.input.{field_path}",
                    text=value,
                    visible_context=visible_text[:1000],
                )
            )
    return out


def iter_artifact_text_fields(tool_input: dict[str, Any]) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    preferred = ("file_text", "content", "text", "replacement")
    for key in preferred:
        value = tool_input.get(key)
        if isinstance(value, str) and len(value.strip()) >= 40:
            fields.append((key, value))
    # Some tools nest payloads one level deep. Keep this intentionally shallow
    # to avoid returning arbitrary large JSON blobs.
    for key, value in tool_input.items():
        if key in preferred:
            continue
        if isinstance(value, dict):
            for child_key in preferred:
                child = value.get(child_key)
                if isinstance(child, str) and len(child.strip()) >= 40:
                    fields.append((f"{key}.{child_key}", child))
    return fields


def artifact_path(tool_input: dict[str, Any]) -> str:
    for key in ("path", "filename", "file_path", "filepath"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
    return ""


def score_candidate(candidate: Candidate, terms: list[str], config: SearchConfig) -> Candidate:
    haystacks = {
        "title": candidate.conversation_title,
        "path": candidate.artifact_path,
        "tool": candidate.tool_name,
        "field": candidate.field_path,
        "context": candidate.visible_context,
        "text": candidate.text,
    }
    score = 0.0
    matched = False
    reasons: list[str] = []

    query_lower = config.query.lower()
    if config.project_hint and contains(haystacks["title"], config.project_hint):
        score += 45
        reasons.append("project_hint:title+45")
        matched = True
    if contains(haystacks["title"], config.query):
        score += 140
        reasons.append("query:title_exact+140")
        matched = True

    wants_artifact = any(term in query_lower for term in ("prompt", "提示词", "artifact", "文件", "原文"))

    for term in terms:
        if not term:
            continue
        lower = term.lower()
        if contains(haystacks["title"], lower):
            score += 26
            reasons.append(f"title:{term}+26")
            matched = True
        if contains(haystacks["path"], lower):
            score += 22
            reasons.append(f"path:{term}+22")
            matched = True
        if contains(haystacks["context"], lower):
            score += 16
            reasons.append(f"context:{term}+16")
            matched = True
        if contains(haystacks["text"], lower):
            score += 9 if len(term) < 5 else 14
            reasons.append(f"text:{term}+{9 if len(term) < 5 else 14}")
            matched = True

    if matched and candidate.kind == "artifact":
        score += 35
        reasons.append("kind:artifact+35")
    elif matched and candidate.kind == "message":
        score += 8
        reasons.append("kind:message+8")
    if matched and wants_artifact and candidate.kind == "artifact":
        score += 60
        reasons.append("intent:artifact+60")
    if matched and candidate.tool_name in ARTIFACT_TOOL_NAMES:
        score += 35
        reasons.append(f"tool:{candidate.tool_name}+35")
    if matched and ("prompt" in haystacks["path"].lower() or "retriever" in haystacks["path"].lower()):
        score += 45
        reasons.append("path:promptish+45")
    if matched and len(candidate.text) > 2000:
        score += 12
        reasons.append("long_artifact+12")
    candidate.score = round(score, 3)
    candidate.reasons = dedupe(reasons)
    return candidate


def rough_score_candidate(candidate: Candidate, terms: list[str], config: SearchConfig) -> float:
    """Cheap first-stage retrieval before full explainable scoring."""

    fields = (
        candidate.conversation_title,
        candidate.artifact_path,
        candidate.tool_name,
        candidate.field_path,
        candidate.visible_context,
        candidate.text,
    )
    score = 0.0
    if config.project_hint and contains(candidate.conversation_title, config.project_hint):
        score += 50
    if contains(candidate.conversation_title, config.query):
        score += 100
    for term in terms:
        for field_index, field in enumerate(fields):
            if contains(field, term):
                score += 6 if field_index < 4 else 2
                break
    if score > 0 and candidate.kind == "artifact":
        score += 8
    return score


def candidate_to_result(candidate: Candidate, config: SearchConfig) -> dict[str, Any]:
    result = {
        "kind": candidate.kind,
        "score": candidate.score,
        "reasons": candidate.reasons,
        "conversation": {
            "uuid": candidate.conversation_uuid,
            "title": candidate.conversation_title,
            "created_at": candidate.conversation_created_at,
            "updated_at": candidate.conversation_updated_at,
        },
        "message": {
            "index": candidate.message_index,
            "uuid": candidate.message_uuid,
            "sender": candidate.message_sender,
            "created_at": candidate.message_created_at,
        },
        "artifact": {
            "tool_name": candidate.tool_name,
            "path": candidate.artifact_path,
            "field_path": candidate.field_path,
            "content_index": candidate.content_index,
        },
        "pointer": candidate.pointer(),
        "excerpt": excerpt(candidate.text, expanded_terms(config.query)),
        "text_length": len(candidate.text),
    }
    if config.include_full_text:
        result["full_text"] = truncate(candidate.text, config.max_text_chars)
        result["truncated"] = len(candidate.text) > config.max_text_chars
    return result


def expanded_terms(query: str) -> list[str]:
    terms = phrase_terms(query)
    lower_query = query.lower()
    for trigger, values in EXPANSIONS:
        if trigger.lower() in lower_query:
            terms.extend(values)
    # English/chinese mixed queries often hide key Chinese phrases inside a
    # long token. Character n-grams recover matches without a segmentation lib.
    for token in phrase_terms(query):
        if re.search(r"[\u4e00-\u9fff]", token) and len(token) >= 4:
            terms.extend(chinese_ngrams(token, min_n=2, max_n=4))
    return dedupe(term.strip() for term in terms if len(term.strip()) >= 2)[:100]


def phrase_terms(text: str) -> list[str]:
    return re.findall(r"[\w\u4e00-\u9fff-]+", (text or "").lower())


def chinese_ngrams(text: str, *, min_n: int, max_n: int) -> list[str]:
    chars = [char for char in text if re.match(r"[\u4e00-\u9fff]", char)]
    grams: list[str] = []
    for size in range(min_n, min(max_n, len(chars)) + 1):
        for index in range(0, len(chars) - size + 1):
            grams.append("".join(chars[index : index + size]))
    return grams


def contains(text: str, term: str) -> bool:
    return str(term or "").lower() in str(text or "").lower()


def excerpt(text: str, terms: list[str], radius: int = 220) -> str:
    lowered = text.lower()
    best_index = -1
    for term in terms:
        if not term:
            continue
        index = lowered.find(term.lower())
        if index >= 0:
            best_index = index
            break
    if best_index < 0:
        return truncate(re.sub(r"\s+", " ", text).strip(), radius * 2)
    start = max(0, best_index - radius)
    end = min(len(text), best_index + radius)
    prefix = "..." if start else ""
    suffix = "..." if end < len(text) else ""
    return prefix + text[start:end].strip() + suffix


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + "\n...[truncated]"


def dedupe(values: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Artifact search query.")
    parser.add_argument("--memory-root", type=Path, required=True, help="Personal memory archive root.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--candidate-limit", type=int, default=DEFAULT_CANDIDATE_LIMIT)
    parser.add_argument("--project-hint", default="")
    parser.add_argument("--no-full-text", action="store_true")
    parser.add_argument("--max-text-chars", type=int, default=DEFAULT_MAX_TEXT_CHARS)
    args = parser.parse_args(argv)
    payload = find_memory_artifacts(
        query=args.query,
        memory_root=args.memory_root,
        limit=args.limit,
        candidate_limit=args.candidate_limit,
        project_hint=args.project_hint,
        include_full_text=not args.no_full_text,
        max_text_chars=args.max_text_chars,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
