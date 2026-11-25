#!/usr/bin/env python3
"""SQLite state store for the Figma-to-Qt batch pipeline."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    id TEXT PRIMARY KEY,
    figma_file TEXT,
    node_id TEXT,
    status TEXT NOT NULL,
    round INTEGER NOT NULL DEFAULT 0,
    best_ssim REAL,
    conversation_id TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rounds (
    page_id TEXT NOT NULL,
    round INTEGER NOT NULL,
    ssim REAL,
    mismatch_ratio REAL,
    delta_e_mean REAL,
    passed INTEGER NOT NULL,
    changed_files TEXT,
    conversation_id TEXT,
    duration REAL,
    artifacts TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (page_id, round)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path))
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def upsert_page(
        self,
        page_id: str,
        *,
        figma_file: str = "",
        node_id: str = "",
        status: str = "pending",
        round_number: int = 0,
        best_ssim: float | None = None,
        conversation_id: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO pages
                (id, figma_file, node_id, status, round, best_ssim,
                 conversation_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                figma_file=excluded.figma_file,
                node_id=excluded.node_id,
                status=excluded.status,
                round=excluded.round,
                best_ssim=COALESCE(excluded.best_ssim, pages.best_ssim),
                conversation_id=COALESCE(excluded.conversation_id,
                                         pages.conversation_id),
                updated_at=excluded.updated_at
            """,
            (
                page_id,
                figma_file,
                node_id,
                status,
                round_number,
                best_ssim,
                conversation_id,
                _now(),
            ),
        )
        self.connection.commit()

    def get_page(self, page_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM pages WHERE id = ?", (page_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_pages(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM pages ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]

    def add_round(
        self,
        page_id: str,
        round_number: int,
        report: dict[str, Any],
        *,
        changed_files: list[str] | None = None,
        conversation_id: str | None = None,
        duration: float | None = None,
    ) -> None:
        metrics = report.get("metrics", {})
        self.connection.execute(
            """
            INSERT OR REPLACE INTO rounds
                (page_id, round, ssim, mismatch_ratio, delta_e_mean,
                 passed, changed_files, conversation_id, duration,
                 artifacts, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                page_id,
                round_number,
                metrics.get("ssim"),
                metrics.get("mismatch_ratio"),
                metrics.get("delta_e_mean"),
                1 if report.get("pass") else 0,
                json.dumps(changed_files or [], ensure_ascii=False),
                conversation_id,
                duration,
                json.dumps(report.get("artifacts", {}), ensure_ascii=False),
                _now(),
            ),
        )
        self.connection.commit()

    def latest_round(self, page_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM rounds WHERE page_id = ?
            ORDER BY round DESC LIMIT 1
            """,
            (page_id,),
        ).fetchone()
        return dict(row) if row else None

    def rounds(self, page_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM rounds WHERE page_id = ? ORDER BY round",
            (page_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("state.db"),
    )
    parser.add_argument("--page", default="")
    args = parser.parse_args(argv)

    store = StateStore(args.db)
    try:
        if args.page:
            payload: Any = {
                "page": store.get_page(args.page),
                "rounds": store.rounds(args.page),
            }
        else:
            payload = {"pages": store.list_pages()}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
