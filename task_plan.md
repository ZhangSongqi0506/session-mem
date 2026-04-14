# Task Plan: session-mem 开发规划
<!-- 
  WHAT: session-mem 单会话级临时记忆系统的实现路线图
  WHY: 将技术方案转化为可执行、可追踪的开发任务
-->

## Goal
实现 `session-mem` 单会话级临时记忆系统的 MVP，包含三层缓冲架构、Cell 生成与检索、时间戳解析、SQLite + sqlite-vec 存储，并通过 LoCoMo 拼接会话完成基准验证。

## Current Phase
Phase 4.1

## Phases

### Phase 1: 项目脚手架与核心接口设计
- [x] 初始化代码仓库结构（Python 包、pyproject.toml、tests 目录）
- [x] 定义核心抽象接口：`MemorySystem`、`SenMemBuffer`、`ShortMemBuffer`、`MemoryCell`、`WorkingMemory`
- [x] 定义存储抽象接口：`VectorIndex`、`CellStore`、`TextStore`，以及统一 `SQLiteBackend`
- [x] 定义 LLM 抽象：`LLMClient`、`QwenClient`，支持 `chat_completion` / `isolated_chat`
- [x] 设计 Prompt 模板：语义边界检测 + Cell 生成（JSON Schema 约束）
- [x] 设计检索层骨架：`HybridSearcher`、`QueryRewriter`
- **涉及代码**:
  - `pyproject.toml`
  - `src/session_mem/core/memory_system.py`
  - `src/session_mem/core/buffer.py`
  - `src/session_mem/core/cell.py`
  - `src/session_mem/core/working_memory.py`
  - `src/session_mem/storage/base.py`
  - `src/session_mem/storage/sqlite_backend.py`
  - `src/session_mem/llm/base.py`
  - `src/session_mem/llm/qwen_client.py`
  - `src/session_mem/llm/prompts.py`
  - `src/session_mem/llm/parser.py`
  - `src/session_mem/retrieval/query_rewriter.py`
  - `src/session_mem/retrieval/hybrid_search.py`
  - `src/session_mem/utils/tokenizer.py`
- **验收标准**:
  1. `uv run python -c "import session_mem"` 无报错
  2. 所有核心类可实例化，接口定义完整（方法签名、类型注解）
  3. `SQLiteBackend` 能创建 `.db` 文件并初始化 4 张表（cells、cell_texts、entity_links、cell_vectors）
  4. `QwenClient` 能成功调用内网接口并返回字符串结果
- **Status:** complete

### Phase 2: 存储层完善与数据库构建
- [x] 修正向量维度不一致问题：`sqlite_backend.py` 与 `AGENTS.md` 统一为 **1024 维**（适配 bge-large-en-v1.5）
- [x] 完善 SQLite schema：确保 `cell_type` 枚举包含 `fragmented`，补充 `cells` 表缺失字段与外键约束；新增 `meta_cells` 表与相关存储方法
- [x] 实现存储层单元测试：CRUD、会话隔离、实体共现查询、向量增删查
- [x] 解决 sqlite-vec 依赖与多线程使用注意事项（`enable_load_extension(True)` + `check_same_thread=False`）
- **涉及代码**:
  - `src/session_mem/storage/sqlite_backend.py`（核心：schema 修正、vector dims 改为 1024、新增 `meta_cells` 表）
  - `src/session_mem/storage/base.py`（如有需要补充接口方法）
  - `src/session_mem/core/cell.py`（确认 `cell_type` 支持 `fragmented`）
  - `tests/test_storage.py`（新建）
