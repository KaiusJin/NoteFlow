# NoteFlow 项目审计报告

> 审计日期：2026-07-30
>
> 审计方式：代码审查、配置与依赖扫描、修复实施、空库迁移验证、单元与 PostgreSQL 集成测试。
> 审计范围：`services/api`、`services/worker`、`apps/web`、`apps/editor`、`infra`、根目录运行配置、测试与主要技术文档。

> **修复跟进（2026-07-30）**：用户已确认产品边界为“仅限本机单用户运行”。正文保留首次审计时的证据快照；下表及“修复实施记录”优先于正文原始状态。

| 风险 | 跟进状态 | 已完成内容 | 仍需处理 |
|---|---|---|---|
| SEC-01 | 在既定边界内已处理 | API 默认绑定 `127.0.0.1`；PostgreSQL/Redis Compose 端口仅发布到回环地址 | 若未来允许局域网、远程或多用户访问，必须先增加认证、授权和生产级基础设施凭据 |
| SEC-02 | 已修复当前已知入口 | 设置页所有模型、Key 提示和错误消息在进入 HTML 前转义；后端限制模型 ID 与 Key 输入；Web 入口已增加 CSP | 浏览器 E2E/XSS 回归门禁 |
| SEC-03 | 部分缓解 | 本机 `.env` 权限已收紧为 `0600` | 数据库中的 AI Key 仍为明文；本地单用户边界下暂未引入 Keychain |
| SEC-04 | 已修复主要路径 | capability 由 API 根据本轮用户原文和 UI 开关签发；写入需读后写与 ETag；删除同时要求 delete capability、原文删除意图和 `confirm` | capability 尚未做签名 nonce（当前 API/Worker 均限本机） |
| REL-01 | 已修复 | 任务与 `task_outbox` 同事务提交；Publisher 使用 `SKIP LOCKED`、稳定 event ID、指数退避与保留期清理 | Redis 仍采用 at-least-once，消费者必须保持幂等 |
| REL-02 | 已修复 | 租约读取、核验、删除和按优先级重投递改为单个 Lua 脚本；续租也改为原子 Lua | 无 |
| SEC-05/06 | 部分缓解 | PDF 文件头校验；笔记导入有界读取；Provider 全局并发；Pillow/pypdf 已升级到修复版本 | OS 级恶意 PDF 沙箱和专门的复杂度 fuzz 仍未完成 |
| LOG-01/02 | 已修复 | 生成版本使用事务 advisory lock；Note 更新强制 expectedUpdatedAt/Markdown Hash，冲突返回 409 | 文档级编辑器旧接口仍可进一步统一到标准 HTTP ETag |
| LOG-03 | 主迁移路径已修复 | Flyway V1/V2、`ddl-auto:validate`、空库启动及现有库 baseline 均验证通过；Java 启动期建表 Bean 已停用 | Worker Repository 中仍保留部分幂等兼容 DDL，尚未完全删除 |
| DEP-01 | 已修复 | `uv.lock` 含 Hash；LangGraph 1.2.10/checkpoint 4.1.1；Pillow 12.3.0、pypdf 6.14.2；Gradle 9.6 Wrapper/locking；Dependabot、pip-audit、Trivy、SBOM 门禁 | 本机未执行 Trivy，交由 CI 执行 |
| PERF | 大部分已实施 | 文档/笔记 cursor、Markdown Range、CSS 原生虚拟化、任务 SSE、Quiz 批量保存、Provider 全局并发、HNSW 巡检、真实 token/费用字段 | Agent 工具目录 Prompt Cache、跨文档 PDF/OCR/Markdown 全局内容寻址缓存未完成 |
| OBS | 已实施基础设施 | Java Logstash JSON、Worker logging、`X-Trace-Id`、Actuator/Prometheus、Outbox event ID 链路 | 还没有 OpenTelemetry exporter 与完整仪表盘 |
| TEST | 门禁已增强 | CI 默认运行 PostgreSQL 集成测试、XSS/并发单测、依赖审计、Trivy 和 SBOM | 浏览器 E2E、恶意 PDF 沙箱/fuzz、Redis/DB 故障注入仍未完成 |
| SEC-07 | 已修复当前已知入口 | 资源文件读取使用真实路径，限制在托管 storage root 内，并验证 `.png` 扩展名和 PNG 签名 | 新增其他资源类型时必须复用同一边界服务 |
| 浏览器防御纵深 | 已增加 | Web 入口增加 CSP，仅允许本地脚本、样式、图片和 localhost API 连接 | 仍建议增加浏览器 E2E/XSS 回归门禁 |
| 文档可移植性 | 已修复 | `CHUNKING_STRATEGY.md` 中的本机绝对路径改为仓库相对链接 | 无 |

跟进验证：API 69 项（包含 PostgreSQL 集成测试）全部通过；Worker 137 项（包含 PostgreSQL 集成测试）全部通过；Flyway 从空库执行 V1/V2 成功，现有库成功 baseline 并升级到 v2；`pip-audit` 返回 0 个已知漏洞；前端语法、Compose 配置和 `git diff --check` 通过。

## 0.1 修复实施记录

