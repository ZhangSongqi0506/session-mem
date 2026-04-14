# 单会话级临时记忆系统（Session-scoped Working Memory）技术方案

> **代码仓库**：`session-mem`  
> **文档版本**：v2.0

**目标**：构建低成本、低延迟的单会话记忆系统，实现 Input Token 降低 40-60%，回答准确率损失 <5%。

**验证方法**：基于 LoCoMo 数据集，将同一 conversation 的多个 session 按时间顺序拼接为单一连续会话，模拟真实场景下的长跨度单会话对话，评估 Token 节省率与回答准确率变化。

---

## 1. 核心架构理念

### 1.1 设计哲学

本系统采用**分层保真、批量压缩、按需回溯的三段式架构，在**延迟敏感性\*\*（TTFT < 50ms）和**信息保真度**（可回溯原文）之间取得平衡。

*   **写入阶段（零压缩）**：新对话轮次以**原始文本**形式进入感觉缓冲，确保信息零损失，支持即时回溯。
    
*   **切分阶段（批量压缩）**：当累积量达到工作区上限时，**一次性**调用 LLM 生成结构化记忆单元（Cell），原文降级至暂存区。
    
*   **检索阶段（轻量匹配）**：通过向量相似度 + 关键词桥接，快速定位相关 Cell，**按需**将原文加载回工作记忆。
    

---

## 2. 三层缓冲架构详解

系统通过三层递进式缓冲，实现从**原始对话**到**LLM 可用上下文**的转化：

### 2.1 第一层：感觉缓冲（SenMemBuffer）

**职能**：原始对话的**保真暂存池**与**智能切分闸门**，通过动态累积与语义边界检测，确保 Cell 生成时逻辑单元的完整性，避免过早切分导致的上下文碎片化。

#### 动态容量机制（弹性窗口 512-2048）

为平衡**延迟敏感**与**逻辑完整性**，采用**分段软上限**策略：

*   **基线阈值**：512 tokens（常规触发检查点）
    
*   **弹性上限**：2048 tokens（硬性封顶，防止无限累积）
    
*   **窗口逻辑**：对话流在 Buffer 内持续累积，每达到 512 的整数倍（512/1024/1536/2048）触发一次**语义边界检测**，而非强制切分。
    

#### 智能切分策略（语义滑移检测）

当 Buffer 达到检测阈值时，**调用轻量 LLM 进行语义连续性判断**：

**检测维度**：

*   **话题滑移（Topic Drift）**：当前轮次与 Buffer 内首轮对话的主题相似度是否低于阈值（<0.6）
    
*   **任务闭环（Task Closure）**：是否出现"解决了"、"明白了"等语义终止信号
    
*   **意图转折（Intent Shift）**：用户是否显式切换目标（"换个话题"、"回到正题"）
    

**分支处理**：

| **检测结果** | **处理策略** | **结果状态** |
| --- | --- | --- |
| **语义连贯**（无滑移） | 继续累积，保留原文在 Buffer 内，等待下一检测点（512→1024→...） | 累积态 |
| **语义滑移**（话题转折） | 触发 Cell 生成：<br>1. **旧主题**：将当前 Buffer 内**前序内容**（转折前的完整逻辑单元）打包生成 Cell<br>2. **新主题**：将**触发转折的当前轮次及后续内容**保留在 Buffer 中，作为新逻辑单元的起点 | 切分态 |

**优势**：避免传统固定窗口（如每 5 轮强制切分）可能造成的**逻辑链断裂**（如切断"因为...所以..."的因果关系），确保每个 Cell 承载**语义闭合的完整话题单元**。

#### 溢出兜底（硬上限保护）

若对话持续累积至 **2048 tokens** 仍未检测到语义滑移（如超长单段独白）：

1.  **强制切分**：以 2048 为界强制生成 Cell，避免内存溢出
    
2.  **质量标记**：此类 Cell 自动标记为 `confidence: low` 与 `type: fragmented`，提示后续检索时优先回溯原文验证（因可能包含多个未闭合子话题）
    

#### 关键设计原则