- **数据库构建细节**:
  1. `cells` 表：包含 `id`, `session_id`, `cell_type`, `confidence`, `summary`, `keywords` (JSON), `entities` (JSON), `linked_prev`, `timestamp_start`, `timestamp_end`, `vector_id`, `created_at`
  2. `cell_texts` 表：`cell_id` (PK), `raw_text`, `token_count`，外键关联 `cells(id)`
  3. `entity_links` 表：`cell_id`, `entity`，索引 `idx_entity_links_entity`
  4. `cell_vectors` 虚拟表：`cell_id` (PK), `embedding FLOAT[1024]`
  5. `meta_cells` 表：`session_id`, `cell_id`, `version`, `cell_type`='meta', `status` (active/archived), `raw_text`, `token_count`, `linked_cells` (JSON), `created_at`, `updated_at`，主键 `(session_id, version)`，索引 `session_id` 和 `status`
  6. 所有表均需支持按 `session_id` 过滤，保证单会话隔离
- **验收标准**:
  1. `SQLiteBackend` 初始化后 schema 正确，vector dims = 1024，共 5 张表
  2. 单元测试覆盖：Cell 保存后 `get` / `list_by_session` / `find_by_entity` 结果正确
  3. 向量写入后 `search` 能返回近似的 `cell_id` 列表
  4. `delete_session` 能级联清理一个会话的全部数据（元数据、原文、实体关系、向量、meta_cells）
- **Status:** complete

### Phase 3: SenMemBuffer 实现与语义边界检测
- [x] 完善 `SenMemBuffer`：Token 估算（使用 tiktoken 或字符 fallback）、512 整数倍检测阈值、2048 硬上限切分
- [x] 时间戳注入与间隔检测：ISO 8601 UTC 解析，30 分钟阈值触发强制切分
- [x] 语义边界检测集成：`SemanticBoundaryDetector` 主路径（qwen2.5:72b 独立新会话）+ 规则 fallback（超长内容直接切分、LLM 异常时返回 False）
- [x] 强制切分与滑动保留逻辑：切分后保留的轮次作为新 Cell 种子
- [x] 将 `SenMemBuffer` 切分流程接入 `MemorySystem.add_turn()`（gap → hard limit → soft limit → boundary detection）
- **涉及代码**:
  - `src/session_mem/core/buffer.py`（`SenMemBuffer`：token 估算、gap_detected、extract_for_cell）
  - `src/session_mem/core/boundary_detector.py`（`should_split` 集成 fallback）
  - `src/session_mem/core/memory_system.py`（`add_turn` 中加入切分触发逻辑）
  - `src/session_mem/utils/tokenizer.py`（完善 `TokenEstimator`）
  - `src/session_mem/llm/prompts.py`（边界检测 prompt 调优）
- **验收标准**:
  1. `SenMemBuffer` 累积 512 tokens 时触发 `should_trigger_check` 为 True
  2. 时间差超过 30 分钟的两轮对话，`gap_detected()` 返回 True
  3. `SemanticBoundaryDetector` 对明显话题转折返回 `True`，对连续对话返回 `False`
  4. `MemorySystem.add_turn()` 在检测到边界时，能正确调用 `CellGenerator` 生成 Cell 并清空已切分轮次
  5. 达到 2048 tokens 仍未切分时，强制提取全部内容生成 Cell 并标记 `fragmented`
- **Status:** complete

