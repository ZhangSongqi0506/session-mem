# Task Plan: session-mem 开发规划
<!-- 
  WHAT: session-mem 单会话级临时记忆系统的实现路线图
  WHY: 将技术方案转化为可执行、可追踪的开发任务
-->

## Goal
实现 `session-mem` 单会话级临时记忆系统的 MVP，包含三层缓冲架构、Cell 生成与检索、时间戳解析、SQLite + sqlite-vec 存储，并通过 LoCoMo 拼接会话完成基准验证。

## Current Phase
Phase 9

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
  - **Phase 8.6**（已完成）
    10. [x] **P1 - 关键词检索升级为 BM25**：`hybrid_search.py` 的 `keyword_scores()` 从集合 Jaccard 替换为基于 session-level 动态 IDF 的 BM25，参数 `k1=1.5`、`b=0.75` 写入 `RetrievalConfig`。保留 `entities` 的 `entity_bonus` 作为后处理加权。
    - **涉及代码**：`src/session_mem/retrieval/hybrid_search.py`、`src/session_mem/config.py`、`tests/test_retrieval.py`。
    - **验证结果**：全部 104 个测试通过；black + ruff 通过。新增 `test_bm25_penalizes_common_words` 和 `test_bm25_length_normalization` 验证 IDF 降权与长度归一化效果。
  - **Phase 8.7**（已完成）
    11. [x] **P0 - activated_cells 按时间顺序组装 + Cell 时间戳注入 Prompt + 时间类问题强制回答绝对时间**：
      1. `memory_system.py:retrieve_context()` 中最终 `activated_cells` 改为按 `timestamp_start` 升序排列，恢复自然叙事顺序，避免高分通用 Cell 因 RRF 排序被前置而淹没具体答案 Cell。
      2. `working_memory.py:to_prompt()` 中给每个 `activated_cell` 的 `raw_text` 前增加 `[timestamp_start - timestamp_end]` 前缀，让 LLM 感知各段内容的绝对时间锚点。
      3. `locomo_runner.py:_answer()` 的 system 指令增加一条补充："If the question asks about time, dates, or when something happened, you must answer with the specific absolute timestamp or date explicitly."
    - **涉及代码**：`src/session_mem/core/memory_system.py`、`src/session_mem/core/working_memory.py`、`benchmarks/locomo_runner.py`、`tests/test_retrieval.py`。
    - **验证结果**：全部 106 个测试通过；black + ruff 通过。新增 `test_retrieve_context_sorts_activated_cells_by_timestamp` 和 `test_working_memory_includes_timestamp_prefix`。
  - **Phase 8.8**（已完成）
    12. [x] **P0 - benchmark latency 口径统一 + TTFT 首 token 延迟采集**：
      - **问题**：当前 `avg_session_mem_latency_ms` 仅测量 `retrieve_context()`（检索+组装，约 2.8s），而 `avg_baseline_latency_ms` / `avg_sliding_latency_ms` 测量的是完整 LLM 回答生成时间（约 10-12s）。三者口径不一致，导致 session-mem 的延迟数据无法与 baseline/sliding 直接对比，存在显著误导性。
      - **改进项 1 - 统一总延迟口径**：
        - 为 session-mem 新增 `session_mem_total_latency_ms` = `session_mem_latency_ms`（检索）+ LLM 生成时间。
        - baseline / sliding 的 latency 保持为完整生成时间，但语义上明确为 "total generation latency"。
      - **改进项 2 - 三种方法均增加 TTFT**：
        - 通过 OpenAI streaming API 采集 `time_to_first_token`（从发请求到收到第一个 chunk 的时间）。
        - 新增字段：`baseline_ttft_ms`、`sliding_ttft_ms`、`session_mem_ttft_ms`。
        - 对于不支持 streaming 的 backend（如 `FakeLLMClient`），fallback 为 TTFT = total latency。
      - **改进项 3 - 聚合指标与报告输出**：
        - `metrics.py` 的 `EvaluationResult` 增加 `avg_*_total_latency_ms`、`avg_*_ttft_ms`、median/p95 分位数。
        - `save_text_report()` 与 `locomo_runner.py` 的日志输出同步增加新指标。
    - **涉及代码**：`benchmarks/locomo_runner.py`、`benchmarks/metrics.py`、`tests/test_benchmark.py`。
    - **验收标准**：全部单元测试通过；black + ruff 通过；聚合 JSON 与文本报告中同时出现 total latency 和 TTFT 指标。
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

