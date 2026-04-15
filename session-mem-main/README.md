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
└── pyproject.toml            # 项目配置
```

## 开发进度

| Phase | 状态 | 说明 |
|-------|------|------|
| Phase | 状态 | 说明 |
|-------|------|------|
| Phase 1 | 已完成 | 项目脚手架与核心接口设计 |
| Phase 2 | 已完成 | 存储层完善与数据库构建（SQLite + sqlite-vec，5 张表，1024 维向量） |
| Phase 3 | 已完成 | SenMemBuffer 实现与语义边界检测（gap / hard limit / soft limit） |
| Phase 4 | 已完成 | Cell 生成、Meta Cell 与 ShortMemBuffer（44 个测试通过） |
| Phase 4.1 | 已完成 | 多切分点语义边界检测落地：LLM 返回切分点索引列表，支持一次检测生成多个 Cell |
| Phase 5 | 已完成 | 检索策略与 Working Memory（查询重写、Hybrid Search、Meta Cell 前置） |
| Phase 6 | 已完成 | 边界情况与异常处理（linked_prev 因果链、实体共现激活） |
| Phase 7 | 进行中 | LoCoMo 验证与测试（benchmark 脚本已就绪，待数据集跑测） |

## 技术栈

- Python 3.11+
- SQLite + sqlite-vec（向量索引）
- qwen2.5:72b（语义边界检测 + Cell 生成）
- OpenAI 兼容接口

## LoCoMo 评估复现

1. 将 LoCoMo 数据文件放入 `benchmarks/data/`
2. 运行评估脚本（仅 Token 节省率 + 延迟）：
   ```bash
   python benchmarks/locomo_runner.py \
     --data_path benchmarks/data/locomo_sessions.jsonl \
     --max_sessions 50 \
     --output benchmarks/results/locomo_results.json
   ```
3. 运行完整评估（含 LLM 回答 + Judge 评分）：
   ```bash
   python benchmarks/locomo_runner.py \
     --data_path benchmarks/data/locomo_sessions.jsonl \
     --max_sessions 50 \
     --run_accuracy \
     --output benchmarks/results/locomo_results.json
   ```
4. 结果文件 `benchmarks/results/locomo_results.json` 包含：
   - `avg_token_saving_rate`：平均 Token 节省率
   - `avg_retrieve_latency_ms`：平均检索延迟
   - `median_retrieve_latency_ms` / `p95_retrieve_latency_ms`：延迟分位值
   - `avg_judge_score`：LLM-as-Judge 平均评分（启用 `--run_accuracy` 时）

## 最新更新

- **2026-04-14** 完成 Phase 4.1：语义边界检测支持**多切分点索引**（`[3, 6]`），一次检测可生成多个 Cell。全部 54 个测试通过。
- **2026-04-15** 完成 Phase 5-6：检索策略（Hybrid Search + Query Rewriter + Working Memory 组装）与边界异常处理（linked_prev 因果链、实体共现激活）全部落地，66 个测试通过。Phase 7 benchmark 脚本（`locomo_runner.py`、`data_loader.py`、`metrics.py`、`prompt_assembler.py`）已完成开发，待数据集验证。
