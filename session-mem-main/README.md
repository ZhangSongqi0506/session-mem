# session-mem

> Session-scoped Working Memory for LLMs

单会话级临时记忆系统，低成本、低延迟的会话历史压缩与检索方案。

## 核心特性

- **三层缓冲架构**：SenMemBuffer（零压缩保真）→ ShortMemBuffer（摘要索引）→ Working Memory（按需组装）
- **语义驱动 Cell**：基于话题转折自动切分，生成结构化记忆单元；支持一次检测产出多个 Cell
- **会话主旨单元（Meta Cell）**：全局摘要常驻于 Working Memory 最前端，防止长会话主旨丢失
- **双路召回**：向量相似度 + 关键词桥接，解决短查询与长摘要的语义不对齐
- **模块化存储**：默认 SQLite + sqlite-vec，单文件零运维，支持后端切换
- **多形态集成**：Skill / MCP Tool / LangChain Memory 组件化目标

## 快速开始

```bash
# 1. 创建虚拟环境（Python 3.11）
uv venv --python 3.11

# 2. 安装依赖（含开发依赖）
uv pip install -e ".[dev]"

# 3. 运行测试
uv run pytest tests/ -v

# 4. 代码格式化与检查
uv run black src/ tests/
uv run ruff check src/ tests/
```

## 环境配置

创建 `.env` 文件并配置以下变量：

```bash
# 主 LLM（qwen2.5:72b）
SESSION_MEM_API_LLM_PROVIDER=openai
SESSION_MEM_API_LLM_MODEL=qwen2.5:72b-instruct-nq
SESSION_MEM_API_LLM_BASE_URL=http://172.10.10.200/v1
SESSION_MEM_API_LLM_API_KEY=sk-...

# Embedding 服务（Xinference + bge-large-en-v1.5）
SESSION_MEM_API_EMBEDDINGS_PROVIDER=openai
SESSION_MEM_API_EMBEDDINGS_LOCAL_MODEL=bge-large-en-v1.5
SESSION_MEM_API_EMBEDDINGS_BASE_URL=http://localhost:8001/v1
```

## 项目结构

```
session-mem-main/
├── src/session_mem/          # 核心包
│   ├── core/                 # MemorySystem、Buffer、Cell、MetaCellGenerator
│   ├── llm/                  # LLM 客户端、Prompt、解析器
│   ├── storage/              # 存储抽象与 SQLite 实现
│   ├── retrieval/            # 查询重写、双路召回
│   ├── integrations/         # LangChain / MCP 适配（预留）
│   └── utils/                # 工具函数
├── tests/                    # 单元测试与集成测试
├── benchmarks/               # LoCoMo 评估脚本
│   ├── data/                 # 数据集
│   ├── results/              # 评估结果
│   ├── locomo_runner.py
│   ├── data_loader.py
│   ├── metrics.py
│   └── prompt_assembler.py
└── pyproject.toml            # 项目配置
```

## 开发进度

| Phase | 状态 | 说明 |
|-------|------|------|
| Phase 1 | 已完成 | 项目脚手架与核心接口设计 |
| Phase 2 | 已完成 | 存储层完善与数据库构建（SQLite + sqlite-vec，5 张表，1024 维向量） |
| Phase 3 | 已完成 | SenMemBuffer 实现与语义边界检测（gap / hard limit / soft limit） |
| Phase 4 | 已完成 | Cell 生成、Meta Cell 与 ShortMemBuffer |
| Phase 4.1 | 已完成 | 多切分点语义边界检测落地：LLM 返回切分点索引列表，支持一次检测生成多个 Cell |
| Phase 5 | 已完成 | 检索策略与 Working Memory（查询重写、Hybrid Search、Meta Cell 前置） |
| Phase 6 | 已完成 | 边界情况与异常处理（linked_prev 因果链、实体共现激活） |
| Phase 7 | 已完成 | LoCoMo 验证与测试（benchmark 已适配真实数据，支持全量/滑窗/session-mem 三向对比） |
| Phase 8 | 已完成 | 运行优化与问题修复（服务器跑测问题全部修复，核心指标达标） |
| Phase 8.1 | 已完成 | 热区构建修复 + benchmark 保留原始 speaker 名称 |
| Phase 8.2 / 8.2.1 | 已完成 | 检索策略重构为双路独立召回 + RRF 融合，参数配置化，取消总预算硬截断 |
| Phase 8.4 | 已完成 | benchmark 方法级并发优化（per-QA 三回答并发） |
| Phase 8.5 | 已完成 | LLM 回答指令优化（抑制过度解读）+ 内部 token 开销统计 |
| Phase 8.6 | 已完成 | 关键词检索升级为 BM25（k1=1.5, b=0.75，session-level 动态 IDF） |
| Phase 8.7 | 已完成 | 时序信息闭环优化（activated_cells 按 timestamp_start 升序 + 时间戳注入 Prompt） |

