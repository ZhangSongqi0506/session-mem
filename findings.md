# Findings & Decisions: session-mem
<!-- 
  WHAT: session-mem 项目的技术决策库与研究发现
  WHY: 将方案中的关键设计、约束和发现持久化到磁盘
-->

## Requirements
<!-- 从用户请求和技术方案中提取的核心需求 -->
- 构建单会话级临时记忆系统，降低 Input Token 40-60%
- 回答准确率损失控制在 <5%
- 支持长跨度单会话（用户可能隔段时间再回来提问）
- 通过 LoCoMo 数据集的 session 拼接进行验证
- 低成本、低延迟（TTFT < 50ms）

## Research Findings
- 技术方案采用「三层缓冲 + 语义驱动 Cell + Meta Cell」架构
  - SenMemBuffer：零压缩原始文本，512-2048 tokens 弹性窗口
  - ShortMemBuffer：Cell 摘要索引库，MVP 阶段全量 Cell 参与检索（不区分活跃/存储窗口）
  - Meta Cell：会话级全局摘要单元，强制常驻于 Working Memory 最前端，解决长会话主旨任务缺失问题
  - Working Memory：Meta Cell 全文 + 热区原文 + 命中 Cell 全文
- 时间戳解析用于支持跨长时间会话的时序定位（30 分钟间隔作为切分信号）
- 文档版本 v2.0 已通过预检审查，补充了验证方法、时间戳机制、存储与持久化设计（第 5 章）及 Meta Cell 设计（第 3.3 节）
- **向量维度修正**：早期技术方案草稿写为 512 维，但实际采用 `bge-large-en-v1.5` 输出为 1024 维，`sqlite_backend.py` 默认值已同步修正
- **LoCoMo 评估设计**：每个 conversation 含 10-32 个 session，合并为单一长会话后 QA 数量可达 100+；评估粒度下沉到 QA 级，同时对比全量历史/滑窗/session-mem 三种方式的 Token 数、延迟、准确率

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 三层缓冲架构 | 在延迟敏感性和信息保真度之间取得平衡 |
| Cell 四层结构（检索/回溯/元信息/关系） | 支持从匹配到全文回溯的灵活加载 |
| 双路召回（向量为主 + 关键词为辅） | 解决短查询与长摘要的语义空间不对齐 |
| 命中即全量 | 避免压缩命中内容导致关键约束丢失 |
| 时间戳统一 ISO 8601 UTC | 系统收到时间统一可控，避免客户端时钟偏差 |
| SQLite + sqlite-vec 作为默认存储后端 | 零运维、单文件、适合组件化分发；schema 预留 session_id 支持未来跨会话扩展 |
| Python 实现 | 与 LangChain 生态天然契合，团队熟练度高 |
| qwen2.5:72b 作为统一 LLM 后端 | 内网已部署，语义边界检测和 Cell 生成共用同一模型；边界检测以独立新会话调用，不影响主会话 token |
| uv 作为包管理工具 | 极速依赖解析，统一替代 pip+venv+pip-tools，与 pyproject.toml 原生兼容 |
| 向量维度 1024 | 与 bge-large-en-v1.5 输出维度严格一致，消除维度不一致导致的运行时错误 |
| ShortMemBuffer MVP 阶段不区分窗口 | 保证召回完整性，简化实现；未来会话 Cell >100 时再引入分级策略 |
| Meta Cell（会话主旨单元）| 以少量额外 Token（约 400）换取全局主旨的确定性不丢，作为普通 Cell 检索的双保险 |
| LoCoMo 评估采用 QA 级三向对比 | 同时对比全量历史、最近 N 轮滑窗、session-mem，精确测量 Token 节省率和准确率损失 |
| benchmark 方法级并发（per-QA）| 单个 QA 内三种回答生成并行化，与 session 级并发正交，显著缩短 `--run_accuracy` 总耗时 |
| LLM 回答指令统一注入 `_answer()` | 在 benchmark runner 层统一注入 system 硬指令，强制直接引用、抑制过度解读，不侵入 PromptAssembler/WorkingMemory 接口 |
| 检索策略升级为双路独立召回 + RRF 融合 | 解决旧加权融合下关键词路径无独立召回能力的问题，向量与关键词各自召回后用 RRF 排名，更公平地合并两路信号 |
| 向量检索分数阈值过滤 | 默认 0.3，剔除低质量向量候选，避免 Embedding 噪声污染 RRF 结果 |
| 取消 `total_budget=8` 硬性截断 | 激活 Cell 数量由 `min_cells`/`max_cells` 动态上下限 + 实体共现门槛自然调节，给高相关后期 Cell 更多进入空间 |
| 检索参数集中配置化 | 全部阈值、top_k、RRF k 值抽取到 `RetrievalConfig`，便于实验调参与服务器端快速迭代 |

