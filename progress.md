# Progress Log: session-mem
<!-- 
  WHAT: session-mem 项目的会话级进度日志
-->

## Session: 2026-04-15

### Phase 8: 运行优化与问题修复（Meta Cell 膨胀修复后 benchmark 重跑与分析）
- **Status:** in_progress（Meta Cell 修复验证成功，准确率差距成新瓶颈）
- **Actions taken:**
  1. 重构 `MetaCellGenerator.generate()`：签名从 `cell: MemoryCell` 改为 `cells: list[MemoryCell]`，支持批量 Cell 输入
  2. 修正 `raw_text` 赋值逻辑：优先使用 LLM 返回的 `summary`，fallback 时使用 Cell 摘要拼接，彻底消除全文累积导致的 11,578 tokens 膨胀
  3. 更新 `build_meta_cell_prompt()`：支持 `list[dict]` 多 Cell 输入，Prompt 中列出每个 Cell 的 ID 和原文
  4. 更新 `MemorySystem.add_turn()`：软限分支先收集全部新生成的 Cell，再一次性调用 `_update_meta_cell(new_cells)`
  5. 更新 `MemorySystem._generate_cell()`：返回类型从 `None` 改为 `MemoryCell | None`
  6. 更新 `test_meta_cell_generator.py` 和 `test_memory_system.py`，新增批量更新测试
  7. 全部 102 个测试通过；black + ruff 通过
  8. **本地重跑 benchmark**（`locomo_quick_test.json`，200 QA），产出详细分析报告：
     - **Token 节省率 vs baseline：77.92%**（目标 40%+），Meta Cell 平均降至 **1033 tokens**（最低 425）
     - **准确率**：Baseline Judge 0.511，session-mem Judge **0.358**，差距 **0.153**，未达标（目标 <0.05）
     - **激活 Cell 数量**：平均 **6.8 个**（Min 6, Max 7），但大量为低频通用 Cell（C_001 被激活 96%，C_002 被激活 80%）
     - **准确率下降根因**：实体共现激活机制几乎每次都将早期通用背景 Cell（如 C_001 关于“Jon 失业、热爱舞蹈”）拉入 Working Memory，挤占了真正包含答案的特定后期 Cell 的位置
     - 典型案例：Q41（Gina 最喜欢的舞蹈记忆）、Q42（Gina 团队获奖舞蹈名称）均因缺少对应 Cell 而回答错误，但 Baseline 正确
- **缺陷清单（按优先级排序，待修复）**：
  1. **P0 - 热区构建错误**：`_build_hot_zone()` 只取 SenMemBuffer 末尾 2 轮，但零压缩缓冲区的全部内容都应属于热区。导致热区 token 仅 32（预期约 400），最新上下文严重丢失。
  2. **P0 - 实体共现召回无关通用 Cell**：`retrieve_context()` 的实体共现激活无条件拉入 C_001（96%）、C_002（80%）、C_004（74.5%）等早期背景 Cell，挤占真正含答案的后期 Cell，是准确率从 0.511 → 0.358 的主因。
  3. **P1 - 数据集角色映射失真**：`benchmarks/data_loader.py` 将 Jon/Gina 的对等对话强制映射为 `user`/`assistant`，把第三方对话硬套进人机助手范式。应保留原始 speaker 名称。
  4. **P1 - 激活 Cell 缺少二次相关性截断**：当前 `top_k=2` + `linked_prev` + `extra_limit=3` = 刚性 6-7 个 Cell，缺少统一按查询相关度重新排序和截断。
  5. **P2 - 关键词匹配对通用词过于敏感**：Jaccard 匹配下 "dance"、"Jon"、"Gina" 等高频词几乎每个 Cell 都有，导致早期通用 Cell 获得虚高 keyword score。
  6. **P2 - benchmark 流程中问题未进入热区**：`locomo_runner.py` 直接 `ms.retrieve_context(question)`，问题本身没有先入 SenMemBuffer，QueryRewriter 做指代消解时缺少当前问题上下文。
  7. **P3 - Judge 阈值审视**：Baseline 自身 Judge 仅 0.511，部分 0 分案例可能并非完全错误，需抽样复核评估标准是否过严。