- Agent 写授权不再信任模型生成的 `confirm`：API 只在用户原文含对应写/删除意图且 UI 明确授权时，把 capability 写入服务端 assistant placeholder；Worker 只读取服务端元数据。
- Note 编辑工具必须携带 `expectedMarkdownHash` 和 `expectedUpdatedAt`，API 使用条件 UPDATE；并发修改不会再静默覆盖。
- DB→Redis 改为 Transactional Outbox。Redis 临时故障只会推迟 `available_at`，不会让 `PENDING` 任务永久失联。
- Redis 过期租约回收和续租使用 Lua 原子执行；保留稳定 `eventId` 贯穿 API、Redis 与 Worker 日志。
- Quiz/Flashcard `MAX(version)+1` 前增加按文档和产物类型的事务 advisory lock。
- Flyway 接管正式 Schema，Hibernate 改为 `validate`；Compose 初始化 SQL 不再是应用启动的唯一迁移路径。
- 上传 PDF 在事务回滚后自动删除已写入的磁盘文件；数据库异常不再伪装成 `NOT_STARTED`。
- 文档和笔记列表采用上限 200 的 keyset cursor；Markdown 支持字符 offset/length；Quiz 答案一次批量保存。
- 活跃任务改用 SSE 推送，浏览器只在流断开时回退轮询；长列表启用 `content-visibility`。
- Notes 与 Embedding Provider 使用进程级信号量，避免“任务并发 × 文档内并发”乘法放大。
- HNSW 对新维度启动时创建并每 5 分钟巡检；失败进入结构化日志。
- Provider usage 保存真实输入/输出 Token；费用按可配置的每百万 Token 单价计算，不再把估算 Token 冒充账单。

## 0.2 本轮仍未完成的项目

以下项目没有被标记为已完成，原因是它们需要单独的架构迁移或测试基础设施，而不是局部补丁：

1. Python `Repository`、Conversation/Memory/Study store 中仍留有历史兼容用的幂等 `CREATE/ALTER`；正式启动路径已由 Flyway 管理，但这些兼容 DDL 尚未全部删除。
2. `apps/web/app.js`、Worker 大型 Repository 和 `StudyService` 尚未按领域彻底拆文件；本轮只处理了安全、可靠性与热点性能路径。
3. 已有 VLM input fingerprint、Embedding content hash 和解析 manifest 复用，但尚未实现跨文档、跨任务共享的 PDF/OCR/Markdown/模型结果全局 CAS。
4. Agent Prompt 已避免重复 evidence 正文并记录真实 usage，但尚未接入 Provider 显式 Prompt Cache，也未把所有长 observation 改为增量 handle。
5. 浏览器 Playwright E2E/XSS、恶意 PDF fuzz/OS 沙箱、Redis/数据库故障注入仍未落地；CI 当前覆盖单元、PostgreSQL 集成、依赖扫描、SBOM 和文件系统漏洞扫描。
6. 已提供 JSON 日志、Trace ID、Prometheus endpoint；尚未接入 OpenTelemetry collector、跨 Java/Python span 和 Grafana dashboard。
7. AI Key 仍明文存于本机数据库。鉴于明确的“本机单用户”边界，本轮没有引入 macOS Keychain；若机器存在多个不互信系统用户，这仍需升级。

## 1. 结论摘要

NoteFlow 已经不是简单的 PDF 摘要工具，而是一个包含文档解析、多模态处理、混合检索、可恢复异步任务、学习记忆、测验/闪卡和 Tool Calling Agent 的本地 AI 学习工作台。核心流程的工程质量总体不错，尤其是：

- 检索链路具备并行召回、融合、去重、重排、证据判断和上下文预算；
- 模型输出普遍使用结构化 Schema、重试、置信度和引用校验；
- Worker 已经区分 CPU 进程池与 I/O 线程池，并对部分模型调用设置全局信号量；
- Redis 队列具备优先级、租约、过期恢复和后台任务防饥饿机制；
- Agent 具备最大步数、时间/Token 预算、重复调用拦截、读后写约束、异步暂停/恢复及质量反思；
- 学习事件、SRS 更新和部分生成任务已经考虑幂等、锁和断点续作。

但当前安全模型严格依赖“单机、本地、可信用户、可信网络”这一前提。只要 API、PostgreSQL 或 Redis 被暴露到局域网、容器网络或公网，现有边界就不再安全。审计没有发现已提交到 Git 的真实密钥，也没有发现明显的通用 SQL 注入或任意命令执行入口；不过发现了 4 项高优先级安全问题、2 项高优先级可靠性问题，以及若干中优先级性能和逻辑问题。

总体判断：

| 维度 | 评价 | 说明 |
|---|---|---|
| 本地个人开发可用性 | 良好 | 在仅回环地址、可信本机的条件下，主要流程合理 |
| 数据与模型调用质量 | 良好 | 证据、Schema、置信度、去重与断点恢复设计较完整 |
| 网络暴露安全性 | 较弱 | API 无认证，数据库和 Redis 使用弱默认值且端口直接发布 |
| Agent 安全性 | 中等 | 有提示词注入与工具前置条件，但缺少不可伪造的授权票据 |
| 异步可靠性 | 中等 | 有租约恢复，但 DB→Redis 仍存在提交后丢消息窗口 |
| 可扩展性能 | 中等 | 小规模本地使用足够；列表、轮询、DDL、Agent Prompt 成本会随数据量放大 |
| 可维护性 | 中等偏上 | 模块边界清晰，但存在超大文件、运行时建表及过度压缩的 Java 实现 |
| 测试保障 | 良好但不完整 | 单元测试覆盖较强，数据库集成与浏览器安全测试未在本次环境执行 |

本次没有发现“必须立即停止使用”的 Critical 问题。若保持纯本地使用，最高优先级应是限制监听/端口暴露、保护密钥、修复设置页 XSS 和建立 Agent 工具授权层；若计划部署为多人或远程服务，这四项应视为上线阻断项。

## 2. 审计与验证记录

### 2.1 已执行检查