*   **严禁压缩**：Buffer 内全程以**原始文本**形态存储，禁止任何形式的摘要、提取或向量化，确保切分前系统持有**信息无损的完整副本**
    
*   **读-写分离**：写入阶段仅做累积与边界检测，零计算开销；切分阶段才调用 LLM 进行质量压缩，成本发生在对话间隙（非用户等待路径）
    
*   **滑动保留**：触发切分后，**新主题的首轮对话不进入旧 Cell**，而是留在 Buffer 作为新 Cell 的种子，保证相邻 Cell 间的**时序连续性**（避免丢轮）

#### 时间戳注入（进入缓存时）

每一轮对话进入 SenMemBuffer 时，系统为其注入**统一格式的收到时间戳**（ISO 8601，UTC）。该时间戳服务于单会话跨长时间场景：

*   **间隔检测**：当相邻两轮时间差超过阈值（如 30 分钟），可作为额外的语义切分信号，提示用户可能已离开并重新发起话题。
*   **时序检索**：支持后续按时间范围定位历史内容（如"昨天说的预算"）。
*   **元信息继承**：生成 Cell 时，将该 Cell 内首轮与末轮的时间戳写入元信息层，形成该单元的时间范围标记。
    

### 2.2 第二层：短期缓冲（ShortMemBuffer）

**职能**：Cell 摘要的**统一检索池**，连接压缩后的历史与当前查询。

*   **内容形态**：Cell 的语义摘要、关键词、实体标签、向量嵌入（非原文）
    
*   **统一检索**：当前阶段，单会话内所有 Cell 均参与向量检索与关键词匹配，不区分活跃/存储窗口。
    
*   **生命周期预留**：Cell 的向量索引与元数据均持久化到 SQLite。未来可根据会话长度引入内存缓存或分级淘汰策略，但 MVP 阶段优先保证召回完整性。
    

### 2.3 第三层：工作记忆区（Working Memory）

**职能**：实际组装进 LLM Prompt 的**最终上下文**，仅携带**必要的原文**，而非全量历史。

*   **内容构成**：
    
    *   **Meta Cell 全局摘要**：会话主旨单元，强制常驻（约 200-600 tokens，随会话深度动态增长）
    
    *   **热区原文**：最近 2-3 轮对话（未压缩，保证对话连贯性，约 400 tokens）
        
    *   **激活 Cell 原文**：通过检索选中的 1-2 个 Cell 的**完整原文**（从冷区按需回溯，约 800-1200 tokens）
        
    *   **当前查询**：用户最新问题（经查询重写后的版本，约 100 tokens）
        

*   **加载原则**：
    
    *   **命中即全量**：凡是被检索命中的，**无条件回溯其完整原文**进入 Prompt，不截断、不摘要化
        
    *   **未命中即隔离**：未被检索命中的历史 Cell，**完全不进入**工作记忆区，其原文保留在冷区暂存区，不占用 Prompt Token
        

*   **Token 节省逻辑**：
    
    *   20 轮全量历史约 4000+ tokens
        
    *   本方案仅携带：Meta Cell 400 + 热区 400 + 命中 Cell 原文 1000 + 查询 100 = **约 1900 tokens**
        
    *   **节省率 50%+** 来源于**丢弃未被引用的历史 Cell**，而非压缩命中 Cell 的内容。Meta Cell 以少量额外 Token 换取全局主旨的确定性不丢
        

---

## 3. 记忆单元（Memory Cell）生命周期

Cell 是本系统的核心数据结构，承载从原始对话到结构化记忆的转化。

### 3.1 Cell 生成触发条件

Cell 并非固定轮次生成，而是**语义驱动**：

*   **Token 阈值**：SenMemBuffer ≥ 512 tokens（必要条件）
    
*   **语义边界**（充分条件，满足其一即可）：
    
    *   用户明确话题切换（"换个话题..."）
        
    *   任务闭环信号（"解决了"、"明白了"）
        
    *   上下文关联度骤降（当前轮与上一轮 Embedding 相似度 <0.6）
        
    *   累积轮次超过 5 轮（强制切分，防止单 Cell 过大）
        

**边界检测实现**：

*   主路径：轻量小模型（1.5B 级）二分类判断（是否话题转折）
    
