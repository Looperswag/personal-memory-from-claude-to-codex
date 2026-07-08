from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CATEGORIES = {
    "AI导购/电商": ["导购", "天猫", "淘宝", "购物", "千问", "意图分类", "电商", "qwen"],
    "Agent/多智能体": ["agent", "swarm", "多智能体", "mcp", "tool", "工作流", "memory"],
    "视频/AIGC": ["视频", "剪辑", "成片", "capcut", "aigc", "素材"],
    "内容创作": ["公众号", "小红书", "twitter", "x平台", "博客", "文案", "文章"],
    "职业/个人": ["简历", "面试", "职业", "收入", "独立开发", "个人"],
    "旅行/生活": ["旅行", "旅游", "travel", "route", "trip", "生活"],
    "游戏/设计": ["德州扑克", "poker", "游戏", "视觉", "设计", "ui"],
    "论文/研究": ["论文", "paper", "研究", "调研", "benchmark", "评测"],
}


@dataclass(frozen=True)
class Node:
    id: str
    type: str
    title: str
    summary: str = ""
    source_path: str = ""
    updated_at: str = ""
    size: int = 0
    meta: dict[str, Any] | None = None
    content: str = ""


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    type: str
    weight: float = 1.0
    meta: dict[str, Any] | None = None


class MemoryGraphIndexer:
    def __init__(self, root: Path | str, db_path: Path | str):
        self.root = Path(root).resolve()
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def rebuild(self, reason: str = "manual", changed_path: str | Path | None = None) -> int:
        nodes, edges, files = self._scan()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute("delete from nodes")
            conn.execute("delete from edges")
            conn.execute("delete from files")
            conn.execute("delete from nodes_fts")
            conn.executemany(
                """
                insert into nodes(id, type, title, summary, source_path, updated_at, size, meta_json)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        node.id,
                        node.type,
                        node.title,
                        node.summary,
                        node.source_path,
                        node.updated_at,
                        node.size,
                        json.dumps(node.meta or {}, ensure_ascii=False),
                    )
                    for node in nodes.values()
                ],
            )
            conn.executemany(
                "insert into edges(source, target, type, weight, meta_json) values (?, ?, ?, ?, ?)",
                [
                    (
                        edge.source,
                        edge.target,
                        edge.type,
                        edge.weight,
                        json.dumps(edge.meta or {}, ensure_ascii=False),
                    )
                    for edge in edges
                ],
            )
            conn.executemany(
                "insert into files(path, mtime_ns, size, sha256, indexed_at) values (?, ?, ?, ?, ?)",
                files,
            )
            conn.executemany(
                "insert into nodes_fts(id, title, summary, content) values (?, ?, ?, ?)",
                [(node.id, node.title, node.summary, node.content) for node in nodes.values()],
            )
            cursor = conn.execute(
                "insert into events(ts, kind, path, message) values (?, ?, ?, ?)",
                (now, reason, self._rel(changed_path) if changed_path else "", f"indexed {len(nodes)} nodes"),
            )
            return int(cursor.lastrowid)

    def changed_paths(self) -> list[str]:
        current = {path: (mtime_ns, size) for path, mtime_ns, size in self._file_manifest()}
        with self._connect() as conn:
            stored = {
                row["path"]: (row["mtime_ns"], row["size"])
                for row in conn.execute("select path, mtime_ns, size from files")
            }
        changed = {path for path, sig in current.items() if stored.get(path) != sig}
        changed.update(path for path in stored if path not in current)
        return sorted(changed)

    def graph(self, since: int | None = None) -> dict[str, Any]:
        with self._connect() as conn:
            nodes = [self._row_node(row) for row in conn.execute("select * from nodes order by type, title")]
            edges = [self._row_edge(row) for row in conn.execute("select * from edges")]
            event_id = conn.execute("select coalesce(max(id), 0) from events").fetchone()[0]
        return {"event_id": event_id, "nodes": nodes, "edges": edges, "since": since or 0}

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        terms = re.findall(r"[\w\u4e00-\u9fff]+", query or "")
        if not terms:
            sql = "select * from nodes order by updated_at desc, title limit ?"
            with self._connect() as conn:
                return [self._row_node(row) for row in conn.execute(sql, (limit,))]
        expr = " OR ".join(f'"{term}"' for term in terms)
        with self._connect() as conn:
            rows = conn.execute(
                """
                select nodes.*
                from nodes_fts
                join nodes on nodes.id = nodes_fts.id
                where nodes_fts match ?
                order by bm25(nodes_fts)
                limit ?
                """,
                (expr, limit),
            )
            return [self._row_node(row) for row in rows]

    def node_detail(self, node_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            node_row = conn.execute("select * from nodes where id = ?", (node_id,)).fetchone()
            if not node_row:
                return None
            edge_rows = conn.execute(
                "select * from edges where source = ? or target = ? order by type, weight desc",
                (node_id, node_id),
            ).fetchall()
            neighbor_ids = sorted(
                {row["target"] if row["source"] == node_id else row["source"] for row in edge_rows}
            )
            neighbors = []
            for neighbor_id in neighbor_ids:
                row = conn.execute("select * from nodes where id = ?", (neighbor_id,)).fetchone()
                if row:
                    neighbors.append(self._row_node(row))
        return {"node": self._row_node(node_row), "edges": [self._row_edge(row) for row in edge_rows], "neighbors": neighbors}

    def latest_event_id(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("select coalesce(max(id), 0) from events").fetchone()[0])

    def _scan(self) -> tuple[dict[str, Node], list[Edge], list[tuple[str, int, int, str, str]]]:
        nodes: dict[str, Node] = {}
        edges: list[Edge] = []
        file_rows = []
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        def add_node(node: Node) -> None:
            existing = nodes.get(node.id)
            if not existing or len(node.content) > len(existing.content):
                nodes[node.id] = node

        def add_file_node(path: Path) -> None:
            rel = self._rel(path)
            text = _read_text(path) if path.suffix.lower() in {".md", ".skill"} else ""
            add_node(
                Node(
                    id=f"file:{rel}",
                    type="file",
                    title=rel,
                    summary=_summary(text) if text else f"{path.stat().st_size} bytes",
                    source_path=rel,
                    updated_at=_iso_mtime(path),
                    size=path.stat().st_size,
                    meta={"extension": path.suffix.lower()},
                    content=text[:12000],
                )
            )

        for rel, mtime_ns, size in self._file_manifest():
            path = self.root / rel
            file_rows.append((rel, mtime_ns, size, _sha256(path), now))
            add_file_node(path)

        memory = _read_json(self.root / "memories.json", [])
        project_memories = (memory[0].get("project_memories") if memory else {}) or {}
        profile_memory = (memory[0].get("conversations_memory") if memory else "") or ""

        profile_path = self.root / "codex_memory" / "profile.md"
        profile_text = _read_text(profile_path)
        add_node(
            Node(
                id="profile:me",
                type="profile",
                title="个人画像",
                summary=_summary(profile_memory or profile_text),
                source_path=self._rel(profile_path) if profile_path.exists() else "memories.json",
                updated_at=_iso_mtime(profile_path) if profile_path.exists() else "",
                meta={"memory_chars": len(profile_memory)},
                content=profile_memory + "\n" + profile_text,
            )
        )
        for topic in self._topics_for(profile_memory + "\n" + profile_text):
            add_node(Node(id=f"topic:{topic}", type="topic", title=topic, content=topic))
            edges.append(Edge("profile:me", f"topic:{topic}", "has_topic"))

        projects = self._load_projects()
        project_topics: dict[str, set[str]] = {}
        for project in projects:
            uuid = project.get("uuid") or Path(project.get("_path", "project")).stem
            source = Path(project.get("_path", "")) if project.get("_path") else None
            rel = self._rel(source) if source and source.exists() else ""
            memory_text = project_memories.get(uuid, "")
            content = "\n".join(
                [
                    project.get("name", ""),
                    project.get("description") or "",
                    memory_text,
                    "\n".join(doc.get("content", "") for doc in project.get("docs") or []),
                ]
            )
            status = _infer_status(content, project.get("updated_at", ""))
            node_id = f"project:{uuid}"
            add_node(
                Node(
                    id=node_id,
                    type="project",
                    title=project.get("name") or uuid,
                    summary=_summary(project.get("description") or memory_text or content),
                    source_path=rel,
                    updated_at=_date_only(project.get("updated_at", "")),
                    size=len(content),
                    meta={
                        "uuid": uuid,
                        "status": status,
                        "doc_count": len(project.get("docs") or []),
                        "created_at": _date_only(project.get("created_at", "")),
                    },
                    content=content,
                )
            )
            if rel:
                edges.append(Edge(node_id, f"file:{rel}", "defined_in"))
            for doc in project.get("docs") or []:
                doc_id = f"doc:{uuid}:{_slug(doc.get('filename') or 'doc')}"
                add_node(
                    Node(
                        id=doc_id,
                        type="doc",
                        title=doc.get("filename") or "Project doc",
                        summary=_summary(doc.get("content", "")),
                        updated_at=_date_only(project.get("updated_at", "")),
                        size=len(doc.get("content") or ""),
                        meta={"project_uuid": uuid},
                        content=doc.get("content", ""),
                    )
                )
                edges.append(Edge(node_id, doc_id, "has_doc"))
            topics = self._topics_for(content)
            project_topics[node_id] = set(topics)
            for topic in topics:
                add_node(Node(id=f"topic:{topic}", type="topic", title=topic, content=topic))
                edges.append(Edge(node_id, f"topic:{topic}", "has_topic"))

        for md_path in sorted((self.root / "codex_memory" / "projects").glob("*.md")):
            text = _read_text(md_path)
            title = _first_heading(text) or md_path.stem
            uuid = _extract_uuid(text) or md_path.stem
            node_id = f"project:{uuid}"
            add_node(
                Node(
                    id=node_id,
                    type="project",
                    title=title,
                    summary=_summary(text),
                    source_path=self._rel(md_path),
                    updated_at=_iso_mtime(md_path)[:10],
                    size=len(text),
                    meta={"uuid": uuid, "status": _infer_status(text, "")},
                    content=text,
                )
            )
            edges.append(Edge(node_id, f"file:{self._rel(md_path)}", "has_markdown"))
            for topic in self._topics_for(text):
                add_node(Node(id=f"topic:{topic}", type="topic", title=topic, content=topic))
                edges.append(Edge(node_id, f"topic:{topic}", "has_topic"))
                project_topics.setdefault(node_id, set()).add(topic)

        for chat in self._load_design_chats():
            uuid = chat.get("uuid") or Path(chat.get("_path", "chat")).stem
            project_uuid = ((chat.get("project") or {}).get("uuid")) or ""
            rel = self._rel(Path(chat["_path"])) if chat.get("_path") else ""
            node_id = f"design:{uuid}"
            add_node(
                Node(
                    id=node_id,
                    type="design",
                    title=chat.get("title") or uuid,
                    summary=f"{len(chat.get('messages') or [])} messages",
                    source_path=rel,
                    updated_at=_date_only(chat.get("updated_at", "")),
                    meta={"project_uuid": project_uuid},
                    content=json.dumps(chat, ensure_ascii=False)[:12000],
                )
            )
            if rel:
                edges.append(Edge(node_id, f"file:{rel}", "defined_in"))
            if project_uuid:
                edges.append(Edge(f"project:{project_uuid}", node_id, "has_design"))

        for skill_path in sorted((self.root / "skills").glob("*.skill")):
            text = _read_text(skill_path)
            node_id = f"skill:{skill_path.stem}"
            add_node(
                Node(
                    id=node_id,
                    type="skill",
                    title=_first_heading(text) or skill_path.stem,
                    summary=_summary(text),
                    source_path=self._rel(skill_path),
                    updated_at=_iso_mtime(skill_path)[:10],
                    size=len(text),
                    content=text,
                )
            )
            edges.append(Edge("profile:me", node_id, "uses_skill"))
            edges.append(Edge(node_id, f"file:{self._rel(skill_path)}", "defined_in"))

        project_ids = sorted(project_topics)
        for index, left in enumerate(project_ids):
            for right in project_ids[index + 1 :]:
                shared = project_topics[left] & project_topics[right]
                if shared:
                    edges.append(Edge(left, right, "related_topic", len(shared), {"topics": sorted(shared)}))

        deduped_edges = {}
        for edge in edges:
            if edge.source in nodes and edge.target in nodes:
                deduped_edges[(edge.source, edge.target, edge.type)] = edge
        return nodes, list(deduped_edges.values()), file_rows

    def _load_projects(self) -> list[dict[str, Any]]:
        projects = []
        for path in sorted((self.root / "projects").glob("*.json")):
            data = _read_json(path, {})
            if isinstance(data, dict):
                data["_path"] = str(path)
                projects.append(data)
        return projects

    def _load_design_chats(self) -> list[dict[str, Any]]:
        chats = []
        for path in sorted((self.root / "design_chats").glob("*.json")):
            data = _read_json(path, {})
            if isinstance(data, dict):
                data["_path"] = str(path)
                chats.append(data)
        return chats

    def _topics_for(self, text: str) -> list[str]:
        lowered = (text or "").lower()
        return [name for name, words in CATEGORIES.items() if any(word.lower() in lowered for word in words)]

    def _file_manifest(self) -> list[tuple[str, int, int]]:
        paths: set[Path] = set()
        for pattern in (
            "codex_memory/**/*.md",
            "projects/*.json",
            "design_chats/*.json",
            "skills/*.skill",
            "memories.json",
            "users.json",
        ):
            paths.update(self.root.glob(pattern))
        rows = []
        for path in sorted(p for p in paths if p.is_file() and p.name != ".DS_Store"):
            stat = path.stat()
            rows.append((self._rel(path), stat.st_mtime_ns, stat.st_size))
        return rows

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists nodes(
                    id text primary key,
                    type text not null,
                    title text not null,
                    summary text not null default '',
                    source_path text not null default '',
                    updated_at text not null default '',
                    size integer not null default 0,
                    meta_json text not null default '{}'
                );
                create table if not exists edges(
                    source text not null,
                    target text not null,
                    type text not null,
                    weight real not null default 1,
                    meta_json text not null default '{}',
                    primary key(source, target, type)
                );
                create table if not exists files(
                    path text primary key,
                    mtime_ns integer not null,
                    size integer not null,
                    sha256 text not null,
                    indexed_at text not null
                );
                create table if not exists events(
                    id integer primary key autoincrement,
                    ts text not null,
                    kind text not null,
                    path text not null default '',
                    message text not null default ''
                );
                create virtual table if not exists nodes_fts using fts5(
                    id unindexed,
                    title,
                    summary,
                    content
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _row_node(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "type": row["type"],
            "title": row["title"],
            "summary": row["summary"],
            "source_path": row["source_path"],
            "updated_at": row["updated_at"],
            "size": row["size"],
            "meta": _loads(row["meta_json"]),
        }

    def _row_edge(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "source": row["source"],
            "target": row["target"],
            "type": row["type"],
            "weight": row["weight"],
            "meta": _loads(row["meta_json"]),
        }

    def _rel(self, path: str | Path | None) -> str:
        if not path:
            return ""
        path = Path(path)
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _loads(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _summary(text: str, limit: int = 220) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def _first_heading(text: str) -> str:
    for line in (text or "").splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _extract_uuid(text: str) -> str:
    match = re.search(r"UUID[：:]\s*`?([0-9a-fA-F-]{8,})`?", text or "")
    return match.group(1) if match else ""


def _slug(text: str) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "-", (text or "").lower()).strip("-")
    return value or "doc"


def _date_only(value: str) -> str:
    return (value or "")[:10]


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")


def _infer_status(text: str, updated_at: str) -> str:
    lowered = (text or "").lower()
    if any(word in lowered for word in ("blocked", "阻塞", "pending dependency")):
        return "blocked"
    if any(word in lowered for word in ("active", "ongoing", "in progress", "正在", "进行中")):
        return "active"
    try:
        updated = datetime.fromisoformat((updated_at or "")[:10])
        if (datetime.now() - updated).days <= 120:
            return "active"
    except ValueError:
        pass
    return "archived"
