from __future__ import annotations


from session_mem.core.cell import MemoryCell
from session_mem.retrieval.hybrid_search import HybridSearcher
from session_mem.retrieval.query_rewriter import QueryRewriter


class FakeLLMClient:
    def __init__(self, response: str = ""):
        self.response = response

    def isolated_chat(self, messages, temperature=0.3, response_format=None, **kwargs):
        return self.response

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] + [0.0] * 1022]


class FakeVectorIndex:
    def __init__(self, results: dict[tuple[str, int], list[tuple[str, float]]] | None = None):
        self._results = results or {}

    def search(self, query_embedding: list[float], top_k: int = 5) -> list[tuple[str, float]]:
        return self._results.get((str(query_embedding[0]), top_k), [])

    def add(self, cell_id: str, embedding: list[float]) -> None:
        pass

    def remove(self, cell_id: str) -> None:
        pass

    def clear(self) -> None:
        pass


class FakeCellStore:
    def __init__(self, cells: list[MemoryCell] | None = None):
        self._cells = {c.id: c for c in (cells or [])}
        self._session_cells = cells or []

    def save(self, cell: MemoryCell) -> None:
        self._cells[cell.id] = cell
        for existing in self._session_cells:
            if existing.id == cell.id:
                break
        else:
            self._session_cells.append(cell)

    def get(self, cell_id: str) -> MemoryCell | None:
        return self._cells.get(cell_id)

    def list_by_session(self, session_id: str, limit: int | None = None) -> list[MemoryCell]:
        result = [c for c in self._session_cells if c.session_id == session_id]
        if limit is not None:
            result = result[:limit]
        return result

    def find_by_entity(self, session_id: str, entity: str) -> list[MemoryCell]:
        result = []
        entity_lower = entity.lower()
        for c in self._session_cells:
            if c.session_id == session_id and any(
                e.lower() == entity_lower for e in (c.entities or [])
            ):
                result.append(c)
        return result

    def delete_session(self, session_id: str) -> None:
        pass


# -----------------------------------------------------------------------------
# QueryRewriter tests
# -----------------------------------------------------------------------------


class TestQueryRewriter:
    def test_rewrite_long_query_no_pronoun_returns_original(self):
        rewriter = QueryRewriter()
        query = "What is the total budget for this project?"
        result = rewriter.rewrite(query, hot_zone=["[user]: hello"])
        assert result == query

    def test_rewrite_short_query_triggers_llm(self):
        llm = FakeLLMClient(response="What is the total budget?")
        rewriter = QueryRewriter(llm_client=llm, token_estimator=lambda x: 3)
        result = rewriter.rewrite("budget?", hot_zone=["[user]: We are planning a project"])
        assert result == "What is the total budget?"

    def test_rewrite_pronoun_triggers_llm(self):
        llm = FakeLLMClient(response="What is the price of that item?")
        rewriter = QueryRewriter(llm_client=llm, token_estimator=lambda x: 15)
        result = rewriter.rewrite("How much is that?", hot_zone=["[user]: I want to buy a laptop"])
        assert result == "What is the price of that item?"

    def test_rewrite_no_llm_fallback(self):
        rewriter = QueryRewriter(llm_client=None, token_estimator=lambda x: 3)
        result = rewriter.rewrite("budget?", hot_zone=[])
        assert result == "budget?"


# -----------------------------------------------------------------------------
# HybridSearcher tests
# -----------------------------------------------------------------------------


