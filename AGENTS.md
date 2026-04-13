# AGENTS.md - session-mem 项目开发规范

> session-mem: Session-scoped Working Memory  
> 单会话级临时记忆系统，低成本低延迟的会话历史压缩与检索方案

---

## 1. 概述

本文档定义了 AI Agent 参与 session-mem 项目开发的规范和最佳实践，包括：
- session-mem 项目特定信息
- 代码开发规范
- 子代理（Sub-agent）使用规范
- Git 工作流
- 实验验证流程（LoCoMo）

---

## 2. 项目信息

### 2.1 项目路径

| 环境 | 路径 |
|------|------|
| **项目根目录（本地）** | `C:\zsq\单会话项目` |
| **主要代码目录（本地）** | `C:\zsq\单会话项目\session-mem-main` |
| **服务器运行路径** | `/home/zhangsongqi/session-mem` |



### 2.2 技术栈


### 2.3 项目结构



---

## 3. 开发规范

### 3.1 核心原则

**必须遵守**:
1. **虚拟环境**: 使用 `conda` 管理环境（`session-mem`，Python 3.11）
2. **Git 同步**: 任何代码修改后立即提交并推送
3. **数据隔离**: 模型权重、数据集、实验结果、日志不提交到 git
4. **测试优先**: 修改核心逻辑后运行相关测试
5. **规划先行**: 复杂任务必须使用 planning-with-files 技能
6. **子代理分工**: 复杂任务拆分子代理并行执行
7. **删除确认**: 删除文件或大量代码前必须告知用户并获得确认

**禁止事项**:
- 不要将模型权重（>100MB）提交到 git
- 不要提交包含 API Key 的 `.env` 文件
- 不要修改已通过的测试用例逻辑
- 不要在复杂任务中单线程执行

### 3.2 命名规范



### 3.3 代码风格


### 3.4 环境配置（conda）



---

## 4. 模型与数据配置


### 4.1 LLM 配置

#### 4.1.1 主 LLM（Hindsight API + benchmark 推理）

本项目使用部署在内网的 `qwen2.5:72b-instruct-nq` 作为核心 LLM。

| 配置项 | 值 |
|--------|------|
| Provider | `openai`（OpenAI 兼容接口） |
| Model | `qwen2.5:72b-instruct-nq` |
| Base URL | `http://172.10.10.200/v1` |
| API Key | `sk-TIQFLyBRDLXqmBvmCbD7674dC8F6426eA7Ed6d2a7a4e75A7` |

**.env 写法**：
```bash
SESSION_MEM_API_LLM_PROVIDER=openai
SESSION_MEM_API_LLM_MODEL=qwen2.5:72b-instruct-nq
SESSION_MEM_API_LLM_BASE_URL=http://172.10.10.200/v1
SESSION_MEM_API_LLM_API_KEY=sk-TIQFLyBRDLXqmBvmCbD7674dC8F6426eA7Ed6d2a7a4e75A7
```

**benchmark 脚本常量写法**（如直接修改 Python 脚本）：
```python
LLM_API_KEY = "sk-TIQFLyBRDLXqmBvmCbD7674dC8F6426eA7Ed6d2a7a4e75A7"
LLM_BASE_URL = "http://172.10.10.200/v1"
LLM_MODEL = "qwen2.5:72b-instruct-nq"
```

#### 4.1.2 Judge Model（实验评估用）

用于 LoCoMo / LongMemEval 的答案正确性评判。

| 配置项 | 值 |
|--------|------|
| Provider | `openai` |
| Model | `gpt-4o-mini` |
| Base URL | `https://api2.aigcbest.top/v1` |
| API Key | `sk-ddmj3Q8H2EI4r67mEHfrJLtrgGo2YO6SXkSpTMF7YBPTZ96O` |

**benchmark 脚本常量写法**：
```python
JUDGE_API_KEY = "sk-ddmj3Q8H2EI4r67mEHfrJLtrgGo2YO6SXkSpTMF7YBPTZ96O"
JUDGE_BASE_URL = "https://api2.aigcbest.top/v1"
JUDGE_MODEL = "gpt-4o-mini"
```

### 4.2 Embedding 配置（Xinference）

通过本地 Xinference 服务提供 Embedding，使用 `bge-large-en-v1.5`。

| 配置项 | 值 |
|--------|------|
| Provider | `openai`（Xinference 兼容 OpenAI Embedding API） |
| Model | `bge-large-en-v1.5` |
| Base URL | `http://localhost:8001/v1` |
| API Key | `not-needed` |
| 维度 | `1024` |