*   Fallback：规则匹配（检测特定标点、关键词）+ 向量相似度阈值
    

### 3.2 Cell 内容结构设计

每个 Cell 包含**四层信息**，支持从极简提示到全文回溯的灵活加载：

1.  **检索层**（用于匹配）：
    
    *   语义摘要（30-50 tokens，描述该 Cell 核心内容）
        
    *   实体标签（3-5 个关键实体，如"预算"、"1万元"）
        
    *   关键词（5-8 个 TF-IDF 提取术语）
        
    *   512 维向量嵌入（用于语义相似度计算）
        
2.  **回溯层**（用于加载进 Prompt）：
    
    *   完整原文指针（在冷区的存储位置，非实际内容）（或者直接原文）
        
    *   原文 Token 数（用于预算计算）
        
3.  **元信息层**（用于管理）：
    
    *   Cell ID（时序编码，如 C\_003）
        
    *   置信度分数（0-1，LLM 生成摘要时的自我评估）
        
    *   时序链接（`linked_prev` 指向前一 Cell，形成逻辑链）
        
    *   Cell 类型（`fact`/`constraint`/`preference`/`task`，影响回溯优先级）
        
4.  **关系层**（可选，用于复杂场景）：
    
    *   因果标记（如当前 Cell 的"折扣"依赖于 Cell\_002 的"预算"）
        
    *   实体共现（记录与其他 Cell 共享的实体）
        

### 3.3 Meta Cell（会话主旨单元）

在普通 Cell 之外，系统引入一个**会话级全局摘要单元**（Meta Cell），用于承载整个会话的**核心目标、有效约束、当前状态与关键决策**。它不参与向量检索竞争，而是**强制常驻于 Working Memory 最前端**，作为所有普通 Cell 的"上级视图"，解决长会话中因 Top-K 检索筛选导致"主旨任务缺失"的问题。

#### 核心设计

*   **每会话唯一**：Meta Cell 与 `session_id` 绑定，一个会话始终只维护 1 个 active 版本。
*   **全量融合更新**：每生成一个普通 Cell，立即将"当前 Meta Cell 全文 + 新 Cell 原文"送入 LLM，由 LLM 重写输出新的全局摘要。
*   **无条件注入**：Working Memory 组装时，Meta Cell 固定放在 Prompt 最前端，优先级高于热区原文与检索召回 Cell。
*   **长度不设硬上限**：接受随会话深度自然膨胀，以全局连贯性的保真为优先。

#### 生命周期

| 阶段 | 触发条件 | 行为 |
|------|---------|------|
| **诞生** | 首个普通 Cell（`C_001`）生成完毕 | 以 `C_001` 的原文为唯一输入，调用 LLM 生成初始 Meta Cell |
| **更新** | 此后每生成一个普通 Cell `C_N` | 输入 = 当前 active Meta Cell 全文 + `C_N` 原文，调用 LLM 全量重写，输出新版 Meta Cell |
| **归档** | 新版 Meta Cell 写入后 | 旧版状态标记为 `archived`，仅最新版状态为 `active` |

#### 更新 Prompt 核心要求

LLM 在全量融合时需遵循：
1. **保留有效约束**：核心目标、预算、偏好、时间等仍成立的信息必须保留。
2. **直接修正覆盖**：若新 Cell 明确推翻旧信息，直接更新为新状态，不保留过时内容。
3. **融入关键进展**：新 Cell 带来的决策、结论、新约束需被吸收。
4. **丢弃纯细节**：具体价格、时刻表等局部事实不进入 Meta Cell，留给普通 Cell 承载。
5. **长度自由**：以准确传达当前全局状态为准，不截断。

#### 数据结构

复用现有 `cells` 表结构，以 `cell_type = 'meta'` 区分，通过 `status` 字段管理版本生命周期：

```sql
CREATE TABLE meta_cells (
    session_id TEXT,
    cell_id TEXT,           -- 固定格式如 META
    version INTEGER,        -- 从 1 开始递增
    cell_type TEXT DEFAULT 'meta',
    status TEXT CHECK(status IN ('active', 'archived')),
    raw_text TEXT,          -- 实际注入 Prompt 的全局摘要全文
    token_count INTEGER,
    linked_cells TEXT,      -- JSON array，记录融合过的普通 Cell ID 列表
    created_at DATETIME,
    updated_at DATETIME,
    PRIMARY KEY (session_id, version)
);
```

