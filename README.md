# Codex 个人记忆插件

> 中文首页。英文版见 [README-english.md](README-english.md)。

`personal-memory-from-claude-to-codex` 是一个可复用的 Codex 本地个人记忆插件。它让 Codex 在任意项目目录中，都能通过本地 profile 找到你的 Claude 导出记忆、历史项目笔记、压缩后的 Markdown 记忆库和 SQLite 索引。

这个公开仓库**不包含任何个人记忆数据**。你的原始导出文件、SQLite 索引、profile 配置和私有项目记忆都保留在本机。

## 解决什么问题

当你在一个普通项目目录里打开 Codex 时，Codex 通常看不到另一个文件夹里的历史记忆库，因此无法理解过往 Claude 对话、项目背景和长期上下文。

这个插件提供一个稳定入口：

- Codex skill：`personal-memory`
- MCP 工具：`recall_memory`、`search_memory`、`get_memory_node`、`eval_memory`、`refresh_memory_index`
- 本地 profile：`~/.personal-memory/profiles/default.json`
- 确定性重排序，减少宽泛记忆污染
- eval gate，防止检索质量退化

## 快速开始

克隆仓库：

```bash
git clone https://github.com/Looperswag/personal-memory-from-claude-to-codex.git
cd personal-memory-from-claude-to-codex
```

添加本地 Codex marketplace 并安装插件：

```bash
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

也可以直接使用 CLI：

```bash
cd plugins/personal-memory
python3 scripts/memory_query.py recall "shopping agent intent routing" --cwd "$PWD" --limit 6 --profile default
python3 scripts/memory_query.py recall "paper-interpreter" --limit 3 --profile default --json
python3 scripts/memory_query.py search "SwiftData OCR ledger" --profile default
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

## 构建或刷新索引

如果你已经有 Claude 导出目录，可以先整理和重建索引：

```bash
cd plugins/personal-memory
python3 scripts/organize_claude_memory.py /absolute/path/to/your/claude_memory_archive
python3 -c 'from pathlib import Path; from memory_graph.indexer import MemoryGraphIndexer; root=Path("/absolute/path/to/your/claude_memory_archive"); MemoryGraphIndexer(root, root / "memory_graph.db").rebuild("manual")'
```

也可以通过 MCP 维护工具刷新：

```text
refresh_memory_index(profile="default")
```

## MCP 工具

| 工具 | 用途 |
| --- | --- |
| `recall_memory(query, cwd?, limit?, profile?)` | 主召回入口，会结合当前目录和项目 alias |
| `search_memory(query, limit?, profile?)` | 显式关键词搜索 |
| `get_memory_node(id, profile?)` | 按 id 获取一个索引节点 |
| `eval_memory(top_k?, profile?)` | 运行 `codex_memory/memory_eval_cases.json` 评测集 |
| `refresh_memory_index(profile?)` | 维护操作：重建 SQLite 索引 |

除 `refresh_memory_index` 外，其余工具默认只读。

## V2 检索策略

V2 使用两阶段检索：

1. SQLite FTS 先召回较宽候选集。
2. 确定性 rerank 再根据项目、alias、cwd、skill 名称、标题和路径命中进行排序。

默认会抑制宽泛来源，避免污染主结果：

- `codex_memory/conversations.md`
- 项目总索引
- 泛 design chat

只有当 query 明确包含 `raw`、`conversation`、`history`、`全量会话`、`聊天记录` 等意图时，宽泛会话记忆才会进入主结果。

每条结果会输出：

- `score`
- `reasons`
- `read_next`
- `source_path`
- `suppressed_results`
- `diagnostics`

## 评测门禁

运行公开 synthetic tests：

```bash
python3 -m unittest discover -s tests -v
```

运行你的私有记忆评测：

```bash
cd plugins/personal-memory
python3 scripts/eval_memory_query.py --top-k 5 --profile default --fail-under 0.97
```

建议门槛：

- Recall@5 >= 0.97
- MRR >= 0.90
- Forbidden hits = 0
- skills Recall@5 = 1.00，MRR >= 0.80
- cwd_alias MRR >= 0.90

## V1 到 V2 的效果

原 32 条评测试卷上，V2 相比 V1：

| 指标 | V1 Baseline | V2 Optimized |
| --- | ---: | ---: |
| Recall@5 | 0.969 | 1.000 |
| MRR | 0.749 | 0.979 |
| Forbidden hits | 4 | 0 |
| skills Recall@5 | 0.667 | 1.000 |
| skills MRR | 0.133 | 1.000 |
| cwd_alias MRR | 0.698 | 1.000 |

更详细的技术路线对比见 [docs/V1_V2_COMPARISON.md](docs/V1_V2_COMPARISON.md)。

## 隐私说明

请不要提交你的私有记忆库。本仓库 `.gitignore` 已默认排除常见 Claude 导出文件和生成产物：

- `conversations.json`
- `memories.json`
- `users.json`
- `projects/`
- `design_chats/`
- `codex_memory/`
- `memory_graph.db`

公开仓库只保留框架代码、插件结构、示例配置和脱敏评测摘要。