**.env 写法**：
```bash
SESSION_MEM_API_EMBEDDINGS_PROVIDER=openai
SESSION_MEM_API_EMBEDDINGS_LOCAL_MODEL=bge-large-en-v1.5
SESSION_MEM_API_EMBEDDINGS_BASE_URL=http://localhost:8001/v1  # 注意：若 API 与 Embedding 基座不同，建议修改代码显式传入
```


**benchmark 脚本常量写法**：
```python
EMBEDDING_MODEL = "bge-large-en-v1.5"
EMBEDDING_API_KEY = "not-needed"
EMBEDDING_BASE_URL = "http://localhost:8001/v1"
EMBEDDING_DIMS = 1024
```

---

## 5. 任务管理（planning-with-files）

### 5.1 使用规则

**强制使用场景**:


**规划文件**: 项目根目录 `C:\zsq\单会话项目` 下创建三个文件
- `task_plan.md` — 任务计划和阶段追踪
- `findings.md` — 技术选型和研究发现
- `progress.md` — 会话日志和进度记录

**代码文件**: 主要代码目录 `C:\zsq\单会话项目\session-mem-main` 下
- `pyproject.toml`
- `src/session_mem/...`

### 5.2 关键规则

| 规则 | 说明 |
|------|------|
| 2-Action Rule | 每 2 次 view/browser/search 后立即保存发现到文件 |
| Read Before Decide | 重大决策前重新读取 plan 文件 |
| Update After Act | 每完成一个 phase，立即更新 task_plan.md 状态 |
| **Phase 确认机制** | **每个 Phase 完成后必须显式获得用户确认，才能标记为 complete 并进入下一阶段** |
| Log ALL Errors | 所有错误记录到 progress.md，防止重复犯错 |
| 3-Strike Protocol | 同一错误 3 次未解决，升级给用户 |

---

## 6. 子代理（Sub-agent）使用规范

### 6.1 强制使用场景

| 场景 | 示例 |
|------|------|
| 多模块独立修改 | 同时修改 buffer 层和 retrieval 层 |
| 实验并行运行 | 同时跑不同参数配置的 LoCoMo 验证 |
| 模型下载检查 | 并行检查 embedding 模型是否存在 |
| 配置验证 | 验证不同 LLM Provider 的配置格式 |

### 6.2 子代理创建标准流程

**第一步：发送阅读指令**

```
## 🔴 强制要求：阅读规范

你必须先阅读以下文件，才能开始编码：

**文件路径**: C:\zsq\单会话项目\AGENTS.md

**必须阅读的章节**:
1. 第 2 节：项目信息（路径、技术栈、结构）
2. 第 3 节：开发规范（命名、环境）
3. 第 4 节：模型与数据配置
4. 第 6 节：子代理使用规范

**关键信息**:
- 本地编辑路径: C:\zsq\单会话项目\session-mem-main
- 服务器运行路径: /home/zhangsongqi/session-mem
- 命名规范: 类名 PascalCase，函数/变量 snake_case
- 文件编码: UTF-8
- 包管理: conda / uv（后续确定）
- 子代理只能编辑代码，不能运行代码
- **Phase 确认规则：每个 Phase 完成后必须获得用户确认才能标记 complete**

## ✅ 编码前检查清单

在开始写代码前，请确认：
- [ ] 已阅读 AGENTS.md 第 2、3、4、6 节
- [ ] 了解本地编辑 vs 服务器运行的差异
- [ ] 确认命名规范
- [ ] 确认文件编码 UTF-8
- [ ] 明确任务目标和输出文件路径

## 📝 确认回复模板

请回复以下内容，确认你已了解规范：
"已阅读 AGENTS.md，确认了解以下规范：
1. 项目路径: C:\zsq\单会话项目\session-mem-main (本地) / /home/zhangsongqi/session-mem (服务器)
2. 命名规范: PascalCase 类名，snake_case 函数
3. 编码: UTF-8
4. 包管理: conda / uv（后续确定）
5. 子代理只编辑不运行
6. Phase 确认规则：每个 Phase 完成后必须获得用户确认才能标记 complete"

确认后，我会发送具体任务需求。
```

**第二步：等待确认**

必须等待子代理回复确认，才能发送具体任务。

**第三步：发送具体任务**