**查询规则**：`WorkingMemory` 组装时，始终取 `status = 'active'` 且 `version` 最大的那一条。

#### Working Memory 注入位置

Meta Cell 固定置于 Prompt 最前端：

```
[会话主旨]
{meta_cell.raw_text}

[近期对话]
{hot_zone}

[相关历史]
{retrieved_cells}

[当前查询]
{query}
```

Prompt 内具体标签名称（如 `[会话主旨]`、`[全局上下文]` 等）由开发阶段根据实际效果调整，方案中不做死规定。

#### 与现有模块的改动点

| 模块 | 改动 |
|------|------|
| `CellGenerator` | 生成普通 Cell 后，追加调用 `MetaCellGenerator.update(new_cell)` |
| `MetaCellGenerator` | 新增模块，负责首次生成与全量融合更新 |
| `SQLiteBackend` | 新增 `save_meta_cell()` / `get_active_meta_cell()` 方法 |
| `WorkingMemory` | `assemble()` 方法首行无条件插入 active Meta Cell |

#### 成本与风险兜底

*   **成本**：20 轮对话若生成 5 个普通 Cell，则 Meta Cell 更新 5 次。每次输入 ≈ 当前 Meta Cell + 新 Cell 原文 + Prompt 开销。总增量成本约为普通 Cell 生成成本的 **0.5–1 倍**。
*   **幻觉风险**：LLM 更新时可能错误删除有效约束。但由于原始普通 Cell 完整保留，后续检索仍有机会召回原文，形成双重保险。
*   **长度膨胀**：明确接受该 trade-off，以换取全局连贯性的确定性保障。

---

## 4. 检索策略：从查询到上下文组装

检索的核心挑战是**短查询与长摘要的语义空间不对齐**（如"多少钱？"与"用户设定预算上限为 1 万元"）。系统采用**查询重写 + 双路召回 + 预算感知的动态加载**三阶段策略。

### 4.1 查询重写（Query Rewriting）

在进入检索前，利用**热区上下文**（最近 2 轮原文）对短查询进行扩展：

*   **指代消解**：将"这"、"那"、"刚才说的"替换为具体实体
    
    *   示例："这个多少钱？" → "这个\[预算上限\]多少钱？"
        
*   **语义扩展**：利用小模型将口语化短查询扩展为伪文档（Pseudo-Document）
    
    *   输入："多少钱？"
        
    *   输出："用户询问之前讨论的项目预算具体金额是多少"
        
*   **实体补全**：提取查询中的隐式实体（如"价格"映射到"预算"）
    

**重写触发条件**：

*   查询长度 <10 tokens，或包含指代词
    
*   重写延迟预算 <30ms（使用本地小模型，非 API 调用）
    

### 4.2 双路召回机制

检索不采用完全独立的 BM25 分支（维护成本高），而是**以向量为主、关键词为辅的混合评分**：

**主路：向量相似度**

*   使用轻量 Encoder（如 `bge-large-en-v1.5`，1024 维，通过 Xinference 本地部署）
    
*   在 ShortMemBuffer 的**全量 Cell**（当前会话所有已生成 Cell）中计算余弦相似度
    
*   取 Top 5 进入候选池
    

**辅路：关键词桥接**

*   计算查询与 Cell 关键词的 Jaccard 相似度（Set 交集/并集）
    
*   实体匹配奖励：若查询实体与 Cell 实体标签有共现，额外加分
    
*   关键词匹配作为**重排序信号**，而非独立召回分支
    

**融合公式**： final\_score = 0.75 \* vector\_score + 0.25 \* keyword\_score

### 4.3 低置信度 Fallback（可选增强）

当 Top-1 Cell 的融合分数 <0.6（阈值可配置），认为向量检索可能漏召回：

1.  放宽向量检索相似度阈值，扩大候选集范围
    