### Phase 9.1: 检索召回率修复——BM25 标点清洗 + RRF 权重调整 + Query 停用词过滤
- **Status:** complete
- **问题发现（v5 benchmark 分析）**：
  - Session-mem Judge 0.528 vs Baseline 0.549，差距虽小但缺陷高度集中：session-mem 在 `when` 类问题上仅 0.076（baseline 0.182），大量精确事实题回答「文中未提及」。
  - 根因诊断：`hybrid_search.py` 的 BM25 实现存在 **标点未清洗** 的 bug——query token `"birthday?"` 与文档 token `"birthday."` 因标点差异被判定为不匹配，导致包含答案的 cell 在 BM25 路得 0 分。
  - 同时 `MEMORY_SYSTEM_THRESHOLD = 0.015` 过高，对 keyword-only hit（BM25 排名 7+ 但向量路未进前 5）的 cell 产生硬截断，大量事实片段被丢弃。
  - Query 中停用词（`how`, `long`, `ago`, `was`）稀释了有效关键词密度，进一步降低 BM25 召回精度。
- **修复动作**：
  1. [x] **BM25 标点清洗**：在 `keyword_scores()` 中对 query token 和文档 token 统一执行 `re.sub(r'[^\w\s]', '', token)`，消除标点干扰。
  2. [x] **提升 BM25 路在 RRF 中的权重**：从当前 `vector_weight=0.75 / keyword_weight=0.25` 调整为 `vector_weight=0.6 / keyword_weight=0.4`，让事实型问题的字面匹配信号获得更强话语权。
  3. [x] **Query 停用词过滤**：定义中英文停用词集合，在计算 BM25 前从 query tokens 中剔除 `how/long/ago/was/the/and` 等无意义词，提升关键词匹配密度。
  4. [x] **降低 `MEMORY_SYSTEM_THRESHOLD`**：从 `0.015` 降至 `0.008`，允许更多 keyword-only hit 进入候选池。
- **涉及代码**：
  - `src/session_mem/retrieval/hybrid_search.py`（标点清洗、停用词过滤）
  - `src/session_mem/config.py`（RRF 权重、`MEMORY_SYSTEM_THRESHOLD`）
  - `tests/test_retrieval.py`（新增 BM25 标点清洗测试、停用词过滤测试）
- **验收标准**：
  1. [x] 构造 query `"birthday?"` 与文档 `"birthday."`，BM25 能正确匹配。
  2. [x] 全部单元测试通过；black + ruff 通过。
  3. [x] 计划文件同步更新为 Phase 9.1 并提交 git。

### Phase 9.2: 实体共现激活改为 BM25 扩展
- **Status:** complete（代码已修改、测试通过、已提交 git）
- **代码变更**：
  1. `src/session_mem/core/memory_system.py`：废弃 `find_by_entity()` 硬匹配，改为将已激活 cell 的 entities 拼接成扩展查询，对未入选 cell 调用 `hybrid_search.keyword_scores()` 做 BM25 评分。
  2. 实体扩展前为候选 cell 回填 `raw_text`，确保 BM25 能基于原文计算。
  3. 按 BM25 得分降序取前 `extra_limit=3` 加入激活列表。
- **涉及代码**：
  - `src/session_mem/core/memory_system.py`
  - `tests/test_retrieval.py`（新增 `test_retrieve_context_bm25_entity_expansion_filters_high_freq`）
- **验收标准**：
  1. [x] 构造含高频实体（覆盖 >50% cell）的会话，验证该实体不再触发无关早期 cell 的共现激活。
  2. [x] 构造含低频实体的会话，验证 BM25 实体扩展仍能召回相关细粒度 cell。
  3. [x] 全部单元测试通过；black + ruff 通过。

