# NoteFlow 业务完成度、性能与重构审计

> 审计日期：2026-09-03  
> 审计对象：`agent/noteflow-security-remediation`、PR #27，以及审计开始时的未提交工作区
> 文档性质：发布决策、重构建议与实施验收记录。本文不把“测试通过”直接等同于“产品可发布”。
> 重要说明：第 1–15 节保留审计开始时的基线证据；下面的“实施更新”是当前状态的优先解释。

## 0. 实施更新（2026-09-03，优先于后文基线）

### 0.1 已确定的产品与部署边界

本轮已按讨论结果正式选择：**登录制、云端保存的 Web App/PWA**。主前端使用 React + TypeScript + Vite 并部署到 Cloudflare Pages；Supabase 提供 Auth、Postgres/pgvector 和私有 Storage；Spring Boot API 与 Python/LangGraph Worker 保留；Redis 继续承担任务分发、租约、退避和死信语义。第一阶段的离线能力只包括应用壳和 IndexedDB 草稿，不承诺离线 AI 推理。

这套方案不要求固定购买 Railway。低流量演示可以用 Cloudflare Pages、Supabase 免费额度、Upstash Redis 免费额度以及 Cloud Run scale-to-zero/Job 的免费额度起步；但免费额度、区域和价格会变化，且模型调用、超额资源、网络流量和冷启动仍需接受真实运行监控。部署决策与变量清单见 `docs/deployment/FREE_CLOUD_ARCHITECTURE.md`。

### 0.2 本轮已落地

| 范围 | 当前实现 | 状态 |
|---|---|---:|
| 身份 | 邮箱+密码、6 位邮箱 OTP、重发验证码、用户名唯一性、Google OAuth；Spring 验证 Supabase JWT | 代码完成，待真实 Supabase 配置 |
| 多租户 | `profiles/workspaces/workspace_members`、RLS、服务端 tenant context、内部服务身份 | 代码完成，待两用户云端隔离 E2E |
| 前端 | 模块化 PWA；文档上传/列表/详情、Search、Agent、AI 笔记、IndexedDB 草稿、云端保存、闪卡复习、Quiz 作答/评分、设置 | MVP 主闭环完成 |
| 私有文件 | API 上传 Supabase 私有 bucket；Worker 临时下载、派生 PNG 回传和清理；API 鉴权代理资源 | 代码完成，待真实 bucket E2E |
| Redis/任务 | outbox、优先级队列、Redis lease、DB CAS execution lease、有限重试、DLQ、恢复、Cloud Run Job 唤醒 | 完成并有回归测试 |
| 并发 | Java 有界执行池和超时降级；Python 有界线程/进程池、全局 provider 限流、优雅停机 | 完成 |
| Agent | 现有 LangGraph/ReAct、工具权限、暂停/恢复和引用链保留 | 保留并加固 |
| 运行交付 | API/Worker 非 root Dockerfile、Cloud Run Service/Job 模板、Supabase/Upstash/Cloudflare 配置说明 | 可重复构建，尚未真实部署 |
| 工程治理 | PR #27、main 强制 PR、管理员也受保护、严格 required checks、线性历史、禁止 force push/删除 | 完成 |

### 0.3 本轮修复的性能与正确性问题

- 修复跨用户 AI 设置快照串用，并把按用户缓存限制为 1024 项 access-order LRU；用户 API Key 不再进入 Gemini URL 或回显上游错误体。
- 会话消息与引用改为批量查询；消息分页取“最新一页”再正序返回，避免超过 100 条后只看到最旧消息。
- `ContextBuilder` 只读取命中 chunk 的前后邻居，不再把每个命中文档的全部正文装入内存。
- 自定义检索 scope、学习目标文档校验、文件夹删除、Quiz 自动评分均改为批量数据库操作。
- AI 笔记版本分配加入 PostgreSQL advisory lock；HyDE 增加独立超时并可降级。
- 全局 API 异常处理覆盖格式错误、数据冲突、超大上传和未知异常，同时保留框架原有 4xx 状态。
- 前端继续按页面拆包；新增页面后生产构建约 553 KiB precache，React/数据层仍保持懒加载与请求去重。

### 0.4 验证证据

| 验证 | 当前结果 |
|---|---:|
| Java 测试 | 88，0 failure |
| API 空库启动 | 隔离 PostgreSQL 上 V1–V6 从零迁移，完整 Spring Context 启动，health=`UP` |
| Python 测试 | 160 passed，13 skipped（跳过项依赖可选外部/数据库条件） |
| Web v2 | TypeScript + Vite 生产构建通过 |
| 旧 Web 安全测试 | Playwright 1/1 通过，JavaScript 语法通过 |
| 容器 | API 与 Worker 镜像本机构建成功；均以 uid 10001 运行 |
| 配置 | Cloud Run YAML 可解析；`docker-compose config` 通过 |
| 浏览器 | 登录/注册/验证码表单、移动端 375px、桌面端 1440px 无横向溢出、无控制台错误 |

没有 Supabase、Google OAuth、Upstash 和 GCP 的用户项目凭据，因此**没有**声称真实云端端到端已经通过。上线前必须完成：Supabase migration + Auth 模板/Google provider、私有 bucket、Cloud Run 服务身份与 Secret Manager、Upstash TLS、Cloudflare Pages 环境变量，并用两个测试用户执行上传→解析→笔记→搜索/Agent→复习→重新登录的完整流程。

### 0.5 审计开始时未提交重构的最终处理意见