2.  启用 BM25 精确匹配（针对罕见术语、数字、ID）
    
3.  使用 Reciprocal Rank Fusion（RRF）合并向量与 BM25 结果
    
4.  若仍无满意匹配，标记为"新话题"，不加载历史 Cell（避免引入无关信息）
    

### 4.4 原文回溯策略

**原则**：**检索命中 → 全量回溯 → 完整进入 Prompt**

### 4.4.1 确定性回溯流程

```python
# 伪代码逻辑
def assemble_working_memory(query, hot_zone, faiss_index, cold_storage, meta_cell):
    # 1. 检索（向量+关键词混合）
    candidate_cells = retrieve_top_k(query, faiss_index, k=2)  # 仅取Top-2
    
    # 2. 全量回溯（无预算检查，无条件加载）
    activated_content = []
    for cell in candidate_cells:
        full_text = cold_storage.load(cell.pointer)  # 完整原文，不截断
        activated_content.append(full_text)
    
    # 3. 组装（Meta Cell + 热区 + 命中Cell全文 + 查询）
    prompt_context = [meta_cell.raw_text] + hot_zone + activated_content + [query]
    return prompt_context
```

### 4.4.2 准确性保障机制

既然**不压缩命中内容**，如何保证**不丢失关键约束**？

*   **全文保真**：只要 Cell 被命中，用户第 2 轮说的"预算 1 万"原话，第 20 轮依然原封不动出现在 Prompt 中
    
*   **Meta Cell 兜底**：即使某约束所在的普通 Cell 未被检索命中，Meta Cell 中仍保留该约束的摘要形态（如"预算上限 1 万元"），避免主旨任务完全丢失
    
*   **防漏召回**：通过**时序链接**主动激活关联 Cell（见 6.2 节），避免"预算约束在第 2 轮 Cell，第 20 轮查询只命中折扣 Cell，导致遗忘预算"的问题
    

---

## 5. 存储与持久化设计

为支撑 Skill、MCP Tool 及 LangChain Memory 组件化目标，存储层采用**模块化可拔插**设计：底层定义统一抽象接口，默认以**零运维的 SQLite + sqlite-vec**实现，同时预留后端切换能力（如 FAISS、Chroma、PostgreSQL 等）。

### 5.1 存储抽象层

系统对持久化需求拆分为三个正交接口，任何后端只需实现对应接口即可接入：

| 接口 | 职责 | 当前默认实现 |
|------|------|-------------|
| `VectorIndex` | 语义向量检索（Top-K 相似度搜索） | `sqlite-vec` 虚拟表 |
| `CellStore` | Cell 元信息、关系链、实体共现的增删查改 | SQLite 关系表 |
| `TextStore` | 完整原文的按需加载 | SQLite TEXT 字段 |

### 5.2 默认后端：SQLiteBackend

默认使用单个 `.db` 文件承载全部数据，无需额外服务，开箱即用：

**表 1：cells（Cell 结构化数据）**

```sql
CREATE TABLE cells (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    cell_type TEXT CHECK(cell_type IN ('fact', 'constraint', 'preference', 'task')),
    confidence REAL,
    summary TEXT,
    keywords TEXT,          -- JSON array
    entities TEXT,          -- JSON array
    linked_prev TEXT,       -- 指向前一 Cell
    timestamp_start TEXT,   -- ISO 8601
    timestamp_end TEXT,     -- ISO 8601
    vector_id TEXT,         -- 关联向量索引
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_cells_session ON cells(session_id);
CREATE INDEX idx_cells_type ON cells(cell_type);
CREATE INDEX idx_cells_linked_prev ON cells(linked_prev);
```

**表 2：cell_texts（原文回溯）**

```sql
CREATE TABLE cell_texts (
    cell_id TEXT PRIMARY KEY,
    raw_text TEXT,          -- 完整原文
    token_count INTEGER,
    FOREIGN KEY (cell_id) REFERENCES cells(id)
);
```

**表 3：entity_links（实体共现关系）**

```sql
CREATE TABLE entity_links (
    cell_id TEXT,
    entity TEXT,
    FOREIGN KEY (cell_id) REFERENCES cells(id)
);
CREATE INDEX idx_entity_links_entity ON entity_links(entity);
```