### Phase 4: Cell 生成、Meta Cell 与 ShortMemBuffer
- [x] 完善 `CellGenerator`：集成 LLM Prompt 调用、JSON 解析 fallback、四层信息填充
- [x] 集成 Embedding 服务：通过 Xinference/OpenAI 兼容接口获取 1024 维向量，写入 `SQLiteVectorIndex`
- [x] `ShortMemBuffer` 与存储层联动：从 `CellStore` 加载当前会话全部 Cell，而非仅内存列表
- [x] 实现 `MemorySystem` 中 Cell 生成的完整闭环（生成 → 存元数据 → 存原文 → 存向量 → 加入 ShortMemBuffer）
- [x] 实现 `MetaCellGenerator`：首个普通 Cell 生成后创建初始 Meta Cell；后续每生成一个普通 Cell，调用 LLM 全量融合重写 Meta Cell
- [x] `SQLiteBackend` 新增 `save_meta_cell()` / `get_active_meta_cell()` / `delete_meta_cells_by_session()`
- **涉及代码**:
  - `src/session_mem/core/cell_generator.py`（完善 `generate`，处理 LLM 失败 fallback）
  - `src/session_mem/core/meta_cell_generator.py`（新建：初始生成 + 全量融合更新）
  - `src/session_mem/core/buffer.py`（`ShortMemBuffer` 改为查询 SQLite）
  - `src/session_mem/core/memory_system.py`（Cell 生成闭环、Meta Cell 触发逻辑）
  - `src/session_mem/llm/prompts.py`（Cell 生成 prompt + Meta Cell 生成/更新 prompt）
  - `src/session_mem/llm/parser.py`（JSON 解析 fallback 增强）
  - `src/session_mem/storage/sqlite_backend.py`（Meta Cell 存储方法）
- **验收标准**:
  1. 给定 3-5 轮对话，`CellGenerator.generate()` 输出合法 `MemoryCell`，`summary` 非空，`keywords` 长度 5-8
  2. Cell 生成后，元数据、原文、向量分别写入对应 SQLite 表，且可通过 `cell_id` 读出
  3. `ShortMemBuffer.all_cells()` 返回当前会话已生成的全部 Cell（从 DB 读取）
  4. LLM 返回非法 JSON 时，`parser.py` 的 fallback 能提取出有效字段，不抛异常导致流程中断
  5. 生成首个普通 Cell 后，`MetaCellGenerator` 能产出初始 Meta Cell 并存入 `meta_cells` 表
  6. 生成第二个普通 Cell 后，Meta Cell 被更新为新版本，旧版本标记 `archived`，新版本标记 `active`

### Phase 4.1: 多切分点语义边界检测落地
- [x] 重构 `SemanticBoundaryDetector`：从 `should_split()` 返回 `bool` 改为返回**切分点索引列表**（如 `[3, 6]`）
- [x] 更新边界检测 Prompt：要求 LLM 分析当前 Buffer 全部轮次，输出 JSON 格式的切分点索引列表及理由
- [x] 更新 `MemorySystem.add_turn()` 软限分支：支持接收多个切分点，依次生成 N 个 Cell，最后一段保留在 Buffer
- [x] 更新 `SenMemBuffer.extract_for_cell()` 或新增批量提取方法：按多个切分点连续切分 Buffer
- [x] 更新 `llm/parser.py`：增强 JSON fallback，支持解析切分点列表格式
- [x] 更新 `tests/test_boundary_detector.py`：补充多边界场景测试
- [x] 更新 `tests/test_memory_system.py`：补充一次检测生成多个 Cell 的集成测试
- [x] 更新 `tests/test_buffer.py`：补充多切分点提取后 Buffer 状态验证
- **涉及代码**:
  - `src/session_mem/core/boundary_detector.py`（核心：返回类型改为 `list[int]`）
  - `src/session_mem/llm/prompts.py`（新增/修改边界检测 Prompt，要求输出切分点列表）
  - `src/session_mem/llm/parser.py`（JSON 解析增强）
  - `src/session_mem/core/buffer.py`（`SenMemBuffer` 支持多段提取）
  - `src/session_mem/core/memory_system.py`（软限分支支持循环生成多个 Cell）
  - `tests/test_boundary_detector.py`（新增测试用例）
  - `tests/test_memory_system.py`（新增集成测试）
  - `tests/test_buffer.py`（新增 Buffer 提取测试）
- **验收标准**:
  1. `SemanticBoundaryDetector.should_split(turns)` 返回 `list[int]`，无边界时返回 `[]`
  2. 给定 10 轮含 3 个主题的对话，LLM 能正确返回 2 个切分点索引
  3. `MemorySystem.add_turn()` 在检测到 2 个切分点时，依次生成 2 个 Cell，且最后一段（新主题）保留在 Buffer
  4. 生成的多个 Cell 均正确写入 SQLite，且 `linked_prev` 链连续
  5. 多 Cell 生成后，Meta Cell 更新仅触发 **1 次**（基于最新生成的 Cell 做增量融合，而非每个 Cell 都更新）
  6. 全部单元测试通过；black + ruff 通过