| 改动组 | 决定 | 原因 |
|---|---|---|
| Java 安全/性能重构 | 已修正后纳入 PR | 收益可验证，完整 Java 测试通过；修正了会话分页回归、缓存无界和 4xx 被误报 500 |
| 旧 Web 事件/错误提示 | 已纳入 PR | 修复持久根节点重复绑定和无提示失败，Playwright 安全测试通过 |
| Tempo/Collector、`.claude` | 已纳入 PR | 本地观测数据持久化、内存上限明确；个人启动配置不应入库 |
| `apps/web/vendor/editor` 全量哈希产物替换 | **建议撤回，不纳入 PR** | 约 7.8 万行噪声且新 Web v2 不依赖它；如继续维护旧编辑器，应由 CI 构建 artifact，而非人工提交 hash bundle |
| `FULL_PROJECT_REVIEW_2026-08-30_ZH.md`、`REMEDIATION_IMPLEMENTATION_2026-08-30_ZH.md` 与指向它们的旧索引改动 | **建议撤回或移入历史目录，不纳入 PR** | 内容假设工作区 clean/本地单用户，已被本文件、ADR 和实际云端实现取代 |

### 0.6 仍未完成、不能包装成“已上线”的部分

1. 真实云资源尚未创建/绑定，故生产域名、邮件投递、Google consent、RLS、私有对象下载和 scale-to-zero 唤醒仍需云端验收。
2. 文档删除/恢复、模型成本预估与硬配额、编辑冲突 UI、管理员 DLQ 重放面板仍是上线前的高优先级产品项。
3. CI 已成为 main 合并门禁；PR 只有所有 required checks 成功且分支与 main 同步后才能合并。
4. 旧 Web 仅作为本地兼容层，不应继续承载新的云端产品功能；富编辑器能力应通过受控 adapter 逐步迁入 Web v2。

## 1. 结论先行

NoteFlow 已经不是一个空壳或概念原型。PDF 解析、版面/公式处理、分块、向量与混合检索、引用式问答、AI 笔记、编辑器、文件夹、闪卡、测验、学习记忆、异步任务和 Agent 编排都已经有较多真实代码与测试。最值得保留的是后端领域能力与 Python 文档处理流水线。

但当前版本**不具备可发布条件**。问题不在功能数量，而在产品定义、启动正确性、身份/数据模型和任务可靠性四条主线没有收拢：

1. **全新数据库上 API 当前无法启动**：`StudyService` 有两个构造函数且未指定注入构造函数，Spring 无法实例化 Bean。
2. **“本地无账户”文档与数据库事实冲突**：schema 仍有 `users` 表，四个学习模块外键仍强制引用它；默认本地工作区 UUID 没有被创建，真实写入会失败。
3. **当前未提交的可靠性重构有方向正确、实现危险的部分**：统一异常重投会把已经由流水线标记为失败的业务错误再次重试；Agent 恢复还会在重投前被调度；Outbox 进入死信后原任务可永久停留在 `PENDING`。
4. **需求基线分裂**：`PROJECT_PLAN.md` 描述登录、Next.js、Tiptap、独立语义搜索、PDF 导出和托管演示；当前架构文档又声明无登录、无 `users` 表、本地单工作区；实际代码则处于两者之间。
5. **前端已到需要重构的规模**：单个 `app.js` 3398 行、`styles.css` 2520 行，业务状态、请求、轮询、渲染和事件绑定混在一起；没有真正覆盖产品流程的浏览器测试。

推荐的产品方向是：

> **先做登录制、云端保存的 Web App；Supabase 提供 Auth、Postgres/pgvector 和 Storage；保留 Spring Boot API 与 Python Worker。离线能力先限定为 PWA 的草稿、阅读缓存和待同步操作，不做“完整离线 AI 桌面版”。**

不建议推倒全部后端重写。建议大范围重写前端应用壳，重构身份、存储、任务状态机和数据库迁移边界；保留已经有测试和复杂领域知识的解析、检索、生成、学习与 Agent 模块。

## 2. 推荐的产品决策

### 2.1 为什么选择云优先 Web App

当前系统运行依赖 PostgreSQL + pgvector、Redis、Java API、Python Worker、PDF/视觉处理、文件系统以及外部模型 API。把这套运行时完整打包为离线桌面应用，会同时引入数据库升级、后台进程管理、端口冲突、模型下载、密钥保管、崩溃恢复和跨平台安装问题；而外部 AI 调用本身仍然不能离线。

云优先更符合现在的代码资产，也能最短路径实现：

- 跨设备访问、自动备份和稳定的异步任务；
- 登录后的文档、笔记、学习进度与对话持久化；
- 统一的模型额度、成本限制和滥用控制；
- 可部署、可演示、可观测的真实产品。

建议第一版要求登录。匿名试用可以以后用“临时工作区 + 到期清理 + 严格配额”实现，不要为了匿名模式再次引入第二套身份语义。

### 2.2 离线能力做到什么程度

第一阶段离线只做以下能力：

- 用 Service Worker 缓存应用壳；
- 用 IndexedDB 保存正在编辑的 Markdown、最后查看的笔记和同步队列；
- 网络恢复后按版本号串行同步，冲突时保留双方副本；
- 明确显示 `已保存到本机 / 正在同步 / 已同步 / 冲突`；
- AI 生成、向量检索、跨设备历史仍需联网。

不建议第一阶段实现：在用户电脑上捆绑 PostgreSQL、Redis、Java、Python Worker，或承诺完全离线 PDF AI 处理。若未来“隐私优先、完全本地”成为核心卖点，应另建本地运行时：SQLite/SQLite-vec（或同类嵌入式方案）、本地对象目录和单机作业调度器，而不是让云端架构同时扮演桌面架构。

### 2.3 建议的一句话产品定义

> NoteFlow 是一个面向技术课程与论文阅读的 AI 学习工作区：上传 PDF 后生成可追溯到原页的结构化笔记、搜索/问答、闪卡与测验，并在同一编辑器中继续整理和复习。

