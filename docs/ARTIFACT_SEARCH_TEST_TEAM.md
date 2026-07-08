# Artifact 深度检索 Agent Test Team

这份文档描述 `find_memory_artifact` 的回归测试团队。它的目标是防止工具退化成“只找到项目摘要”，并保证它能稳定返回旧 prompt、created file、tool artifact 的精确原文。

## 1. Artifact Hunter

目标：证明工具能从压缩记忆背后的原始 `conversations.json` 中找回真实 artifact。

检查点：

- 查询：`AI导购 商品改写模块 prompt`
- 期望：命中 `create_file.input.file_text`
- 结果必须包含 `kind=artifact`、`tool_name=create_file`、`field_path`

## 2. Pointer Auditor

目标：每个结果都必须可复现。

检查点：

- 返回 `conversation_uuid`
- 返回 `message_index`
- tool artifact 返回 `content_index`
- 返回精确 `field_path`

## 3. Privacy Guard

目标：不检索、不返回 internal `thinking` blocks。

检查点：

- 只存在于 `content[].thinking` 中的 token 不应命中
- `full_text` 只能来自用户可见文本或 artifact payload

## 4. Ranker Guard

目标：真实 artifact 排名高于附近摘要。

检查点：

- 只提到 `prompt` 的 summary message 不能排在包含 prompt 正文的 `create_file.file_text` 前面
- `prompt`、`提示词`、`原文`、`文件` 等 artifact 意图词只在已有真实命中后加分，不能凭工具类型空命中

## 5. Domain Recall Agent

目标：中英文混合查询能连到变量名和历史术语。

检查点：

- `商品ID丢失` 扩展到 `item_id`、`anchored_item`、`relevant_item`
- `商品改写` 扩展到 `rewrite_query`、`query rewrite`、`L2 改写`
- `记忆检索器` 扩展到 `Memory Retriever`、`memory_context`

## 6. Performance Sentry

目标：避免再出现 8 分钟人工摸索。

检查点：

- fixture 单测在 1 秒内完成
- 真实 archive smoke test 一条命令返回目标 artifact
- MCP 进程内复用 `conversations.json` mtime/size cache

## 7. Contract Sentinel

目标：新增工具必须是 additive change。

检查点：

- 旧工具仍然存在：`recall_memory`、`search_memory`、`get_memory_node`、`eval_memory`、`refresh_memory_index`
- 新增工具：`find_memory_artifact`
- MCP schema 使用 `additionalProperties=false`

## 自动化覆盖

当前测试位于 `tests/`：

- `test_artifact_search.py`
  - Artifact Hunter
  - Pointer Auditor
  - Privacy Guard
  - Ranker Guard
- `test_mcp_contract.py`
  - Contract Sentinel

建议每次改动后运行：

```bash
python3 -m unittest discover -s tests -v
python3 plugins/personal-memory/scripts/personal_memory_mcp.py --self-test
```