- **Files created/modified:**
  - `session-mem-main/src/session_mem/core/meta_cell_generator.py`
  - `session-mem-main/src/session_mem/llm/prompts.py`
  - `session-mem-main/src/session_mem/core/memory_system.py`
  - `session-mem-main/tests/test_meta_cell_generator.py`
  - `session-mem-main/tests/test_memory_system.py`
  - `benchmarks/results/locomo_quick_test.json`
  - `benchmarks/results/locomo_quick_test.txt`

## Session: 2026-04-15

### Phase 8: 运行优化与问题修复（小样本跑测 v2 结果与评测增强计划）
- **Status:** in_progress（阻塞性问题已修复，Token 节省率根因已定位：Meta Cell 膨胀 + 聚合指标错误）
- **Actions taken:**
  1. 完成服务器小样本 v2 跑测，总耗时约 23 分钟，产出 `locomo_quick_test_v2.json/log`
  2. 验证两个阻塞性问题已修复：0 次 400 Bad Request，Judge 正常发出请求并返回有效分数
  3. 新发现：Token 节省率仅 **10.73%**（vs baseline），远低于目标 40%+
  4. 问题：现有评测输出过于简略，无法判断是 Meta Cell 过大、热区长、还是激活 Cell 过多导致的膨胀
  5. 决定：先增强评测结果详细度（per-QA token 拆解、三个回答独立 Judge 评分、可读文本报告），同时为 benchmark 增加并发支持（per-QA 多回答并发或跨 QA/session 并发），再基于详细数据制定压缩/检索优化方案
  6. 同步更新 `task_plan.md`、`findings.md`、`progress.md` 三个计划文件
  7. 服务器跑测 v3（增强版输出）分析完成，定位两个新问题：
     - Meta Cell `raw_text` 全文累积导致 11,578 tokens 膨胀（baseline 仅 13,800），是 Token 节省率仅 2.67% 的根因
     - 聚合指标 `avg_judge_score_vs_baseline` 并非用户所需，应替换为三个回答各自 vs ground_truth 的独立平均分
  8. 更新三个计划文件，将上述两点纳入 Phase 8 修复清单
  9. 完成 **benchmark 并发优化**：`locomo_runner.py` 新增 `--max_workers` 参数，使用 `ThreadPoolExecutor` 实现跨 session 并发；增加 `--reuse_db` 与并发冲突保护
  10. 完成 **评测聚合指标修正**：`metrics.py` 删除交叉对比指标，替换为 `avg_baseline_judge_score` / `avg_sliding_judge_score` / `avg_session_mem_judge_score`；同步更新 `locomo_runner.py` 日志输出与 `tests/test_benchmark.py` 回归测试
  11. 全部 15 个 benchmark 回归测试通过；black + ruff 通过
  12. Meta Cell 膨胀修复和激活 Cell 优化暂时搁置，待后续实施
- **Files created/modified:**
  - `task_plan.md`
  - `findings.md`
  - `progress.md`
  - `benchmarks/locomo_runner.py`
  - `benchmarks/metrics.py`
  - `tests/test_benchmark.py`

## Session: 2026-04-15

### Phase 8: 运行优化与问题修复（小样本跑测问题定位与修复计划）
- **Status:** complete（代码已修复并推送）
- **Actions taken:**
  1. 完成服务器小样本跑测（`--max_sessions 2 --max_qa_per_session 2 --run_accuracy`），产出日志与结果文件
  2. 定位问题 1：LLM 后端（vLLM/OneAPI 代理）不支持 `json_schema` 类型的 `response_format`，导致 CellGenerator/MetaCellGenerator 全部 400 失败，Token 节省率仅 4.84%
  3. 定位问题 2：`QwenClient.chat_completion()` 硬编码 `model=self.model`，与 `judge_answer()` 传入的 `model=judge_model` 冲突，抛出 `TypeError` 后被裸 `except Exception: pass` 静默吞掉，Judge 全为 0.0
  4. 制定修复计划并更新 `task_plan.md`、`findings.md`、`progress.md` 三个计划文件
  5. 修复计划已确认：新增 `supports_json_schema` 开关、允许 kwargs 覆盖 model、修复 Judge 异常静默、新增 `--skip_judge` 参数
- **Files created/modified:**
  - `src/session_mem/llm/qwen_client.py`
  - `benchmarks/metrics.py`
  - `benchmarks/locomo_runner.py`
  - `tests/test_qwen_client.py`
  - `tests/test_benchmark.py`
  - `task_plan.md`
  - `findings.md`
  - `progress.md`

