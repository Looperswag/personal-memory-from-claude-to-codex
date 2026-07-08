import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "personal-memory"
MEMORY_QUERY = PLUGIN_ROOT / "scripts" / "memory_query.py"
MCP_SERVER = PLUGIN_ROOT / "scripts" / "personal_memory_mcp.py"

sys.path.insert(0, str(PLUGIN_ROOT))


class PersonalMemoryPluginTest(unittest.TestCase):
    def make_memory_archive(self, root: Path) -> None:
        from memory_graph.indexer import MemoryGraphIndexer

        (root / "projects").mkdir()
        (root / "codex_memory" / "projects").mkdir(parents=True)

        (root / "memories.json").write_text(
            json.dumps(
                [
                    {
                        "conversations_memory": "The user builds shopping agents, finance tools, and paper-reading workflows.",
                        "project_memories": {
                            "shopping": "Shopping Agent handles intent routing, product relevance, and memory recall.",
                            "finance": "Finance App uses OCR ledger review and local accounting workflows.",
                        },
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (root / "projects" / "shopping.json").write_text(
            json.dumps(
                {
                    "uuid": "shopping",
                    "name": "Shopping Agent",
                    "description": "AI shopping guide",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-07-01T00:00:00Z",
                    "docs": [{"filename": "intent.md", "content": "intent routing and product relevance"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (root / "projects" / "finance.json").write_text(
            json.dumps(
                {
                    "uuid": "finance",
                    "name": "Finance App",
                    "description": "OCR accounting app",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-06-01T00:00:00Z",
                    "docs": [{"filename": "ledger.md", "content": "OCR ledger review"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (root / "codex_memory" / "profile.md").write_text(
            "# Profile\n\nThe user works on shopping agents and finance tooling.\n",
            encoding="utf-8",
        )
        (root / "codex_memory" / "projects" / "shopping-agent.md").write_text(
            "# Shopping Agent\n\nIntent routing and product relevance memory.\n",
            encoding="utf-8",
        )
        (root / "codex_memory" / "projects" / "finance-app.md").write_text(
            "# Finance App\n\nOCR ledger review and accounting workflows.\n",
            encoding="utf-8",
        )
        (root / "codex_memory" / "conversations.md").write_text(
            "# Conversation Index\n\nShopping Agent Finance App raw conversation history.\n",
            encoding="utf-8",
        )
        (root / "codex_memory" / "project_aliases.json").write_text(
            json.dumps(
                {
                    "Shopping Agent": ["shopping-agent", "tmall-agent", "intent-router"],
                    "Finance App": ["finance-ios", "ledger", "ocr-accounting"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        MemoryGraphIndexer(root, root / "memory_graph.db").rebuild("test")

    def run_query(self, args: list[str], cwd: Path, personal_home: Path) -> dict:
        completed = subprocess.run(
            [sys.executable, str(MEMORY_QUERY), *args],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            env={**os.environ, "PERSONAL_MEMORY_HOME": str(personal_home)},
        )
        return json.loads(completed.stdout)

    def test_profile_alias_recall_suppresses_broad_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory_root = base / "memory"
            project_dir = base / "tmall-agent-sandbox"
            profile_dir = base / "personal-memory" / "profiles"
            memory_root.mkdir()
            project_dir.mkdir()
            profile_dir.mkdir(parents=True)
            self.make_memory_archive(memory_root)
            (profile_dir / "default.json").write_text(json.dumps({"memory_root": str(memory_root)}), encoding="utf-8")

            payload = self.run_query(
                ["recall", "intent routing", "--cwd", str(project_dir), "--limit", "5", "--profile", "default", "--json"],
                project_dir,
                base / "personal-memory",
            )

            titles = [item["title"] for item in payload["results"]]
            result_paths = {item["source_path"] for item in payload["results"]}
            suppressed_paths = {item["source_path"] for item in payload["suppressed_results"]}
            self.assertTrue(any("Shopping Agent" in title or "shopping-agent" in title for title in titles))
            self.assertNotIn("codex_memory/conversations.md", result_paths)
            self.assertIn("codex_memory/conversations.md", suppressed_paths)
            self.assertTrue(all("score" in item and "reasons" in item and "read_next" in item for item in payload["results"]))

    def test_explicit_raw_history_query_allows_conversations(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memory_root = base / "memory"
            profile_dir = base / "personal-memory" / "profiles"
            memory_root.mkdir()
            profile_dir.mkdir(parents=True)
            self.make_memory_archive(memory_root)
            (profile_dir / "default.json").write_text(json.dumps({"memory_root": str(memory_root)}), encoding="utf-8")

            payload = self.run_query(
                ["recall", "raw conversation history shopping", "--profile", "default", "--json"],
                base,
                base / "personal-memory",
            )

            self.assertIn("codex_memory/conversations.md", {item["source_path"] for item in payload["results"]})

    def test_mcp_self_test_lists_expected_tools(self):
        completed = subprocess.run(
            [sys.executable, str(MCP_SERVER), "--self-test"],
            cwd=PLUGIN_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["server"], "personal-memory")
        self.assertIn("recall_memory", payload["tools"])
        self.assertIn("eval_memory", payload["tools"])


if __name__ == "__main__":
    unittest.main()