## 技术栈

- Python 3.11+
- SQLite + sqlite-vec（向量索引）
- qwen2.5:72b（语义边界检测 + Cell 生成）
- OpenAI 兼容接口

## LoCoMo 评估复现

1. 将 LoCoMo 数据文件放入 `benchmarks/data/`
2. 运行评估脚本（仅 Token 节省率 + 延迟，三向对比）：
   ```bash
   python benchmarks/locomo_runner.py \
     --data_path benchmarks/data/locomo10.json \
     --max_sessions 10 \
     --sliding_window 10 \
     --output benchmarks/results/locomo_results.json
   ```
3. 运行完整评估（含 LLM 回答 + Judge 评分）：
   ```bash
   python benchmarks/locomo_runner.py \
     --data_path benchmarks/data/locomo10.json \
     --max_sessions 10 \
     --sliding_window 10 \
     --run_accuracy \
     --output benchmarks/results/locomo_results.json
   ```
4. 结果文件 `benchmarks/results/locomo_results.json` 包含：
   - `avg_token_saving_rate_vs_baseline`：相比**全量历史**的平均 Token 节省率
   - `avg_token_saving_rate_vs_sliding`：相比**滑窗基线**的平均 Token 节省率
   - `avg_session_mem_latency_ms` / `median_session_mem_latency_ms` / `p95_session_mem_latency_ms`：session-mem 检索延迟
   - `avg_baseline_judge_score` / `avg_sliding_judge_score` / `avg_session_mem_judge_score`：三种方式各自 vs ground_truth 的独立 Judge 平均分
   - `avg_judge_score_vs_baseline` / `avg_judge_score_vs_sliding`：session-mem 与 baseline/sliding 的交叉对比 Judge 分
   - `session_mem_meta_cell_tokens` / `session_mem_hot_zone_tokens` / `session_mem_internal_tokens`：session-mem 内部 Token 构成拆解

### 服务器跑测注意事项
- **LLM `response_format` 兼容性**：部分 vLLM/OneAPI 代理不支持 `json_schema` 类型的 `response_format`。`QwenClient` 默认 `supports_json_schema=False`，会自动跳过该参数，完全依赖 Prompt 约束 + `parser.py` fallback 解析 JSON。若你的后端明确支持 `json_schema`，可在初始化 `QwenClient` 时显式设置 `supports_json_schema=True`。
- **Judge API 配置**：默认 Judge endpoint 为 `https://api2.aigcbest.top/v1`（`gpt-4o-mini`）。若服务器端无法访问该地址，可通过 `--judge_base_url` 指向其他可到达的 OpenAI 兼容接口（如内网 LLM），或使用 `--skip_judge` 在 `--run_accuracy` 模式下仅生成回答、不做 Judge 评分。

## 最新更新

- **2026-04-14** 完成 Phase 4.1：语义边界检测支持**多切分点索引**（`[3, 6]`），一次检测可生成多个 Cell。全部 54 个测试通过。
- **2026-04-15 上午** 完成 Phase 5-6：检索策略与边界异常处理全部落地，83 个测试通过。
- **2026-04-15 下午** 完成 Phase 7 benchmark 开发：适配真实 `locomo10.json` 数据结构，将多个 session 合并为单一长会话，支持**全量历史/滑窗/session-mem 三向对比**（Token 数、延迟、准确率）。新增 `tests/test_benchmark.py`（11 个测试），全部 94 个测试通过。
- **2026-04-16** 完成 Phase 8 全部优化：
  - Phase 8.1-8.2.1：修复服务器跑测发现的语义边界检测 role 失真、检索召回失败、检索策略重构为双路独立召回 + RRF 融合。
  - Phase 8.4：benchmark 方法级并发优化，单个 QA 内三种回答生成并行化。
  - Phase 8.5：统一注入 system 硬指令抑制 LLM 过度解读，新增内部 token 开销统计。
  - Phase 8.6：关键词检索升级为 **BM25**（k1=1.5, b=0.75），解决通用词虚高和长 Cell 占便宜问题。
  - Phase 8.7：**时序信息闭环优化**，`activated_cells` 按 `timestamp_start` 升序排列，`to_prompt()` 注入 `[timestamp_start - timestamp_end]` 时间戳前缀。
  - 全部 **106 个测试通过**，`black` + `ruff` 通过，核心指标达标（Token 节省率 >60%）。