## Architecture Notes
### 数据库 Schema 设计（SQLiteBackend）
- `cells`：Cell 结构化元数据，含类型、置信度、摘要、关键词（JSON）、实体（JSON）、时序链接
- `cell_texts`：原文回溯，独立表便于未来按时间清理
- `entity_links`：实体共现反查表，支持快速加载同实体关联 Cell
- `cell_vectors`：sqlite-vec 虚拟表，主键 `cell_id`，向量维度 **1024**
- `meta_cells`：会话级 Meta Cell 存储，主键 `(session_id, version)`，含 `status`（active/archived）、`raw_text`、`token_count`、`linked_cells`（JSON）

### 关键接口契约
- `MemorySystem.add_turn(role, content, timestamp)`：写入新轮次，内部触发语义边界检测与 Cell 生成
- `MemorySystem.retrieve_context(query)`：返回 `WorkingMemory`，含 Meta Cell 全文 + 热区 + 激活 Cell 全文 + 查询
- `SemanticBoundaryDetector.should_split(turns)`：独立新会话调用，返回布尔值
- `CellGenerator.generate(turns, session_id, cell_id)`：返回填充完整的 `MemoryCell`
- `MetaCellGenerator.generate(session_id, cells, previous_meta=None)`：生成或全量重写会话级 Meta Cell，返回 `MemoryCell`（`cell_type='meta'`）

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| 文档章节从 4 跳到 6 | 删除原第 5 章内容，将 6.x 改为 5.x |
| git push 到空仓库失败 | 先创建初始 commit 再 push |
| 向量维度 512 vs 1024 不一致 | 技术方案 v2.0 和 AGENTS.md 已统一为 1024；`sqlite_backend.py` 已在 2026-04-14 热修复 |
| sqlite-vec 扩展加载失败：`OperationalError: not authorized` | 在 `sqlite_vec.load(conn)` 前调用 `conn.enable_load_extension(True)` 解决 |
| `SenMemBuffer.gap_detected()` 尚未实现 ISO 8601 解析 | 已在 Phase 3 实现：`datetime.fromisoformat` 解析含 `Z` 和时区偏移的字符串 |
| `HybridSearcher.search()` 尚未实现 | 已在 Phase 5 完成：向量+关键词融合 + 低置信度 fallback |
| `MetaCellGenerator` 尚未实现 | 已在 Phase 4 完成：初始生成 + 增量融合更新 |
| LoCoMo 数据结构差异 | `locomo10.json` 中 conversation 含多个 session_x，已统一合并为单一会话；speaker_a→user、speaker_b→assistant；按 session 日期生成递增时间戳 |
| 后端 LLM 不支持 `json_schema` response_format | 内网 vLLM/OneAPI 代理返回 `400 Bad Request`。修复方案：在 `QwenClient` 中新增 `supports_json_schema=False` 开关，跳过不兼容参数，完全依赖 Prompt + parser fallback |
| `QwenClient.chat_completion()` model 硬编码与 `judge_answer()` 冲突 | `chat_completion()` 显式写死 `model=self.model`，而 `judge_answer()` 通过 kwargs 再传 `model=judge_model`，导致 `TypeError: got multiple values for keyword argument 'model'`，被 `except Exception: pass` 静默吞掉。修复方案：`model=kwargs.pop("model", self.model)`，允许覆盖 |
| 语义边界检测因非标准 role 失效 | Phase 8.1 保留原始 speaker 名称（`Jon`/`Gina`）后，`boundary_detector.py` 直接将非标准 role 传给 LLM API，导致 `should_split()` 返回空列表，所有 Cell 均变成 `fragmented` 且高达 2200+ tokens。修复方案：在 `boundary_detector.py` 构建 prompt 时将非标准 role fallback 为 `user` |
| 检索召回失败导致准确率暴跌 | 服务器 benchmark（v2）显示 session-mem 准确率 0.275 vs baseline 0.465。根因：`hybrid_search.py` 的 `keyword_scores()` 仅扫描 `keywords` + `summary`，未覆盖 `raw_text` 原文；且 `VECTOR_SCORE_THRESHOLD = 0.6` 过滤掉大量语义相关候选。修复方案：关键词路改为扫描 `raw_text`；向量阈值降至 0.3 |

