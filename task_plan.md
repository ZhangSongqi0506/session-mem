# Task Plan: session-mem 开发规划
<!-- 
  WHAT: session-mem 单会话级临时记忆系统的实现路线图
  WHY: 将技术方案转化为可执行、可追踪的开发任务
-->

## Goal
实现 `session-mem` 单会话级临时记忆系统的 MVP，包含三层缓冲架构、Cell 生成与检索、时间戳解析，并通过 LoCoMo 拼接会话完成基准验证。

## Current Phase
Phase 1

## Phases

### Phase 1: 项目脚手架与核心接口设计
- [x] 初始化代码仓库结构（Python 包）
- [x] 定义核心抽象接口：`MemorySystem`、`Buffer`、`MemoryCell`
- [x] 确定技术栈（sqlite-vec、qwen2.5:72b、OpenAI 兼容接口）
- [x] 设计对外 API（`add_turn()`, `retrieve_context()`）
- **Status:** complete

### Phase 2: SenMemBuffer 实现
- [ ] 实现原始对话的累积与 Token 估算
- [ ] 时间戳注入（ISO 8601 UTC）与间隔检测（30 分钟阈值）
- [ ] 语义边界检测集成（1.5B 小模型 / 规则 fallback）
- [ ] 强制切分与滑动保留逻辑
- **Status:** pending

### Phase 3: Cell 生成与 ShortMemBuffer
- [ ] 设计 Cell 生成的 LLM Prompt（单轮 JSON 输出）
- [ ] 实现 Cell 四层信息提取与解析 fallback
- [ ] 实现 ShortMemBuffer 的双窗口机制（活跃窗口 512 / 存储窗口 2048）
- [ ] 向量索引构建与关键词索引
- **Status:** pending

### Phase 4: 检索策略与 Working Memory
- [ ] 实现查询重写（指代消解 + 伪文档扩展）
- [ ] 实现向量相似度 + 关键词桥接的双路召回
- [ ] 实现 Working Memory 组装（热区 + 命中 Cell 原文 + 查询）
- [ ] 低置信度 Fallback（扩展存储窗口、BM25、RRF）
- **Status:** pending

### Phase 5: 边界情况与异常处理
- [ ] 超长会话分级存储与归档策略
- [ ] 因果链断裂的 `linked_prev` 追踪与实体共现激活
- [ ] 检索失败零回溯模式与新话题检测
- [ ] Cell 生成失败 / 向量服务不可用的降级策略
- **Status:** pending

### Phase 6: 验证与测试
- [ ] 基于 LoCoMo 数据集的 session 拼接与测试流水线
- [ ] Token 节省率测算（对比全量历史 vs session-mem）
- [ ] 准确率评估（任务完成率 / 语义相似度）
- [ ] 整理测试报告并更新 README
- **Status:** pending

## Key Questions
1. ✅ 选择 Python 还是 Node.js 作为主要实现语言？ → **Python**
2. ✅ 1.5B 语义边界检测模型是本地部署还是调用 API？ → **内网 qwen2.5:72b，独立新会话调用**
3. 向量索引使用 sqlite-vec（默认）还是 FAISS？ → **sqlite-vec**
4. ✅ Cell 生成 LLM 使用哪个模型？ → **内网 qwen2.5:72b**
5. 项目结构如何组织（src/ 还是扁平模块）？

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

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| git push 失败：src refspec main does not match any | 1 | 先执行 `git add` + `git commit` 创建初始提交后再 push |

## Notes
- 先完成 Phase 1 接口设计，再并行开发 Phase 2 和 Phase 3
- 每完成一个 Phase，更新 progress.md 并同步修改本文件状态