## Session: 2026-04-15

### Phase 8: 运行优化与问题修复（框架搭建）
- **Status:** pending（待跑测后根据具体问题填充）
- **Actions taken:**
  1. 在 `task_plan.md` 中新增 Phase 8 章节，明确该阶段目标：解决服务器端到端跑测中的实际运行问题、代码性能优化、根据跑测结果调参、补充回归测试
  2. 同步更新 `findings.md` 和 `README.md` 的规划结构
- **Files created/modified:**
  - `task_plan.md`
  - `findings.md`
  - `README.md`
  - `session-mem-main/README.md`

## Session: 2026-04-15

### Phase 7: 验证与测试（适配真实数据 + 三向对比）
- **Status:** in_progress（脚本已适配 LoCoMo 真实数据，待服务器跑测）
- **Actions taken:**
  1. 分析真实 `locomo10.json` 结构：`conversation` 下含多个 `session_x` 和 `session_x_date_time`，`qa` 数组含问题、标准答案与 `evidence` 证据链
  2. 重构 `data_loader.py`：将同一 conversation 的所有 session 按顺序合并为单一长会话；自动归一化 `speaker_a`→`user`、`speaker_b`→`assistant`；基于 session 日期为每个 turn 生成递增 ISO 时间戳
  3. 重构 `prompt_assembler.py`：新增 `build_sliding_window()`，支持最近 N 轮对话作为滑窗基线
  4. 重构 `metrics.py`：评估粒度从 session 级下沉到 **QA 级**，同时记录全量/滑窗/session-mem 三者的 token 数、延迟、回答、Judge 评分
  5. 重构 `locomo_runner.py`：流程改为"先全量 add_turn 写入 MemorySystem，再对每个 QA 分别跑全量/滑窗/session-mem 三种方式"；新增 `--sliding_window` 参数；输出 `token_saving_rate_vs_baseline` 与 `token_saving_rate_vs_sliding`
  6. 新建 `tests/test_benchmark.py`：11 个测试覆盖数据加载、全量/滑窗 Prompt 组装、三向对比集成、聚合指标、Judge 评分
  7. 全部 94 个测试通过；black + ruff 通过；核心模块覆盖率均 > 60%
  8. 同步更新 `task_plan.md`、`findings.md`、两个 `README.md`
- **Files created/modified:**
  - `benchmarks/locomo_runner.py`
  - `benchmarks/data_loader.py`
  - `benchmarks/metrics.py`
  - `benchmarks/prompt_assembler.py`
  - `tests/test_benchmark.py`
  - `README.md`
  - `session-mem-main/README.md`
  - `task_plan.md`
  - `findings.md`

### Phase 7: 验证与测试（脚本开发完成）
- **Status:** in_progress（脚本就绪，待数据集跑测）
- **Actions taken:**
  1. 创建 `benchmarks/` 目录结构：`data/`、`results/`、`locomo_runner.py`、`data_loader.py`、`metrics.py`、`prompt_assembler.py`
  2. 实现 `data_loader.py`：支持 JSON/JSONL 格式，自动推断 `role`/`content`/`timestamp` 字段，兼容 speaker/text 别名
  3. 实现 `prompt_assembler.py`：组装全量历史基线 Prompt，用于 Token 节省率对比
  4. 实现 `metrics.py`：`SessionMetrics`、`EvaluationResult`、聚合计算、LLM-as-Judge 评分函数
  5. 实现 `locomo_runner.py`：主评估脚本，支持命令行参数，逐 session 调用 `MemorySystem.add_turn()` + `retrieve_context()`，测量 Token 节省率、检索延迟（avg/median/P95），可选 `--run_accuracy` 触发 baseline / session-mem 回答生成与 Judge 评分
  6. 补充测试：`tests/test_parser.py`（15 个测试，覆盖 safe_json_loads 全部 fallback 分支）、`tests/test_tokenizer.py`（2 个测试），parser 覆盖率从 52% 提升至 100%
  7. 全部 83 个测试通过；black + ruff 通过；核心模块（buffer 93%、cell_generator 90%、retrieval 88-94%、storage 94%）均满足 > 60%
  8. 更新 `README.md`、`session-mem-main/README.md`、`task_plan.md`：补充 LoCoMo 复现命令、当前状态