## Phase 8 Framework
- **目标**：解决服务器端到端跑测中发现的实际运行问题，进行代码性能优化与健壮性增强
- **已发现问题**：
  1. LLM 后端不支持 `json_schema` response_format → Cell/Meta Cell 生成全部失败，Token 节省率仅 4.84%
  2. `QwenClient.chat_completion()` model 参数硬编码 → `judge_answer()` 调用冲突，Judge 评分全为 0.0
- **修复动作**：
  - `qwen_client.py`：新增 `supports_json_schema` 开关 + 允许 `kwargs` 覆盖 `model`
  - `metrics.py`：`judge_answer()` 异常处理改为 `logger.warning` 记录
  - `locomo_runner.py`：新增 `--skip_judge` 参数
- **服务器 v2 跑测结果（2026-04-15）**：
  - 阻塞性问题已解决：0 次 400 Bad Request，Judge 请求正常发出并返回有效分数
  - Token 节省率仍过低：vs baseline 仅 **10.73%**（目标 40%+）
  - 现有输出过于简略，无法判断瓶颈是 Meta Cell、热区还是激活 Cell 过多
  - 下一步：先增强评测结果的详细度（per-QA token 拆解、三个回答独立 Judge 评分、可读文本报告）；同时增加 benchmark 并发支持（per-QA 多回答并发或跨 QA/session 并发）以减少串行等待时间，再基于数据做精准优化
- **服务器 v3 诊断结论（2026-04-15 晚，基于增强版 `_report.txt`）**：
  1. **Meta Cell 严重膨胀**：每个 QA 的 Meta Cell 固定为 **11,578 tokens**，而 baseline 仅 13,800 tokens。根因为 `meta_cell_generator.py` 中 `raw_text` 被设为 `previous_meta.raw_text + cell.raw_text` 的累积拼接，而非 LLM 返回的简短 `summary`。
  2. **聚合指标错误**：`EvaluationResult` 输出的是 `avg_judge_score_vs_baseline` / `vs_sliding`（session-mem vs baseline/sliding 的交叉对比），但用户需要的是三个回答各自 vs ground_truth 的独立平均分。
  3. **激活 Cell 数量偏多**：每个 QA 平均激活 5-7 个 Cell，部分 Cell 与查询的直接相关性不高，存在进一步优化空间。
- **Meta Cell 架构修正（2026-04-15）**：
  1. `raw_text` 必须存储 LLM 返回的 `summary`（全局摘要），不存任何原文拼接。
  2. 当一次切分产出 N 个 Cell 时，一次性把这 N 个 Cell 的原文 + 上一个 Meta Cell 摘要全部传给 LLM，只做 **1 次** Meta Cell 更新。
  3. Meta Cell 摘要长度不人为截断，随会话内容自然增长，但绝非原始文本的堆砌。
