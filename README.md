# Codex 个人记忆迁移插件（from Claude）

> 中文首页。英文版见 [README-english.md](README-english.md)。

`personal-memory-from-claude-to-codex` 是一个可复用的 Codex 本地个人记忆插件。它把 Claude 导出的个人记忆、历史项目、压缩 Markdown 和 SQLite 索引，包装成 Codex 可直接调用的 profile + skill + MCP 工具。

这个公开仓库**不包含任何个人记忆数据**。你的原始 Claude 导出、`conversations.json`、SQLite 索引、profile 配置和私有项目记忆都保留在本机。

## 解决什么问题

<img width="924" height="412" alt="Claude 导出记忆示例" src="https://github.com/user-attachments/assets/6e13cffe-5753-443b-bb56-efc8bcc37e9b" />

Claude 导出的记忆和对话文件通常很大，不能直接塞进 Codex 上下文；当你在一个普通项目目录里打开 Codex 时，它也不会天然知道另一个目录里的历史记忆库。

这个插件提供一个稳定入口，让 Codex 可以：

- 在任意项目目录中召回你的历史项目背景、长期偏好和工作上下文；
- 用 SQLite FTS + deterministic rerank 从压缩记忆中快速找到“附近的正确记忆”；
- 在需要精确原文时，进一步钻取 `conversations.json` 里的 prompt、created file、tool artifact 和小节原文；
- 用 eval gate 防止检索质量在迭代中退化。

## 核心能力

- Codex skill：`personal-memory`
- 本地 profile：`~/.personal-memory/profiles/default.json`
- SQLite FTS + deterministic rerank
- `find_memory_artifact` 深度 artifact 检索
- 解释性结果：`score`、`reasons`、`read_next`、`source_path`
- 评测入口：`eval_memory`
- 本地优先：公开仓库不提交任何私人记忆

## 快速开始

最简单的方式是直接让 Codex 帮你安装：

```text
请帮我安装这个插件：https://github.com/Looperswag/personal-memory-from-claude-to-codex
我的 Claude 记忆文件绝对路径是：{填你的本地记忆目录}
```

也可以手动安装：

```bash
git clone https://github.com/Looperswag/personal-memory-from-claude-to-codex.git
cd personal-memory-from-claude-to-codex

codex plugin marketplace add "$PWD"
codex plugin add personal-memory@personal-memory
```

创建默认 profile：

```bash
mkdir -p ~/.personal-memory/profiles
cp examples/default-profile.example.json ~/.personal-memory/profiles/default.json
```

编辑 `~/.personal-memory/profiles/default.json`，把 `memory_root` 改成你自己的本地记忆库路径：

```json
{
  "name": "default",
  "description": "Local personal memory archive.",
  "memory_root": "/absolute/path/to/your/claude_memory_archive"
}
```

安装后请新开一个 Codex 线程。新线程会加载插件 skill 和 MCP 工具。

## 推荐用法

在任意项目目录中，可以让 Codex 先调用个人记忆：

```text
请先用 personal-memory 回忆这个项目可能关联的历史上下文，再回答我的问题。
```

如果你要找旧 prompt、旧文件、某次对话里创建的 artifact，可以更明确地说：

```text
请用 personal-memory 找到我之前 AI 导购项目里「商品改写模块」的完整 prompt 原文。
```

日常 CLI：

```bash
cd plugins/personal-memory
python3 scripts/memory_query.py recall "shopping agent intent routing" --cwd "$PWD" --limit 6 --profile default
python3 scripts/memory_query.py recall "paper-interpreter" --limit 3 --profile default --json
python3 scripts/memory_query.py search "SwiftData OCR ledger" --profile default
```

深度 artifact 检索 CLI：

```bash
python3 scripts/artifact_search.py \
  "AI导购 商品改写模块 prompt" \
  --memory-root /absolute/path/to/your/claude_memory_archive \
  --limit 1
```

## 记忆库结构

你的私有 `memory_root` 通常类似这样：

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

日常召回主要依赖 `memory_graph.db` 和压缩后的 `codex_memory/` Markdown。原始导出文件保留本地，不应提交到公开仓库。

## MCP 工具