- **Status:** complete

### Phase 5: 检索策略与 Working Memory
- [ ] 实现 `QueryRewriter`：基于热区上下文的指代消解、短查询扩展（<10 tokens 触发）
- [ ] 实现 `HybridSearcher`：向量相似度（sqlite-vec `search`）+ 关键词 Jaccard + 实体匹配奖励，融合公式 `0.75*vector + 0.25*keyword`
- [ ] 实现 `MemorySystem.retrieve_context()` 完整流程：查询重写 → 双路召回 → 全量回溯原文 → 组装 `WorkingMemory`
- [ ] `WorkingMemory` 组装时无条件注入 active Meta Cell（固定置于 Prompt 最前端）
- [ ] 低置信度 Fallback：Top-1 融合分数 <0.6 时放宽阈值、BM25 精确匹配、RRF 合并
- **涉及代码**:
  - `src/session_mem/retrieval/query_rewriter.py`（实现 rewrite 逻辑，热区传入）
  - `src/session_mem/retrieval/hybrid_search.py`（实现向量+关键词融合搜索）
  - `src/session_mem/core/working_memory.py`（调整 Prompt 组装格式，支持 Meta Cell 前置）
  - `src/session_mem/core/memory_system.py`（`retrieve_context` 完整流程 + Meta Cell 获取）
  - `src/session_mem/storage/sqlite_backend.py`（`get_active_meta_cell` 调用）
- **验收标准**:
  1. 查询"这个多少钱？"在热区含"预算"时能重写成"预算多少钱？"或类似明确查询
  2. `HybridSearcher.search(query, top_k=2)` 返回的 Cell ID 与查询语义相关
  3. 检索命中后，`WorkingMemory` 中包含 **Meta Cell 全文** + 热区原文 + 命中 Cell 的完整原文 + 当前查询
  4. 对于无关查询（如历史是编程，查询是"今天天气"），`HybridSearcher` 返回空或低分，`WorkingMemory` 仍含 Meta Cell + 热区+查询
  5. 完整检索链路端到端延迟 < 200ms（不含 LLM 重写时 < 50ms）

### Phase 6: 边界情况与异常处理
- [ ] 因果链断裂防护：通过 `linked_prev` 自动加载关联约束类 Cell
- [ ] 实体共现激活：命中 Cell 的实体与同一会话其他 Cell 有共现时，级联加载相关 Cell
- [ ] 检索失败零回溯模式：连续无匹配时仅返回热区+查询；连续 3 轮低置信度触发"新话题"标记并强制归档旧 Buffer
- [ ] 降级策略：Embedding 服务不可用、LLM 超时、sqlite-vec 异常时的安全 fallback
- **涉及代码**:
  - `src/session_mem/core/memory_system.py`（linked_prev 追踪、实体共现激活、新话题检测）
  - `src/session_mem/retrieval/hybrid_search.py`（低置信度 fallback、BM25/RRF 预留）
  - `src/session_mem/core/buffer.py`（新话题检测时强制切分旧 Buffer）
- **验收标准**:
  1. 查询涉及跨 Cell 因果时（如"基于预算，刚才的配置还能优化吗？"），系统自动加载 budget 所在的约束 Cell
  2. 用户突然切换话题且连续 3 轮检索无命中，系统标记新话题，旧 Buffer 内容强制生成 Cell
  3. Embedding 服务不可用时，系统降级为仅关键词匹配或仅热区模式，不崩溃
  4. LLM 调用超时（>10s）时，边界检测降级为规则匹配，Cell 生成降级为简单摘要