此定义比“通用 Agent”更容易验证价值，也能覆盖当前最成熟的资产。

## 3. 本次核验范围与证据

### 3.1 工作区状态

- 当前分支：`agent/noteflow-security-remediation`
- 当前 HEAD：`1688876 harden NoteFlow reliability and agent safety`
- 未暂存：66 个跟踪文件，约 `+1545/-529`
- 已暂存：删除 `.claude/launch.json`
- 未跟踪：5 个文件，其中包含 V4/V5 数据库迁移与 Worker 可靠性测试
- 结论：这是一个较大的在途重构，不能按“几处小修”评估，也不能把未跟踪 migration 留在提交之外。

### 3.2 实际验证结果

| 验证项 | 结果 | 解释 |
|---|---:|---|
| Java 单元/集成测试 | 70/70 通过 | 包含显式启用数据库测试后的结果 |
| Python Worker 测试 | 152/152 通过 | 使用隔离 PostgreSQL 后，原先跳过的 12 个数据库测试也执行通过 |
| 浏览器测试 | 1/1 通过 | 只覆盖 rich renderer 的 XSS，不是完整产品流程 |
| JavaScript 语法检查 | 通过 | 不能代替模块测试与浏览器流程测试 |
| Editor 生产构建 | 通过 | 但产物体积显著偏大，见性能章节 |
| 全新数据库迁移 | V1–V5 通过 | schema 可以创建，不代表应用可以启动或业务写入成立 |
| API 全量启动烟雾测试 | **失败** | Spring 无法选择 `StudyService` 构造函数 |
| 默认本地用户的学习数据写入 | **失败** | `user_id=000...001` 不存在，触发外键错误 |

测试通过但启动失败，说明现有测试主要验证“类的局部行为”，还没有覆盖“生产装配后的系统行为”。

### 3.3 规模与性能样本

- Java API 主代码约 9.7k 行，Python Worker 主代码约 16.6k 行；不是适合继续随意堆叠状态的规模。
- Editor 构建产物：CSS 约 1.51 MB（gzip 约 961 KB），主 JS 约 2.24 MB（gzip 约 602 KB），并产生约百个语言相关 chunk。
- PDF 转换并发基准（48 页）：1/2/4/8 Worker 分别约 36.72/37.42/37.25/37.14 页/秒；2 个进程后继续加并发没有收益。
- 学习生成压力模型（5000 chunks、300 目标项）：约 120 万源 token、500 个分组、约 500 次模型调用；规划结果会把闪卡和测验数量扩到各 500，超过配置的 300/120。
- 学习记忆隔离库基准（200 events、16 并发、500 reads）：唯一写 p95 40.56 ms / 600.7 ops/s，重复幂等写 p95 9.0 ms / 1875.7 ops/s，profile read p95 4.21 ms / 5211.2 ops/s。此子系统当前不是首要性能瓶颈。

## 4. 业务需求完成度

评分含义：`完成` 表示核心用户流程已存在且可验证；`部分` 表示后端或 UI 只有一侧完成，或被可靠性问题阻断；`未完成` 表示需求仍停留在计划/文档。

| 业务能力 | 当前事实 | 完成度 | 发布前动作 |
|---|---|---:|---|
| 用户注册、登录、会话 | 当前代码使用固定本地工作区，没有真正 Auth | 未完成 | 采用 Supabase Auth；API 验证 JWT |
| PDF 上传与元数据 | 上传、类型、大小限制、任务创建均存在 | 部分偏高 | 加页数/成本限制、对象存储、取消与删除 |
| PDF 解析与结构还原 | 文本/混合/VLM 路由、版面、公式、图片、缓存较完整 | 部分偏高 | 做真实语料质量基线与失败恢复 |
| 异步任务 | DB task + outbox + Redis + lease 已存在 | 部分 | 修复状态机、原子 claim、重试/死信/恢复 |
| Embedding | Gemini/OpenAI、pgvector、生成任务存在 | 部分偏高 | 修复 >100 条的批次索引，按批落库 |
| 独立自然语言搜索 | 后端有 `/search` 与 `/retrieval` | 部分偏低 | 增加独立 Search 页面并合并 API 语义 |
| 引用式 RAG 问答 | 混合召回、RRF、rerank、上下文与引用较完整 | 部分偏高 | 建立答案质量集、权限过滤与成本限制 |
| AI 笔记 | 生成、分段、版本、质量信息、编辑入口存在 | 部分偏高 | 修复大任务预算、错误状态与分页载荷 |
| 富文本/Markdown/公式编辑 | Milkdown Crepe、KaTeX、CodeMirror 已集成 | 部分 | 重写前端状态层；优化包体与同步语义 |
| 自动保存 | debounce、乐观锁、localStorage fallback 存在 | 部分 | 串行保存、IndexedDB outbox、冲突 UI |
| 文件夹与笔记库 | CRUD、导入、游标 API 存在 | 部分偏高 | 前端真正消费 cursor；列表用 projection |
| 闪卡与间隔重复 | 生成、复习状态、SM-2、Agent 工具存在 | 部分 | 当前被启动错误和 `users` 外键阻断 |
| Quiz 与批量保存/评分 | 生成、作答、提交、AI 评分存在 | 部分 | 同上；增加整条用户流程测试 |
| 学习记忆/个性化 | 事件、profile、策略数据及幂等逻辑存在 | 部分偏高 | 多租户约束、保留策略和隐私控制 |
| Markdown 导出 | AI 笔记脚本/流程存在 | 部分 | 统一为产品内下载；明确导出版本 |
| Notion-friendly 导出 | 没有独立、可验证的产品能力 | 未完成 | 可延后；先保证标准 Markdown |
| PDF 导出 | 未实现 | 未完成 | MVP 后再做，除非是销售/课程交付必需 |
| 文档删除 | 没有完整删除 API/对象级联流程 | 未完成 | 上线前必须实现软删、对象清理和恢复窗 |
| 托管部署 | Compose 只覆盖基础设施，没有完整 API/Worker/Web 镜像交付 | 未完成 | 建立 staging、容器镜像、迁移和回滚 |
| 完整离线桌面 | 当前只是本地开发服务栈，不是桌面产品 | 未完成 | 不纳入云端 MVP |

