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
uv pip install -e .
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
├── tests/                    # 测试（待补充）
└── pyproject.toml            # 项目配置
```

## 技术栈

- Python 3.11+
- SQLite + sqlite-vec（向量索引）
- qwen2.5:72b（语义边界检测 + Cell 生成）
- OpenAI 兼容接口

## 验证方法

基于 LoCoMo 数据集，将同一 conversation 的多个 session 拼接为单一连续会话，评估 Token 节省率与回答准确率。
