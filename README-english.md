# Personal Memory for Codex

> English version. For the Chinese homepage, see [README.md](README.md).

Profile-based local long-term memory recall for Codex.

This repository packages a reusable `personal-memory` Codex plugin that lets Codex recall your local Claude-exported memory, project notes, and compact Markdown summaries from any project directory.

The public repo intentionally does **not** include any personal memory data. You keep your exported memories, SQLite index, and profile config on your own machine.

## What It Solves

When Codex is opened inside a project that is not your memory archive, it normally cannot see historical Claude memory or past project context. This plugin adds a stable global entrypoint:

- A Codex plugin skill: `personal-memory`
- MCP tools: `recall_memory`, `search_memory`, `find_memory_artifact`, `get_memory_node`, `eval_memory`, `refresh_memory_index`
- A local profile file: `~/.personal-memory/profiles/default.json`
- Deterministic reranking to avoid broad memory pollution
- Deep raw artifact lookup for old prompts, created files, and tool outputs
- Eval gate to prevent retrieval regressions

## Quick Start

Clone this repository:

```bash
git clone https://github.com/Looperswag/personal-memory-from-claude-to-codex.git
cd personal-memory-from-claude-to-codex
```

Add the local marketplace and install the plugin:

```bash
codex plugin marketplace add "$PWD"
codex plugin add personal-memory@personal-memory
```

Create your default profile:

```bash
mkdir -p ~/.personal-memory/profiles
cp examples/default-profile.example.json ~/.personal-memory/profiles/default.json
```

Edit `~/.personal-memory/profiles/default.json`:

```json
{
  "name": "default",
  "description": "Local personal memory archive.",
  "memory_root": "/absolute/path/to/your/claude_memory_archive"
}
```

Open a new Codex thread after installing. Then ask Codex to use `personal-memory` before answering project-history questions.

## Memory Archive Layout

Your private memory root should look like this after indexing:

```text
your_memory_archive/
  memory_graph.db
  codex_memory/
    profile.md
    conversations.md
    project_aliases.json
    memory_eval_cases.json
    projects/
      example-project.md
  projects/
    claude-project-export.json
  design_chats/
  memories.json
  conversations.json
```

Only `memory_graph.db` and compact Markdown are needed for normal recall. Raw export files stay local and should not be committed.

## Build Or Refresh The Index

If you already have a Claude export directory:

```bash
cd plugins/personal-memory
python3 scripts/organize_claude_memory.py /absolute/path/to/your/claude_memory_archive
python3 -c 'from pathlib import Path; from memory_graph.indexer import MemoryGraphIndexer; root=Path("/absolute/path/to/your/claude_memory_archive"); MemoryGraphIndexer(root, root / "memory_graph.db").rebuild("manual")'
```

You can also refresh through MCP with:

```text
refresh_memory_index(profile="default")
```

## CLI Usage

From the plugin directory:

```bash
python3 scripts/memory_query.py recall "shopping agent intent routing" --cwd "$PWD" --limit 6 --profile default
python3 scripts/memory_query.py recall "paper-interpreter" --limit 3 --profile default --json
python3 scripts/memory_query.py search "SwiftData OCR ledger" --profile default
python3 scripts/artifact_search.py "AI shopping query rewrite prompt" --memory-root /absolute/path/to/your/claude_memory_archive --limit 1
```

## MCP Tools

The plugin exposes:

| Tool | Purpose |
| --- | --- |
| `recall_memory(query, cwd?, limit?, profile?)` | Main ranked recall with cwd/project hints |
| `search_memory(query, limit?, profile?)` | Explicit keyword search |
| `find_memory_artifact(query, limit?, candidate_limit?, project_hint?, include_full_text?, max_text_chars?, profile?)` | Drill into raw `conversations.json` for exact prompts, created files, tool artifacts, and deeply nested conversation sections |
| `get_memory_node(id, profile?)` | Fetch one indexed node |
| `eval_memory(top_k?, profile?)` | Run eval cases from `codex_memory/memory_eval_cases.json` |
| `refresh_memory_index(profile?)` | Rebuild the SQLite index |

## Retrieval Strategy

V2 uses a two-stage pipeline:

1. SQLite FTS pulls a wider candidate set.
2. Deterministic rerank promotes exact project, alias, cwd, skill, title, and path matches.

Broad sources such as `codex_memory/conversations.md`, project index files, and generic design chats are suppressed by default. They only enter main results when the query explicitly asks for raw/full conversation history.

When a user needs the exact body of an old prompt, created file, or tool artifact, use `find_memory_artifact` after broad recall. It searches visible message text and artifact payloads, returns deterministic pointers (`conversation_uuid`, `message_index`, `content_index`, `field_path`), and excludes internal `thinking` blocks.

Every result includes:

- `score`
- `reasons`
- `read_next`
- `source_path`
- `suppressed_results`
- `diagnostics`

## Eval Gate

Run tests:

```bash
python3 -m unittest discover -s tests -v
```

Run the MCP self-test:

```bash
python3 plugins/personal-memory/scripts/personal_memory_mcp.py --self-test
```

Run your memory eval:

```bash
cd plugins/personal-memory
python3 scripts/eval_memory_query.py --top-k 5 --profile default --fail-under 0.97
```

Suggested gates:

- Recall@5 >= 0.97
- MRR >= 0.90
- Forbidden hits = 0
- skills Recall@5 = 1.00 and MRR >= 0.80
- cwd_alias MRR >= 0.90

## Privacy

Do not commit your private memory archive. This repository's `.gitignore` excludes common Claude export files and generated memory artifacts:

- `conversations.json`
- `memories.json`
- `users.json`
- `projects/`
- `design_chats/`
- `codex_memory/`
- `memory_graph.db`

Keep only framework code, examples, and sanitized eval summaries in git.
