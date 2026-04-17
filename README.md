# session-mem

> Session-scoped Working Memory for LLMs

**session-mem** 是一个面向大语言模型的**单会话级临时记忆系统**，通过语义驱动的历史压缩与智能检索，在保证回答准确率的前提下，将输入 Token 降低 **40%-60%**，首字响应延迟（TTFT）控制在毫秒级。

---

## 目录

1. [项目背景](#项目背景)
2. [核心架构](#核心架构)
3. [核心特性](#核心特性)
4. [性能指标](#性能指标)
5. [快速开始](#快速开始)
6. [环境配置](#环境配置)
7. [项目结构](#项目结构)
8. [LoCoMo 评估复现](#locomo-评估复现)
9. [开发文档](#开发文档)
10. [仓库地址](#仓库地址)
11. [更新日志](#更新日志)

---

## 项目背景

在典型的 LLM 对话应用中，随着会话轮次增加，全量历史 Prompt 的 Token 消耗呈线性增长，导致：

- **成本激增**：API 调用费用随历史长度线性上升
- **延迟恶化**：长上下文导致首字响应（TTFT）显著变慢
- **注意力稀释**：关键信息淹没在海量历史轮次中，模型回答质量下降

现有解决方案（如简单滑窗截断）虽能降低 Token，但会粗暴丢弃早期关键信息，导致准确率大幅下降。`session-mem` 旨在提供一种**低成本、低延迟、高保真**的替代方案：通过语义边界自动切分对话历史，生成结构化记忆单元（Cell），在回答查询时仅召回最相关的记忆片段 + 热区 + 全局摘要。

---

## 核心架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        Working Memory                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │  Meta Cell  │  │  Hot Zone   │  │   Activated Cells       │  │
│  │  (全局摘要)  │  │ (最新对话)  │  │  (检索召回的相关 Cell)   │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              ▲
                              │ retrieve_context(query)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Retrieval Layer                          │
│   QueryRewriter ──► HybridSearcher (向量 + BM25 双路召回 + RRF) │
│                      ▲                                          │
│                      │ Entity Expansion (BM25 扩展)             │
└─────────────────────────────────────────────────────────────────┘
                              ▲
                              │ list_by_session / search
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        ShortMemBuffer                           │
│              当前会话的全部 Cell 摘要索引库                        │
│         (Meta Cell + 语义边界切分生成的 Memory Cells)             │
└─────────────────────────────────────────────────────────────────┘
                              ▲
                              │ add_turn() 触发 Cell 生成
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         SenMemBuffer                            │
│              零压缩原始对话轮次缓冲区（弹性窗口）                   │
│        512 tokens 软上限触发语义边界检测 | 2048 tokens 硬上限      │
│        30 分钟时间间隔触发强制切分                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 三层缓冲设计

| 层级 | 组件 | 作用 | 压缩策略 |
|------|------|------|----------|
| L0 | **SenMemBuffer** | 接收最新对话轮次，零压缩保真 | 无压缩，2048 tokens 硬上限触发切分 |
| L1 | **ShortMemBuffer** | 存储历史 Cell 的元数据与向量索引 | 语义摘要压缩，原始文本按需回溯 |
| L2 | **Working Memory** | 回答当前查询时的最终 Prompt 组装 | 仅注入 Meta Cell + 热区 + 激活 Cell |

### 语义驱动 Cell

当 SenMemBuffer 达到软上限（512 tokens 的整数倍）时，系统调用 `qwen2.5:72b` 进行**语义边界检测**，分析当前 Buffer 内是否存在话题转折。若存在，则按切分点将对话切分为多个 **MemoryCell**。

每个 Cell 包含四层信息：
- **检索层**：`summary`、`keywords`、`entities`（用于召回）
- **回溯层**：`raw_text`、`token_count`（命中后全量注入 Prompt）
- **元信息层**：`cell_type`（fact / constraint / preference / task / fragmented）、`confidence`、`timestamp_start/end`
- **关系层**：`linked_prev`（时序链接）、`causal_deps`（因果依赖，预留）

> **命中即全量**：检索阶段只使用轻量元数据，一旦命中，该 Cell 的完整原文将无损注入 Working Memory，避免压缩导致的关键约束丢失。

### 会话主旨单元（Meta Cell）

长会话中，早期 Cell 往往包含会话的核心目标（如"帮我规划一次旅行"），但检索系统可能因查询具体化（如"酒店订好了吗？"）而无法召回这些高层信息。Meta Cell 是一个**强制常驻**于 Working Memory 最前端的会话级全局摘要单元，以约 400-1000 tokens 的代价，确保 LLM 始终掌握对话主旨。

Meta Cell 的更新策略：
- 首个普通 Cell 生成后，创建初始 Meta Cell
- 后续每次语义切分产生新 Cell 时，将新 Cell 的原文与当前 Meta Cell 摘要一并传给 LLM，做**增量融合更新**
- 旧版本标记为 `archived`，新版本标记为 `active`

### 检索策略：双路独立召回 + RRF 融合

为解决"短查询与长摘要语义空间不对齐"的问题，检索层采用：

1. **向量路**：基于 `bge-large-en-v1.5`（1024 维）的向量相似度搜索，覆盖语义泛化
2. **关键词路**：基于 session-level 动态 **BM25** 的字面匹配，覆盖精确事实型查询
   - 动态计算 IDF：`log((N - df + 0.5) / (df + 0.5) + 1)`
   - 参数：`k1=1.5`，`b=0.75`
   - 支持中英文停用词过滤、标点清洗
3. **RRF 融合**：两路各自召回后，使用 Reciprocal Rank Fusion 融合排名
4. **实体扩展**：将已激活 Cell 的 entities 拼接为扩展查询，对未入选候选做 BM25 评分，取 top 3 补充召回

激活 Cell 的最终排序按 `timestamp_start` 升序排列，恢复自然时间线，避免高分通用 Cell 前置打乱叙事顺序。

---

## 核心特性

- **语义驱动切分**：基于 LLM 的话题转折检测，支持一次检测返回多个切分点
- **强制时间戳保真**：ISO 8601 UTC 时间戳贯穿 Cell 生成、存储、检索、Prompt 注入全流程
- **低延迟设计**：检索链路（查询重写 + 向量搜索 + BM25 + RRF + 组装）端到端 < 200ms
- **参数配置化**：RRF k 值、BM25 参数、向量阈值、召回上下限全部集中在 `config.py`，便于实验调参
- **完整 Benchmark 流水线**：基于 LoCoMo 数据集的真实评估，支持全量历史 / 滑窗 / session-mem 三向对比
- **高测试覆盖**：109+ 单元测试覆盖核心模块，black + ruff 代码规范全通过

---

## 性能指标

基于 LoCoMo 数据集的长对话拼接评估（304 QA）：

| 指标 | 目标 | 当前状态 |
|------|------|----------|
| **Token 节省率 vs baseline** | >= 40% | **~64%** |
| **准确率差距 vs baseline** | < 0.05 | 持续优化中（Phase 9.4 修复 threshold 过低问题） |
| **检索延迟（P95）** | < 200ms | **< 150ms** |
| **平均激活 Cell 数** | 动态 2-8 个 | 优化中 |
| **Meta Cell 平均 Token** | 300-1000 | **~400-600** |

> 注：Baseline 为"全量历史 Prompt"，Sliding 为"最近 N 轮滑窗 Prompt"。

---

## 快速开始

### 1. 克隆仓库

```bash
git clone http://172.10.10.244:8001/zhangsongqi/session-mem.git
cd session-mem/session-mem-main
```

### 2. 创建虚拟环境并安装依赖

本项目使用 `uv` 进行包管理（推荐），也可使用 `pip`：

```bash
# 使用 uv（推荐）
uv venv --python 3.11
uv pip install -e ".[dev]"

# 或使用 pip
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### 3. 运行测试

```bash
uv run pytest tests/ -v
```

当前全部 **109+** 个测试通过。

### 4. 代码格式化

```bash
uv run black src/ tests/
uv run ruff check src/ tests/
```

---

## 环境配置

在 `session-mem-main/` 目录下创建 `.env` 文件：

```bash
# 主 LLM（语义边界检测 + Cell 生成）
SESSION_MEM_API_LLM_PROVIDER=openai
SESSION_MEM_API_LLM_MODEL=qwen2.5:72b-instruct-nq
SESSION_MEM_API_LLM_BASE_URL=http://172.10.10.200/v1
SESSION_MEM_API_LLM_API_KEY=sk-...

# Embedding 服务（Xinference 本地部署）
SESSION_MEM_API_EMBEDDINGS_PROVIDER=openai
SESSION_MEM_API_EMBEDDINGS_LOCAL_MODEL=bge-large-en-v1.5
SESSION_MEM_API_EMBEDDINGS_BASE_URL=http://localhost:8001/v1
```

> 若后端 LLM 不支持 `json_schema` 类型的 `response_format`（如部分 vLLM/OneAPI 代理），`QwenClient` 默认 `supports_json_schema=False`，会自动跳过该参数，完全依赖 Prompt + JSON fallback 解析。

---

## 项目结构

```
session-mem/                         # 项目根目录（开发规划 + 规范文档）
├── session-mem-main/                # 核心代码仓库
│   ├── src/session_mem/             # Python 核心包
│   │   ├── core/                    # MemorySystem、Buffer、Cell、MetaCellGenerator
│   │   ├── llm/                     # LLM 客户端、Prompt 模板、JSON 解析器
│   │   ├── storage/                 # 存储抽象（VectorIndex / CellStore / TextStore）
│   │   │   └── sqlite_backend.py    # SQLite + sqlite-vec 默认实现
│   │   ├── retrieval/               # 查询重写、Hybrid Search（向量 + BM25 + RRF）
│   │   ├── integrations/            # LangChain / MCP 适配（预留）
│   │   └── utils/                   # TokenEstimator 等工具
│   ├── tests/                       # 单元测试与集成测试
│   ├── benchmarks/                  # LoCoMo 评估脚本
│   │   ├── data/                    # 数据集目录
│   │   ├── results/                 # 评估结果输出
│   │   ├── locomo_runner.py         # 主评估脚本
│   │   ├── data_loader.py           # 数据加载与预处理
│   │   ├── metrics.py               # 聚合指标与 LLM-as-Judge
│   │   └── prompt_assembler.py      # Prompt 组装器
│   ├── pyproject.toml               # 项目配置与依赖
│   └── README.md                    # 核心代码详细说明
├── AGENTS.md                        # 项目开发规范与 AI Agent 指南
├── task_plan.md                     # 开发计划与阶段追踪
├── findings.md                      # 技术发现与决策记录
├── progress.md                      # 会话日志与进度记录
└── README.md                        # 本文件
```

---

## LoCoMo 评估复现

### 准备数据

将 LoCoMo 数据文件（如 `locomo10.json`）放入 `session-mem-main/benchmarks/data/`。

### 快速评估（仅 Token 与延迟）

```bash
cd session-mem-main
python benchmarks/locomo_runner.py \
  --data_path benchmarks/data/locomo10.json \
  --max_sessions 10 \
  --sliding_window 10 \
  --output benchmarks/results/locomo_results.json
```

### 完整评估（含回答生成 + Judge 评分）

```bash
python benchmarks/locomo_runner.py \
  --data_path benchmarks/data/locomo10.json \
  --max_sessions 10 \
  --sliding_window 10 \
  --run_accuracy \
  --output benchmarks/results/locomo_results.json
```

### 并发加速

```bash
python benchmarks/locomo_runner.py \
  --data_path benchmarks/data/locomo10.json \
  --max_sessions 10 \
  --sliding_window 10 \
  --run_accuracy \
  --max_workers 4 \
  --output benchmarks/results/locomo_results.json
```

### 输出指标说明

评估结果 JSON 与 `_report.txt` 包含：

- `avg_token_saving_rate_vs_baseline`：相比全量历史的 Token 节省率
- `avg_session_mem_latency_ms` / `avg_session_mem_total_latency_ms`：检索延迟 / 总延迟
- `avg_baseline_ttft_ms` / `avg_sliding_ttft_ms` / `avg_session_mem_ttft_ms`：三种方式的 TTFT
- `avg_baseline_judge_score` / `avg_sliding_judge_score` / `avg_session_mem_judge_score`：各自 vs ground_truth 的 Judge 平均分
- `session_mem_meta_cell_tokens` / `session_mem_hot_zone_tokens` / `session_mem_activated_cell_tokens`：内部 Token 拆解
- `session_mem_internal_tokens`：检索阶段内部开销（QueryRewriter + Embedding）

---

## 开发文档

| 文档 | 说明 |
|------|------|
| [AGENTS.md](AGENTS.md) | 项目开发规范、子代理使用规则、Git 工作流、LLM 配置 |
| [task_plan.md](task_plan.md) | 完整开发计划与 Phase 状态追踪 |
| [findings.md](findings.md) | 技术选型、架构设计、问题根因分析与修复记录 |
| [progress.md](progress.md) | 每次会话的详细进度日志与 benchmark 结果分析 |
| [session-mem-main/README.md](session-mem-main/README.md) | 核心代码层面的快速参考与模块说明 |

---

## 仓库地址

- **GitLab**: `http://172.10.10.244:8001/zhangsongqi/session-mem`
- **GitHub**（镜像）: `https://github.com/ZhangSongqi0506/session-mem`

---

## 更新日志

### 2026-04-17
- **Phase 9.4.1**: 修复 LoCoMo 日期格式 `April 20, 2026` / `Apr 20` 解析，data_loader 统一 fallback 为 `datetime.now(UTC)`
- **fix**: 增加 `cell_type` 白名单校验（fact / constraint / preference / task / fragmented），防止 LLM 返回非法类型导致 SQLite CHECK constraint 失败

### 2026-04-16
- **Phase 9.4**: 提高 `MEMORY_SYSTEM_THRESHOLD`（4K → 8K tokens），修复 `data_loader` 时间戳 fallback 为空字符串问题
- **Phase 8.7**: 时序信息闭环优化，`activated_cells` 按 `timestamp_start` 升序排列，注入 `[timestamp_start - timestamp_end]` 时间戳前缀
- **Phase 8.6**: 关键词检索升级为 BM25（`k1=1.5, b=0.75`），解决通用词虚高和长 Cell 占便宜问题
- **Phase 8.5**: LLM 回答指令优化（注入 system 硬指令抑制过度解读）+ 内部 Token 开销统计
- **Phase 8.4**: benchmark 方法级并发优化（单个 QA 内三回答生成并行化）
- **Phase 8.2 / 8.2.1**: 检索策略重构为双路独立召回 + RRF 融合，参数配置化，取消总预算硬截断
- **Phase 8.1**: 热区构建修复 + benchmark 保留原始 speaker 名称
- 全部 **106+ 单元测试通过**，核心指标达标

### 2026-04-15
- **Phase 7**: LoCoMo benchmark 三向对比（全量历史 / 滑窗 / session-mem）开发完成，新增 `tests/test_benchmark.py`
- **Phase 5-6**: 检索策略（查询重写、Hybrid Search、Meta Cell 前置）与边界异常处理（`linked_prev` 因果链、实体共现激活）落地

### 2026-04-14
- **Phase 4.1**: 语义边界检测支持多切分点索引（`[3, 6]`），一次检测可生成多个 Cell
- **Phase 1-4**: 核心接口、存储层（SQLite + sqlite-vec）、SenMemBuffer、Cell 生成、Meta Cell 全部落地
