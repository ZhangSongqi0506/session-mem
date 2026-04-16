# Task Plan: session-mem 开发规划
<!-- 
  WHAT: session-mem 单会话级临时记忆系统的实现路线图
  WHY: 将技术方案转化为可执行、可追踪的开发任务
-->

## Goal
实现 `session-mem` 单会话级临时记忆系统的 MVP，包含三层缓冲架构、Cell 生成与检索、时间戳解析、SQLite + sqlite-vec 存储，并通过 LoCoMo 拼接会话完成基准验证。

## Current Phase
Phase 8

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
- [x] 实现 `QueryRewriter`：基于热区上下文的指代消解、短查询扩展（<10 tokens 触发）
- [x] 实现 `HybridSearcher`：向量相似度（sqlite-vec `search`）+ 关键词 Jaccard + 实体匹配奖励，融合公式 `0.75*vector + 0.25*keyword`
- [x] 实现 `MemorySystem.retrieve_context()` 完整流程：查询重写 → 双路召回 → 全量回溯原文 → 组装 `WorkingMemory`
- [x] `WorkingMemory` 组装时无条件注入 active Meta Cell（固定置于 Prompt 最前端）
- [x] 低置信度 Fallback：Top-1 融合分数 <0.6 时放宽阈值、精确关键词匹配、RRF 合并
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
- [x] 因果链断裂防护：通过 `linked_prev` 自动加载关联约束类 Cell
- [x] 实体共现激活：命中 Cell 的实体与同一会话其他 Cell 有共现时，级联加载相关 Cell
- **涉及代码**:
  - `src/session_mem/core/memory_system.py`（linked_prev 追踪、实体共现激活）
  - `src/session_mem/retrieval/hybrid_search.py`（实体共现级联加载）
- **验收标准**:
  1. 查询涉及跨 Cell 因果时（如"基于预算，刚才的配置还能优化吗？"），系统自动加载 budget 所在的约束 Cell
  2. 命中 Cell 包含实体"预算"时，系统级联加载同一会话中其他含"预算"实体的 Cell

### Phase 7: 验证与测试
- [x] 基于 LoCoMo 数据集的 session 拼接脚本与测试流水线
- [x] Token 节省率测算：对比全量历史 Prompt vs session-mem 组装后的 Prompt
- [x] 准确率评估：任务完成率 / LLM-as-Judge（gpt-4o-mini）评分
- [x] 核心模块单元测试与集成测试补全
- [x] 整理测试报告并更新 README
- [x] 跑通 LoCoMo 数据集脚本开发（已支持全量/滑窗/session-mem 三向对比）
- [x] 服务器端到端跑测并产出 Token 节省率、准确率、延迟报告（v3 核心指标已达标：Token 节省率 50.17%，准确率差距 0.041）
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

### Phase 8: 运行优化与问题修复
- **Status:** complete
- [x] 定位并修复服务器小样本跑测中的两个阻塞性问题（`json_schema` response_format 不兼容、`model` 参数冲突导致 Judge 静默失败）
- [x] 服务器重跑 v2 验证：400 错误与 Judge 静默失败已解决
- [x] **评测结果增强**：扩展 `QAMetrics` 的 session-mem Token 拆解字段（Meta Cell / 热区 / 各激活 Cell）
- [x] **评测结果增强**：为 Baseline / Sliding / session-mem 三个回答各自增加 vs ground_truth 的独立 Judge 评分
- [x] **评测结果增强**：`locomo_runner.py` 输出详细的可读文本报告（`_report.txt`）
- [x] **评测结果增强**：补充回归测试
- [x] **评测效率优化**：为 `locomo_runner.py` 增加并发支持（`--max_workers` 跨 session 并发），减少 benchmark 总耗时
- [x] 基于增强后的详细评测数据，分析 Token 节省率过低（~10%）的根因并制定压缩/检索优化方案
- **新增待修复项**（2026-04-15 晚）
  1. [x] **评测聚合指标修正**：`benchmarks/metrics.py` 删除 `avg_judge_score_vs_baseline` / `avg_judge_score_vs_sliding`，替换为 `avg_baseline_judge_score` / `avg_sliding_judge_score` / `avg_session_mem_judge_score`
  2. [x] **Meta Cell 膨胀修复**：`meta_cell_generator.py` 让 `raw_text` 优先使用 LLM 返回的 `summary`（预期 300-500 tokens），而非全文累积拼接（当前 11,578 tokens）