- **Files created/modified:**
  - `benchmarks/locomo_runner.py`
  - `benchmarks/data_loader.py`
  - `benchmarks/metrics.py`
  - `benchmarks/prompt_assembler.py`
  - `tests/test_parser.py`
  - `tests/test_tokenizer.py`
  - `README.md`
  - `session-mem-main/README.md`
  - `task_plan.md`

## Session: 2026-04-14

### Phase 6: 边界情况与异常处理
- **Status:** complete
- **Actions taken:**
  1. 实现 `linked_prev` 因果链自动加载：`MemorySystem.retrieve_context()` 在回溯命中 Cell 原文后，遍历已激活 Cell 的 `linked_prev`，将未加载的前序 Cell 自动补充进 `activated_cells`
  2. 实现实体共现激活：遍历已激活 Cell 的 `entities`，通过 `cell_store.find_by_entity()` 级联加载同一会话中含相同实体的其他 Cell，限制额外加载上限为 3 个，避免 WorkingMemory 膨胀
  3. 使用 `seen: set[str]` 对直接命中、因果链补充、实体共现补充三路来源进行去重，保证 Cell 不重复出现
  4. `tests/test_retrieval.py` 新增 2 个集成测试：`test_retrieve_context_loads_linked_prev` 验证 `C_002` 命中后自动加载 `C_001`；`test_retrieve_context_activates_entity_cooccurrence` 验证同实体 "budget" 级联加载 `C_001` 与 `C_002`
  5. 全部 66 个测试通过；black + ruff 通过
- **Files created/modified:**
  - `src/session_mem/core/memory_system.py`
  - `tests/test_retrieval.py`

### Phase 5: 检索策略与 Working Memory
- **Status:** complete
- **Actions taken:**
  1. 完善 `QueryRewriter`：引入 `token_estimator` 参数，短查询触发阈值从字符长度改为 <10 tokens；扩充中英文指代词库
  2. 实现 `HybridSearcher` 双路召回：向量检索（sqlite-vec）+ 关键词 Jaccard + 实体匹配奖励，融合公式 `0.75*vector + 0.25*keyword`
  3. `HybridSearcher` 新增低置信度 fallback：Top-1 融合分数 <0.6 时，扩大向量检索范围并执行全量精确关键词扫描，RRF 合并结果
  4. `MemorySystem` 自动构造 `HybridSearcher` 与 `QueryRewriter`，`retrieve_context()` 完成"查询重写 → 双路召回 → 全量回溯 → 组装 WorkingMemory"完整链路
  5. `WorkingMemory.to_prompt()` 已确保 Meta Cell 无条件前置
  6. 新建 `tests/test_retrieval.py`，覆盖 QueryRewriter、HybridSearcher（含 fallback、实体奖励）及 MemorySystem 检索集成，共 10 个测试
  7. 全部 64 个测试通过；black + ruff 通过
- **Files created/modified:**
  - `src/session_mem/retrieval/query_rewriter.py`
  - `src/session_mem/retrieval/hybrid_search.py`
  - `src/session_mem/core/memory_system.py`
  - `tests/test_retrieval.py`（新建）

### Phase 4.1: 多切分点语义边界检测落地
- **Status:** complete
- **Actions taken:**
  1. 重构 `SemanticBoundaryDetector`：`should_split()` 从返回 `bool` 改为返回 `list[int]` 切分点索引列表
  2. 更新边界检测 Prompt：要求 LLM 输出 JSON 格式 `{"split_indices": [3, 6]}`，标明 Buffer 内多处主题转折位置
  3. 增强 `safe_json_loads`：新增方括号 `[...]` 数组提取能力，支持解析切分点列表
  4. `SenMemBuffer` 新增 `extract_segments(split_indices)`：按多个切分点连续切分为 N+1 段，前 N 段生成 Cell，最后一段保留
  5. `MemorySystem.add_turn()` 软限分支重构：接收切分点列表后循环调用 `_generate_cell`，支持一次检测产出多个 Cell
  6. `_generate_cell` 新增 `trigger_meta` 参数：批量生成时仅最后一个 Cell 触发 Meta Cell 更新，避免重复调用
  7. `cell_generator.py` 与 `meta_cell_generator.py` 增加 `isinstance(data, dict)` 防御，防止 list 返回值导致 `AttributeError`
  8. 测试覆盖：`test_boundary_detector.py`（9 个测试）、`test_buffer.py`（+3 个测试）、`test_memory_system.py`（+2 个集成测试）
  9. 全部 54 个测试通过；black + ruff 通过
