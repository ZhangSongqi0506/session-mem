# session-mem

> Session-scoped Working Memory for LLMs

单会话级临时记忆系统，低成本、低延迟的会话历史压缩与检索方案。

## 核心特性

- **三层缓冲架构**：SenMemBuffer（零压缩保真）→ ShortMemBuffer（摘要索引）→ Working Memory（按需组装）
- **语义驱动 Cell**：基于话题转折自动切分，生成结构化记忆单元
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
| Phase 1 | 已完成 | 项目脚手架与核心接口设计 |
| Phase 2 | 已完成 | 存储层完善与数据库构建（SQLite + sqlite-vec，5 张表，1024 维向量） |
| Phase 3 | 已完成 | SenMemBuffer 实现与语义边界检测（28 个测试全部通过） |
| Phase 4 | 进行中 | Cell 生成、Meta Cell 与 ShortMemBuffer |
| Phase 5 | 待开始 | 检索策略与 Working Memory |
| Phase 6 | 待开始 | 边界情况与异常处理 |
| Phase 7 | 待开始 | LoCoMo 验证与测试 |

## 技术栈

- Python 3.11+
- SQLite + sqlite-vec（向量索引）
- qwen2.5:72b（语义边界检测 + Cell 生成）
- OpenAI 兼容接口

## 验证方法

基于 LoCoMo 数据集，将同一 conversation 的多个 session 拼接为单一连续会话，评估 Token 节省率与回答准确率。