### Phase 9.2.1: BM25 实体扩展候选池去前置过滤
- **Status:** complete（代码已修改、测试通过、已提交 git）
- **逻辑**：
  1. **全部 session cell** 参与 BM25 评分，不预先过滤 `seen`，确保打分公平。
  2. 取 **全局 BM25 top `extra_limit=3`**。
  3. 只把 top 3 中**不在 `seen` 里**的补充进 `activated_cells`（可能 0~3 个）。
- **原因**：若前置过滤 `seen`，已被召回的高 BM25 分 cell 会占据 top 3 名额，导致真正未被召回的新候选被挤出视野；但如果 top 3 都已在召回池中，说明扩展无新增价值，自然补充 0 个。
- **涉及代码**：
  - `src/session_mem/core/memory_system.py`
- **验收标准**：
  1. [x] 全部 session cell 参与 BM25 评分。
  2. [x] 取 top 3 后仅补充其中未入选的 cell，不重复加入。
  3. [x] 全部单元测试通过；black + ruff 通过。
- **原因**：若前置过滤 `seen`，已被召回的高 BM25 分 cell 会占据 top 3 名额，导致

### Phase 9.3: 移除 linked_prev 因果链断裂防护
- **Status:** complete（代码已修改、测试通过、已提交 git）
- **备注**：与 9.2 一并完成，全部完成后统一跑 **v7 benchmark**。
- **决策**：在检索阶段暂时移除 `linked_prev` 自动回溯逻辑。
- **原因**：
  1. 当前实现为**无条件单层回溯**，长链会话中容易拉入与查询弱相关的前序 Cell，挤占 prompt 空间。
  2. `timestamp_start` 升序排列 + 动态 `min_cells`/`max_cells` 已能在多数场景下保证叙事连贯性。
  3. `causal_deps` 多元依赖尚未启用，单层 `linked_prev` 的防护价值有限，反而成为无关 Cell 混入的通道。
  4. 后续若需恢复因果链防护，将改为**带相关度门槛的递归回溯**（结合 BM25/RRF 分数过滤 + 最大深度限制），而非无条件加载。
- **涉及代码**：
  - `src/session_mem/core/memory_system.py`（删除或注释 `retrieve_context()` 中 `linked_prev` 自动加载块）
- **验收标准**：
  1. `retrieve_context()` 不再自动加载命中 Cell 的 `linked_prev` 前驱 Cell。
  2. 全部单元测试通过；black + ruff 通过。
  3. 计划文件同步更新为 Phase 9.3 并提交 git。

### Phase 9.4: 检索阈值修复 + 时间戳机制整顿
- **Status:** pending（问题已定位，待开发）
- **Part A: 检索阈值与上限修复**
  - **v7 诊断结论**：SM Judge 0.479 vs Baseline 0.560，差距 **0.081**（v5 仅 0.022）；激活 Cell 刚性化为 8-10 个（82.6% 恰好 8 个）。
  - **根因**：`MEMORY_SYSTEM_THRESHOLD = 0.008` 过低 + `max_cells = 8` 过高，导致低分干扰 Cell 大量涌入。
  - **修复动作**：
    1. **提高 `MEMORY_SYSTEM_THRESHOLD`**：从 `0.008` 恢复至 `0.015`（v5 值）或更高（如 `0.018`）。
    2. **降低 `max_cells` 上限**：从 `8` 降至 `6` 或 `7`，恢复弹性。
    3. **严格保持时序排序**：`retrieve_context()` 中 `activated_cells` 必须继续按 `timestamp_start` 升序排列（Phase 8.7 的约束），不得以 RRF 分数或其他指标重新打乱叙事时间线。调整 threshold 和 max_cells 时，需验证时序排序不被破坏。