```python
Task(
    description="任务简述",
    prompt='''
## 项目背景
- 项目: session-mem 单会话记忆系统
- 本地路径: C:\\zsq\\单会话项目\\session-mem-main
- 服务器路径: /home/zhangsongqi/session-mem
- 规范: 已确认阅读 AGENTS.md
- 技术栈: Python 3.11（后续确定具体框架）

## 任务目标
[具体描述]

## 已有模块
[相关文件列表]

## 具体要求
[详细需求]

## 技术要求
- 遵循 AGENTS.md 命名规范
- 使用 Python 3.11 语法
- 文件编码 UTF-8
- 如有新增依赖，更新 requirements.txt 或 pyproject.toml

## 输出文件
[文件路径列表]

## 重要提醒
⚠️ 只在本地编辑代码，不要尝试运行
⚠️ 遵循 AGENTS.md 命名规范
⚠️ 文件编码 UTF-8
'''
)
```

---

## 7. 开发工作流

### 7.1 双环境工作流

```
本地开发机 (Windows)                 服务器 (Linux)
┌──────────────────────┐           ┌──────────────────────┐
│ 1. 编写代码          │           │                      │
│    - 编辑文件        │           │                      │
│    - Git 管理        │           │                      │
└──────────┬───────────┘           │                      │
           │ git push              │                      │
           ▼                       │                      │
┌──────────────────────┐           │                      │
│ 2. 提交到远程仓库    │──────────▶│ 3. 拉取代码          │
│    - git add -A      │           │    - cd /home/...    │
│    - git commit      │           │    - git pull        │
│    - git push        │           │    或 sftp 上传代码  │
└──────────────────────┘           └──────────┬───────────┘
                                              │
                                              ▼
                                 ┌──────────────────────┐
                                 │ 4. 运行/测试         │
                                 │    - conda activate  │
                                 │      session-mem     │
                                 │    - 安装依赖        │
                                 │    - source .env     │
                                 │    - 运行 benchmark  │
                                 │    - 验证结果        │
                                 └──────────────────────┘
```

### 7.2 Git 提交规范

**提交注释语言**：**必须使用中文**，清晰描述本次改动的目的和内容。

**提交格式**（约定式提交）:
```bash
git commit -m "feat: 新增 LongMemEval 类别过滤支持"
git commit -m "fix: 修复 LoCoMo 日期解析时区问题"
git commit -m "docs: 更新 benchmark 复现文档"
git commit -m "test: 添加 recall 性能测试"
git commit -m "exp: 完成 LoCoMo 实验配置与结果"
git commit -m "refactor: 重构 benchmark runner 配置加载"
```

**提交类型说明**:
- `feat`: 新功能
- `fix`: Bug 修复
- `docs`: 文档更新
- `exp`: 实验相关（配置、脚本、结果）
- `refactor`: 代码重构
- `test`: 测试相关
- `chore`: 构建过程或辅助工具的变动

**强制规则**：
- 所有 `git commit -m` 的注释内容必须使用中文
- 禁止仅使用 "update"、"fix"、"ok" 等无意义英文单词作为提交信息

---

## 9. 流程执行保障

### 9.1 子代理创建检查点

**每次创建子代理前，必须自问：**

```
检查点 1: 我是否要求子代理先阅读 AGENTS.md 第 6 节？
   → 如果没有，停止，先发送阅读指令

检查点 2: 子代理是否已确认阅读？
   → 如果没有，等待确认，不发送任务

检查点 3: 任务 Prompt 是否包含完整上下文？
   → 必须包含：项目路径、服务器路径、规范引用

检查点 4: 是否强调了本地编辑 vs 服务器运行？
   → 必须强调：子代理只编辑，不运行
```

### 9.2 记忆重置后的恢复

**如果发生 /clear 或记忆重置：**

1. **立即读取 AGENTS.md**
2. **重点阅读第 6 节**（子代理使用规范）
3. **读取 task_plan.md, findings.md, progress.md**
4. **按照第 6 节流程创建子代理**
5. **永不跳过确认步骤**

### 9.3 快速参考卡片

**创建子代理的固定开场白：**

```
你必须先阅读 C:\zsq\单会话项目\AGENTS.md，
特别是第 2 节（项目信息）、第 3 节（开发规范）、第 5 节（Phase 确认规则）和第 6 节（子代理规范）。

请回复确认：
"已阅读 AGENTS.md，确认了解命名规范、编码规范和执行环境差异"

确认后，我会发送具体任务。
```

---