- 使用 `rg` 扫描硬编码 URL、模型名、默认账号、固定 UUID、绝对路径、潜在密钥格式；
- 检查 `.env` 是否被 Git 跟踪，只记录变量名和是否为空，未把密钥值写入报告；
- 检查所有 Spring Controller、CORS、文件上传/下载、AI 设置、任务队列和本地 workspace 边界；
- 检查 Python 中的网络请求、子进程、动态 SQL、异常吞噬、并发池和运行时 Schema 变更；
- 检查前端所有主要 `innerHTML`/`insertAdjacentHTML` 路径及 Markdown/LaTeX 转换；
- 追踪上传→解析→Embedding→检索→对话 Agent→学习产物的状态流；
- 核对 npm 官方 Advisory 接口和 LangGraph/pypdf 官方安全公告。

### 2.2 测试结果

- Python `compileall`：通过；
- Worker 测试：共 134 项，122 项通过，12 项因需要本地 PostgreSQL 而跳过；
- API Gradle 测试：52 项中 47 项通过，5 项 PostgreSQL 集成测试跳过，0 失败；
- npm 全依赖审计：267 个依赖，0 个已知漏洞；
- Gradle 构建成功，但报告使用了未来与 Gradle 10 不兼容的弃用特性；
- 没有执行真实模型调用、恶意 PDF 动态沙箱测试、浏览器端 E2E/XSS 测试和负载测试。

## 3. 风险总表

风险等级含义：

- **高**：远程化/多人化之前必须处理，或可能导致敏感数据泄漏、越权写入、任务永久丢失；
- **中**：当前本地模式下可接受，但数据量、并发、恶意输入或部署方式变化后会成为实际问题；
- **低**：防御纵深、可维护性或边缘条件问题；
- **建议**：并非缺陷，但值得工程化。

| ID | 等级 | 类型 | 结论 |
|---|---|---|---|
| SEC-01 | 高 | 暴露面 | API 无认证；PostgreSQL/Redis 以弱默认配置发布到宿主机端口 |
| SEC-02 | 高 | XSS | 自定义模型名可形成持久化 DOM XSS |
| SEC-03 | 高 | 密钥 | AI API Key 明文存库，本地 `.env` 权限为 `0644` |
| SEC-04 | 高 | Agent | 写工具授权主要依赖模型判断，`confirm: true` 可由模型自行生成 |
| REL-01 | 高 | 消息可靠性 | DB 提交后 Redis 入队失败会留下永久 `PENDING` 任务 |
| REL-02 | 高/中 | 并发可靠性 | Redis 过期租约回收不是原子操作，多 Worker 可重复投递 |
| SEC-05 | 中 | 文件安全 | PDF 仅按扩展名校验，缺少魔数、复杂度预算和隔离 |
| SEC-06 | 中 | 资源耗尽 | 笔记导入整文件读内存，多个入口缺少速率/成本限制 |
| SEC-07 | 中 | 文件边界 | 资源下载信任数据库中的绝对路径，缺少存储根目录校验 |
| DEP-01 | 中（潜在） | 依赖 | 当前 LangGraph transitive checkpoint 组件落在已知漏洞版本区间 |
| LOG-01 | 中 | 并发逻辑 | Quiz/Flashcard 版本号使用 `MAX(version)+1`，并发创建存在竞争 |
| LOG-02 | 中 | Agent 编辑 | 乐观锁 Hash 是可选参数，完整更新没有版本条件 |
| LOG-03 | 中 | 状态/迁移 | JPA `ddl-auto:update` 与多套运行时 DDL 并存 |
| PERF-01 | 中 | API | 多个列表和详情接口无分页，会全量加载实体和大文本 |
| PERF-02 | 中 | Agent 成本 | 每一步重复发送完整工具目录、上下文和历史，Token 成本放大 |
| PERF-03 | 中 | 前端网络 | 任务轮询、逐题顺序保存造成额外请求与刷新压力 |
| PERF-04 | 中 | 并发 | Notes/Embedding 的文档内线程池会被 Worker 任务并发再次放大 |
| PERF-05 | 中 | 向量索引 | 新 Embedding 维度出现后，HNSW 索引不一定自动补建 |
| MAINT-01 | 中 | 可维护性 | 超大前端文件、超大 Repository、单行压缩 Java 服务增加变更风险 |
| OBS-01 | 中 | 可观测性 | 主要使用 `print/System.out`，缺少结构化日志、指标和 Trace 关联 |
| TEST-01 | 中 | 测试 | 数据库集成、浏览器端安全和并发故障注入未成为默认门禁 |

## 4. 安全审计详情

### SEC-01：无认证服务与弱默认基础设施

证据：

- [LocalWorkspaceService.java](../../services/api/src/main/java/com/noteflow/workspace/LocalWorkspaceService.java) 明确把固定 workspace 当作当前用户，而不是认证身份；
- [application.yml](../../services/api/src/main/resources/application.yml) 没有 `server.address: 127.0.0.1`，也没有 Spring Security；
- [CorsConfig.java](../../services/api/src/main/java/com/noteflow/common/CorsConfig.java) 对任意 localhost 端口开放全部 API 的 GET/POST/PUT/DELETE；
- [docker-compose.yml](../../docker-compose.yml) 使用 `noteflow/noteflow` 数据库凭据，并将 `5432`、`6379` 直接发布到宿主机；Redis 无密码和 TLS。

影响：

- CORS 不是访问控制，非浏览器客户端不受它限制；
- 任何能连接 API 的进程都可以读取文档、笔记、对话、Agent trace、学习记忆，并创建/修改/删除内容；
- `/settings/ai` 允许写入新的 Provider Key；即使响应不回显完整 Key，攻击者也能替换设置并驱动付费模型调用；
- Redis 暴露后可注入、删除或重复投递任务；PostgreSQL 暴露后可直接读取明文 Key 和用户内容。

建议：

