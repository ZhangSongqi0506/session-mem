# Findings & Decisions: session-mem
<!-- 
  WHAT: session-mem 项目的技术决策库与研究发现
  WHY: 将方案中的关键设计、约束和发现持久化到磁盘
-->

## Requirements
<!-- 从用户请求和技术方案中提取的核心需求 -->
- 构建单会话级临时记忆系统，降低 Input Token 40-60%
- 回答准确率损失控制在 <5%
- 支持长跨度单会话（用户可能隔段时间再回来提问）
- 通过 LoCoMo 数据集的 session 拼接进行验证
- 低成本、低延迟（TTFT < 50ms）

## Research Findings
- 技术方案采用「三层缓冲 + 语义驱动 Cell」架构
  - SenMemBuffer：零压缩原始文本，512-2048 tokens 弹性窗口
  - ShortMemBuffer：Cell 摘要索引库，活跃窗口 512 + 存储窗口 2048
  - Working Memory：仅携带热区原文 + 命中 Cell 全文
- 时间戳解析用于支持跨长时间会话的时序定位
- 文档版本 v2.0 已通过预检审查，补充了验证方法和时间戳机制

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 三层缓冲架构 | 在延迟敏感性和信息保真度之间取得平衡 |
| Cell 四层结构（检索/回溯/元信息/关系） | 支持从匹配到全文回溯的灵活加载 |
| 双路召回（向量为主 + 关键词为辅） | 解决短查询与长摘要的语义空间不对齐 |
| 命中即全量 | 避免压缩命中内容导致关键约束丢失 |
| 时间戳统一 ISO 8601 UTC | 系统收到时间统一可控，避免客户端时钟偏差 |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| 文档章节从 4 跳到 6 | 删除原第 5 章内容，将 6.x 改为 5.x |
| git push 到空仓库失败 | 先创建初始 commit 再 push |

## Resources
- 项目仓库：https://github.com/ZhangSongqi0506/session-mem
- 技术方案文档：`单会话级临时记忆系统（Session-scoped Working Memory）技术方案.md`
- LoCoMo 数据集：用于长对话记忆系统评测的公开数据集

## Visual/Browser Findings
-

---
*Update this file after every 2 view/browser/search operations*