- **服务器 v4 跑测结果（2026-04-15，Meta Cell 修复后）**：
  - 数据集：`locomo_quick_test.json`，共 200 QA
  - **Token 节省率 vs baseline：77.92%**（目标 40%+）✅
  - **Meta Cell tokens**：平均 1033（最低 425，最高 1641），膨胀问题已彻底解决 ✅
  - **准确率**：Baseline Judge 0.511，session-mem Judge **0.358**，差距 **0.153**，**未达标**（目标 <0.05）❌
  - **session-mem Token 拆解**：Meta Cell 37.5%（1033t）+ Hot Zone 1.2%（32t）+ Activated Cells 61.3%（1688t）
  - **激活 Cell 数量**：平均 6.8 个（Min 6, Max 7），但大量为通用背景 Cell
  - **准确率下降根因**：实体共现激活机制几乎每次都把早期通用背景 Cell（C_001 被激活 96%，C_002 被激活 80%，C_004 被激活 74.5%）拉入 Working Memory，挤占了真正包含答案的特定后期 Cell 的位置
  - **典型案例**：
    - Q41 "What was Gina's favorite dancing memory?" → Baseline 正确（regionals competition），session-mem 回答 "没有明确提到"
    - Q42 "What kind of dance piece did Gina's team perform?" → Baseline 正确（"Finding Freedom"），session-mem 回答 "没有提及"
    - 共性：缺失的后期 Cell（如 C_020 关于 Gina 的舞蹈奖杯/比赛）未被召回，而 C_001/C_002 等通用 Cell 占用了激活名额