1. 本地模式显式绑定 `127.0.0.1`，Docker 端口使用 `127.0.0.1:5432:5432` 和 `127.0.0.1:6379:6379`；
2. Redis 开启 ACL/密码，数据库改随机强密码并通过环境注入；
3. 引入“本地安装令牌”，桌面前端在每次请求附带短期 Bearer Token；
4. 若支持远程/多人，必须增加真正的账户、Session/OIDC、资源级授权和 CSRF/Origin 策略；
5. 将 `/internal/**` 与用户 API 分离，使用 Worker 专用凭据和独立端口/Unix Socket。

### SEC-02：设置页存在持久化 DOM XSS

证据：

- [app.js](../../apps/web/app.js) 的设置表单把 `current.geminiLlmModel`、`current.openaiLlmModel`、Embedding Model 等值直接放进 `value="..."`；
- `renderEffectiveSettings()` 也将返回值直接拼接到 HTML；
- 后端只对字段做 `trim()`，没有字符白名单或长度限制；
- 这些值存入 `user_ai_settings`，因此属于持久化输入。

可行路径：

1. 通过未认证的 `PUT /settings/ai` 保存包含引号和 HTML 事件属性的模型名；
2. 用户打开 Settings 页面；
3. 值进入 `innerHTML`，形成属性逃逸或新元素注入。

建议：

- 表单用 DOM API 设置 `.value`，不要把动态值拼入 HTML；
- 所有展示字段统一经过 `escapeHtml`；
- 后端对模型 ID 设置长度上限和允许字符，例如 `[A-Za-z0-9._:/-]`；
- 增加严格 CSP，至少禁止 inline script/event handler；
- 添加包含引号、尖括号、SVG、事件属性的浏览器回归测试。

Markdown/对话富文本的主渲染路径做得较好：`renderRich()` 先转义普通文本，再处理受限 Markdown 子集；本次未在该路径确认可利用 XSS。

### SEC-03：API Key 明文存储与文件权限

证据：

- [AiSettings.java](../../services/api/src/main/java/com/noteflow/settings/AiSettings.java) 直接将 Gemini/OpenAI Key 存为字符串列；
- Worker 在任务开始时从同一表读取并应用设置；
- 根目录 `.env` 未被 Git 跟踪，这是正确的；但当前权限为 `-rw-r--r--`，并且至少存在一个非空模型 Key。

影响：

- 数据库备份、调试导出、同机其他用户、误配置的数据库端口都可能泄漏 Key；
- XSS、无认证 API 或 Agent trace 泄漏与明文 Key 组合后会放大攻击面。

建议：

- 优先使用 macOS Keychain/系统 Secret Store，数据库只保存 secret reference；
- 若必须存库，使用安装级主密钥做 envelope encryption，主密钥不进入数据库；
- `.env` 权限设为 `0600`，提供 `.env.example` 而不是复制真实文件；
- Key 更新、读取和模型调用写审计事件，但日志永不包含 Key；
- 若当前 `.env` 曾进入云同步、工单、截图或备份，轮换其中非空 Key。

### SEC-04：Agent 缺少不可伪造的工具授权

已有优点：

- [agent.py](../../services/worker/noteflow_worker/conversation/agent.py) 明确声明“证据和消息是非可信数据”；
- 工具参数严格按 Schema 校验；
- 编辑前要求同一 Run 先成功 `read_markdown`；
- 引用必须指向已检索证据；
- 重复工具调用、最大步数、Token 预算和反思次数均有边界；
- 学习记忆修正同时检查用户原始请求中的显式关键词。

缺口：

- `delete_section` 的 `confirm: true` 由 LLM 自己生成，不是由 UI/服务端签发的确认票据；
- `edit_markdown`、`insert_section`、`rewrite_paragraph`、`update_note` 只需模型判断“用户要求了修改”；
- `save_artifact`、设置学习目标/偏好、记录反馈、关联产物等写操作不在统一 mutation policy 中；
- 恶意文档或历史消息虽然被标记为“不可信”，仍与工具说明一起进入同一模型上下文；Prompt 指令不是权限边界。

建议采用 Capability 模型：

1. 服务端根据当前用户动作生成短期、单用途、绑定资源 ID 的 capability；
2. 工具调用必须携带 capability，Worker 验证签名、动作、资源、过期时间和一次性 nonce；
3. 工具按风险分级：只读自动执行、可逆写入需会话授权、删除/覆盖需逐次确认、外部副作用需二次确认；
4. 为所有写工具统一执行 `policy_check()`，不要只靠 Prompt；
5. 对文档证据、工具观察和用户消息使用结构化隔离标签，并增加间接 Prompt Injection 测试集；
6. Agent 展示“计划执行什么、将修改什么”，用户确认后再签发 capability。

### SEC-05/06：文件验证和资源耗尽

证据：

- [DocumentService.java](../../services/api/src/main/java/com/noteflow/documents/DocumentService.java) 只检查文件名是否以 `.pdf` 结尾；
- 全局上传上限为 50 MB，但没有页数、对象数、解压后大小、图片像素、OCR 时间或 CPU 预算；
- [LibraryController.java](../../services/api/src/main/java/com/noteflow/library/LibraryController.java) 使用 `file.getBytes()` 把导入文件一次性读入堆；
- 没有接口级速率限制、并发配额和模型费用预算。

建议：

