#!/usr/bin/env python3
"""Minimal stdio MCP server for the local personal-memory plugin."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable


SERVER_NAME = "personal-memory"
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
IMPL_ROOT = Path(os.environ.get("PERSONAL_MEMORY_IMPL_ROOT", str(PLUGIN_ROOT))).expanduser().resolve()

for path in (IMPL_ROOT, IMPL_ROOT / "scripts"):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

try:
    from scripts import eval_memory_query, memory_query
    from scripts.memory_config import load_profile
except ImportError:  # pragma: no cover - direct script fallback.
    import eval_memory_query  # type: ignore
    import memory_query  # type: ignore
    from memory_config import load_profile  # type: ignore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="Print local MCP server diagnostics and exit.")
    args = parser.parse_args(argv)
    if args.self_test:
        print(json.dumps({"server": SERVER_NAME, "impl_root": str(IMPL_ROOT), "tools": [tool["name"] for tool in tools()]}, ensure_ascii=False, indent=2))
        return 0
    serve_stdio()
    return 0


def serve_stdio() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
        except Exception as exc:  # pragma: no cover - defensive stdio boundary.
            response = error_response(None, -32700, str(exc))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        return result_response(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": "0.1.0"},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return result_response(request_id, {"tools": tools()})
    if method == "tools/call":
        params = request.get("params") or {}
        try:
            payload = call_tool(str(params.get("name", "")), params.get("arguments") or {})
        except Exception as exc:
            return error_response(request_id, -32603, str(exc))
        return result_response(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(payload, ensure_ascii=False, indent=2),
                    }
                ]
            },
        )
    if request_id is None:
        return None
    return error_response(request_id, -32601, f"unknown method: {method}")


def tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "recall_memory",
            "description": "Recall ranked personal memory for a task, using cwd hints when provided.",
            "inputSchema": object_schema(
                {
                    "query": string_schema("Task or question to recall memory for."),
                    "cwd": string_schema("Current working directory used for project alias hints."),
                    "limit": integer_schema("Maximum number of primary results to return.", 1, 20),
                    "profile": string_schema("Personal memory profile name."),
                },
                required=["query"],
            ),
        },
        {
            "name": "search_memory",
            "description": "Search personal memory with explicit keywords without cwd expansion.",
            "inputSchema": object_schema(
                {
                    "query": string_schema("Keywords to search."),
                    "limit": integer_schema("Maximum number of primary results to return.", 1, 20),
                    "profile": string_schema("Personal memory profile name."),
                },
                required=["query"],
            ),
        },
        {
            "name": "get_memory_node",
            "description": "Fetch one indexed memory node by id.",
            "inputSchema": object_schema(
                {
                    "id": string_schema("Memory node id."),
                    "profile": string_schema("Personal memory profile name."),
                },
                required=["id"],
            ),
        },
        {
            "name": "eval_memory",
            "description": "Run the memory retrieval eval suite for a profile.",
            "inputSchema": object_schema(
                {
                    "top_k": integer_schema("Top-k window for recall scoring.", 1, 20),
                    "profile": string_schema("Personal memory profile name."),
                }
            ),
        },
        {
            "name": "refresh_memory_index",
            "description": "Maintenance operation: rebuild the SQLite memory index for a profile.",
            "inputSchema": object_schema({"profile": string_schema("Personal memory profile name.")}),
        },
    ]


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    dispatch: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "recall_memory": recall_memory,
        "search_memory": search_memory,
        "get_memory_node": get_memory_node,
        "eval_memory": eval_memory,
        "refresh_memory_index": refresh_memory_index,
    }
    if name not in dispatch:
        raise ValueError(f"unknown tool: {name}")
    return dispatch[name](arguments)


def recall_memory(arguments: dict[str, Any]) -> dict[str, Any]:
    return memory_query.build_payload(
        mode="recall",
        memory_root=None,
        db_path=None,
        profile=profile_arg(arguments),
        query=str(arguments.get("query", "")),
        cwd=Path(str(arguments.get("cwd") or Path.cwd())),
        limit=int(arguments.get("limit") or 6),
        candidate_limit=int(arguments.get("candidate_limit") or memory_query.CANDIDATE_LIMIT),
        include_cwd_hints=True,
    )


def search_memory(arguments: dict[str, Any]) -> dict[str, Any]:
    return memory_query.build_payload(
        mode="search",
        memory_root=None,
        db_path=None,
        profile=profile_arg(arguments),
        query=str(arguments.get("query", "")),
        cwd=Path.cwd(),
        limit=int(arguments.get("limit") or 6),
        candidate_limit=int(arguments.get("candidate_limit") or memory_query.CANDIDATE_LIMIT),
        include_cwd_hints=False,
    )


def get_memory_node(arguments: dict[str, Any]) -> dict[str, Any]:
    node_id = str(arguments.get("id") or "").strip()
    if not node_id:
        raise ValueError("id is required")
    profile = load_profile(profile_arg(arguments), fallback_root=IMPL_ROOT)
    with sqlite3.connect(profile.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("select nodes.*, 0.0 as rank from nodes where id = ?", (node_id,)).fetchone()
    if row is None:
        raise ValueError(f"memory node not found: {node_id}")
    item = memory_query.row_to_result(row)
    item["read_next"] = memory_query.read_next(item, profile.memory_root)
    return {"profile": profile.name, "memory_root": str(profile.memory_root), "node": item}


def eval_memory(arguments: dict[str, Any]) -> dict[str, Any]:
    top_k = int(arguments.get("top_k") or 5)
    profile = load_profile(profile_arg(arguments), fallback_root=IMPL_ROOT)
    cases_path = profile.memory_root / "codex_memory" / "memory_eval_cases.json"
    cases = eval_memory_query.load_cases(cases_path)

    def retrieve(case: dict[str, Any]) -> list[dict[str, Any]]:
        payload = memory_query.build_payload(
            mode="recall",
            memory_root=None,
            db_path=None,
            profile=profile.name,
            query=case.get("query", ""),
            cwd=eval_memory_query.case_cwd(case),
            limit=max(top_k, int(case.get("limit", top_k))),
            include_cwd_hints=True,
        )
        return payload["results"]

    report = eval_memory_query.evaluate_cases(cases, retrieve, top_k)
    report["profile"] = profile.name
    report["memory_root"] = str(profile.memory_root)
    report["cases_path"] = str(cases_path)
    return report


def refresh_memory_index(arguments: dict[str, Any]) -> dict[str, Any]:
    profile = load_profile(profile_arg(arguments), fallback_root=IMPL_ROOT)
    root_text = str(profile.memory_root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from memory_graph.indexer import MemoryGraphIndexer

    MemoryGraphIndexer(profile.memory_root, profile.db_path).rebuild("mcp-refresh")
    return {
        "status": "rebuilt",
        "profile": profile.name,
        "memory_root": str(profile.memory_root),
        "db_path": str(profile.db_path),
    }


def profile_arg(arguments: dict[str, Any]) -> str:
    return str(arguments.get("profile") or "default")


def object_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def string_schema(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def integer_schema(description: str, minimum: int, maximum: int) -> dict[str, Any]:
    return {"type": "integer", "description": description, "minimum": minimum, "maximum": maximum}


def result_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    raise SystemExit(main())