- **Part B: 时间戳机制整顿**
  - **问题发现**：v7 中出现大量时间戳为 `2026-04-17`（即当前运行日期）的 Cell，原因是 `data_loader.py` 在 session date 解析失败时 fallback 到 `datetime.now()`。
  - **核心原则**：对话发生的时间由 session 的 `session_x_date_time` 决定，Cell 的 `timestamp_start` / `timestamp_end` 必须严格对应此时间。只要保证时间戳真实、准确，并在 Prompt 中清晰呈现，LLM 就能基于对话发生时间回答时间类问题，无需额外提取 turn text 中的 mentioned time。
  - **修复方案**：
    1. **修复 benchmark 时间戳 fallback**：
       - `data_loader.py`：将 `datetime.now()` fallback 替换为固定基准日期（如 `2023-05-01T00:00:00+00:00`），与 LoCoMo 真实时间对齐。
       - 增加解析失败时的 warning 日志，便于排查数据质量问题。
    2. **确保 Prompt 中时间戳清晰可见**：
       - 确认 `WorkingMemory.to_prompt()` 已给每个 activated cell 的 `raw_text` 前加上 `[timestamp_start - timestamp_end]` 前缀（Phase 8.7 已完成）。
       - 若格式不够醒目，可微调前缀样式（如换行分隔），提升 LLM 对时序信息的感知度。

- **涉及代码**：
  - `src/session_mem/config.py`（`MEMORY_SYSTEM_THRESHOLD`）
  - `src/session_mem/core/memory_system.py`（`max_cells` 计算逻辑）
  - `benchmarks/data_loader.py`（fallback 修复）
  - `src/session_mem/core/working_memory.py`（Prompt 时间戳前缀样式微调，可选）
- **验收标准**：
  1. benchmark 生成的 Cell 时间戳不再出现 `2026-04-17` 等当前日期。
  2. 抽样检查 `mentioned_times` 能正确提取 "last month"、"next Friday" 等相对时间。
  3. v8 benchmark 激活 Cell 数量分布恢复弹性（不再刚性 8-10 个）。
  4. SM Judge 与 Baseline 差距缩小至 ≤0.05。
  5. Token 节省率保持 ≥40%。
  6. 全部单元测试通过；black + ruff 通过。

- **问题发现（v5 benchmark 深度分析）**：
  - 大量早期通用背景 cell（如 C_001、C_010）在 v5 中被激活了 **80-90%** 的 QA 次数，严重挤占 prompt 空间。
  - 根因：`memory_system.py` 的实体共现激活机制使用 `find_by_entity()` 做硬等值匹配：一旦召回 cell 的 entities 中包含高频实体（如 `Jon`、`Gina`），系统就会把会话中**所有**含该实体的 cell 都拉进来，实质上变成了全表扫描。
  - 在 LoCoMo benchmark 中这一问题被极端放大（仅 2 个固定主角），但真实场景下若出现贯穿始终的高频实体（产品名、项目名、地点），同样会触发通用 cell 的过度召回。
- **修复方案**：
  1. **废弃 `find_by_entity` 硬匹配**：不再逐个实体去数据库做等值查询。
  2. **改用 BM25 做实体扩展**：将当前已激活 cell 的 entities 去重后拼接成扩展查询（如 `"Jon dance studio Marley flooring"`），对未入选的候选 cell 调用 `hybrid_search.keyword_scores()` 进行 BM25 评分。
  3. **BM25 IDF 天然过滤高频实体**：高频贯穿实体（如 `Jon`）因出现在绝大多数 cell 中，IDF 极低，对 BM25 贡献微弱；低频特异性实体（如 `Marley flooring`）IDF 高，能显著提权相关 cell。
  4. **取 top `extra_limit` 加入激活列表**：按 BM25 得分排序，取前 N 个最优候选，避免早期通用 cell 大规模混入。
- **涉及代码**：
  - `src/session_mem/core/memory_system.py`（实体共现激活逻辑重构）
  - `tests/test_retrieval.py` 或 `tests/test_memory_system.py`（新增 BM25 实体扩展测试）
- **验收标准**：
  1. 构造含高频实体（覆盖 >50% cell）的会话，验证该实体不再触发无关早期 cell 的共现激活。
  2. 构造含低频实体的会话，验证 BM25 实体扩展仍能召回相关细粒度 cell。
  3. 全部单元测试通过；black + ruff 通过。
  4. 计划文件同步更新为 Phase 9.2 并提交 git。

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
