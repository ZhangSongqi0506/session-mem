from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from session_mem.core.cell import MemoryCell
from session_mem.storage.sqlite_backend import SQLiteBackend


@pytest.fixture
def backend():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    be = SQLiteBackend(db_path, vector_dims=1024)
    yield be
    be.close()
    Path(db_path).unlink(missing_ok=True)


def test_cell_store_crud(backend: SQLiteBackend) -> None:
    cell = MemoryCell(
        id="c1",
        session_id="s1",
        cell_type="fact",
        confidence=0.9,
        summary="summary one",
        keywords=["k1", "k2"],
        entities=["e1"],
        linked_prev=None,
        timestamp_start="2026-04-14T10:00:00Z",
        timestamp_end="2026-04-14T10:01:00Z",
        vector_id="v1",
    )
    backend.cell_store.save(cell)

    retrieved = backend.cell_store.get("c1")
    assert retrieved is not None
    assert retrieved.id == "c1"
    assert retrieved.session_id == "s1"
    assert retrieved.cell_type == "fact"
    assert retrieved.summary == "summary one"
    assert retrieved.keywords == ["k1", "k2"]
    assert retrieved.entities == ["e1"]
    assert retrieved.timestamp_start == "2026-04-14T10:00:00Z"

    cells = backend.cell_store.list_by_session("s1")
    assert len(cells) == 1
    assert cells[0].id == "c1"


def test_session_isolation(backend: SQLiteBackend) -> None:
    for sid in ("sA", "sB"):
        backend.cell_store.save(
            MemoryCell(
                id=f"{sid}_c1",
                session_id=sid,
                cell_type="task",
                confidence=0.8,
                summary=f"summary {sid}",
            )
        )

    assert len(backend.cell_store.list_by_session("sA")) == 1
    assert len(backend.cell_store.list_by_session("sB")) == 1
    assert backend.cell_store.list_by_session("sC") == []


def test_entity_links(backend: SQLiteBackend) -> None:
    backend.cell_store.save(
        MemoryCell(
            id="c1",
            session_id="s1",
            cell_type="fact",
            confidence=0.9,
            summary="budget discussion",
            entities=["budget", "team"],
        )
    )
    backend.cell_store.save(
        MemoryCell(
            id="c2",
            session_id="s1",
            cell_type="fact",
            confidence=0.8,
            summary="timeline discussion",
            entities=["timeline", "team"],
        )
    )

    by_budget = backend.cell_store.find_by_entity("s1", "budget")
    assert len(by_budget) == 1
    assert by_budget[0].id == "c1"

    by_team = backend.cell_store.find_by_entity("s1", "team")
    assert len(by_team) == 2


def test_text_store(backend: SQLiteBackend) -> None:
    backend.cell_store.save(
        MemoryCell(
            id="c1",
            session_id="s1",
            cell_type="fact",
            confidence=0.9,
            summary="summary",
        )
    )
    backend.text_store.save("c1", "raw text content", token_count=42)
    assert backend.text_store.load("c1") == "raw text content"


def test_vector_index(backend: SQLiteBackend) -> None:
    emb1 = [1.0] * 1024
    emb2 = [0.0] * 1024
    backend.vector_index.add("v1", emb1)
    backend.vector_index.add("v2", emb2)

    results = backend.vector_index.search(emb1, top_k=2)
    assert len(results) == 2
    assert results[0][0] == "v1"

    backend.vector_index.remove("v1")
    results_after = backend.vector_index.search(emb1, top_k=2)
    assert all(r[0] != "v1" for r in results_after)


def test_delete_session_cascades(backend: SQLiteBackend) -> None:
    backend.cell_store.save(
        MemoryCell(
            id="c1",
            session_id="s1",
            cell_type="fact",
            confidence=0.9,
            summary="summary",
            entities=["e1"],
        )
    )
    backend.text_store.save("c1", "raw", token_count=1)
    backend.vector_index.add("c1", [1.0] * 1024)

    meta = MemoryCell(
        id="m1",
        session_id="s1",
        cell_type="meta",
        confidence=1.0,
        summary="meta summary",
        raw_text="meta raw",
        token_count=10,
        status="active",
        version=1,
        linked_cells=["c1"],
    )
    backend.save_meta_cell(meta)

    backend.delete_session("s1")

    assert backend.cell_store.list_by_session("s1") == []
    assert backend.text_store.load("c1") == ""
    assert backend.vector_index.search([1.0] * 1024, top_k=5) == []
    assert backend.get_active_meta_cell("s1") is None


def test_meta_cells(backend: SQLiteBackend) -> None:
    meta_v1 = MemoryCell(
        id="m1",
        session_id="s1",
        cell_type="meta",
        confidence=1.0,
        summary="meta v1",
        raw_text="meta raw v1",
        token_count=10,
        status="active",
        version=1,
        linked_cells=["c1"],
    )
    backend.save_meta_cell(meta_v1)

    active = backend.get_active_meta_cell("s1")
    assert active is not None
    assert active.id == "m1"
    assert active.version == 1
    assert active.status == "active"
    assert active.linked_cells == ["c1"]

    meta_v2 = MemoryCell(
        id="m2",
        session_id="s1",
        cell_type="meta",
        confidence=1.0,
        summary="meta v2",
        raw_text="meta raw v2",
        token_count=12,
        status="active",
        version=2,
        linked_cells=["c1", "c2"],
    )
    backend.save_meta_cell(meta_v2)

    active = backend.get_active_meta_cell("s1")
    assert active is not None
    assert active.id == "m2"
    assert active.version == 2

    backend.delete_meta_cells_by_session("s1")
    assert backend.get_active_meta_cell("s1") is None


def test_cell_type_fragmented(backend: SQLiteBackend) -> None:
    cell = MemoryCell(
        id="c_frag",
        session_id="s1",
        cell_type="fragmented",
        confidence=0.7,
        summary="forced split",
    )
    backend.cell_store.save(cell)
    retrieved = backend.cell_store.get("c_frag")
    assert retrieved is not None
    assert retrieved.cell_type == "fragmented"


def test_get_full_cell_backfills_text_and_tokens(backend: SQLiteBackend) -> None:
    backend.cell_store.save(
        MemoryCell(
            id="c1",
            session_id="s1",
            cell_type="fact",
            confidence=0.9,
            summary="summary",
        )
    )
    backend.text_store.save("c1", "full raw text", token_count=123)

    cell = backend.get_full_cell("c1")
    assert cell is not None
    assert cell.raw_text == "full raw text"
    assert cell.token_count == 123


def test_causal_deps_and_metadata_persistence(backend: SQLiteBackend) -> None:
    cell = MemoryCell(
        id="c1",
        session_id="s1",
        cell_type="constraint",
        confidence=0.9,
        summary="budget limit",
        causal_deps=["C_001"],
        metadata={"source": "user_explicit", "priority": "high"},
    )
    backend.cell_store.save(cell)

    retrieved = backend.cell_store.get("c1")
    assert retrieved is not None
    assert retrieved.causal_deps == ["C_001"]
    assert retrieved.metadata == {"source": "user_explicit", "priority": "high"}
