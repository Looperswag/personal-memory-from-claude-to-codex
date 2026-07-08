from __future__ import annotations

import argparse
from pathlib import Path

from .indexer import MemoryGraphIndexer
from .server import MemoryGraphApp


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local memory graph system.")
    parser.add_argument("--source", type=Path, default=Path.cwd(), help="Claude/Codex memory export folder")
    parser.add_argument("--db", type=Path, default=Path.cwd() / "memory_graph.db", help="SQLite database path")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--poll", type=float, default=2.0, help="file polling interval in seconds")
    args = parser.parse_args()

    indexer = MemoryGraphIndexer(args.source, args.db)
    if not args.db.exists() or not indexer.latest_event_id():
        indexer.rebuild("initial")
    app = MemoryGraphApp(indexer)
    app.start_watcher(args.poll)
    server = app.serve(args.host, args.port)
    print(f"Memory graph running at http://{args.host}:{args.port}")
    print(f"Source: {args.source.resolve()}")
    print(f"Database: {args.db.resolve()}")
    server.serve_forever()


if __name__ == "__main__":
    main()

