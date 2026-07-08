from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "personal-memory"
os.environ.setdefault("PERSONAL_MEMORY_IMPL_ROOT", str(PLUGIN_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import personal_memory_mcp  # noqa: E402


class McpContractTests(unittest.TestCase):
    def test_tool_list_is_additive(self) -> None:
        names = {tool["name"] for tool in personal_memory_mcp.tools()}
        self.assertIn("recall_memory", names)
        self.assertIn("search_memory", names)
        self.assertIn("get_memory_node", names)
        self.assertIn("eval_memory", names)
        self.assertIn("refresh_memory_index", names)
        self.assertIn("find_memory_artifact", names)

    def test_find_memory_artifact_schema_is_closed(self) -> None:
        tool = next(tool for tool in personal_memory_mcp.tools() if tool["name"] == "find_memory_artifact")
        schema = tool["inputSchema"]
        self.assertEqual(schema["additionalProperties"], False)
        self.assertIn("query", schema["required"])
        self.assertIn("include_full_text", schema["properties"])
        self.assertIn("max_text_chars", schema["properties"])


if __name__ == "__main__":
    unittest.main()
