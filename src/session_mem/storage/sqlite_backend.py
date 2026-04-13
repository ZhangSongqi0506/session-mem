from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from session_mem.core.cell import MemoryCell
from session_mem.storage.base import CellStore, TextStore, VectorIndex


class SQLiteVectorIndex(VectorIndex):
    """基于 sqlite-vec 的向量索引实现。"""

    def __init__(self, conn: sqlite3.Connection, dims: int = 512):
        self.conn = conn
        self.dims = dims
        self._ensure_table()

    def _ensure_table(self) -> None:
        self.conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS cell_vectors USING vec0("
            f"cell_id TEXT PRIMARY KEY, embedding FLOAT[{self.dims}]"
            f")"
        )
        self.conn.commit()

    def add(self, cell_id: str, embedding: list[float]) -> None:
        emb_json = json.dumps(embedding)
        self.conn.execute(
            "INSERT OR REPLACE INTO cell_vectors (cell_id, embedding) VALUES (?, ?)",
            (cell_id, emb_json),
        )
        self.conn.commit()

    def search(self, query_embedding: list[float], top_k: int = 5) -> list[tuple[str, float]]:
        emb_json = json.dumps(query_embedding)
        rows = self.conn.execute(
            "SELECT cell_id, distance FROM cell_vectors WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (emb_json, top_k),
        ).fetchall()
        return [(r[0], float(r[1])) for r in rows]

    def remove(self, cell_id: str) -> None:
        self.conn.execute(
            "DELETE FROM cell_vectors WHERE cell_id = ?", (cell_id,)
        )
        self.conn.commit()

    def clear(self) -> None:
        self.conn.execute("DELETE FROM cell_vectors")
        self.conn.commit()


class SQLiteCellStore(CellStore):
    """基于 SQLite 的 Cell 元数据存储实现。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cells (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                cell_type TEXT CHECK(cell_type IN ('fact', 'constraint', 'preference', 'task', 'fragmented')),
                confidence REAL,
                summary TEXT,
                keywords TEXT,
                entities TEXT,
                linked_prev TEXT,
                timestamp_start TEXT,
                timestamp_end TEXT,
                vector_id TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cells_session ON cells(session_id)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cells_type ON cells(cell_type)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cells_linked_prev ON cells(linked_prev)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entity_links (
                cell_id TEXT,
                entity TEXT,
                FOREIGN KEY (cell_id) REFERENCES cells(id)
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entity_links_entity ON entity_links(entity)"
        )
        self.conn.commit()

    def save(self, cell: MemoryCell) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO cells (
                id, session_id, cell_type, confidence, summary,
                keywords, entities, linked_prev, timestamp_start, timestamp_end, vector_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cell.id,
                cell.session_id,
                cell.cell_type,
                cell.confidence,
                cell.summary,
                json.dumps(cell.keywords, ensure_ascii=False),
                json.dumps(cell.entities, ensure_ascii=False),
                cell.linked_prev,
                cell.timestamp_start,
                cell.timestamp_end,
                cell.vector_id,
            ),
        )
        # 更新实体共现表
        self.conn.execute("DELETE FROM entity_links WHERE cell_id = ?", (cell.id,))
        for entity in cell.entities:
            self.conn.execute(
                "INSERT INTO entity_links (cell_id, entity) VALUES (?, ?)",
                (cell.id, entity),
            )
        self.conn.commit()

    def get(self, cell_id: str) -> MemoryCell | None:
        row = self.conn.execute(
            "SELECT * FROM cells WHERE id = ?", (cell_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_cell(row)

    def list_by_session(
        self, session_id: str, limit: int | None = None
    ) -> list[MemoryCell]:
        sql = "SELECT * FROM cells WHERE session_id = ? ORDER BY created_at"
        params: list[Any] = [session_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_cell(r) for r in rows]

    def find_by_entity(self, session_id: str, entity: str) -> list[MemoryCell]:
        rows = self.conn.execute(
            """
            SELECT c.* FROM cells c
            JOIN entity_links e ON c.id = e.cell_id
            WHERE c.session_id = ? AND e.entity = ?
            """,
            (session_id, entity),
        ).fetchall()
        return [self._row_to_cell(r) for r in rows]

    def delete_session(self, session_id: str) -> None:
        cell_ids = [
            r[0]
            for r in self.conn.execute(
                "SELECT id FROM cells WHERE session_id = ?", (session_id,)
            ).fetchall()
        ]
        for cid in cell_ids:
            self.conn.execute("DELETE FROM entity_links WHERE cell_id = ?", (cid,))
        self.conn.execute("DELETE FROM cells WHERE session_id = ?", (session_id,))
        self.conn.commit()

    def _row_to_cell(self, row: sqlite3.Row) -> MemoryCell:
        return MemoryCell(
            id=row["id"],
            session_id=row["session_id"],
            cell_type=row["cell_type"],
            confidence=row["confidence"],
            summary=row["summary"],
            keywords=json.loads(row["keywords"] or "[]"),
            entities=json.loads(row["entities"] or "[]"),
            linked_prev=row["linked_prev"],
            timestamp_start=row["timestamp_start"],
            timestamp_end=row["timestamp_end"],
            vector_id=row["vector_id"],
        )


class SQLiteTextStore(TextStore):
    """基于 SQLite 的原文存储实现。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._ensure_table()

    def _ensure_table(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cell_texts (
                cell_id TEXT PRIMARY KEY,
                raw_text TEXT,
                token_count INTEGER,
                FOREIGN KEY (cell_id) REFERENCES cells(id)
            )
            """
        )
        self.conn.commit()

    def save(self, cell_id: str, raw_text: str, token_count: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO cell_texts (cell_id, raw_text, token_count) VALUES (?, ?, ?)",
            (cell_id, raw_text, token_count),
        )
        self.conn.commit()

    def load(self, cell_id: str) -> str:
        row = self.conn.execute(
            "SELECT raw_text FROM cell_texts WHERE cell_id = ?", (cell_id,)
        ).fetchone()
        return row[0] if row else ""

    def delete(self, cell_id: str) -> None:
        self.conn.execute("DELETE FROM cell_texts WHERE cell_id = ?", (cell_id,))
        self.conn.commit()


class SQLiteBackend:
    """
    统一 SQLite 后端，一个 db 文件包含 VectorIndex + CellStore + TextStore。
    """

    def __init__(self, db_path: str | Path, vector_dims: int = 512):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.vector_index = SQLiteVectorIndex(self.conn, dims=vector_dims)
        self.cell_store = SQLiteCellStore(self.conn)
        self.text_store = SQLiteTextStore(self.conn)