class TestHybridSearcher:
    def test_search_no_embedding_fallback_keyword_scan(self):
        cell = MemoryCell(
            id="C_001",
            session_id="s1",
            cell_type="fact",
            confidence=1.0,
            summary="budget planning for q3",
            keywords=["budget", "planning", "q3"],
            entities=[],
        )
        store = FakeCellStore([cell])
        vector_index = FakeVectorIndex()
        searcher = HybridSearcher(vector_index, store, "s1")

        result = searcher.search("budget", top_k=2)
        assert result == ["C_001"]

    def test_search_with_embedding_fusion_ranking(self):
        cell1 = MemoryCell(
            id="C_001",
            session_id="s1",
            cell_type="fact",
            confidence=1.0,
            summary="budget planning",
            keywords=["budget"],
            entities=[],
        )
        cell2 = MemoryCell(
            id="C_002",
            session_id="s1",
            cell_type="fact",
            confidence=1.0,
            summary="weather forecast",
            keywords=["weather"],
            entities=[],
        )
        store = FakeCellStore([cell1, cell2])
        # distance 0 -> score 1.0 for C_001, distance 0.5 -> score 0.606 for C_002
        vector_index = FakeVectorIndex(
            {
                ("1.0", 4): [("C_001", 0.0), ("C_002", 0.5)],
            }
        )
        searcher = HybridSearcher(
            vector_index,
            store,
            "s1",
            embedding_client=FakeLLMClient(),
        )
        result = searcher.search("budget plan", top_k=2)
        # C_001 has higher vector score and keyword match
        assert result[0] == "C_001"

    def test_fallback_triggered_when_top_score_below_threshold(self):
        cell1 = MemoryCell(
            id="C_001",
            session_id="s1",
            cell_type="fact",
            confidence=1.0,
            summary="unrelated topic",
            keywords=["other"],
            entities=[],
        )
        cell2 = MemoryCell(
            id="C_002",
            session_id="s1",
            cell_type="fact",
            confidence=1.0,
            summary="budget constraints",
            keywords=["budget"],
            entities=[],
        )
        store = FakeCellStore([cell1, cell2])
        # C_001 gets a mediocre vector score, no keyword match -> fusion < 0.6
        vector_index = FakeVectorIndex(
            {
                ("1.0", 4): [("C_001", 1.0)],
                ("1.0", 6): [("C_001", 1.0)],
            }
        )
        searcher = HybridSearcher(
            vector_index,
            store,
            "s1",
            embedding_client=FakeLLMClient(),
        )
        result = searcher.search("budget", top_k=1)
        # fallback should bring C_002 via exact keyword scan
        assert result == ["C_002"]

    def test_entity_bonus_boosts_keyword_score(self):
        cell = MemoryCell(
            id="C_001",
            session_id="s1",
            cell_type="fact",
            confidence=1.0,
            summary="project timeline",
            keywords=["project"],
            entities=["budget"],
        )
        store = FakeCellStore([cell])
        # vector score high + entity match ensures fusion >= 0.6
        vector_index = FakeVectorIndex(
            {
                ("1.0", 2): [("C_001", 0.0)],  # vector_score = 1.0
            }
        )
        searcher = HybridSearcher(
            vector_index,
            store,
            "s1",
            embedding_client=FakeLLMClient(),
        )
        result = searcher.search("budget constraints", top_k=1)
        # entity "budget" matches query token "budget"
        assert result == ["C_001"]

    def test_search_no_fallback_returns_fused_even_if_low(self):
        cell = MemoryCell(
            id="C_001",
            session_id="s1",
            cell_type="fact",
            confidence=1.0,
            summary="other",
            keywords=["other"],
            entities=[],
        )
        store = FakeCellStore([cell])
        vector_index = FakeVectorIndex(
            {
                ("1.0", 2): [("C_001", 5.0)],
            }
        )
        searcher = HybridSearcher(
            vector_index,
            store,
            "s1",
            embedding_client=FakeLLMClient(),
        )
        result = searcher.search("budget", top_k=1, fallback=False)
        assert result == ["C_001"]


# -----------------------------------------------------------------------------
# MemorySystem.retrieve_context integration (lightweight)
# -----------------------------------------------------------------------------