- **新增待修复项**（2026-04-15 benchmark 重跑后，按新计划分为 Phase 8.1 / 8.2 / 8.3）
  - **Phase 8.1**（已完成）
    1. [x] **P0 - 热区构建错误**：`_build_hot_zone()` 只取 SenMemBuffer 末尾 2 轮，但零压缩缓冲区的全部内容都应属于热区。改为直接返回 `sen_buffer.turns` 全部内容。
    2. [x] **P2 - benchmark 流程中问题未进入热区**：`locomo_runner.py` 直接调用 `ms.retrieve_context(question)`。改为通过 `extra_turns` 参数将问题临时注入热区，避免 `add_turn()` 触发 Cell 生成的副作用。
    3. [x] **P1 - 数据集角色映射失真**（仅 benchmark 代码）：`benchmarks/data_loader.py` 将 speaker 强制映射为 `user`/`assistant`。改为保留原始 speaker 名称，仅涉及 `data_loader.py` 和 `prompt_assembler.py`。
    4. [x] **Hotfix - 语义边界检测因非标准 role 失效**：`data_loader.py` 保留原始 speaker 名称后，`boundary_detector.py` 直接把 `Jon`/`Gina` 等非标准 role 传给 LLM API，导致 `should_split()` 返回空列表。修复：在构建 boundary prompt 时将非标准 role fallback 为 `user`，恢复细粒度语义切分。
  - **Phase 8.2**（已完成）
    4. [x] **P0 - 实体共现召回无关通用 Cell**：`retrieve_context()` 中实体共现激活无条件拉入早期背景 Cell。优化为：实体共现候选需满足 `keyword score > 0` 且 `fused_score >= 0.4`，并按相关性排序后取前 3 个。
    5. [x] **P1 - 激活 Cell 缺少二次相关性截断 + 取消固定 top_k**：当前 `top_k=2` + `linked_prev` + `extra_limit=3` 过于刚性。改为阈值法（`threshold=0.55`，动态上下限 `min_cells`/`max_cells`），最终统一按 `fused_score` 截断到 `total_budget=8`。
  - **Phase 8.2.1**（已完成）
    6. [x] **P0 - 检索策略升级为真正的双路召回 + RRF 融合**：`HybridSearcher` 从"先向量检索再对向量结果做关键词加权融合"改为向量路与关键词路各自独立召回，再用 RRF 融合排名。向量路增加 `vector_score_threshold` 过滤低质量候选。
    7. [x] **P1 - 取消最终总预算截断**：移除 `MemorySystem.retrieve_context()` 中 `total_budget=8` 的硬性截断逻辑，激活 Cell 数量由动态上下限和实体共现门槛自然调节。
    8. [x] **P2 - 检索参数配置化**：新建 `src/session_mem/config.py`，将 RRF k 值、各路 top_k、向量分数阈值、RRF fallback 阈值、`MemorySystem` 主阈值等可调节参数集中管理，避免代码硬编码。
    9. [x] **Hotfix - 关键词路覆盖不足 + 向量阈值过高**：`keyword_scores()` 仅扫描 `keywords` 和 `summary`，未覆盖 `raw_text` 原文，导致 LLM 提取遗漏时关键词路完全失效；同时 `VECTOR_SCORE_THRESHOLD = 0.6` 将大量语义相关但距离中等的 Cell 直接过滤。修复：关键词路改为扫描 `raw_text`；向量阈值从 0.6 降至 0.3。
  - **Phase 8.3**（已跳过）
    6. [ ] ~~P2 - 关键词匹配对通用词过于敏感~~：v3 benchmark 后准确率差距已降至 0.041（<0.05），决定跳过 Phase 8.3 的动态 IDF 惩罚，优先处理 LLM 过度解读问题（Phase 8.5）。
  - **Phase 8.4**（已完成）
    7. [x] **P1 - benchmark 方法级并发优化**：`locomo_runner.py` 中单个 QA 的 baseline / sliding / session-mem 三种回答改为 `ThreadPoolExecutor(max_workers=3)` 并发生成。检索（`retrieve_context`）仍串行执行以保留 latency 指标，Judge 评分在三个回答返回后串行执行。与 `--max_workers` session 级并发正交叠加。
    - **涉及代码**：`benchmarks/locomo_runner.py`、`tests/test_benchmark.py`
    - **验证结果**：全部 102 个测试通过；black + ruff 通过。
  - **Phase 8.5**（已完成）
    8. [x] **P0 - LLM 回答指令优化（抑制过度解读）**：在 `locomo_runner.py` 的 `_answer()` 中统一注入 system 硬指令：`"Based only on the provided context, answer directly and concisely. Quote the relevant sentence explicitly. Do not infer or over-interpret."`。baseline / sliding / session-mem 三种回答生成均受该指令约束，避免改动 `PromptAssembler` 和 `WorkingMemory.to_prompt()` 接口。
    9. [x] **P1 - 内部 token 开销统计补充**：新增 `session_mem_internal_tokens` 字段，统计检索阶段 QueryRewriter prompt tokens + Embedding tokens，输出到 JSON 聚合结果与 `_report.txt`。
    - **涉及代码**：`benchmarks/locomo_runner.py`、`benchmarks/metrics.py`、`tests/test_benchmark.py`
    - **验证结果**：全部 102 个测试通过；black + ruff 通过；已提交 commit `5a33af9`。
  - **Phase 8.6**（待执行）
    10. [ ] **P1 - 关键词检索升级为 BM25**：当前 `hybrid_search.py` 的 `keyword_scores()` 使用集合 Jaccard，仅判断 token 是否存在，缺少词频（TF）、逆文档频率（IDF）和长度归一化。升级为 BM25 后，可更精准地抑制通用背景 Cell（如 "Jon"、"dance" 等高频词获得低 IDF 权重），同时提升稀有词（如 "Marley"、"regionals"）的区分度，有望进一步缩小剩余的检索失败型 bad case。计划在 Phase 8.5 服务器 benchmark 验证后，根据准确率差距是否仍 ≥0.03 决定是否实施。
    - **涉及代码**：`src/session_mem/retrieval/hybrid_search.py`、`tests/test_retrieval.py`
    - **实施要点**：
      1. 在 `HybridSearcher` 内维护 session-level 词频统计（或基于当前 `cell_store.list_by_session` 动态计算 IDF）。
      2. 将 `keyword_scores()` 的 Jaccard 替换为 BM25 分数计算，保留 `entities` 的 entity_bonus 作为后处理加权。
      3. 参数建议：k1=1.5，b=0.75，作为可配置项写入 `RetrievalConfig`。
- **涉及代码**:
  - `src/session_mem/llm/qwen_client.py`（已修复）
  - `benchmarks/metrics.py`
  - `benchmarks/locomo_runner.py`
  - `benchmarks/data_loader.py`
  - `benchmarks/prompt_assembler.py`
  - `tests/test_benchmark.py`
  - `src/session_mem/core/meta_cell_generator.py`（已修复）
  - `src/session_mem/core/memory_system.py`
  - `src/session_mem/retrieval/hybrid_search.py`
- **验收标准**:
  1. 每个 QA 的 JSON 结果包含 Meta Cell tokens、热区 tokens、激活 Cell 列表、三个回答的独立 Judge 分数
  2. 运行后自动生成 `_report.txt` 文本报告，包含 per-QA 的 token 拆解与回答对比
  3. 代码通过 black + ruff，全部单元测试通过
  4. `_report.txt` 中 Meta Cell token 数从 ~11,500 降至数百级别（已完成）
  5. Token 节省率 vs baseline 保持 >60%
  6. **session-mem Judge 评分 vs baseline 差距缩小到 <0.05**（当前 0.153）
  7. 热区 token 从 ~32 提升到 ~300-500
  8. 激活 Cell 数量从刚性 6-7 变为动态 2-8

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
