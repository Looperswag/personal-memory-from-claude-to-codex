# Codex 个人记忆迁移（from claude）插件

> 中文首页。英文版见 [README-english.md](README-english.md)。

`personal-memory-from-claude-to-codex` 是一个可复用的 Codex 本地个人记忆插件。它让 Codex 在任意项目目录中，都能通过本地 profile 找到你的 Claude 导出记忆、历史项目笔记、压缩后的 Markdown 记忆库和 SQLite 索引。

这个公开仓库**不包含任何个人记忆数据**。你的原始导出文件、SQLite 索引、profile 配置和私有项目记忆都保留在本机。

## 解决什么问题
<img width="924" height="412" alt="截图" src="https://github.com/user-attachments/assets/6e13cffe-5753-443b-bb56-efc8bcc37e9b" />
你从claude导出的记忆长这样。。。文件非常大且检索起来比较麻烦，无法直接加载给上下文；
AND
当你在一个普通项目目录里（非claude记忆文件的目录）打开 Codex 时，Codex 通常看不到另一个文件夹里的历史记忆库，因此无法理解过往 Claude 对话、项目背景和长期上下文。

这个插件提供一个稳定入口：

- Codex skill：`personal-memory`
- MCP 工具：`recall_memory`、`search_memory`、`get_memory_node`、`eval_memory`、`refresh_memory_index`
- 本地 profile：`~/.personal-memory/profiles/default.json`
- 确定性重排序，减少宽泛记忆污染
- eval gate，防止检索质量退化

## 快速开始

纯文本描述（最最最简单的方法）：

```bash
请帮我安装这个插件：https://github.com/Looperswag/personal-memory-from-claude-to-codex + claude记忆文件的绝对路径：{填你记忆文件放的地方}，请codex帮我安装
```

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



## 内置的 MCP 工具盘点

| 工具 | 用途 |
| --- | --- |
| `recall_memory(query, cwd?, limit?, profile?)` | 主召回入口，会结合当前目录和项目 alias |
| `search_memory(query, limit?, profile?)` | 显式关键词搜索 |
| `get_memory_node(id, profile?)` | 按 id 获取一个索引节点 |
| `eval_memory(top_k?, profile?)` | 运行 `codex_memory/memory_eval_cases.json` 评测集 |
| `refresh_memory_index(profile?)` | 维护操作：重建 SQLite 索引 |

除 `refresh_memory_index` 外，其余工具默认只读。

## 检索策略

使用两阶段检索：

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