| 工具 | 用途 |
| --- | --- |
| `recall_memory(query, cwd?, limit?, profile?)` | 主召回入口，会结合当前目录和项目 alias |
| `search_memory(query, limit?, profile?)` | 显式关键词搜索，不使用 cwd 扩展 |
| `find_memory_artifact(query, limit?, candidate_limit?, project_hint?, include_full_text?, max_text_chars?, profile?)` | 深入 raw `conversations.json`，查找 prompt、created file、tool artifact 和精确小节原文 |
| `get_memory_node(id, profile?)` | 按 id 获取一个 SQLite 索引节点 |
| `eval_memory(top_k?, profile?)` | 运行 `codex_memory/memory_eval_cases.json` 评测集 |
| `refresh_memory_index(profile?)` | 维护操作：重建 SQLite 索引 |

除 `refresh_memory_index` 外，其余工具默认只读。

## 推荐检索流程

1. **先用 `recall_memory`**：找项目、主题、历史背景和压缩记忆节点。
2. **再用 `search_memory`**：当关键词明确但召回不够准时，做窄关键词搜索。
3. **最后用 `find_memory_artifact`**：当用户需要“完整 prompt 原文”“之前创建的文件”“tool artifact”“某条对话的小节原文”时，直接钻取 raw `conversations.json`。

这个分层很重要：压缩索引适合快速找到“附近”，artifact 检索适合找到“原文”。

## `find_memory_artifact` 示例

MCP 参数示例：

```json
{
  "query": "AI导购 商品改写模块 prompt",
  "limit": 3,
  "candidate_limit": 120,
  "project_hint": "AI导购项目",
  "include_full_text": true,
  "max_text_chars": 20000,
  "profile": "default"
}
```

典型返回字段：

```json
{
  "kind": "artifact",
  "conversation": {
    "uuid": "d85f88c3-ce24-4da9-b31c-7fd82592b2ef",
    "title": "电商AI导购Agent框架设计方案"
  },
  "message": {
    "index": 60,
    "uuid": "019d2291-6869-7bc0-81fc-95a6c04afb43"
  },
  "artifact": {
    "tool_name": "create_file",
    "path": "/mnt/user-data/outputs/Phase1_Query改写模块_system_prompt.md",
    "field_path": "content[5].tool_use.input.file_text",
    "content_index": 5
  },
  "excerpt": "...",
  "full_text": "..."
}
```

返回的 `conversation_uuid`、`message_index`、`content_index`、`field_path` 可以精确复现来源，不需要再靠人工在巨大 JSON 里摸索。

## 检索策略

常规记忆召回使用两阶段检索：

1. SQLite FTS 先召回较宽候选集。
2. deterministic rerank 再根据项目、alias、cwd、skill 名称、标题和路径命中进行排序。

深度 artifact 检索使用另一条只读链路：

1. 读取本地 profile 指向的 `conversations.json`；
2. 抽取用户可见消息和 tool artifact 文本；
3. 排除 `thinking` blocks；
4. 做中英文/domain query expansion；
5. coarse prefilter 后 explainable rerank；
6. 返回精确 pointer、excerpt 和可选 full text。

## 隐私与安全

- 公开仓库不包含任何个人记忆数据。
- 原始 `conversations.json`、`memories.json`、`users.json`、`memory_graph.db` 都应只保存在本机。
- `find_memory_artifact` 不检索、不返回 Claude 导出里的 internal `thinking` blocks。
- `.gitignore` 已排除常见 Claude 导出文件和生成记忆目录。

## 开发与测试

运行单元测试：

```bash
python3 -m unittest discover -s tests -v
```

MCP 自检：

```bash
python3 plugins/personal-memory/scripts/personal_memory_mcp.py --self-test
```

真实记忆库 smoke test：

```bash
python3 plugins/personal-memory/scripts/artifact_search.py \
  "AI导购 商品改写模块 prompt" \
  --memory-root /absolute/path/to/your/claude_memory_archive \
  --limit 1 \
  --no-full-text
```

评测门禁：

```bash
cd plugins/personal-memory
python3 scripts/eval_memory_query.py --top-k 5 --profile default --fail-under 0.97
```

建议门槛：

- Recall@5 >= 0.97
- MRR >= 0.90
- Forbidden hits = 0
- skills Recall@5 = 1.00 and MRR >= 0.80
- cwd_alias MRR >= 0.90

## Roadmap

- 将 conversation / message / artifact 提升为 SQLite 图里的一级节点，进一步减少 raw JSON 扫描。
- 为 `find_memory_artifact` 增加更多领域 query expansion 模板。
- 增加 artifact 级 eval cases，覆盖 prompt、文档、代码片段和历史文件找回。

## License

MIT