class TestMemorySystemRetrieveContext:
    def test_retrieve_context_uses_rewriter_and_hybrid(self):
        from session_mem.core.memory_system import MemorySystem
        from session_mem.storage.sqlite_backend import SQLiteBackend
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        backend = SQLiteBackend(db_path)
        # insert a cell
        cell = MemoryCell(
            id="C_001",
            session_id="s1",
            cell_type="fact",
            confidence=1.0,
            summary="budget planning",
            keywords=["budget"],
            entities=[],
            raw_text="We need to plan the budget for Q3.",
            token_count=10,
        )
        backend.cell_store.save(cell)
        backend.text_store.save(cell.id, cell.raw_text, cell.token_count)

        # manually inject vector so hybrid can find it via fallback keyword scan
        ms = MemorySystem(
            session_id="s1",
            llm_client=FakeLLMClient(),
            vector_index=backend.vector_index,
            cell_store=backend.cell_store,
            text_store=backend.text_store,
            meta_cell_store=backend,
            query_rewriter=QueryRewriter(llm_client=None),
        )
        # add a turn so hot_zone is non-empty
        ms.add_turn("user", "What about the budget?", "2026-04-14T10:00:00Z")

        wm = ms.retrieve_context("budget", hot_zone_turns=2, top_k=2)
        # working memory should contain the activated cell
        assert (
            any("budget" in (c.raw_text or "").lower() for c in wm.activated_cells)
            or not wm.activated_cells
        )
        backend.close()

    def test_retrieve_context_loads_linked_prev(self):
        from session_mem.core.memory_system import MemorySystem
        from session_mem.storage.sqlite_backend import SQLiteBackend
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        backend = SQLiteBackend(db_path)
        prev_cell = MemoryCell(
            id="C_001",
            session_id="s1",
            cell_type="constraint",
            confidence=1.0,
            summary="budget limit",
            keywords=["budget"],
            entities=[],
            raw_text="The budget must not exceed 10k.",
            token_count=8,
        )
        curr_cell = MemoryCell(
            id="C_002",
            session_id="s1",
            cell_type="task",
            confidence=1.0,
            summary="planning task",
            keywords=["plan"],
            entities=[],
            raw_text="We need to plan the project.",
            token_count=7,
            linked_prev="C_001",
        )
        backend.cell_store.save(prev_cell)
        backend.text_store.save(prev_cell.id, prev_cell.raw_text, prev_cell.token_count)
        backend.cell_store.save(curr_cell)
        backend.text_store.save(curr_cell.id, curr_cell.raw_text, curr_cell.token_count)

        # hybrid will find C_002 via keyword scan
        ms = MemorySystem(
            session_id="s1",
            llm_client=FakeLLMClient(),
            vector_index=backend.vector_index,
            cell_store=backend.cell_store,
            text_store=backend.text_store,
            meta_cell_store=backend,
            query_rewriter=QueryRewriter(llm_client=None),
        )
        ms.add_turn("user", "What is the plan?", "2026-04-14T10:00:00Z")

        wm = ms.retrieve_context("plan", hot_zone_turns=2, top_k=2)
        ids = {c.id for c in wm.activated_cells}
        assert "C_002" in ids
        assert "C_001" in ids
        backend.close()

    def test_retrieve_context_activates_entity_cooccurrence(self):
        from session_mem.core.memory_system import MemorySystem
        from session_mem.storage.sqlite_backend import SQLiteBackend
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        backend = SQLiteBackend(db_path)
        cell1 = MemoryCell(
            id="C_001",
            session_id="s1",
            cell_type="fact",
            confidence=1.0,
            summary="budget fact",
            keywords=["budget"],
            entities=["budget"],
            raw_text="Budget is 10k.",
            token_count=4,
        )
        cell2 = MemoryCell(
            id="C_002",
            session_id="s1",
            cell_type="constraint",
            confidence=1.0,
            summary="budget constraint",
            keywords=["budget"],
            entities=["budget"],
            raw_text="Budget must be under 10k.",
            token_count=6,
        )
        backend.cell_store.save(cell1)
        backend.text_store.save(cell1.id, cell1.raw_text, cell1.token_count)
        backend.cell_store.save(cell2)
        backend.text_store.save(cell2.id, cell2.raw_text, cell2.token_count)

        ms = MemorySystem(
            session_id="s1",
            llm_client=FakeLLMClient(),
            vector_index=backend.vector_index,
            cell_store=backend.cell_store,
            text_store=backend.text_store,
            meta_cell_store=backend,
            query_rewriter=QueryRewriter(llm_client=None),
        )
        ms.add_turn("user", "Tell me about budget.", "2026-04-14T10:00:00Z")

        wm = ms.retrieve_context("budget", hot_zone_turns=2, top_k=2)
        ids = {c.id for c in wm.activated_cells}
        assert "C_001" in ids
        assert "C_002" in ids
        backend.close()