- **新修复计划（Phase 8.1 / 8.2 / 8.2.1 / 8.4 / 8.5）**：
  - **Phase 8.1**：
    1. **热区构建错误**：`_build_hot_zone()` 改为直接返回 `sen_buffer.turns` 全部内容，不预设 token 软上限（当前 Token 节省率 77.92% 有余量）。
    2. **benchmark 问题未进入热区**：废弃 `add_turn()+pop()` 方案（会触发语义边界检测污染 session），改为给 `retrieve_context()` 新增 `extra_turns` 参数临时注入问题。
    3. **数据集角色映射失真**（仅 benchmark 代码）：`data_loader.py` 保留原始 speaker 名称，不再强制映射为 user/assistant，影响面限定在 `benchmarks/` 目录。
  - **Phase 8.2**：
    4. **检索策略阈值法重构**：取消固定 `top_k=2`，改用 `search_with_scores()` 获取全部候选，以 `threshold=0.55` 筛选，配合动态上下限 `min_cells = max(2, min(5, total_cells // 10))`、`max_cells = max(min_cells + 1, min(8, total_cells // 3))`，最终统一按 `fused_score` 截断到 `total_budget=8`。
    5. **实体共现优化**：候选需同时满足 `keyword_score > 0` 和 `fused_score >= 0.4`，按相关性排序后取前 3 个，阻止早期通用 Cell 无条件混入。
  - **Phase 8.2.1**（已完成，2026-04-16）：
    6. **双路独立召回 + RRF 融合**：`HybridSearcher` 从"先向量检索再关键词加权融合"改为向量路与关键词路各自独立召回，再用 RRF（Reciprocal Rank Fusion）融合排名。向量路增加 `vector_score_threshold`（默认 0.3）过滤低质量候选。
    7. **取消最终总预算截断**：移除 `MemorySystem.retrieve_context()` 中 `total_budget=8` 的硬性截断，激活 Cell 数量由动态上下限和实体共现门槛自然调节。
    8. **检索参数配置化**：新建 `src/session_mem/config.py`，集中管理 RRF k 值、各路 top_k、向量分数阈值、RRF fallback 阈值（0.015）、MemorySystem 主阈值（0.015）等可调节参数，避免代码硬编码。
    - **代码变更**：`src/session_mem/config.py`（新建）、`hybrid_search.py`（重构为 `_vector_search` / `_keyword_search` / `_rrf_fuse`）、`memory_system.py`（阈值适配 RRF、删除总预算截断）、`tests/test_retrieval.py`（fixture 适配）。
    - **测试结果**：全部 102 个测试通过；black + ruff 通过。
  - **Phase 8.3（已跳过）**：
    9. ~~高频共现词惩罚~~：v3 benchmark（200 QA）显示准确率差距已降至 0.041（<0.05），Token 节省率 50.17%，核心指标均已达标。动态 IDF 惩罚收益有限且实现复杂，决定跳过，将资源投入 LLM 过度解读修复（Phase 8.5）。
  - **Hotfix（2026-04-16，语义边界检测 role fallback）**：
    - 服务器 benchmark（Phase 8.2.1 后）出现反常结果：所有 Cell 类型均为 `fragmented`，单 Cell 2200-2400 tokens，仅生成 4 个 Cell 即覆盖 369 轮对话。
    - 根因：`data_loader.py` 保留原始 speaker 名称导致 `boundary_detector.py` 向 LLM API 传入非标准 role（`Jon`/`Gina`），`should_split()` 返回空列表，语义切分完全失效，全部对话堆积到硬上限后被打包为巨大 `fragmented` Cell。
    - 修复：在 `boundary_detector.py` 内将非标准 role fallback 为 `user`，恢复细粒度语义边界检测。全部 102 个测试通过。
  - **Hotfix（2026-04-16，检索召回失败修复）**：
    - 服务器 benchmark（v2）显示 session-mem 准确率 0.275 vs baseline 0.465，差距 0.190。典型案例："What Jon thinks the ideal dance studio should look like?"（GT: by the water, natural light, Marley flooring）——召回的 11 个 Cell 中无一包含这三个关键特征。
    - 根因：关键词路仅扫描 `keywords` + `summary`，未覆盖 `raw_text` 原文，LLM 提取遗漏时关键词路完全失效；向量路 `VECTOR_SCORE_THRESHOLD = 0.6` 过于严格，把语义相关但 embedding 距离中等的 Cell 直接过滤。两路同时失效导致含精确答案的 Cell 被系统性漏掉。
    - 修复：`hybrid_search.py` 的 `keyword_scores()` 改为扫描 `raw_text`；`config.py` 中 `VECTOR_SCORE_THRESHOLD` 从 0.6 降至 0.3。全部 102 个测试通过。
  - **Phase 8.4（已完成）**：
    10. **benchmark 方法级并发优化**：`locomo_runner.py` 中单个 QA 的 baseline / sliding / session-mem 三种回答生成改为 `ThreadPoolExecutor(max_workers=3)` 并发执行。`retrieve_context()` 仍串行以保留检索延迟指标，Judge 在三个回答返回后串行。与 `--max_workers` session 级并发正交叠加，显著缩短 `--run_accuracy` 时的 benchmark 总耗时。
    - **代码变更**：`benchmarks/locomo_runner.py`（`_timed_answer` + `ThreadPoolExecutor`）、`tests/test_benchmark.py`。
    - **验证结果**：全部 102 个测试通过；black + ruff 通过。
  - **Phase 8.5（已完成）**：
    11. **LLM 回答指令优化（抑制过度解读）**：在 `locomo_runner.py` 的 `_answer()` 中统一注入 system 硬指令： `"Based only on the provided context, answer directly and concisely. Quote the relevant sentence explicitly. Do not infer or over-interpret."`。baseline / sliding / session-mem 三种回答生成均受该指令约束，无需修改 `PromptAssembler` 或 `WorkingMemory.to_prompt()` 接口。
    12. **内部 token 开销统计**：新增 `session_mem_internal_tokens` 字段，统计检索阶段 QueryRewriter prompt tokens + Embedding tokens，输出到 JSON 聚合结果与 `_report.txt`。
    - **代码变更**：`benchmarks/locomo_runner.py`、`benchmarks/metrics.py`、`tests/test_benchmark.py`。
    - **验证结果**：全部 102 个测试通过；black + ruff 通过；已提交 commit `5a33af9`。
  - **Phase 8.6（已完成，2026-04-16）**：
    13. **关键词检索升级为 BM25**：`hybrid_search.py` 的 `keyword_scores()` 从集合 Jaccard 替换为基于 session-level 动态 IDF 的 BM25。
    - **实现细节**：
      - 基于 `cells` 列表动态计算每个 token 的文档频率（DF）和 IDF（标准 BM25 平滑公式 `log((N - df + 0.5) / (df + 0.5) + 1)`）。
      - 统计每个 cell 的 term frequency（TF）和文档长度，计算 BM25 分数。
      - 保留 `entity_bonus` 作为后处理加权项。
    - **配置参数**：`RetrievalConfig.BM25_K1 = 1.5`，`RetrievalConfig.BM25_B = 0.75`。
    - **代码变更**：`src/session_mem/retrieval/hybrid_search.py`、`src/session_mem/config.py`、`tests/test_retrieval.py`。
    - **验证结果**：全部 104 个测试通过；black + ruff 通过。新增 `test_bm25_penalizes_common_words` 和 `test_bm25_length_normalization` 分别验证 IDF 对高频通用词的降权效果和长度归一化对长文档的抑制作用。
  - **Phase 8.7（已完成，2026-04-16）**：
    14. **时序信息闭环优化**：Cell 本身已有 `timestamp_start`/`timestamp_end`，但 `memory_system.py:retrieve_context()` 最终按 RRF 分数降序组装 `activated_cells`，`working_memory.py:to_prompt()` 也未把时间戳写入 Prompt。这导致：
      - LLM 无法判断 Cell 的先后顺序，叙事链被高分通用 Cell 前置打乱；
      - 用户问 "When did..." 时，LLM 看不到时间锚点，只能盲猜；
      - `gap_detected()` 和 Cell 生成阶段的时间戳信号在最后一步丢失，未能形成闭环。
    - **修复方案**：
      1. `memory_system.py`：筛选后的 `activated_cells` 改按 `timestamp_start` 升序排列（None 值放最后），恢复自然时间线；`linked_prev` 因果链也会按时间自然展开。
      2. `working_memory.py`：`to_prompt()` 给每个 Cell 的 `raw_text` 前加上 `[timestamp_start - timestamp_end]\n` 前缀，让 LLM 感知绝对时间。
      3. `locomo_runner.py`：`_answer()` 的 system 指令追加 `"If the question asks about time, dates, or when something happened, you must answer with the specific absolute timestamp or date explicitly."`。
    - **代码变更**：`src/session_mem/core/memory_system.py`、`src/session_mem/core/working_memory.py`、`benchmarks/locomo_runner.py`、`tests/test_retrieval.py`。
    - **验证结果**：全部 106 个测试通过；black + ruff 通过。新增 `test_retrieve_context_sorts_activated_cells_by_timestamp` 和 `test_working_memory_includes_timestamp_prefix` 分别验证时间升序排列和时间戳前缀注入。
  - **Phase 8.8（待执行，2026-04-16 新增）**：
    15. **benchmark latency 口径不一致问题**：v5 benchmark 结果分析时发现，`avg_session_mem_latency_ms`（2.76s）与 `avg_baseline_latency_ms`（12.3s）/`avg_sliding_latency_ms`（9.8s）**不是同一维度**。session-mem 仅测量 `retrieve_context()`（检索+组装），而 baseline/sliding 测量的是完整 LLM 回答生成时间。这导致 session-mem "看起来快很多"，但实际无法直接对比。
    - **修复方案**：
      1. `locomo_runner.py`：通过 streaming API 为三种方法统一采集 **TTFT**（Time To First Token）和完整生成时间。
      2. `metrics.py`：新增 `session_mem_total_latency_ms`（检索 + 生成），以及 `baseline_ttft_ms`、`sliding_ttft_ms`、`session_mem_ttft_ms`；聚合结果同步输出 avg/median/p95。
      3. 对于不支持 streaming 的 backend（如测试用的 `FakeLLMClient`），TTFT fallback 为 total latency，保证兼容性。
    - **预期收益**：消除 latency 指标的口径歧义，让三向延迟对比具备实际可比性；TTFT 作为用户感知的首字响应延迟，可验证 session-mem 的检索开销是否影响首字体验。

