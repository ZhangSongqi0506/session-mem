from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import sqlite_vec

from session_mem.core.cell import MemoryCell
from session_mem.storage.base import CellStore, TextStore, VectorIndex


class SQLiteVectorIndex(VectorIndex):
    """基于 sqlite-vec 的向量索引实现。"""

    def __init__(self, conn: sqlite3.Connection, dims: int = 1024):
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
        self.conn.execute("DELETE FROM cell_vectors WHERE cell_id = ?", (cell_id,))
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
        self.conn.execute("""
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
                causal_deps TEXT,
                metadata TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_cells_session ON cells(session_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_cells_type ON cells(cell_type)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_cells_linked_prev ON cells(linked_prev)")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS entity_links (
                cell_id TEXT,
                entity TEXT,
                FOREIGN KEY (cell_id) REFERENCES cells(id)
            )
            """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entity_links_entity ON entity_links(entity)"
        )
        self.conn.commit()

    def save(self, cell: MemoryCell) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO cells (
                id, session_id, cell_type, confidence, summary,
                keywords, entities, linked_prev, timestamp_start, timestamp_end, vector_id,
                causal_deps, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(cell.causal_deps, ensure_ascii=False),
                json.dumps(cell.metadata, ensure_ascii=False),
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
        row = self.conn.execute("SELECT * FROM cells WHERE id = ?", (cell_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_cell(row)

    def list_by_session(self, session_id: str, limit: int | None = None) -> list[MemoryCell]:
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
            self.conn.execute("DELETE FROM cell_texts WHERE cell_id = ?", (cid,))
            self.conn.execute("DELETE FROM cell_vectors WHERE cell_id = ?", (cid,))
        self.conn.execute("DELETE FROM meta_cells WHERE session_id = ?", (session_id,))
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
            causal_deps=json.loads(row["causal_deps"] or "[]"),
            metadata=json.loads(row["metadata"] or "{}"),
        )


class SQLiteTextStore(TextStore):
    """基于 SQLite 的原文存储实现。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._ensure_table()

    def _ensure_table(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS cell_texts (
                cell_id TEXT PRIMARY KEY,
                raw_text TEXT,
                token_count INTEGER,
                FOREIGN KEY (cell_id) REFERENCES cells(id)
            )
            """)
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
    统一 SQLite 后端，一个 db 文件包含 VectorIndex + CellStore + TextStore + MetaCellStore。
    """

    def __init__(self, db_path: str | Path, vector_dims: int = 1024):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # 加载 sqlite-vec 扩展并启用外键约束
        self.conn.enable_load_extension(True)
        sqlite_vec.load(self.conn)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.vector_index = SQLiteVectorIndex(self.conn, dims=vector_dims)
        self.cell_store = SQLiteCellStore(self.conn)
        self.text_store = SQLiteTextStore(self.conn)
        self._ensure_meta_tables()

    def _ensure_meta_tables(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS meta_cells (
                session_id TEXT NOT NULL,
                cell_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                cell_type TEXT DEFAULT 'meta',
                status TEXT CHECK(status IN ('active', 'archived')),
                raw_text TEXT,
                token_count INTEGER DEFAULT 0,
                linked_cells TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (session_id, version)
            )
            """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_meta_cells_session ON meta_cells(session_id)"
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_cells_status ON meta_cells(status)")
        self.conn.commit()

    def save_meta_cell(self, cell: MemoryCell) -> None:
        """保存或更新 Meta Cell；将同一 session 的旧版本标记为 archived。"""
        with self.conn:
            self.conn.execute(
                "UPDATE meta_cells SET status = 'archived' WHERE session_id = ? AND status = 'active'",
                (cell.session_id,),
            )
            self.conn.execute(
                """
                INSERT INTO meta_cells (
                    session_id, cell_id, version, cell_type, status,
                    raw_text, token_count, linked_cells, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    cell.session_id,
                    cell.id,
                    cell.version or 1,
                    cell.cell_type or "meta",
                    cell.status or "active",
                    cell.raw_text,
                    cell.token_count,
                    json.dumps(cell.linked_cells, ensure_ascii=False),
                ),
            )

    def get_active_meta_cell(self, session_id: str) -> MemoryCell | None:
        """获取指定会话当前 active 的 Meta Cell。"""
        row = self.conn.execute(
            """
            SELECT * FROM meta_cells
            WHERE session_id = ? AND status = 'active'
            ORDER BY version DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return MemoryCell(
            id=row["cell_id"],
            session_id=row["session_id"],
            cell_type=row["cell_type"],
            confidence=1.0,
            summary=row["raw_text"] or "",
            raw_text=row["raw_text"] or "",
            token_count=row["token_count"] or 0,
            status=row["status"],
            version=row["version"],
            linked_cells=json.loads(row["linked_cells"] or "[]"),
        )

    def delete_meta_cells_by_session(self, session_id: str) -> None:
        """删除指定会话的全部 Meta Cell。"""
        self.conn.execute("DELETE FROM meta_cells WHERE session_id = ?", (session_id,))
        self.conn.commit()

    def close(self) -> None:
        """关闭数据库连接。"""
        self.conn.close()
