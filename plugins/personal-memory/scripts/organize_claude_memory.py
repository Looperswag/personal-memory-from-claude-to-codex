#!/usr/bin/env python3
"""Turn a Claude export folder into Codex-friendly Markdown notes."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


CATEGORIES = {
    "AI导购/电商": ["导购", "天猫", "淘宝", "购物", "千问", "意图分类", "电商", "qwen"],
    "Agent/多智能体": ["agent", "swarm", "多智能体", "mcp", "tool", "工作流"],
    "视频/AIGC": ["视频", "剪辑", "成片", "capcut", "aigc", "素材"],
    "内容创作": ["公众号", "小红书", "twitter", "x平台", "博客", "文案", "文章"],
    "职业/个人": ["简历", "面试", "职业", "收入", "独立开发", "个人"],
    "旅行/生活": ["旅行", "旅游", "漫途", "sydney", "悉尼", "生活"],
    "游戏/设计": ["德州扑克", "poker", "游戏", "视觉", "设计", "ui"],
    "论文/研究": ["论文", "paper", "研究", "调研", "benchmark", "评测"],
}


def read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def one_line(value, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def cell(value) -> str:
    return one_line(value).replace("|", "\\|")


def mask_email(email: str) -> str:
    if "@" not in (email or ""):
        return ""
    name, domain = email.split("@", 1)
    return f"{name[:2]}***@{domain}" if len(name) > 2 else f"{name[:1]}***@{domain}"


def mask_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 7:
        return ""
    prefix = "+" if str(phone).strip().startswith("+") else ""
    return f"{prefix}{digits[:4]}****{digits[-4:]}"


def slug(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "-", (text or "").lower()).strip("-")
    return (cleaned[:60].strip("-") or fallback).replace("_", "-")


def date_only(value: str) -> str:
    return (value or "")[:10]


def first_human_text(conversation: dict) -> str:
    for message in conversation.get("chat_messages") or []:
        if message.get("sender") == "human" and message.get("text"):
            return message["text"]
    return ""


def conversation_text(conversation: dict) -> str:
    pieces = [conversation.get("name"), conversation.get("summary"), first_human_text(conversation)]
    return " ".join(str(p or "") for p in pieces).lower()


def classify(conversation: dict) -> list[str]:
    text = conversation_text(conversation)
    matches = [name for name, words in CATEGORIES.items() if any(w.lower() in text for w in words)]
    return matches or ["未分类"]


def keywords_for_project(project: dict, memory: str) -> set[str]:
    raw = " ".join([project.get("name", ""), project.get("description", ""), memory[:1200]])
    chunks = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]{2,}", raw)
    words: set[str] = set()
    for chunk in chunks:
        lowered = chunk.lower()
        if len(lowered) >= 3:
            words.add(lowered)
        if re.search(r"[\u4e00-\u9fff]", chunk):
            for size in (2, 3, 4):
                for i in range(0, max(0, len(chunk) - size + 1)):
                    words.add(chunk[i : i + size].lower())
    return {w for w in words if len(w) >= 2 and w not in {"the", "and", "for", "with", "app"}}


def related_conversations(project: dict, memory: str, conversations: list[dict], limit: int = 12) -> list[tuple[int, dict]]:
    words = keywords_for_project(project, memory)
    scored = []
    for conversation in conversations:
        text = conversation_text(conversation)
        score = sum(len(word) for word in words if word in text)
        if project.get("name") and project["name"].lower() in text:
            score += 50
        if score >= 6:
            scored.append((score, conversation))
    scored.sort(key=lambda item: (item[0], item[1].get("updated_at", "")), reverse=True)
    return scored[:limit]


def project_doc_name(project: dict) -> str:
    return f"{slug(project.get('name'), project.get('uuid', 'project'))}-{project.get('uuid', '')[:8]}.md"


def render_profile(user: dict, memory: str, conversations: list[dict], skills: list[Path]) -> str:
    counts = Counter()
    for conversation in conversations:
        counts.update(classify(conversation))
    recent = sorted(conversations, key=lambda c: c.get("updated_at", ""), reverse=True)[:20]

    lines = [
        "# 个人画像",
        "",
        "> 从 Claude 导出自动整理。联系方式默认遮蔽，原始数据仍在 `users.json`。",
        "",
        "## 账户",
        "",
        f"- 姓名：{user.get('full_name', '')}",
        f"- UUID：{user.get('uuid', '')}",
        f"- 邮箱：{mask_email(user.get('email_address', ''))}",
        f"- 手机：{mask_phone(user.get('verified_phone_number', ''))}",
        "",
        "## Claude 全局记忆",
        "",
        memory.strip() or "_未找到 conversations_memory。_",
        "",
        "## 主题分布",
        "",
        "| 主题 | 会话数 |",
        "| --- | ---: |",
    ]
    lines += [f"| {name} | {count} |" for name, count in counts.most_common()]
    lines += [
        "",
        "## 最近会话信号",
        "",
        "| 日期 | 标题 | 分类 | 摘要/首条输入 |",
        "| --- | --- | --- | --- |",
    ]
    for conversation in recent:
        summary = conversation.get("summary") or first_human_text(conversation)
        lines.append(
            f"| {date_only(conversation.get('updated_at'))} | {cell(conversation.get('name'))} | "
            f"{cell(', '.join(classify(conversation)))} | {cell(summary)} |"
        )
    lines += [
        "",
        "## Claude 自定义技能",
        "",
    ]
    if skills:
        lines += [f"- `{path.name}`" for path in skills]
    else:
        lines.append("_未找到 skills/*.skill。_")
    return "\n".join(lines)


def render_conversations(conversations: list[dict]) -> str:
    counts = Counter()
    for conversation in conversations:
        counts.update(classify(conversation))
    lines = [
        "# 会话索引",
        "",
        f"- 会话总数：{len(conversations)}",
        f"- 有 Claude 摘要：{sum(bool(c.get('summary')) for c in conversations)}",
        "",
        "## 分类计数",
        "",
        "| 分类 | 会话数 |",
        "| --- | ---: |",
    ]
    lines += [f"| {name} | {count} |" for name, count in counts.most_common()]
    lines += [
        "",
        "## 全量会话",
        "",
        "| 日期 | 标题 | 分类 | 摘要/首条输入 |",
        "| --- | --- | --- | --- |",
    ]
    for conversation in sorted(conversations, key=lambda c: c.get("updated_at", ""), reverse=True):
        summary = conversation.get("summary") or first_human_text(conversation)
        lines.append(
            f"| {date_only(conversation.get('updated_at'))} | {cell(conversation.get('name'))} | "
            f"{cell(', '.join(classify(conversation)))} | {cell(summary)} |"
        )
    return "\n".join(lines)


def render_project(project: dict, memory: str, conversations: list[dict], design_chats: list[dict]) -> str:
    docs = project.get("docs") or []
    linked_designs = [
        chat for chat in design_chats if (chat.get("project") or {}).get("uuid") == project.get("uuid")
    ]
    related = related_conversations(project, memory, conversations)
    lines = [
        f"# {project.get('name') or project.get('uuid')}",
        "",
        "## 基础信息",
        "",
        f"- UUID：{project.get('uuid', '')}",
        f"- 描述：{project.get('description') or '_无_'}",
        f"- 创建：{date_only(project.get('created_at'))}",
        f"- 更新：{date_only(project.get('updated_at'))}",
        f"- Claude 项目文档数：{len(docs)}",
        f"- Claude 设计会话数：{len(linked_designs)}",
        "",
        "## Claude 项目记忆",
        "",
        memory.strip() or "_该项目没有 project_memories 记录。_",
        "",
        "## 项目内文档索引",
        "",
    ]
    if docs:
        lines += ["| 文件 | 字符数 | 内容预览 |", "| --- | ---: | --- |"]
        for doc in docs:
            lines.append(
                f"| {cell(doc.get('filename'))} | {len(doc.get('content') or '')} | {cell(doc.get('content'))} |"
            )
    else:
        lines.append("_无 Claude 项目文档。_")

    lines += ["", "## 设计会话", ""]
    if linked_designs:
        lines += ["| 日期 | 标题 | 消息数 |", "| --- | --- | ---: |"]
        for chat in sorted(linked_designs, key=lambda c: c.get("updated_at", ""), reverse=True):
            lines.append(f"| {date_only(chat.get('updated_at'))} | {cell(chat.get('title'))} | {len(chat.get('messages') or [])} |")
    else:
        lines.append("_无关联 design_chats。_")

    lines += ["", "## 相关会话", ""]
    if related:
        lines += ["| 日期 | 标题 | 分数 | 摘要/首条输入 |", "| --- | --- | ---: | --- |"]
        for score, conversation in related:
            summary = conversation.get("summary") or first_human_text(conversation)
            lines.append(
                f"| {date_only(conversation.get('updated_at'))} | {cell(conversation.get('name'))} | "
                f"{score} | {cell(summary)} |"
            )
    else:
        lines.append("_未按关键词匹配到相关会话。_")
    return "\n".join(lines)


def render_project_index(projects: list[dict], memories: dict, design_by_project: dict[str, list[dict]]) -> str:
    lines = [
        "# 项目索引",
        "",
        "| 项目 | UUID | 项目记忆 | 文档数 | 设计会话数 | 更新 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for project in sorted(projects, key=lambda p: (p.get("updated_at") or "", p.get("name") or ""), reverse=True):
        lines.append(
            f"| [{cell(project.get('name') or project.get('uuid'))}]({project_doc_name(project)}) | "
            f"`{project.get('uuid', '')}` | {len(memories.get(project.get('uuid'), ''))} | "
            f"{len(project.get('docs') or [])} | {len(design_by_project.get(project.get('uuid'), []))} | "
            f"{date_only(project.get('updated_at'))} |"
        )
    return "\n".join(lines)


def render_readme(stats: dict) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return "\n".join(
        [
            "# Claude Memory For Codex",
            "",
            f"- 生成时间：{generated}",
            f"- 项目数：{stats['projects']}",
            f"- 会话数：{stats['conversations']}",
            f"- 设计会话数：{stats['design_chats']}",
            "",
            "## 文件",
            "",
            "- `profile.md`：个人画像、全局记忆、近期会话信号",
            "- `projects/INDEX.md`：项目索引",
            "- `projects/*.md`：每个项目的记忆、文档索引、设计会话和相关会话",
            "- `conversations.md`：全量会话索引",
            "- `skills.md`：Claude 自定义技能索引",
        ]
    )


def render_skills(skills: list[Path]) -> str:
    lines = ["# Claude 自定义技能", ""]
    if not skills:
        return "\n".join(lines + ["_未找到 skills/*.skill。_"])
    for path in skills:
        text = path.read_text(encoding="utf-8", errors="replace")
        title = next((line.strip("# ").strip() for line in text.splitlines() if line.strip().startswith("#")), path.name)
        lines += [f"## {title}", "", f"- 文件：`{path.name}`", f"- 字符数：{len(text)}", ""]
    return "\n".join(lines)


def load_projects(root: Path, design_chats: list[dict]) -> list[dict]:
    projects = [read_json(path, {}) for path in sorted((root / "projects").glob("*.json"))]
    seen = {project.get("uuid") for project in projects}
    for chat in design_chats:
        design_project = chat.get("project") or {}
        uuid = design_project.get("uuid")
        if uuid and uuid not in seen:
            projects.append(
                {
                    "uuid": uuid,
                    "name": design_project.get("name") or uuid,
                    "description": "Only found in design_chats export.",
                    "created_at": chat.get("created_at", ""),
                    "updated_at": chat.get("updated_at", ""),
                    "docs": [],
                }
            )
            seen.add(uuid)
    return projects


def build_archive(root: Path, out: Path) -> dict:
    users = read_json(root / "users.json", [])
    memories_list = read_json(root / "memories.json", [])
    memory = memories_list[0] if memories_list else {}
    conversations = read_json(root / "conversations.json", [])
    design_chats = [read_json(path, {}) for path in sorted((root / "design_chats").glob("*.json"))]
    skills = sorted((root / "skills").glob("*.skill"))
    project_memories = memory.get("project_memories") or {}
    projects = load_projects(root, design_chats)
    design_by_project = defaultdict(list)
    for chat in design_chats:
        uuid = (chat.get("project") or {}).get("uuid")
        if uuid:
            design_by_project[uuid].append(chat)

    stats = {
        "projects": len(projects),
        "conversations": len(conversations),
        "design_chats": len(design_chats),
    }
    write(out / "README.md", render_readme(stats))
    write(out / "profile.md", render_profile(users[0] if users else {}, memory.get("conversations_memory", ""), conversations, skills))
    write(out / "conversations.md", render_conversations(conversations))
    write(out / "skills.md", render_skills(skills))
    write(out / "projects" / "INDEX.md", render_project_index(projects, project_memories, design_by_project))
    for project in projects:
        write(
            out / "projects" / project_doc_name(project),
            render_project(project, project_memories.get(project.get("uuid"), ""), conversations, design_chats),
        )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.cwd(), help="Claude export folder")
    parser.add_argument("--out", type=Path, default=Path.cwd() / "codex_memory", help="Markdown output folder")
    args = parser.parse_args()
    stats = build_archive(args.source, args.out)
    print(f"Wrote {stats['projects']} projects, {stats['conversations']} conversations to {args.out}")


if __name__ == "__main__":
    main()
