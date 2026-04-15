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

## Phase 8 Framework
- **目标**：解决服务器端到端跑测中发现的实际运行问题，进行代码性能优化与健壮性增强
- **待填充项**：
  1. LoCoMo 跑测后记录的具体问题列表
  2. 性能瓶颈分析与优化方案
  3. 参数调优记录（边界检测阈值、检索融合权重、Meta Cell 触发策略等）
  4. 回归测试清单

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
