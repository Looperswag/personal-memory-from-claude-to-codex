from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "personal-memory"
sys.path.insert(0, str(PLUGIN_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import artifact_search  # noqa: E402


class ArtifactSearchTests(unittest.TestCase):
    def make_memory_root(self) -> TemporaryDirectory[str]:
        temp = TemporaryDirectory()
        root = Path(temp.name)
        conversations = [
            {
                "uuid": "conv-ai-shopping",
                "name": "记忆检索模块商品ID丢失问题分析",
                "created_at": "2026-06-22T05:15:17Z",
                "updated_at": "2026-06-22T05:39:23Z",
                "chat_messages": [
                    {
                        "uuid": "msg-user",
                        "sender": "human",
                        "created_at": "2026-06-22T05:30:48Z",
                        "content": [
                            {
                                "type": "text",
                                "text": "请输出过渡态的 prompt 和 diff，解决商品 ID 丢失。",
                            }
                        ],
                    },
                    {
                        "uuid": "msg-assistant",
                        "sender": "assistant",
                        "created_at": "2026-06-22T05:39:23Z",
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": "SECRET_THINKING_ONLY_TOKEN 商品改写 prompt",
                            },
                            {
                                "type": "tool_use",
                                "name": "create_file",
                                "input": {
                                    "path": "/mnt/user-data/outputs/memory_retriever_anchored_v1.md",
                                    "description": "Optimized memory retriever prompt.",
                                    "file_text": (
                                        "你是电商导购助手的「记忆检索器」（Memory Retriever）。\n"
                                        "根据 rewrite_query、anchored_item、memory_context 输出 JSON。\n"
                                        "规则 0：锚定优先判断。商品 id 锚定不可漏召，"
                                        "relevant_item 必须包含 item_id。"
                                    ),
                                },
                            },
                        ],
                    },
                ],
            },
            {
                "uuid": "conv-summary-only",
                "name": "AI导购项目",
                "created_at": "2026-04-24T00:00:00Z",
                "updated_at": "2026-04-24T00:00:00Z",
                "chat_messages": [
                    {
                        "uuid": "msg-summary",
                        "sender": "assistant",
                        "created_at": "2026-04-24T00:00:00Z",
                        "content": [
                            {
                                "type": "text",
                                "text": "项目摘要：AI导购涉及 query rewrite、prompt、商品 ID。",
                            }
                        ],
                    }
                ],
            },
        ]
        (root / "conversations.json").write_text(json.dumps(conversations, ensure_ascii=False), encoding="utf-8")
        return temp

    def test_finds_create_file_prompt_for_chinese_query(self) -> None:
        with self.make_memory_root() as temp:
            payload = artifact_search.find_memory_artifacts(
                query="AI导购 商品改写模块 prompt",
                memory_root=Path(temp),
                limit=3,
            )
        self.assertGreaterEqual(payload["matched_count"], 1)
        first = payload["results"][0]
        self.assertEqual(first["kind"], "artifact")
        self.assertEqual(first["conversation"]["uuid"], "conv-ai-shopping")
        self.assertEqual(first["artifact"]["tool_name"], "create_file")
        self.assertIn("anchored_item", first["full_text"])
        self.assertIn("item_id", first["full_text"])

    def test_returns_precise_artifact_pointer(self) -> None:
        with self.make_memory_root() as temp:
            payload = artifact_search.find_memory_artifacts(
                query="memory retriever anchored_item",
                memory_root=Path(temp),
                limit=1,
            )
        first = payload["results"][0]
        self.assertEqual(first["pointer"]["conversation_uuid"], "conv-ai-shopping")
        self.assertEqual(first["pointer"]["message_index"], 1)
        self.assertEqual(first["pointer"]["content_index"], 1)
        self.assertEqual(first["artifact"]["field_path"], "content[1].tool_use.input.file_text")

    def test_ignores_thinking_blocks(self) -> None:
        with self.make_memory_root() as temp:
            payload = artifact_search.find_memory_artifacts(
                query="SECRET_THINKING_ONLY_TOKEN",
                memory_root=Path(temp),
                limit=3,
            )
        self.assertEqual(payload["matched_count"], 0)

    def test_artifact_outranks_summary_message(self) -> None:
        with self.make_memory_root() as temp:
            payload = artifact_search.find_memory_artifacts(
                query="prompt 商品 ID query rewrite",
                memory_root=Path(temp),
                limit=3,
            )
        self.assertEqual(payload["results"][0]["kind"], "artifact")
        self.assertGreater(payload["results"][0]["score"], payload["results"][1]["score"])


if __name__ == "__main__":
    unittest.main()
