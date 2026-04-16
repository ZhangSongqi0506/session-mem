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

- **Phase 1-7 已完成**：核心接口、存储层、语义边界检测、Cell 生成、Meta Cell、检索策略、边界异常处理、LoCoMo benchmark 三向对比已全部落地
- **Phase 8 已完成**：运行优化与问题修复全部完成，核心指标（Token 节省率 vs baseline >60%，准确率差距 <0.05）已达标
  - Phase 8.1：热区构建修复 + 数据角色映射保留原始 speaker
  - Phase 8.2 / 8.2.1：检索策略重构为双路独立召回 + RRF 融合，参数配置化
  - Phase 8.4：benchmark 方法级并发优化
  - Phase 8.5：LLM 回答指令优化（抑制过度解读）+ 内部 token 开销统计
  - Phase 8.6：关键词检索升级为 BM25（k1=1.5, b=0.75）
  - Phase 8.7：时序信息闭环优化（activated_cells 按时间排序 + 时间戳注入 Prompt）
- **全部 106 个单元测试通过**

## 技术栈

- Python 3.11+
- SQLite + sqlite-vec（向量索引）
- qwen2.5:72b（语义边界检测 + Cell 生成）
- bge-large-en-v1.5（1024 维 Embedding）

## 仓库地址

https://github.com/ZhangSongqi0506/session-mem