**表 4：cell_vectors（向量索引，sqlite-vec 扩展）**

```sql
CREATE VIRTUAL TABLE cell_vectors USING vec0(
    cell_id TEXT PRIMARY KEY,
    embedding FLOAT[512]  -- 维度根据嵌入模型动态配置
);
```

**表 5：meta_cells（主旨单元，独立维护会话级全局摘要）**

```sql
CREATE TABLE meta_cells (
    session_id TEXT,
    cell_id TEXT,           -- 固定格式如 META
    version INTEGER,        -- 从 1 开始递增
    cell_type TEXT DEFAULT 'meta',
    status TEXT CHECK(status IN ('active', 'archived')),
    raw_text TEXT,          -- 实际注入 Prompt 的全局摘要全文
    token_count INTEGER,
    linked_cells TEXT,      -- JSON array，记录融合过的普通 Cell ID 列表
    created_at DATETIME,
    updated_at DATETIME,
    PRIMARY KEY (session_id, version)
);

CREATE INDEX idx_meta_cells_session ON meta_cells(session_id);
CREATE INDEX idx_meta_cells_status ON meta_cells(status);
```

### 5.3 内存与磁盘的分层关系

MVP 阶段，ShortMemBuffer 仅作为**逻辑索引概念**存在：所有 Cell 的元数据和向量索引均保存在 SQLite 中，检索时直接查询数据库。内存中不维护复杂的分级缓存：

*   **全量 Cell**：所有已生成 Cell 的向量索引和元数据均保留在 SQLite，参与统一检索。
*   **生命周期预留**：当会话长度增长到一定规模（如 >100 个 Cell）时，再引入内存缓存、活跃窗口或归档策略。

### 5.4 模型无关与扩展预留

*   **维度可配置**：`cell_vectors` 的向量维度在初始化时根据所选 Encoder 动态确定，不绑定特定模型。
*   **原文优先**：`raw_text` 字段永不被压缩或摘要化替换，确保不同 LLM 跨模型复用时看到的是同一套原始约束。
*   **跨会话预留**：`cells` 表已包含 `session_id` 字段，当前按单会话过滤；未来扩展长期记忆时，可直接放宽该过滤条件实现跨会话检索，无需改表结构。

---

## 6. 边界情况与异常处理

### 6.1 超长会话（>50 轮）

当会话轮次增长，Cell 数量显著增加时：

*   **分级存储（MVP 阶段简化）**：
    
    *   所有 Cell 均保留在 SQLite 中，统一检索。
    *   原文清理策略照常运行：高置信度 Cell 的原文 24 小时后可清理；低置信度保留至会话结束。
        

*   **原文清理**：
    
    *   高置信度 Cell 的原文在 24 小时后可清理
        
    *   低置信度 Cell 原文保留至会话结束
        
    *   提供配置项 `aggressive_cleanup`，开启后仅保留最近 10 个 Cell 的原文
        


### 6.2 因果链断裂（长程依赖）

当用户提问涉及跨多个 Cell 的因果关系（如"基于预算限制，刚才选的配置还能优化吗？"）：

*   **时序链接追踪**：通过 Cell 的 `linked_prev` 链，自动加载关联的约束类 Cell（即使其未被向量检索命中）
    
*   **实体共现激活**：若当前激活 Cell 含"预算"实体，自动检索其他含"预算"的 Cell，构建**约束上下文**
    
*   **显式标记**：在 Cell 生成时，LLM 识别并标记"此 Cell 包含对其他 Cell 的依赖"，检索时强制级联加载
    


### 6.3 检索失败（冷启动/新话题）

当检索无匹配（如用户突然问与历史无关的问题）：

*   **零回溯模式**：工作记忆包含 Meta Cell（全局主旨仍保留）+ 热区原文（最近 2 轮）+ 当前查询，不强行引入无关的普通 Cell
    
*   **新话题检测**：若连续 3 轮检索置信度 <0.5，触发"新话题"标记，清空 SenMemBuffer（旧话题原文强制生成 Cell 归档），避免旧话题碎片干扰新话题
    
    

---