综合判断：**后端能力完成度高于产品完成度；演示单点功能的能力较强，但从注册到上传、生成、编辑、复习、再次登录的数据闭环尚未成立。**

## 5. 发布阻断问题

### P0-1：API 在干净环境无法启动

证据：`services/api/src/main/java/com/noteflow/study/StudyService.java:18` 和 `:26` 同时定义两个构造函数，Spring 没有明确的注入构造函数，也没有默认构造函数。真实 `bootRun` 在完成 V1–V5 后报 `No default constructor found`。

建议：

- 让 `FlashcardStudyService`、`QuizStudyService` 成为正式 Bean，`StudyService` 只保留一个生产构造函数；或明确给主构造函数加 `@Autowired`。
- 测试专用构造方式改为 package factory 或直接 mock 两个正式 Bean。
- CI 必须保留“干净数据库 + bootRun + `/health`”门禁；不能只跑 controller mock 测试。

### P0-2：身份模型与 schema 自相矛盾

证据：

- `README.md` 与 `LOCAL_AGENTIC_STUDY_ARCHITECTURE.md` 声明无账户、无 `users` 表。
- `V1__baseline.sql:1062` 仍创建 `public.users`。
- `V1__baseline.sql:1928、1936、1944、1960` 的四个外键仍引用 `users(id)`。
- 默认 `LocalWorkspaceService` 返回固定 UUID，却没有生产路径插入相应用户。
- 隔离数据库中先插入 document，再插入 flashcard deck，会准确触发缺失用户外键。

建议不要再给本地模式打补丁后又迁往云端。既然产品方向待定且计划使用 Supabase，应直接做身份 ADR，并按“云端登录制”迁移：

- `auth.users` 作为身份源；业务表使用 `workspace_id` 和审计字段，不再把固定 UUID 当用户。
- 创建 `workspaces`、`workspace_members`，即使首版一个用户只有一个 workspace，也保留未来共享空间的演进路径。
- 所有查询在服务层要求 `WorkspaceContext`，禁止从全局单例取固定用户。
- 迁移完成前，如果仍需本地调试，可临时 seed 默认用户，但只能标为过渡方案。

### P0-3：Worker 把业务失败当进程崩溃重新投递

`services/worker/noteflow_worker/main.py:230-245` 对所有 `future.result()` 异常调用 `requeue_or_dead_letter`；但各流水线已在可预期的 provider、输入或校验错误上把 DB task 标为 `FAILED` 后重新抛异常。因此现在会：

1. 内部 provider 重试；
2. task 已记为失败；
3. 队列层再次重投整个任务；
4. 可能重复付费和重复写入。

更严重的是 `:241-243` 在 `finally` 中安排 Agent resume，发生顺序早于队列重投决策。于是 Agent 可能看到依赖任务失败并继续推理，同时同一个原任务又被重新执行。

建议用显式执行结果替代“异常即重试”：

```text
SUCCESS            -> ACK -> resume once
TERMINAL_FAILURE   -> ACK -> DB FAILED -> resume once
TRANSIENT_FAILURE  -> DB RETRYING -> atomic delayed requeue -> no resume
PROCESS_CRASH      -> lease expiry/recovery -> no immediate business resume
```

重试判断必须来自类型化异常或 `TaskOutcome`，不得依赖错误字符串。

### P0-4：Outbox 死信与业务任务脱节

`TaskOutboxPublisher.java:47-53` 达到最大次数后只标记 outbox `dead_letter_at`，没有把对应 `tasks` 行改为 `FAILED`。前端看到的任务可能永久停在 `PENDING`。

同时 `publishBatch` 在 `@Transactional` 方法中锁定最多 100 行，并在持锁期间顺序调用 Redis；Redis 慢或不可用时会拉长事务、占用连接并阻塞后续 publisher。

建议：

- 达到死信时，和 outbox 一起原子更新 task 为 `FAILED`，写入稳定错误码。
- 提供管理员查询、重放、丢弃 DLQ 的能力与指标。
- 采用短事务 claim：先标记一批事件为 `PUBLISHING` 并提交，再做网络调用，最后短事务落结果。
- 无论是否保留 Redis，Worker 都必须使用 DB CAS/执行租约保证同一个 task 只有一个执行者。

### P0-5：没有原子的任务执行 claim

当前 Worker 的 `mark_task_processing` 可无条件改状态。Outbox 重复发布、lease reclaim 或 stale recovery 可能让相同 `task_id` 并发执行；“相同文档是否有另一个任务”不能阻止同一消息重复执行。

建议：

- 在数据库执行 `UPDATE tasks SET status='PROCESSING', execution_id=?, lease_until=? WHERE id=? AND status IN ('PENDING','RETRYING') RETURNING ...`。
- claim 失败即 ACK 重复消息，不进入业务流水线。
- 完成/失败写入必须携带 `execution_id`，防止旧执行覆盖新执行。
- 所有外部副作用使用稳定 idempotency key：`task_id + stage + artifact_id`。

## 6. 高优先级可靠性与正确性问题

### P1-1：Graceful shutdown 的超时实际上不会生效

`main.py:147` 使用 `with ThreadPoolExecutor(...)`。退出 `with` 时 Python 会先执行 `shutdown(wait=True)`；代码要到 `:183` 才开始所谓 120 秒 drain。只要线程任务仍在运行，程序会在到达 drain 逻辑前无限等待。