- **Files created/modified:**
  - `src/session_mem/core/boundary_detector.py`
  - `src/session_mem/llm/prompts.py`
  - `src/session_mem/llm/parser.py`
  - `src/session_mem/core/buffer.py`
  - `src/session_mem/core/memory_system.py`
  - `src/session_mem/core/cell_generator.py`
  - `src/session_mem/core/meta_cell_generator.py`
  - `tests/test_boundary_detector.py`
  - `tests/test_buffer.py`
  - `tests/test_memory_system.py`

### Phase 4: Cell 生成、Meta Cell 与 ShortMemBuffer
- **Status:** complete
- **Actions taken:**
  1. 实现 Embedding 客户端（QwenClient.embed），支持独立配置 Xinference bge-large-en-v1.5
  2. 完善 CellGenerator：增加 LLM 异常捕获、JSON 解析 fallback（代码块/正则/去尾逗号）、简单摘要/关键词 fallback
  3. 实现 MetaCellGenerator：初始生成 + 增量融合更新，配套 Prompt 与 Schema
  4. ShortMemBuffer 重构为从 CellStore 按 session_id 加载
  5. MemorySystem 完善闭环：生成 → 存元数据 → 存原文 → 存向量 → ShortMemBuffer；触发 Meta Cell 生成/更新
  6. WorkingMemory 添加 meta_cell 字段，to_prompt 无条件前置 Meta Cell 全文
  7. 新增 tests/test_cell_generator.py（4 个测试）、tests/test_meta_cell_generator.py（4 个测试）
  8. 扩展 tests/test_buffer.py（ShortMemBuffer 2 个测试）、tests/test_memory_system.py（embedding + meta cell 3 个测试）
  9. 全部 41 个测试通过；black + ruff 通过
- **Post-Phase 4 热修复（2026-04-14）：**
  1. 修复 Cell ID 生成器非持久化：初始化时从 cell_store 解析最大序号
  2. 修复 causal_deps / metadata 有生成无存储：SQLite schema 新增两列并同步序列化
  3. 修复 Meta Cell 更新偏离技术方案：由全量 cells 输入改为增量 single-cell 输入（previous_meta.raw_text + newest_cell.raw_text）
  4. 修复 save_meta_cell() 非原子操作：使用 `with self.conn:` 显式事务包裹 UPDATE + INSERT
  5. 修复 Embedding 失败静默吞异常：`_generate_cell()` 中 `except Exception` 改为 `logger.warning` 记录失败信息
  6. 修复 should_trigger_check() 阈值跨越缺陷：改为 `current_multiple = tokens // soft_limit`，避免单次跨越多个阈值时连续触发
  7. 修复 gap_detected() 静默失败：增加 `logger.warning` 记录时间戳解析异常
  8. 修复 ShortMemBuffer.all_cells() 忽略缓存：合并 `_cache` 与 `cell_store` 结果并去重
  9. 修复 add_turn() 软限分支缺 return：语义边界检测后显式 `return`
  10. 修复 extract_for_cell() 无防御：增加 `cutoff_index <= 0` 时返回空列表
  11. 修复 safe_json_loads 尾逗号正则破坏字符串：仅在正则提取的花括号范围内执行去尾逗号
  12. 修复 CellGenerator LLM 异常静默：增加 `logger.warning`
  13. 修复 _resolve_max_cell_id() 异常时重置为 0：使用 `re.match(r"^C_(\d+)$", c.id)` 健壮解析
  14. 修复 Meta Cell raw_text 不累积：改为 `previous_meta.raw_text + newest_cell.raw_text` 的累积格式
  15. 修复 build_meta_cell_prompt 增量原则偏差：Prompt 中仅传入 `cell.raw_text` 和 `previous_meta.raw_text`
  16. 修复 SQLite 存储层事务与 schema 缺陷：启用 WAL、entity_links/cell_texts 加 ON DELETE CASCADE、delete_session 提升到 SQLiteBackend、json.dumps 防御 None、meta_cells 增加 keywords/entities 列与 updated_at 触发器、新增 `get_full_cell()`
  17. 全部 44 个测试通过；black + ruff 通过
