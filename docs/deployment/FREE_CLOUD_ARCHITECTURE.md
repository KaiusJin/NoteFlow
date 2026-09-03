# NoteFlow 低固定成本云架构

> 配额快照日期：2026-09-03。免费额度会调整，部署前必须重新核对官方页面；“免费额度内可为 $0”不等于生产 SLA，也不等于无需绑定计费账户。

## 推荐结论

做登录制 Web App/PWA，不购买 Railway 也能完成第一阶段。浏览器端可离线保存草稿和最近访问状态，账号、正式文档、任务状态和学习记录以云端为准。

| 层 | 推荐服务 | 固定成本目标 | NoteFlow 中的职责 |
|---|---|---:|---|
| 前端 | Cloudflare Workers Static Assets | $0 | React PWA、静态资源、版本 Preview；初期不执行 Worker 脚本 |
| 身份/数据库/文件 | Supabase Free | $0 | 邮箱验证码、密码、Google OAuth、PostgreSQL、pgvector、Storage |
| Redis | Upstash Redis Free | $0 | 优先级队列、delivery lease、重试唤醒、DLQ |
| API | Google Cloud Run Service，min instances = 0 | 免费额度内可为 $0 | Spring Boot API、JWT/RLS 边界、outbox publisher |
| Worker | Google Cloud Run Job，按需执行 | 免费额度内可为 $0 | Python/LangGraph、PDF/AI、线程池与进程池 |
| AI | 用户选择的模型供应商 | 按量付费 | Gemini/OpenAI 等模型调用 |

截至快照日期，Cloudflare Workers 文档说明 Static Assets 请求免费且不限量；只有实际执行 Worker 脚本的动态请求才计入 Workers 配额。Supabase Free 列出 50,000 MAU、500 MB 数据库、1 GB 文件存储，并提示空闲一周后项目可能暂停；Upstash Redis Free 列出每月 500,000 commands、256 MB；Cloud Run 按实际资源计费并提供月度 free tier。以官方页面为准：[Cloudflare Static Assets billing](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/)、[Supabase pricing](https://supabase.com/pricing)、[Upstash Redis pricing](https://upstash.com/pricing/redis)、[Cloud Run pricing](https://cloud.google.com/run/pricing)。

## 为什么 Redis 可以保留，而且不是装饰

Redis 不保存最终业务事实；它负责需要低延迟的数据流职责：

- 三档优先级和加权公平调度；
- 消费者 delivery lease 与过期回收；
- 失败重投和 dead-letter queue；
- Redis 故障后由 PostgreSQL 状态恢复唤醒消息。

这比“把 session 放进 Redis”更适合作为项目介绍点：能展示 outbox、at-least-once、幂等/CAS、backpressure、线程池与进程池隔离、优雅停机和故障恢复。

## 免费额度成立的前提

云端 worker 不能作为全天候空轮询进程运行。即使每 5 秒只检查四个列表，一个月也可能超过 100 万次队列探测，已经高于当前 Upstash Free 的 500,000 commands。

推荐运行方式：

1. API 完成 outbox 发布后，调用 Cloud Run Jobs Executions API 唤醒一个 worker job；多个 API 实例通过 Redis cooldown key 合并重复唤醒。
2. job 设置 `WORKER_MAX_TASKS_PER_RUN=20`，领取一批任务；队列清空或达到上限后退出。
3. API 每 30 秒用一条 Redis Lua 命令检查三个优先级队列；只在仍有积压时补发唤醒，用于处理“Redis 已入队但即时唤醒调用失败”的情况。
4. 本地开发可保持 `WORKER_MAX_TASKS_PER_RUN=0`，连接本机 Redis 常驻运行。

当前仓库已经实现 job 的“有限任务后退出”和 API 到 Cloud Run Jobs v2 `jobs.run` 的唤醒器。API 使用 Cloud Run metadata server 获取短期 access token，不保存 Google service-account JSON key。部署时仍必须把 API 的运行身份授予目标 job 的 `roles/run.invoker`，并在真实 GCP 项目做一次端到端验证。

关键环境变量：

- `NOTEFLOW_CLOUD_RUN_JOB_RESOURCE=projects/<project>/locations/<region>/jobs/<job>`
- `WORKER_MAX_TASKS_PER_RUN=20`
- `NOTEFLOW_WORKER_WAKEUP_COOLDOWN_SECONDS=20`
- `NOTEFLOW_WORKER_WAKEUP_RECOVERY_MILLIS=30000`

## 数据库连接预算

建议从以下上限开始，并将 Cloud Run API/worker 的 max instances 均设为 1：

| 进程 | 单实例上限 |
|---|---:|
| Spring Hikari | 5 |
| Python 主 worker | 4 |
| 每个 PDF 解析子进程 | 1 |

连接 Supabase 应优先使用其 pooler，并根据事务模式限制检查 prepared statement 兼容性。扩容前先按“实例数 × 每实例池上限”计算总连接数；不能只调大线程池。

## 登录与数据边界

- 注册：邮箱、6 位验证码、用户名、密码；验证码和密码凭据由 Supabase Auth 管理。
- 登录：邮箱 + 密码、Google OAuth。
- 用户名：在 `profiles` 中唯一，不作为密码认证凭据。
- 每个账号自动创建 personal workspace；API 从已验证 JWT `sub` 解析 workspace，worker 使用独立内部服务密钥并显式携带 workspace ID。
- 前端的 anon key 可以公开；service role key、数据库密码、AI provider key、内部服务密钥绝不能进入 Cloudflare 构建产物。

## PWA 的离线边界

离线能力用于 UI shell、草稿、待上传动作和最近访问缓存。正式文档和学习记录仍以服务器版本为准；恢复联网后用幂等 request ID 同步。第一阶段不做“完全离线 AI”，否则本地模型、索引和多端冲突会显著扩大范围。

## 上线门槛

- [x] Cloudflare Workers Static Assets 可部署的 React PWA、SPA fallback 与安全响应头
- [x] Supabase 邮箱验证码/密码/Google 登录 UI 与身份表迁移
- [x] Spring JWT resource server、内部服务身份、workspace 隔离
- [x] PostgreSQL outbox、Redis delivery lease、数据库 execution lease、有限重试/DLQ
- [x] 有界线程池、PDF 进程池、连接池预算和优雅停机
- [x] cloud profile 的私有 Supabase Storage、Worker 临时物化和派生 PNG 回传
- [x] Cloud Run IAM 无密钥唤醒器、Redis 合并和低频恢复机制
- [x] 非 root Cloud Run API/Job 容器、版本化部署模板与容器安全扫描
- [ ] 真实 GCP 项目的 Secret Manager、job-scoped IAM 与冷启动验证
- [ ] Supabase 项目中的真实 Auth/Google/邮件模板端到端验证
- [ ] GitHub branch protection required checks 与 Preview/production 部署工作流
- [ ] 备份、DLQ 运维入口、成本/错误率告警和恢复演练

代码层面的云端基础架构已经贯通；未完成真实 Supabase/GCP 配置、容器部署和恢复演练前，不应宣称生产就绪。