建议手动管理 executor 生命周期：停止取任务，持续续租并在截止时间内等待；超时后停止续租、记录 abandoned execution，再 `shutdown(wait=False, cancel_futures=True)`。对子进程任务同样要定义是否允许强杀以及数据库阶段如何恢复。

### P1-2：stale recovery 与 queue lease 时间不一致

默认 queue lease 为 1800 秒，但 parse、notes、embedding、study 的 stale 阈值多为 10 分钟。长任务还持有有效 Redis lease 时，数据库恢复器就可能重新入队，制造重复执行。

其他缺口：

- Study recovery 没有最大重试次数；
- Parse 只选择未超重试上限的记录，达到上限的 stale task 不会被转为 `FAILED`；
- `MAINTAIN_CONVERSATION_MEMORY` 没有 stale recovery；
- 配置注释说 `recovery_interval_seconds=0` 可禁用，代码却用 `max(1, value)` 变成每秒执行；
- 五类恢复共用一个 `try`，第一类异常会跳过后面所有类型。

建议只保留一个权威 lease：优先以 DB execution lease 为真相，Redis 只传递唤醒消息。每种 task type 有独立 policy，明确 timeout、max attempts、backoff、terminal transition 和告警。

### P1-3：Gemini embedding 超过 100 条时结果位置错误

`embeddings/providers.py:52-68` 先按 100 切块，却用 `enumerate(chunks)` 的 `i` 作为输出起始位置。第二个 chunk 的起点会是 1 而不是 100，导致覆盖/错位。当前外层默认 batch size 为 16，通常掩盖了这个错误，也让内层 100 条批处理和并发能力几乎失去意义。

建议：

- 使用 `for start in range(0, len(texts), 100)`，切片和结果范围都用 `start`；
- 只保留一个 batching 层；
- 每批成功立即以内容 hash/upsert 落库并记录 checkpoint，避免最后一批失败后重付前面所有费用；
- 添加 101、200、201 条输入的顺序与缺失响应测试。

### P1-4：Study 生成会静默突破数量与成本上限

当前“每个内容组至少覆盖一项”的策略会把用户/配置上限向上扩张。500 个组可规划 500 张卡和 500 道题，即使配置上限分别是 300 和 120。更关键的是 500 次模型请求与数百万最大输出 token 预算，不适合无提示执行。

建议：

- `max_per_document` 必须是硬上限，不能由覆盖策略覆写；
- 先做章节级抽取/摘要，再对代表性内容生成学习项；
- 创建任务前返回成本预估：页数、源 token、预计请求数、预计生成项、最大金额；
- 设置用户、workspace、任务三级预算和并发额度；
- 大任务必须可取消，取消后停止新的 provider 请求；
- 生成按批落库，UI 渐进展示。

### P1-5：上传只限字节，不限页面与推理预算

50 MB 的 PDF 可以有非常多页面，或包含高分辨率扫描页。字节限制无法约束 VLM 次数和生成成本。

建议在上传预检后计算：页数、扫描页比例、预计 OCR/VLM 页数、预计 token、预计价格。超过软限制让用户选择页段，超过硬限制拒绝或拆分任务。

### P1-6：托管化后存在直接的安全边界缺口

- `/internal/study/**` 当前依赖 localhost 假设，部署后必须使用服务身份、私有网络或直接移除内部 HTTP。
- AI provider key 当前可存于用户设置；托管环境不能以明文持久化。首版优先使用平台统一密钥和配额。若以后做 BYOK，使用 envelope encryption/托管密钥，并确保 API 永不回传原文。
- 所有文档、对话、笔记、学习记录、任务和导出必须在 repository 层统一带 workspace 范围；仅靠 controller 检查不够。
- `TaskEventStream` 目前是全局 emitter/global `lastSnapshotHash` 模型；多用户会互相抑制更新或扩大查询，必须按 principal/workspace 分组。
- `AiSettingsService` 的 `ConcurrentHashMap<UUID, Snapshot>` 只判断 15 秒有效期，没有清除过期 key；多用户长期运行会持续增长。改为有上限的 Caffeine cache，或去掉这层缓存。

## 7. 前端与产品体验问题

### 7.1 建议重写应用壳，不重写编辑器能力

当前静态前端的单文件结构已经妨碍安全重构：

- `app.js` 3398 行同时处理路由、状态、网络、轮询、HTML 字符串、事件与七个模块；
- `styles.css` 2520 行；
- 大量直接 `fetch`，没有统一认证、超时、取消、错误分类、重试与缓存策略；
- 页面切换时依赖手动清 timer/listener，容易出现竞态和泄漏；
- 测试只有 rich renderer XSS，没有上传到复习的 E2E。

建议建立 `apps/web-v2` 或在独立分支重写为 React + TypeScript + Vite：

```text
src/
  app/                 路由、会话、错误边界
  api/                 typed client、JWT、错误映射
  features/documents/
  features/search/
  features/agent/
  features/notes/
  features/study/
  features/settings/
  editor/              现有 Milkdown adapter，先保留
  offline/             IndexedDB drafts/outbox
```

Spring 已是业务 API，不需要为了“看起来现代”再引入 Next.js 服务端层。只有当营销页面 SEO、SSR 或 edge rendering 成为真实需求时，再单独引入 Next.js。编辑器内核也不应现在立刻从 Milkdown 改回 Tiptap；先用 adapter 隔离，依据包体、公式、Markdown round-trip、粘贴和协作需求做对比基准。

### 7.2 分页虽在后端存在，前端没有消费

Document 与 Library API 都通过 `X-Next-Cursor` 返回下一页，但前端只请求第一页。文档请求上限 100，历史内容超过后会在产品里“消失”。

建议改成响应 body：