- **Files created/modified:**
  - `src/session_mem/llm/base.py`（新增 embed 抽象）
  - `src/session_mem/llm/qwen_client.py`（新增 embed 实现与 Embedding 配置）
  - `src/session_mem/llm/prompts.py`（新增 Meta Cell Prompt/Schema）
  - `src/session_mem/llm/parser.py`（增强 JSON fallback：正则提取、去尾逗号）
  - `src/session_mem/core/cell_generator.py`（增强 fallback 与异常处理）
  - `src/session_mem/core/meta_cell_generator.py`（完整实现）
  - `src/session_mem/core/buffer.py`（ShortMemBuffer 联动 CellStore）
  - `src/session_mem/core/memory_system.py`（embedding、meta cell 闭环）
  - `src/session_mem/core/working_memory.py`（meta_cell 前置）
  - `tests/test_cell_generator.py`（新建）
  - `tests/test_meta_cell_generator.py`（新建）
  - `tests/test_buffer.py`（扩展）
  - `tests/test_memory_system.py`（扩展）

---

### Phase 2: 存储层完善与数据库构建
- **Status:** complete
- **Actions taken:**
  - 使用 uv 创建 Python 3.11 虚拟环境并安装依赖（含 `sqlite-vec`）
  - 扩展 `MemoryCell`，新增 `status`、`version`、`linked_cells` 字段以支持 Meta Cell
  - 完善 `SQLiteBackend`：加载 `sqlite-vec` 扩展、启用外键约束（`PRAGMA foreign_keys = ON`）
  - 新增 `meta_cells` 表及存储方法：`save_meta_cell()`、`get_active_meta_cell()`、`delete_meta_cells_by_session()`
  - 修复 `delete_session()` 级联清理逻辑，确保 `cell_texts`、`cell_vectors`、`entity_links`、`meta_cells` 一并删除
  - 创建 `tests/test_storage.py`，覆盖 CRUD、会话隔离、实体共现查询、向量增删查、级联删除、Meta Cell 生命周期、`fragmented` cell_type
  - 运行 `uv run pytest tests/test_storage.py -v`，8 个测试全部通过
  - 运行 `uv run black src/ tests/` 与 `uv run ruff check src/ tests/ --fix` 完成代码格式化与 lint 修复
- **Files created/modified:**
  - `src/session_mem/core/cell.py`（扩展 MemoryCell 字段）
  - `src/session_mem/storage/sqlite_backend.py`（Meta Cell 表、级联删除、sqlite-vec 加载、外键启用）
  - `tests/test_storage.py`（新建，8 个单元测试）
  - `src/session_mem/core/memory_system.py`（ruff 自动修复未使用导入）
  - `src/session_mem/storage/base.py`（ruff 自动修复未使用导入）

### Phase 3: SenMemBuffer 实现与语义边界检测
- **Status:** complete
- **Actions taken:**
  - 完善 `SenMemBuffer`：修正 `should_trigger_check` 为 512 整数倍首次跨越触发（引入 `_check_count`），`extract_for_cell` 后自动重置计数器
  - 实现 `gap_detected()`：使用 `datetime.fromisoformat` 解析 ISO 8601（含 `Z` 和时区偏移），30 分钟阈值检测
  - 完善 `SemanticBoundaryDetector`：新增超长内容（>8000 字符）直接切分的规则 fallback，LLM 调用异常时安全返回 False
  - 将切分流程接入 `MemorySystem.add_turn()`：按 gap → hard limit → soft limit → boundary detection 顺序触发；硬上限强制切分并标记 `fragmented`；语义切分保留最后一轮作为种子
  - 在 `MemorySystem` 中自动注入 `TokenEstimator`，实例化 `CellGenerator` 与 `SemanticBoundaryDetector`，实现 `_generate_cell()` 辅助方法
  - 新建 `tests/test_buffer.py`（9 个测试）、`tests/test_boundary_detector.py`（5 个测试）、`tests/test_memory_system.py`（5 个测试）
  - 运行 `uv run pytest tests/ -v`，28 个测试全部通过