- 校验 PDF `%PDF-` 魔数，并用独立进程做预检；
- 设置页数、解压后字节、图片像素、对象/字体数量、处理时间和内存上限；
- 解析进程使用 OS 级资源限制，超时强制终止；
- 笔记导入限定扩展名/MIME/字符数，采用流式读取；
- 对上传、生成、Agent Run 和模型调用设置 token bucket、每日预算与熔断；
- 当前 pypdf 安装版本为 6.13.3，已高于已知 LZW 内存耗尽问题的修复版本 6.1.3；但业务层复杂度预算仍然必要。参考 [GitHub Advisory GHSA-jfx9-29x2-rv3j](https://github.com/advisories/GHSA-jfx9-29x2-rv3j)。

### SEC-07：资源文件路径缺少根目录约束

[DocumentPageAssetController.java](../../services/api/src/main/java/com/noteflow/assets/DocumentPageAssetController.java) 和 [DocumentVisionController.java](../../services/api/src/main/java/com/noteflow/vision/DocumentVisionController.java) 直接把数据库路径交给 `FileSystemResource`。正常流程中路径由可信 Worker 产生，当前没有用户直接写路径的入口，因此不是直接路径穿越；但数据库写权限一旦被攻破，攻击者可以借 API 读取进程可访问的任意文件。

建议在返回文件前执行：

- `toRealPath()`；
- 验证路径位于指定 storage root；
- 验证文件类型、大小和扩展名；
- 使用数据库中的相对对象 Key，而不是绝对路径。

### DEP-01：依赖与供应链

- npm 官方审计对 267 个依赖返回 0 个已知漏洞；
- Python 依赖采用较宽范围，例如 `langgraph>=0.2,<1`，缺少可复现 lock 和 Hash；
- 当前环境 `langgraph==0.6.11`、`langgraph-checkpoint==3.0.1`。官方公告指出 checkpoint `<4.0.0` 的 Cache 在特定配置下可回退到不安全反序列化。当前代码 `graph.compile()` 没有启用 Cache 或持久化 Checkpointer，因此本次没有确认可达利用路径，但未来一旦启用持久化 Agent 状态就会成为实际风险。参考 [LangGraph GHSA-mhr3-j7m5-c7c9](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-mhr3-j7m5-c7c9) 和 [GHSA-fjqc-hq36-qh5p](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-fjqc-hq36-qh5p)；
- Spring Boot 固定为 3.3.5，解析出的 Spring Framework 为 6.1.14。它不是本次观察到的直接漏洞来源，但版本已明显落后，应建立自动升级和 Advisory 门禁，而不是长期依赖人工检查；
- Gradle 没有 Wrapper/Dependency Locking；Python 也没有 lock，环境重建可能得到不同依赖。

建议：

- Python 使用 `uv.lock` 或带 Hash 的 constraints 文件；
- 固定并升级 LangGraph/checkpoint，若不需要 LangGraph 可评估移除；
- 增加 `pip-audit`/OSV、OWASP Dependency Check 或 Dependabot；
- 提交 Gradle Wrapper，启用 dependency locking 和版本目录；
- 构建产物生成 SBOM，并对容器镜像做 Trivy/Grype 扫描。

## 5. 硬编码审计

### 5.1 合理或可接受的硬编码

以下值属于算法默认值、协议常量或本地开发默认值，本身不是问题：

- RRF 常数、相似度阈值、Token 预算、重试次数和 SRS 默认参数；
- 任务类型、状态枚举、Prompt Version；
- `localhost` API/Redis/PostgreSQL 默认地址；
- 默认模型 ID；
- 测试中的固定 UUID；
- Redis 队列命名；
- 本地单 workspace 的默认 UUID，前提是产品明确保持单用户。

这些值大多已经可以通过环境变量覆盖。建议把“算法默认值”与“部署安全值”区分：算法值可带默认，密码、监听地址、密钥、生产 Provider 不应有不安全默认。

### 5.2 需要处理的硬编码

1. `docker-compose.yml` 的数据库用户名/密码及公开端口；
2. API 未显式绑定回环地址；
3. 前端模型下拉列表写死，容易过期，且与后端默认模型分散；
4. 模型默认值同时散落在 Java、Python、前端和文档中，存在漂移；
5. 文档 [CHUNKING_STRATEGY.md](CHUNKING_STRATEGY.md) 含 `/Users/kaius/Project/NoteFlow/...` 绝对路径，不利于其他环境浏览；
6. Agent 的显式意图关键词列表写死中英文短语，覆盖不完整，也不应承担安全授权职责；
7. 本地 workspace 固定 UUID 会阻碍未来多用户隔离；
8. Provider URL 主要写在各 Provider 实现中，建议集中到 Provider Registry；
9. Prompt 中阈值、最小字符数和工具目录与运行配置部分重复。

### 5.3 配置治理建议

建立单一 `ModelCatalog`/`ProviderRegistry`：

- Provider、模型能力、上下文长度、结构化输出支持、视觉/Embedding 维度、价格、弃用日期集中管理；
- 前端通过 API 获取 Catalog；
- 每次生成记录 catalog version、模型快照和价格；
- 配置分层为 `safe defaults → install config → workspace config → per-run override`；
- 启动时做配置 Schema 校验，生产模式拒绝默认密码、空鉴权和非回环绑定。

## 6. 可靠性与业务逻辑审计

### REL-01：DB→Redis 双写缺少 Outbox

[TaskDispatchService.java](../../services/api/src/main/java/com/noteflow/tasks/TaskDispatchService.java) 在事务提交后执行 Redis `rightPush`。这避免了“任务先入队、数据库后回滚”，但产生相反窗口：事务已经提交，Redis 写失败，任务永久停在 `PENDING`。现有恢复逻辑主要恢复 `PROCESSING/RETRYING` 的过期任务，不能保证找回从未入队的 `PENDING`。

建议使用 Transactional Outbox：

1. 同一数据库事务写 `tasks` 和 `task_outbox`；
2. 独立 dispatcher 使用 `FOR UPDATE SKIP LOCKED` 投递；
3. Redis 消息带稳定 event ID；
4. 投递成功后标记 outbox；
5. 周期性扫描未投递项；
6. Consumer 保持幂等。

### REL-02：Redis 租约回收竞争

[redis_queue.py](../../services/worker/noteflow_worker/queue/redis_queue.py) 的“读取 payload → 删除 Hash/ZSet → 重新入队”由多个命令组成。两个 Worker 同时看到同一个过期 lease 时可能都读取并重新投递。建议用 Lua 在 Redis 内原子地确认 deadline、取出、删除并转移，或使用 Redis Streams Consumer Group 的 pending/claim 语义。

### LOG-01：Study 版本号并发竞争

[QuizGenerationService.java](../../services/api/src/main/java/com/noteflow/study/QuizGenerationService.java) 与 [FlashcardGenerationService.java](../../services/api/src/main/java/com/noteflow/study/FlashcardGenerationService.java) 使用 `SELECT MAX(version)+1` 后再插入；表上有 `(document_id, version)` 唯一约束。两个不同配置同时创建时可能计算出同一版本，导致其中一个事务唯一键失败。

建议：

- 对 document ID 获取 advisory transaction lock；或
- 使用单独 sequence/version table 原子 `UPDATE ... RETURNING`；或
- 捕获唯一冲突并重新计算重试。

### LOG-02：Agent 编辑的乐观并发控制不完整

`edit_markdown` 支持 `expectedMarkdownHash`，但 Schema 不要求它；`insert_section`、`delete_section` 和 `update_note` 也没有强制版本条件。即使 Agent 在同一 Run 先读后写，用户仍可能在两步之间编辑，造成静默覆盖。

建议：

- `notes` 增加整数 version/ETag；
- 所有更新使用 `WHERE id=? AND version=?`；
- 冲突返回 409，Agent 重新读取并展示差异；
- 删除、整篇覆盖前生成 preview/diff。

### LOG-03：Schema 管理职责混乱

当前同时存在：

- Hibernate `ddl-auto: update`；
- Java `SchemaManager`/`ApplicationRunner`/`@PostConstruct`；
- Python 每条 Pipeline 运行时 `CREATE TABLE/ALTER TABLE`；
- Docker init SQL。

这会导致：

- 启动顺序和首次运行行为难以推理；
- Worker 任务承担 DDL 锁开销；
- 多实例启动时产生系统表竞争；
- 回滚、审计和版本兼容困难；
- 测试环境与生产 Schema 容易漂移。

建议统一到 Flyway 或 Liquibase。API 启动只验证 schema version；Worker 不执行 DDL；迁移脚本可重复部署、可审计、带向后兼容阶段。

### 其他逻辑评价

合理实现：

- 文档、Quiz、Flashcard、Conversation 的多数入口都有 workspace ownership 检查；
- Study source chunk 会验证属于选中文档；
- Quiz answer 会校验 question 与 attempt 属于同一 quiz；
- SRS 更新使用 advisory lock，学习事件支持外部 event ID 幂等；
- 生成任务使用 execution lease、checkpoint 和 PARTIAL/READY 状态；
- 检索动态 SQL中的值大多参数化，动态表名/列名来自内部枚举，而非直接用户输入；
- Agent 引用索引、工具参数和生成产物都有后置验证。

需要改进：

- 上传文件在数据库事务完成前已写磁盘；事务失败会遗留孤儿文件，应增加补偿删除或 staged upload；
- API 大量捕获 `DataAccessException` 后返回 `NOT_STARTED`，会把“Schema/DB 故障”伪装成业务状态；
- `StudyService` 等文件被压缩为极长单行，审查和异常路径维护困难；
- 当前单 workspace 假设渗透到缓存和全局 settings；未来多用户化时不可直接复用；
- Agent wall timeout 只在步骤边界检查，不能中断正在进行的模型/HTTP 调用；
- Agent Token 预算主要用字符估算，不是 Provider 实际 usage，也不是费用硬上限；
- Agent trace 会持久化工具参数和截断观察，可能包含笔记片段；应定义保留期和隐私等级。

## 7. 性能优化建议

### PERF-01：分页与响应裁剪

当前以下数据经常全量返回：

- documents、notes、folders、tasks；
- 文档 chunks、assets、layout blocks、markdown pages；
- AI note sections、Quiz/Deck 历史、全部 cards；
- 对话列表和部分学习记忆查询。

后果是数据库扫描、JPA 实体构造、JSON 序列化和前端 DOM 一起增长。建议：

- 全部列表使用 cursor pagination；
- 列表 DTO 不包含 Markdown/大 JSON；
- 文档详情按 Tab 懒加载；
- 大 Markdown 支持 range/page；
- 对常用列表加 `(workspace_id, updated_at/id)` 复合索引；
- 前端使用虚拟列表。

### PERF-02：Agent Prompt 成本

每个 Agent planning step 都重新包含：

- 全部工具定义和参数 Schema；
- Answer context；
- 累积步骤；
- 证据索引。

在最多 12 步和 60,000 估算 Token 下，长对话会显著增加延迟与费用。建议：

1. 先做轻量 Intent/Tool Router，只向模型暴露相关的 5–10 个工具；
2. 把稳定 system/tool 前缀放入 Provider Prompt Cache；
3. 步骤间只发送增量 observation，并压缩旧 trace；
4. retrieval 与 workspace/learning agent 分层；
5. 简单问题直接走 RAG，不进入 Tool Agent；
6. 使用实际 usage 记账，并设置每 Run 金额预算。

### PERF-03：轮询与批量 API

- 前端频繁轮询 task/message/attempt；
- Quiz 提交逐题 `await PUT`，题数越多 RTT 越大；
- 状态完成后多个视图重新拉取整份列表。

建议：

- 使用 SSE 推送 Task/Agent progress；本地桌面模式也可用单连接；
- 提供批量保存 Quiz answers；
- 使用 ETag/`updatedSince` 增量同步；
- 前端对同一资源请求做去重和取消；
- 页面不可见时暂停轮询。

### PERF-04：嵌套并发放大

Worker 默认同时处理 4 个任务，每个 Notes 任务可再开 3 个请求线程，Embedding 可开 5 个；多个文档并发时会把 Provider 请求数放大。Study Provider 已有进程级全局信号量，Notes/Embedding 应采用相同模式，并按 Provider/Key 分桶。

推荐统一 Resource Governor：

- CPU、OCR、GPU、数据库连接、Provider QPS、Provider Token/minute 分别限流；
- 调度器根据任务估算成本而不是只按任务个数；
- 支持 per-provider backpressure、429 `Retry-After` 和熔断；
- 队列记录 deadline、estimated cost 和 tenant/workspace fairness。

### PERF-05：Embedding 索引生命周期

[RetrievalSchemaManager.java](../../services/api/src/main/java/com/noteflow/retrieval/RetrievalSchemaManager.java) 首次 ready 时为当前已有维度创建 HNSW 索引，然后将 `ready=true`。以后切换到不同维度的模型时，不一定再次创建对应索引，向量检索可能退化为扫描。

建议：

- Embedding 模型注册时执行 migration/job 创建维度索引；
- 定期比较 `DISTINCT embedding_dimension` 与 `pg_indexes`；
- 记录 `EXPLAIN (ANALYZE, BUFFERS)` 基准；
- 对小库、冷库和大库选择不同 HNSW 参数；
- Embedding 状态按 `(provider, model, dimension, content_hash)` 展示，不只显示笼统 READY。

### 其他性能机会

- `RetrievalScopeResolver` 的 CUSTOM scope 逐 ID 查文档，可改一次批量查询并限制 ID 数；
- 全 workspace MIXED 检索先加载全部 READY document ID，长期可用 workspace 条件直接下推；
- Notes/Flashcards 的 near-duplicate 若在 Python 对大量已生成文本做全比较，会逐渐接近 O(n²)，可改 MinHash/SimHash/Embedding ANN；
- 文件与解析产物可采用内容寻址，重复上传直接复用；
- Markdown、OCR 和视觉结果按页/区域 fingerprint 缓存；
- Provider 请求可批处理并缓存相同 prompt/input hash。

## 8. 可维护性与可观测性

### 代码结构

需要优先拆分：

- `apps/web/app.js`：约 3,400 行，视图、状态、网络、渲染和事件全部耦合；
- `db/repository.py`：约 1,800 行，同时负责 Schema、文档、解析、Note、Embedding；
- `pdf/layout.py`、`conversation/agent.py`、`agent_toolkit.py`；
- `AdvancedLearningMemoryService.java`、`StudyService.java` 的压缩单行写法。

建议按 domain/use-case/adapters 拆分，并为 API/Worker 共享 Task Contract 生成 Schema，避免 Java/Python 手工同步枚举和 JSON 字段。

### 可观测性

建议引入 OpenTelemetry：

- `trace_id` 贯穿 HTTP request、task row、Redis payload、Worker、Provider call 和 Agent run；
- 指标：队列等待、处理时间、租约回收、重试、Provider 429、Token/费用、检索 recall、无证据率、生成淘汰率；
- 日志使用结构化 JSON，字段脱敏；
- Agent trace 区分 public summary 与 restricted debug，设置 TTL；
- 提供本地诊断页：数据库/Redis/模型、Schema version、索引健康、积压任务、孤儿文件。

## 9. 修复优先级路线

### P0：远程化或公开演示前

1. API、PostgreSQL、Redis 全部绑定回环地址；
2. 替换默认密码，Redis 启用 ACL；
3. API 增加本地安装令牌；
4. 修复设置页持久化 XSS并增加 CSP；
5. Key 迁移到系统 Secret Store/加密存储；
6. Agent 写工具使用服务端 capability/确认票据；
7. 实现 Transactional Outbox。

### P1：稳定性与数据安全

1. Redis 租约回收改为原子操作；
2. PDF 预检、资源预算与解析沙箱；
3. 强制 Note ETag/version；
4. Study version 分配加锁或原子序列；
5. Flyway/Liquibase 接管全部 Schema；
6. 分页、批量 Quiz answer、SSE；
7. 依赖 lock、SBOM、自动 Advisory 扫描。

### P2：规模与成本

1. Agent 工具路由与 Prompt Cache；
2. 全局 Provider Resource Governor；
3. Embedding index lifecycle 管理；
4. 内容寻址和增量解析；
5. OTel、成本账本、质量评估面板；
6. 前端模块化与虚拟列表。

## 10. 面向 AI Agent 的扩展建议

以下建议不是简单“多加一个聊天按钮”，而是围绕可规划、可执行、可验证、可恢复、可审计的 Agent 平台演进。

### 10.1 学习任务编排 Agent

目标：把“我要在两周后通过考试”转成持续执行的计划。

能力：

- 读取 syllabus、截止日期、学习历史、弱点图谱和可用时间；
- 生成依赖图和每日任务；
- 自动选择文档、生成 Quiz/Flashcard/Study Guide；
- 每次学习后根据表现重排计划；
- 任务有 deadline、预算、完成条件和证据；
- 支持 pause/resume/cancel/replan。

实现建议：

- 使用显式 DAG/State Machine，而不是无限 ReAct；
- Plan 中每个节点声明 input/output schema、cost estimate、permission；
- Scheduler 只执行被授权节点；
- 每日计划变化保留版本和解释。

### 10.2 多 Agent 教学委员会

建立职责分离的 Agent：

- **Curriculum Planner**：规划知识顺序；
- **Retriever**：只负责证据召回；
- **Tutor**：苏格拉底式教学；
- **Examiner**：出题和评分；
- **Critic/Verifier**：核验引用、覆盖、难度和幻觉；
- **Memory Curator**：更新长期学习画像；
- **Safety/Permission Agent**：只做策略判断，不生成内容。

关键点是 Agent 之间传递结构化 artifact，而不是自由文本。最终答案必须经过 Verifier，写操作必须经过 Policy Engine。

### 10.3 可执行的“研究模式”

用户提出复杂主题后，Agent 可以：

1. 拆解研究问题；
2. 在本地资料中做多轮检索；
3. 识别证据缺口；
4. 经用户授权后连接网页、Drive、Notion、课程 LMS；
5. 建立 claim-evidence graph；
6. 生成带逐句来源的研究报告；
7. 对冲突来源给出差异和可信度；
8. 保存可复现 research run。

需要新增：

- Source provenance；
- Snapshot/hash；
- Citation entailment；
- 连接器权限和数据域；
- 外部内容 Prompt Injection 隔离。

### 10.4 知识图谱与误概念图谱

从文档、Quiz 错题、对话中构建：

- 概念节点；
- prerequisite/related/confused-with/derived-from 边；
- claim→source chunk 证据边；
- 用户 mastery、confidence、stability；
- misconception pattern。

Agent 可执行“为什么我总在条件概率上出错”“学生成树前还缺什么”这样的图推理，并生成针对性微课程。

### 10.5 Agent 生成的交互式学习环境

不只生成 Markdown：

- 根据公式生成可调参数模拟器；
- 根据算法生成逐步可视化；
- 根据代码课程生成沙箱练习；
- 根据统计主题生成数据实验；
- 自动选择题目、提示层级和下一步；
- 验证器运行属性测试/数值检查，避免纯 LLM 判断。

产物应采用受限 DSL/组件白名单，不能让 LLM 直接生成任意可执行 HTML/JS。

### 10.6 教学策略实验与个性化

对不同策略做小规模 bandit/实验：

- 先讲解再测验 vs 先测验后讲解；
- 示例优先 vs 定义优先；
- 提示强度；
- 间隔复习时间；
- 题目难度爬升速度。

要求：

- 用户可关闭实验；
- 不以短期正确率作为唯一目标；
- 记录干预、结果和置信区间；
- 防止策略对小样本过拟合；
- 能解释“为什么现在推荐这一步”。

### 10.7 Offline/Local-first Agent Runtime

与项目定位非常贴合：

- 本地小模型做分类、路由、摘要和隐私过滤；
- 云模型只处理需要高能力的最小上下文；
- PII/敏感笔记自动脱敏；
- 所有 Agent Run 可导出、重放和迁移；
- 无网时继续检索、复习和计划；
- 本地模型与云模型按成本/隐私/质量动态路由。

### 10.8 Agent 评估与治理平台

在继续扩工具之前，建议先把评估平台做成产品能力：

- golden conversations；
- retrieval recall/precision；
- citation correctness；
- tool selection accuracy；
- mutation authorization accuracy；
- task completion rate；
- cost/latency；
- prompt injection resistance；
- resume/idempotency correctness；
- user learning outcome。

每次 Prompt、模型、工具或阈值变化都跑回归集，结果与 Prompt Version、Model Catalog Version 绑定。

### 10.9 可插拔工具与 MCP 沙箱

未来可连接：

- Calendar：把学习计划落到时间块；
- Drive/Notion：同步资料与产物；
- GitHub：面向编程课程读取仓库、Issue、CI；
- LMS：读取作业和截止日期；
- 代码执行器/数学求解器；
- 浏览器研究工具。

安全设计：

- 每个连接器独立 OAuth scope；
- 工具声明 read/write/external-side-effect；
- 参数经过 Schema、allowlist 和 capability；
- 网络 egress allowlist；
- 所有写操作可预览、撤销、审计；
- 工具输出作为不可信数据进入 Agent。

## 11. 推荐的目标架构

```text
Desktop/Web UI
    |
    | install token / user session
    v
API Gateway + Policy Engine
    |-- Resource authorization
    |-- Capability issuance
    |-- Rate/cost limits
    |
    +--> Domain API + PostgreSQL
    |        |-- Flyway schema
    |        |-- Outbox
    |        |-- Audit log
    |
    +--> Event Dispatcher --> Redis Streams / durable queue
                              |
                              v
                      Agent/Document Workers
                      |-- Resource Governor
                      |-- Tool Sandbox
                      |-- Provider Registry
                      |-- Checkpoint/Resume
                      |
                      +--> Evaluator / Verifier

Telemetry: HTTP -> Task -> Worker -> Provider -> Artifact
```

## 12. 最终判断

NoteFlow 的“AI 学习 Agent”核心已经具备相当好的技术基础，尤其是结构化输出、证据检索、断点恢复、学习记忆和 Agent 工具目录。当前最大的短板不是模型能力，而是安全边界和平台化治理：

1. 本地单用户假设没有被操作系统/网络层强制执行；
2. Agent 的“是否允许写”仍由同一个可能受 Prompt Injection 影响的模型决定；
3. 异步任务缺少 Outbox 这一最后一段可靠性保证；
4. Schema、配置、依赖和 Provider 能力尚未形成单一治理面；
5. 性能问题主要来自全量列表、轮询、Prompt 重复和嵌套并发。

建议先完成 P0/P1，再继续扩展外部工具和多 Agent。这样后续无论接 Calendar、Drive、Notion、浏览器还是代码执行器，都能复用同一套授权、审计、恢复和评估基础，而不会让每个新工具都增加不可控风险。
