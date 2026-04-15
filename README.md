# session-mem

> Session-scoped Working Memory for LLMs

单会话级临时记忆系统，低成本、低延迟的会话历史压缩与检索方案。

## 项目结构

```
session-mem/
├── session-mem-main/         # 核心代码仓库
│   ├── src/session_mem/      # Python 核心包
│   ├── tests/                # 单元测试与集成测试
│   ├── pyproject.toml        # 项目配置与依赖
│   └── README.md             # 核心代码详细说明
├── AGENTS.md                 # 项目开发规范与 AI Agent 指南
├── task_plan.md              # 开发计划与阶段追踪
├── findings.md               # 技术发现与决策记录
├── progress.md               # 会话日志与进度记录
└── .gitignore
```

## 快速入口

- **核心代码说明** → [session-mem-main/README.md](session-mem-main/README.md)
- **开发规范** → [AGENTS.md](AGENTS.md)
- **当前进度** → [task_plan.md](task_plan.md)
- **技术决策** → [findings.md](findings.md)

## 当前状态

- **Phase 1-6 已完成**：核心接口、存储层、语义边界检测、Cell 生成、Meta Cell、检索策略、边界异常处理已全部落地，83 个测试通过
- **Phase 7 进行中**：LoCoMo benchmark 脚本已就绪，待数据集跑测验证 Token 节省率、准确率与延迟

## 技术栈

- Python 3.11+
- SQLite + sqlite-vec（向量索引）
- qwen2.5:72b（语义边界检测 + Cell 生成）
- bge-large-en-v1.5（1024 维 Embedding）

## 仓库地址

https://github.com/ZhangSongqi0506/session-mem