- **Files created/modified:**
  - `src/session_mem/core/buffer.py`（`should_trigger_check`、`gap_detected`、`extract_for_cell` 重置）
  - `src/session_mem/core/boundary_detector.py`（fallback 规则、异常处理）
  - `src/session_mem/core/memory_system.py`（切分逻辑、`_generate_cell`、TokenEstimator 注入）
  - `tests/test_buffer.py`（新建）
  - `tests/test_boundary_detector.py`（新建）
  - `tests/test_memory_system.py`（新建）

### 文档与规划更新 + 代码热修复
- **Status:** complete
- **Actions taken:**
  - 重新梳理 `task_plan.md`，为每个 Phase 补充：涉及的具体代码文件、数据库构建细节、验收标准
  - 修正并明确向量维度统一为 1024（bge-large-en-v1.5）
  - 将 Phase 1 状态标记为 complete，Phase 2 状态标记为 in_progress
  - 更新 `findings.md`，补充架构说明、接口契约、当前已知占位项（gap_detected、HybridSearcher）
  - **热修复**：修正 `sqlite_backend.py` 中向量维度默认值 512 → 1024（`SQLiteVectorIndex` 与 `SQLiteBackend` 各一处）
- **Files created/modified:**
  - `task_plan.md`（重写）
  - `findings.md`（更新）
  - `progress.md`（更新）
  - `src/session_mem/storage/sqlite_backend.py`（热修复：dims 默认 512 → 1024）

### Meta Cell 架构同步
- **Actions taken:**
  - 技术方案新增 Meta Cell（会话主旨单元）设计，同步更新 `AGENTS.md`、`task_plan.md`、`findings.md`
  - `task_plan.md`：Phase 2 新增 `meta_cells` 表；Phase 4 新增 `MetaCellGenerator` 实现与存储接口；Phase 5 要求 WorkingMemory 无条件前置 active Meta Cell
  - `findings.md`：新增 Meta Cell 技术决策、数据库 Schema（`meta_cells`）、接口契约（`MetaCellGenerator`）、待实现占位项
  - 更新 `README.md` 核心特性与项目结构，补充 Meta Cell 说明
  - 创建 `src/session_mem/core/meta_cell_generator.py` 占位模块
- **Files created/modified:**
  - `AGENTS.md`（更新：项目结构加入 `meta_cell_generator.py`）
  - `task_plan.md`（更新）
  - `findings.md`（更新）
  - `README.md`（更新）
  - `src/session_mem/core/meta_cell_generator.py`（新建）

---

## Session: 2026-04-13

### Phase 1: 项目脚手架与核心接口设计
- **Status:** complete
- **Started:** 2026-04-13 14:34
- **Completed:** 2026-04-13 16:30
- **Actions taken:**
  - 完成技术方案 v2.0 的预检审查
  - 补充验证方法（LoCoMo 数据集 session 拼接）
  - 补充时间戳解析机制（SenMemBuffer 注入 + Cell 元信息层）
  - 修复章节编号（删除原第 5 章，6→5）
  - 确定代码仓库名为 `session-mem`
  - 初始化 Git 仓库并推送首个 commit
  - 使用 planning-with-files skill 创建开发规划文件
  - 确定存储层设计：SQLite + sqlite-vec，模块化可拔插接口
  - 技术方案中新增第 5 章「存储与持久化设计」
  - 确定技术栈：Python + qwen2.5:72b（语义边界检测独立新会话 + Cell 生成）
  - 创建 Python 项目结构（src/session_mem/...）
  - 实现核心接口：MemorySystem、SenMemBuffer、ShortMemBuffer、MemoryCell、WorkingMemory
  - 实现存储层：VectorIndex / CellStore / TextStore 抽象 + SQLiteBackend 完整实现
  - 实现 LLM 层：LLMClient 抽象 + QwenClient（支持独立新会话调用）
  - 实现 Prompt 模板和 JSON 解析 fallback
  - 实现检索层骨架：QueryRewriter、HybridSearcher
- **Files created/modified:**
  - `单会话级临时记忆系统（Session-scoped Working Memory）技术方案.md`（修改）
  - `task_plan.md`（创建）
  - `findings.md`（创建）
  - `progress.md`（创建）
  - `.vscode/sftp.json`（创建/修改）
  - `AGENTS.md`（创建/修改）
  - `pyproject.toml`（创建）
  - `src/session_mem/` 下全部模块（创建）