```json
{
  "items": [],
  "nextCursor": "..."
}
```

这比自定义响应头更容易类型化、测试和跨客户端复用。前端使用无限滚动或“加载更多”，并对 folder/filter 变化取消旧请求。

### 7.3 编辑保存存在竞态与丢失窗口

页面切换时的 fire-and-forget 保存没有完成保证；定时 autosave 也可能产生重叠 PUT 和乐观锁冲突。

建议用单线程保存状态机：

```text
DIRTY -> SAVING(version N) -> SYNCED(version N+1)
             | 新编辑
             v
        DIRTY_AGAIN -> 当前请求完成后立刻串行保存
```

每次编辑先写 IndexedDB，再尝试服务器同步。浏览器关闭时本机草稿仍可恢复，不依赖未必完成的网络请求。

### 7.4 产品信息架构缺口

- 增加独立 Search 页面：搜索是“找原文/笔记”，Agent 是“基于证据生成回答”，两者目的不同。
- General 页不应同时承担上传、解析调试数据和普通用户文档管理；把 debug inspector 放开发模式。
- 增加文档删除、归档、恢复和存储用量反馈。
- 任务错误展示稳定 error code、可重试动作和 trace id，不显示 provider 原始秘密信息。
- 生成 Flashcards/Quiz 前显示数量、范围和预计成本，不要后台突然制造数百项。

## 8. 数据库与 API 性能优化

### 8.1 立即优化

1. `NoteRepository.findCursorPage*` 使用 `SELECT *`，JPA 会加载完整 Markdown，再由 `NoteResponse.summary` 丢掉正文。改为 projection，仅取 id/title/source/folder/timestamps/preview/version。
2. `LibraryMigrationRunner` 在每次启动做 COUNT 子查询和 legacy 表 `findAll()`；迁移应成为一次性 Flyway/Supabase migration，成功后删除 runtime migrator 和旧模型。
3. Outbox 不在数据库事务中做 Redis 网络调用。
4. Embedding/notes/study 每批 checkpoint；失败时只重做未完成批次。
5. 所有列表响应设置稳定 page size 上限；大 JSON 字段和 Markdown 使用 detail endpoint。

### 8.2 连接池预算

当前 API 默认连接池上限约 20；Worker 主进程约 16；两个 parse 子进程又各自可建约 16，总理论连接数可达约 68，还未算迁移、管理和部署副本。Supabase 托管后必须先做连接预算：

- API 实例、Worker 实例、parse 子进程分别设置小池；
- 长事务和 provider 等待期间不持有数据库连接；
- 根据事务语义选择 Supavisor session 或 transaction pool；
- 给迁移与运维保留连接余量；
- 用实际 `pg_stat_activity`、pool wait p95 调整，而不是只看 CPU。