### Phase 7: 验证与测试
- [ ] 基于 LoCoMo 数据集的 session 拼接脚本与测试流水线
- [ ] Token 节省率测算：对比全量历史 Prompt vs session-mem 组装后的 Prompt
- [ ] 准确率评估：任务完成率 / LLM-as-Judge（gpt-4o-mini）评分
- [ ] 核心模块单元测试与集成测试补全
- [ ] 整理测试报告并更新 README
- **涉及代码**:
  - `tests/test_buffer.py`（SenMemBuffer、ShortMemBuffer 测试）
  - `tests/test_boundary_detector.py`（边界检测测试）
  - `tests/test_cell_generator.py`（Cell 生成测试）
  - `tests/test_retrieval.py`（检索与 WorkingMemory 测试）
  - `tests/test_memory_system.py`（端到端集成测试）
  - `benchmarks/locomo_runner.py`（新建：LoCoMo 评估脚本）
- **验收标准**:
  1. 单元测试覆盖率 > 60%（核心模块 buffer、cell_generator、retrieval、storage）
  2. LoCoMo 评估脚本可跑通至少 50 条拼接会话，输出 Token 节省率与准确率
  3. **Token 节省率 >= 40%**（目标 50-60%，含 Meta Cell 后约为 1900 tokens vs 4000+ tokens 全量历史）
  4. **回答准确率损失 < 5%**（对比全量历史基线）
  5. README 包含快速开始、环境配置、LoCoMo 复现命令

## Key Questions
1. ✅ 选择 Python 还是 Node.js 作为主要实现语言？ → **Python**
2. ✅ 1.5B 语义边界检测模型是本地部署还是调用 API？ → **内网 qwen2.5:72b，独立新会话调用**
3. ✅ 向量索引使用 sqlite-vec（默认）还是 FAISS？ → **sqlite-vec**
4. ✅ Cell 生成 LLM 使用哪个模型？ → **内网 qwen2.5:72b**
5. ✅ 向量维度统一为多少？ → **1024（bge-large-en-v1.5）**

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 代码仓库名为 `session-mem` | 简洁、语义明确、便于包管理 |
| 验证基于 LoCoMo 多 session 拼接 | 数据集公开、贴合长跨度单会话场景 |
| 时间戳作为 Cell 元信息 | 支持跨长时间会话的时序检索与间隔分析 |
| SQLite + sqlite-vec 作为默认存储后端 | 零运维、单文件、支持 Skill/MCP/LangChain 组件化，预留后端切换能力 |
| Python 实现 | 团队熟悉、生态丰富、便于与 LangChain 集成 |
| qwen2.5:72b 作为 LLM 后端 | 内网已有部署，语义边界检测与 Cell 生成统一模型，降低成本 |
| 语义边界检测独立新会话 | 避免边界检测调用污染主会话上下文，保证 token 隔离 |
| uv 作为包管理工具 | 极速依赖解析，统一替代 pip+venv，与 pyproject.toml 原生兼容 |
| 向量维度 1024 | 与 bge-large-en-v1.5 模型输出维度一致，避免维度不匹配导致的检索错误 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| git push 失败：src refspec main does not match any | 1 | 先执行 `git add` + `git commit` 创建初始提交后再 push |
| 向量维度不一致：技术方案写 512，AGENTS.md 和模型实际为 1024 | 1 | 在 Phase 2 中统一修正为 1024，并同步更新技术方案和 AGENTS.md |

## Notes
- Phase 2 必须优先完成存储层修正（vector dims = 1024），否则后续 Embedding 写入与检索会出现维度不匹配错误
- Phase 3 与 Phase 4 可部分并行：边界检测与 Cell 生成逻辑相对独立，但需共同接入 `MemorySystem.add_turn()`
- Phase 5 依赖于 Phase 4 的 Embedding 与向量索引可用
- 每完成一个 Phase，更新 progress.md 并同步修改本文件状态；复杂子任务跨 Phase 时需提前沟通