### Phase 2: 存储层完善与数据库构建
- **Status:** in_progress
- **Actions taken:**
  - 已完成 SQLiteBackend 骨架（cells、cell_texts、entity_links、cell_vectors 表）
  - 发现向量维度不一致问题（代码默认 512，模型实际 1024），已记录到 findings.md
- **Files created/modified:**
  - `src/session_mem/storage/sqlite_backend.py`
  - `src/session_mem/storage/base.py`

### Phase 3: SenMemBuffer 实现与语义边界检测
- **Status:** pending
- **Actions taken:**
  - `SenMemBuffer` 骨架已实现（add_turn、estimated_tokens、should_trigger_check、extract_for_cell）
  - `SemanticBoundaryDetector` 骨架已实现（调用 qwen2.5:72b 独立新会话）
  - `gap_detected()` 和 `MemorySystem.add_turn()` 中的切分触发逻辑尚未完成
- **Files created/modified:**
  - `src/session_mem/core/buffer.py`
  - `src/session_mem/core/boundary_detector.py`

### Phase 4: Cell 生成与 ShortMemBuffer
- **Status:** pending
- **Actions taken:**
  - `CellGenerator` 骨架已实现（Prompt + JSON Schema + 解析 fallback）
  - `ShortMemBuffer` 当前为内存列表，尚未与 SQLite 联动
  - Embedding 向量写入尚未接入
- **Files created/modified:**
  - `src/session_mem/core/cell_generator.py`
  - `src/session_mem/core/buffer.py`
  - `src/session_mem/llm/prompts.py`

### Phase 5: 检索策略与 Working Memory
- **Status:** pending
- **Actions taken:**
  - `QueryRewriter` 骨架已实现（基于热区的简单扩展）
  - `HybridSearcher` 返回空列表占位
  - `WorkingMemory` 组装逻辑基本成型
- **Files created/modified:**
  - `src/session_mem/retrieval/query_rewriter.py`
  - `src/session_mem/retrieval/hybrid_search.py`
  - `src/session_mem/core/working_memory.py`

### Phase 6: 边界情况与异常处理
- **Status:** pending
- **Actions taken:**
  - 已定义 `linked_prev`、`causal_deps`、`entities` 等字段，但级联激活逻辑尚未实现
- **Files created/modified:**
  - `src/session_mem/core/cell.py`

### Phase 7: 验证与测试
- **Status:** pending
- **Actions taken:**
  - 已创建 `tests/__init__.py` 和 `tests/conftest.py`
- **Files created/modified:**
  - `tests/__init__.py`
  - `tests/conftest.py`

## Test Results
| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| SQLiteBackend schema 初始化 | 新建 `.db` 文件 | 4 张表成功创建 | 4 张表成功创建 | pass |
| QwenClient 连通性 | 调用内网接口 | 返回字符串 | 返回字符串 | pass |
| MemorySystem import | `import session_mem` | 无报错 | 无报错 | pass |

## Error Log
| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-04-13 14:34 | git push: src refspec main does not match any | 1 | git add + git commit 后再 push |
| 2026-04-14 | 向量维度不一致：代码默认 512，AGENTS.md 与模型为 1024 | 1 | 已修复：`sqlite_backend.py` 中 `SQLiteVectorIndex` 和 `SQLiteBackend` 的默认 dims 均改为 1024 |

## 5-Question Reboot Check
| Question | Answer |
|----------|--------|
| Where am I? | Phase 2: 存储层完善与数据库构建（同时 Phase 3/4/5 已有大量骨架代码） |
| Where am I going? | Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7 |
| What's the goal? | 实现 session-mem MVP，包含三层缓冲、Cell 检索、时间戳解析，Token 节省率 ≥40%，准确率损失 <5% |
| What have I learned? | See findings.md；核心结论是：骨架代码已覆盖全部模块，当前重点是补齐存储层维度修正、边界检测触发逻辑、检索融合算法 |
| What have I done? | Phase 1 完成；task_plan.md / findings.md / progress.md 已根据技术方案 v2.0 更新；512/1024 维度问题已在 2026-04-14 热修复 |

---
*Update after completing each phase or encountering errors*