参考：[Supabase 数据库连接管理](https://supabase.com/docs/guides/database/connection-management) 与 [连接方式/Supavisor](https://supabase.com/docs/guides/database/connecting-to-postgres)。

### 8.3 检索性能与质量

混合向量、词法、exact、RRF 与 rerank 是正确方向，应保留。优化重点不是换语言，而是建立基准：

- 典型课程、论文、公式、代码、跨页问题各自建立 golden set；
- 记录 Recall@K、MRR、citation precision、answer groundedness、p50/p95 和每问成本；
- 按 workspace/document 过滤必须进入索引和 SQL 执行计划；
- HNSW/IVFFlat 参数以真实数据量评估，不应提前凭感觉调优；
- `/search` 与 `/retrieval` 合并成一个版本化检索 contract，Agent 复用同一服务。

Supabase 可继续使用 pgvector，参考其 [向量列文档](https://supabase.com/docs/guides/ai/vector-columns)。

### 8.4 前端性能预算

建议设置 CI 预算：

- 初始应用壳 gzip JS ≤ 250 KB；
- Editor 仅在进入编辑页后加载；
- 代码语言支持按需加载，不生成/预取约百个不使用的 language chunk；
- KaTeX、CodeMirror 和 editor CSS 分路由加载；
- LCP、INP、CLS 与 API p95 纳入 staging 监控；
- 对 1000 文档、5000 笔记、1000 卡片的列表做虚拟化/分页验收。

## 9. Supabase 目标架构

### 9.1 推荐拓扑

```text
Browser / PWA
  ├─ Supabase Auth（登录、JWT）
  ├─ Supabase Storage（私有 PDF；签名/可恢复上传）
  └─ Spring Boot API（Bearer JWT）
       ├─ Supabase Postgres + pgvector
       ├─ task/outbox
       └─ Redis（迁移第一阶段先保留）
             └─ Python Worker
                  ├─ 临时下载 PDF
                  ├─ 解析 / embedding / 生成
                  └─ 写回 Postgres 与 Storage
```

Supabase Auth 使用 JWT；Spring 应作为 Resource Server 验证签名、issuer、audience、expiry，并从 `sub` 解析用户。参考 [Supabase Auth](https://supabase.com/docs/guides/auth) 与 [JWT 说明](https://supabase.com/docs/guides/auth/jwts)。

### 9.2 数据访问策略

第一阶段建议浏览器只直接使用 Auth 和签名 Storage 上传；领域数据统一经过 Spring API。原因是当前权限与业务规则都在 Java 服务中，同时开放 Data API 会产生两套授权路径。

两种安全做法选其一并写入 ADR：

1. 业务表放到不暴露给 Data API 的 `app` schema；或
2. 若保留在 exposed schema，为所有表启用完整 RLS，并撤销不需要的 `anon/authenticated` 权限。

Supabase 明确建议 exposed schema 中的表启用 RLS；即使 API 暂时是唯一入口，也应把数据库策略作为第二道防线。参考 [Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security)。

建议核心模型：

```text
auth.users
  1--* workspace_members *--1 workspaces
                              1--* documents
                              1--* notes
                              1--* conversations
                              1--* tasks
                              1--* study artifacts / learning events
```

所有子实体通过父实体或显式 `workspace_id` 可证明归属。不要仅依赖客户端传 `user_id`；由 JWT 与服务端 membership 决定。

### 9.3 Storage 迁移

当前 API/Worker 深度依赖本地绝对 `storage_path`。应引入：

```text
DocumentObjectStore
  createUpload()
  openForRead(objectKey)
  putDerivedAsset(objectKey, bytes)
  deleteTree(documentId)
```

数据库保存稳定 object key、bucket、etag/hash、size、mime type，不保存某台机器上的绝对路径。Worker 下载到 task 专属临时目录，解析后清理，再把派生图片/导出文件上传到私有 bucket。

大 PDF 用可恢复上传与 signed upload URL；对象访问遵循 workspace 权限。参考 [Supabase Storage](https://supabase.com/docs/guides/storage)、[Storage 访问控制](https://supabase.com/docs/guides/storage/security/access-control) 和 [可恢复上传](https://supabase.com/docs/guides/storage/uploads/resumable-uploads)。

### 9.4 Queue 选择

迁移第一阶段建议先保留 Redis，先修正确性，再评估是否切换 [Supabase Queues](https://supabase.com/docs/guides/queues) / [PGMQ](https://supabase.com/docs/guides/queues/pgmq)。直接同时迁移 Auth、Storage、数据库和队列会让故障定位困难。

若以后换成 PGMQ，可减少 Redis 依赖，并让 task + message 更接近同一数据库事务；但必须先验证优先级、延迟投递、可见性超时、吞吐和运维能力。现有 interactive/user-visible/background 三优先级可使用三条队列和加权消费，不要假定一个 FIFO 队列自动满足优先级语义。

### 9.5 迁移所有权

当前不能同时让 Flyway、手工 `psql` 列表和 Supabase CLI 都自称 schema owner。推荐：

- 把现有 V1–V5 整理为 Supabase baseline；
- 新变化只生成 `supabase/migrations/*`；
- hosted profile 禁止 Spring 自动修改 schema；
- CI 用 Supabase CLI 从零 `db reset`，再执行 API/Worker 集成测试；
- 生产 push 先 dry-run，迁移与应用版本有回滚/前向修复方案。

参考 [Supabase 本地开发与 migration workflow](https://supabase.com/docs/guides/local-development/cli-workflows)。

## 10. 当前未提交重构：保留、修改还是撤回

不建议整体撤回 66 个文件。很多修改有明确价值；风险集中在少数可靠性路径，应“选择性冻结并改正”。

### 建议保留

- per-task AI 设置隔离与线程复用后的清理；
- queue 时间戳使用 locale-independent 格式；
- PUT 中 null 与“未提供字段”的区分；
- learning memory UUID cast 与若干参数化 SQL；
- 批量 DB 写入、稳定的 AI note section id；
- API error handler 与敏感 key 从 header 传递；
- 查询去 N+1、游标分页、部分 projection 改动；
- 前端 listener 修复和错误提示；
- Tempo volume 与 Collector memory limiter；
- 路径遍历防护、毒消息处理、schema governance 方向。

### 合并前必须修改

| 改动 | 处理意见 |
|---|---|
| Worker blanket retry / DLQ | 停止按所有异常重投；引入 `TaskOutcome`/类型化错误 |
| `finally` 中 Agent resume | 只在 terminal outcome 后调度一次 |
| `ThreadPoolExecutor` shutdown | 手工 executor 生命周期，让 drain deadline 真实生效 |
| stale recovery | lease 对齐、分类型隔离、补 max retries/terminal transition/memory recovery |
| Outbox dead-letter | 同步把业务 task 置为 FAILED，增加运维重放 |
| Outbox 事务内 Redis 调用 | 拆成 claim/publish/finalize 短事务 |
| Gemini embedding batching | 修复 start offset，并补 101/200/201 测试 |
| V4/V5 migrations | 与使用它们的代码和测试同一个提交；CI 自动应用全部 migration |
| CI Worker schema | 不再硬编码只执行 V1–V3 |
| “已全部修复”文档 | 改成实施记录，不能充当验证结论 |

### 是否需要撤回

如果必须立刻恢复一个较安全的本地演示版本，可以**选择性撤回/关闭**以下在途行为，而不是回滚整个工作区：

1. Worker 对所有逃逸异常进行自动重投；
2. Agent resume 放在 `finally`；
3. Outbox 达上限后只写死信、不结束 task 的行为。

更推荐 fix-forward：保留毒消息与 lease 的基础设施，按本文状态机完成修复。`StudyService` 启动错误和 `users` 外键问题不属于本轮未提交改动，撤回工作区也解决不了。

## 11. 建议的重构边界

### 保留并加固

- Java Spring Boot 领域 API；
- Python PDF/VLM/Markdown pipeline；
- 混合检索、RRF、引用与 reranking；
- Flashcard、Quiz、学习记忆的领域模型；
- Agent snapshot/tool orchestration；
- 已有的 Worker/API 测试资产。

### 大范围重构

- 身份与 workspace context；
- 本地文件路径到 ObjectStore adapter；
- task/outbox/retry/lease 状态机；
- schema migration 所有权；
- 前端应用壳、API client、路由和状态管理；
- autosave/offline draft；
- 多租户 SSE/事件推送；
- 成本、配额、取消和使用量审计。

### 可以删除或合并

- runtime `LibraryMigrationRunner` 与 legacy `document_editable_notes`；
- 重叠的 `/search`、`/retrieval` contract；
- 没有 producer/consumer 的 `ASK_DOCUMENT`、`EXPORT_MARKDOWN` task type，除非补齐实现；
- 声称 Next.js/Tiptap/无 users 表但与代码不符的陈旧文档段落；
- 用户界面中的解析调试细节，移入开发者 inspector。

## 12. 分阶段实施路线

### Phase 0：建立可信基线（2–4 天）

- 修复 `StudyService`，新增 full-context boot test；
- 决定并记录“云端登录制”ADR；
- 修复/暂时桥接 `users` 外键，确保 study 写入真能运行；
- 把 V4/V5 与相关代码、测试纳入同一提交；
- CI 从零应用全部 migration；
- 给当前 66 文件按“保留/修改/删除”拆成可审查提交。

验收：干净 clone + 单条命令启动；上传 1 个 PDF；完成 parse、embedding、note、flashcard、quiz、RAG；API 重启后数据仍在。

### Phase 1：任务可靠性（4–7 天）

- 定义状态机与 `TaskOutcome`；
- DB 原子 claim/execution lease；
- 修正 outbox 短事务、死信和重放；
- 对齐 stale/lease/retry；
- 修复 shutdown；
- 补重复消息、进程崩溃、Redis 中断、provider 429/400、Agent wait 的集成测试。

验收：每个故障注入场景都不重复生成、不永久 `PENDING/PROCESSING`、不重复付费、可追踪到 terminal outcome。

### Phase 2：Supabase 身份与数据（5–10 天）

- Supabase project、Auth、workspace schema 与 RLS；
- Spring JWT 验证和 `WorkspaceContext`；
- 现有固定 workspace 数据迁移到首个用户；
- service-role 仅限后端/Worker；
- 连接池预算和 hosted 配置；
- 敏感 settings 迁移策略。

验收：两个测试用户互相不能读取、搜索、订阅或下载对方任何对象；自动化权限测试覆盖每个聚合根。

### Phase 3：Storage 与前端 v2（2–4 周，可并行）

- ObjectStore adapter + Supabase 私有 bucket；
- React/TypeScript/Vite 应用壳；
- Documents、Search、Agent、Notes、Study、Settings 模块化迁移；
- 现有 Milkdown 经 adapter 接入；
- IndexedDB draft/outbox 与冲突 UI；
- 分页、删除、取消和成本预览。

验收：首屏/编辑器分包满足预算；离线编辑后可恢复；跨设备登录看到相同数据；对象删除和恢复符合策略。

### Phase 4：部署、质量与成本（1–2 周）

- staging/production 镜像与 migration job；
- 完整 Playwright 主流程；
- 检索/生成质量集；
- tracing、metrics、structured logs、DLQ 告警；
- per-user quota、速率限制、预算和审计；
- 隐私、数据删除、备份恢复演练。

验收：可重复部署、可回滚、可恢复备份；有公开可用的演示环境；错误和成本能在仪表盘上解释。

## 13. CI 与 Definition of Done

每个合并请求至少执行：

1. 全新 Postgres 从零应用**全部** migrations；
2. API full-context boot 与 health smoke；
3. Java tests + Python tests（数据库测试不可静默 skip）；
4. Worker 真实消费一条任务并达到 terminal state；
5. Web build、TypeScript、lint、bundle budget；
6. Playwright：登录、上传、等待解析、生成笔记、编辑、搜索/问答、闪卡/Quiz、退出再登录；
7. 两用户隔离测试；
8. 故障注入：Redis/DB/provider 短暂失败与 Worker 被终止；
9. migration upgrade test：上一个生产 schema + 样本数据升级到当前版本；
10. 日志/错误响应扫描，确保不泄漏 token、API key、signed URL 或用户正文。

发布 Definition of Done：

- 没有无法结束的任务状态；
- 每个模型调用可关联 user/workspace/task、token、费用与结果；
- 用户可删除数据，后台对象与派生物有一致的生命周期；
- 所有列表均可访问完整历史；
- 所有生成内容能回到证据页，引用失效有明确状态；
- 前端离线时不谎称已同步；
- 干净环境安装、迁移、启动和主流程有自动证据。

## 14. 文档治理

建议新增并强制维护以下四份短文档，而不是继续让大计划文档承担所有真相：

1. `PRODUCT_REQUIREMENTS.md`：按用户流程列能力、范围和验收标准；
2. `ADR-001-cloud-web-supabase.md`：云端/离线决策及替代方案；
3. `TASK_STATE_MACHINE.md`：状态、事件、重试、lease、幂等与死信；
4. `DEPLOYMENT_RUNBOOK.md`：环境、migration、回滚、备份、告警。

`PROJECT_PLAN.md` 可保留为历史愿景，但标注哪些技术选择已被替代。`FULL_PROJECT_REVIEW_2026-08-30_ZH.md` 与 `REMEDIATION_IMPLEMENTATION_2026-08-30_ZH.md` 应标为历史审计/实施记录；“已写代码”不等于“已经在干净生产装配中验证”。

## 15. 最终优先级

如果只能先做十件事，顺序如下：

1. 修复 API 干净启动；
2. 确定云端登录制并统一身份/schema；
3. 修复 task outcome、原子 claim、重试与 Agent resume；
4. 修复 outbox 死信和事务内网络调用；
5. 修复 stale recovery 与 graceful shutdown；
6. 修复 embedding >100 条错位；
7. 加页数/token/调用次数/金额硬预算与取消；
8. 建 Supabase Auth + Storage adapter + workspace 隔离；
9. 重写 React/TypeScript 前端应用壳，保留现有编辑器内核；
10. 用完整 E2E、故障注入和质量/成本基准定义“完成”。

一句话判断：**NoteFlow 值得继续做，也不需要推倒全部重来；但需要停止继续横向加功能，先把产品方向、身份、任务可靠性和前端架构四件事收拢。**