## Phase 9.1: 检索召回率修复——BM25 标点清洗 + RRF 权重调整 + Query 停用词过滤
- **Status:** in_progress
- **问题发现（v5 benchmark 深度分析，2026-04-17）**：
  - v5 结果（304 QA，2 sessions 全量）：Baseline Judge 0.549，Session-mem Judge 0.528，差距仅 0.021。但缺陷高度集中在 `when` 类问题（66 题）：Baseline 0.182，SM 仅 **0.076**。
  - 大量精确事实题（书名、地点、数字、时间）SM 回答「文中未提及」或「没有信息」，而 Baseline 能正确回答。在 134 个 SM 得零分的问题中，有 **35** 个 Baseline 得分 > 0，且 **24** 个 Baseline 满分但 SM 零分。
- **根因诊断**：
  1. **BM25 标点清洗缺失**：`hybrid_search.py:keyword_scores()` 中 `query.lower().split()` 得到的 token 含标点（如 `"birthday?"`），文档 token 也含标点（如 `"birthday."`）。BM25 的精确字符串匹配将它们视为不同词，导致 `"birthday"` 命中为 0。
  2. **RRF 权重偏向语义路**：当前 `vector_weight=0.75 / keyword_weight=0.25`，事实型问题更依赖字面匹配，BM25 路权重过低。
  3. **Query 停用词稀释密度**：`"How long ago was Caroline's 18th birthday?"` 被拆成 7 个 token，其中 `how/long/ago/was` 为停用词，有效关键词仅剩 `caroline's` 和 `birthday?`，且后者还因标点 bug 失效。
  4. **`MEMORY_SYSTEM_THRESHOLD` 截断过严**：`0.015` 的 threshold 对 keyword-only hit（BM25 排 7+ 名但向量路未进前 5）几乎全灭，导致包含精确答案但语义偏离的 cell（如 "hand-painted bowl" 对 "birthday" 查询）被系统性丢弃。
- **修复方案**：
  1. **BM25 标点清洗**：对 query token 和文档 token 统一执行 `re.sub(r'[^\w\s]', '', token)`。
  2. **提升 keyword_weight**：从 `0.25` 提升至 `0.4`，让事实型问题的字面匹配获得更大话语权。
  3. **停用词过滤**：定义中英文停用词集合，在 BM25 计算前从 query tokens 中移除。
  4. **降低 `MEMORY_SYSTEM_THRESHOLD`**：从 `0.015` 降至 `0.008`。
- **预期收益**：修复后 `when` 类和精确事实题的召回率应显著提升，缩小与 baseline 在 0.021 差距上的最后一块短板。

## Resources
- 项目仓库：https://github.com/ZhangSongqi0506/session-mem
- 技术方案文档：`单会话级临时记忆系统（Session-scoped Working Memory）技术方案.md`
- LoCoMo 数据集：用于长对话记忆系统评测的公开数据集
- 开发规划：`task_plan.md`
- 进度日志：`progress.md`

## Visual/Browser Findings
-

---
*Update this file after every 2 view/browser/search operations or after major technical decisions*
