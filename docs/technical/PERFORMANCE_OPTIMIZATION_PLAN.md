# NoteFlow 性能与并发优化方案

> 状态：阶段 1–3 已实施（2026-07-18）；阶段 0 压测基线与阶段 4（LangGraph）未实施
> 目标：降低 **API 响应延迟**、提升 **Worker 吞吐 / 并发承载能力**
> 日期：2026-07-18

---

## 0. 结论先行

本系统的主导成本是 **I/O**（外部 LLM/OCR 调用、数据库往返、Redis 队列），
**不是本地 CPU 计算**。因此：

- ✅ 高性价比方向：**连接池、线程/并发模型调优、并行化外部调用**。
- ⚠️ 谨慎：**LangGraph** 是编排层重写（并行/流式收益），属架构改造，非纯性能优化。
- ❌ 不建议：自研 **C/C++**。唯一有意义的 CPU 热点是 PDF 解析/OCR，而 PyMuPDF 已是 C 实现、
  OCR 走 tesseract/paddle（本身 C/C++），自研收益低、维护成本高。

优化按「先测量 → 低风险高收益 → 结构性改造」推进，避免过度工程。

---

## 1. 现状架构

| 组件 | 技术 | 并发模型 | 位置 |
|---|---|---|---|
| API | Spring Boot 3.3.5 / Java 21，同步 MVC (Tomcat) | 请求线程池 + 检索固定线程池(12) | `services/api` |
| Worker | Python 单进程 | `ThreadPoolExecutor`(max 4)，受 GIL 约束 | `services/worker` |
| 存储 | Postgres + pgvector | — | `docker-compose.yml` |
| 队列 | Redis（优先级 + 租约） | — | `noteflow_worker/queue/redis_queue.py` |

---

## 2. 已定位的瓶颈（含代码位置）

### P0 — Worker 每次 DB 操作新建连接（无连接池）
- `services/worker/noteflow_worker/db/repository.py:204` —— `Repository.connect()` 每次
  调用都 `psycopg.connect(...)`，全文 30+ 处 `with self.connect()`。
- 影响：每次操作付出 TCP + 认证 + TLS 握手成本；并发任务数 × 每任务查询数会迅速打满
  Postgres 连接数上限，成为吞吐天花板。
- 同样问题存在于 `StudyRepository`、`ConversationStore`、`memory/store.py` 等（各自独立建连）。

### P0 — API 未配置 HikariCP 连接池
- `services/api/src/main/resources/application.yml` —— 无 `spring.datasource.hikari` 段，
  默认最大连接 10。并发请求一上来即在连接池排队，表现为“响应变慢”。

### P1 — Worker 单进程 + GIL，CPU 密集任务阻塞全局
- `services/worker/noteflow_worker/config.py:17` —— `worker_max_concurrent_tasks=4`。
- PDF 解析 / OCR / 版面分析是 CPU 密集，GIL 下会卡住整个进程，拖累同进程内的 I/O 型任务
  （notes、embeddings、answer）。
- 叠加问题：pipeline 内部又嵌套线程池（`generate_notes.py:70`
  `notes_max_concurrent_requests`），并发是「进程线程 × pipeline 线程」乘出来的，
  进一步放大 DB 连接与 GIL 压力。

### P1 — 检索链路存在串行阻塞点
- `services/api/src/main/java/com/noteflow/retrieval/RetrievalService.java:97` ——
  `hydeQueryExpander.expand(query)` 是一次**同步 LLM 调用**，位于三路召回 fan-out **之前**，
  串行阻塞整个检索响应。
- 三路召回（vector/lexical/exact）已并行（`retrievalExecutor`，固定线程 12 + 有界队列），
  但外部 reranker、HyDE 各自 `HttpClient` 未与召回重叠。

### P2 — API 外部 HTTP 调用未复用/未并行
- `GeminiEmbeddingClient`、`ExternalSemanticReranker`、`HydeQueryExpander` 各自
  `HttpClient.newBuilder()`，`open-in-view: false` 已正确关闭（无需改），但外部调用
  的超时/重试/连接复用值得统一治理。

---

## 3. 优化方案（按优先级 / 风险分层）

### 阶段 0：测量基线（0.5 天，先做）
不猜瓶颈，先建立可对比的数据：
1. API 侧：对 `/retrieval`、`/search`、上传/任务查询接口加请求耗时日志（或
   Spring Boot Actuator + Micrometer `@Timed`），压测工具 `wrk`/`k6` 采集 p50/p95/p99。
2. Worker 侧：在各 pipeline 入口/出口打点（解析、notes、embeddings 各阶段耗时），
   统计 Postgres `pg_stat_activity` 峰值连接数。
3. 记录一份基线表，后续每阶段回归对比。

**验收**：拿到当前 p95 延迟、Worker 单任务各阶段耗时、DB 峰值连接数。

---

### 阶段 1：低风险高收益（1–2 天）——「Java 并发池 + Worker 连接池」

#### 1.1 API：配置 HikariCP
在 `application.yml` 增加：
```yaml
spring:
  datasource:
    hikari:
      maximum-pool-size: 20        # 依据 DB max_connections 与实例数调
      minimum-idle: 5
      connection-timeout: 3000
      max-lifetime: 1800000
```
> 上限需与 Postgres `max_connections` 及 API/Worker 实例数一起规划，避免总连接数超限。

#### 1.2 API：开启虚拟线程（Java 21）
```yaml
spring:
  threads:
    virtual:
      enabled: true
```
- 请求处理不再受固定 Tomcat 线程数限制，I/O 等待期间不占平台线程，直接提升并发承载。
- 检索的 `RetrievalExecutorConfig` 可评估改为虚拟线程执行器（`Executors.newVirtualThreadPerTaskExecutor()`），
  去掉“固定 12 线程 + 有界队列”的人为上限（fan-out 数量小、纯 I/O，非常适合）。

#### 1.3 Worker：引入 DB 连接池（最关键一项）
- 用 `psycopg_pool.ConnectionPool` 替换 `Repository.connect()` 的每次新建连接。
- 单点改造 `connect()` 从池中借还连接，30+ 处调用点无需改动（接口不变）。
- 池大小与 `worker_max_concurrent_tasks` × pipeline 内并发对齐。
- `StudyRepository` / `ConversationStore` / `memory/store` 共享同一进程级池。

**验收**：相同压测下 API p95 下降、DB 峰值连接数受控且不再报连接耗尽；Worker 单任务
DB 相关耗时下降。

---

### 阶段 2：Worker 吞吐 / 绕开 GIL（2–4 天）

#### 2.1 拆分 CPU 型与 I/O 型任务
- **CPU 型**（PDF 解析 / OCR / 版面）：改用 `ProcessPoolExecutor` 或独立进程池 worker
  绕开 GIL；进程数按 CPU 核数与 `pdf_gpu_worker_cap` 规划。
- **I/O 型**（notes / embeddings / answer / quiz）：保留线程模型即可（受益于阶段 1 连接池）。
- 方案：按 `task_type` 路由到不同并发域；或部署两类 Worker 进程（解析专用 / 生成专用），
  各自从 Redis 消费对应优先级，横向扩容更干净。

#### 2.2 收敛嵌套并发
- 目前「worker 线程池 × pipeline 内线程池」相乘，易在高并发下过载 DB 与外部 API。
- 用**全局信号量 / 令牌桶**对外部 LLM 调用（Gemini/OpenAI）限流，避免触发对方限流与本地
  连接爆炸；各 `*_max_concurrent_requests` 收敛为进程级统一预算。

#### 2.3 水平扩展前提
- 队列已支持优先级 + 租约 + 过期重入（`redis_queue.py`、`main.py` 的 `reclaim_expired_leases`），
  天然支持多 Worker 实例。补足连接池后即可通过多进程/多实例线性扩容。

**验收**：解析任务不再阻塞生成任务；固定资源下总吞吐（任务/分钟）提升；外部 API 无限流错误。

---

### 阶段 3：API 检索延迟专项（1–2 天）

#### 3.1 消除 HyDE 串行阻塞
- 将 `hydeQueryExpander.expand()` 与召回并行：先用原始 query 启动 lexical/exact 召回，
  HyDE 完成后再补充/触发 vector 召回；或对 HyDE 设更激进超时 + 降级（失败即用原 query）。
- 目标：把「HyDE 串行 + 三路并行」压缩为整体并行，砍掉一段 LLM 往返的墙钟时间。

#### 3.2 外部 HTTP 客户端治理
- 统一 `HttpClient` 为单例复用（连接复用、HTTP/2），统一连接/读取超时与重试退避。
- reranker/HyDE 走虚拟线程执行器，与召回阶段重叠。

**验收**：检索接口 p95 明显下降（尤其 HyDE 开启时）。

---

### 阶段 4（可选）：LangGraph 编排重构
- 适用场景：notes/quiz/answer 等**多步 LLM** pipeline，希望拿到更好的并行度、可恢复性、
  以及**流式输出**（answer 场景对响应体感提升大）。
- 代价：pipeline 层重写，属架构改造，建议在阶段 1–3 收益兑现、基线稳定后再评估。
- 若做，先从 `answer_conversation_turn`（用户等待感最强、最受益于流式）试点。

---

## 4. 关于所提技术的取舍表

| 技术 | 结论 | 用在哪 |
|---|---|---|
| Java 虚拟线程 / 并发池 | ✅ 强烈推荐 | API 请求承载、检索 fan-out、外部调用重叠 |
| 连接池（Hikari / psycopg_pool） | ✅ 必做 | API + Worker，最高性价比 |
| 进程池 | ✅ 推荐 | Worker 绕开 GIL 跑 CPU 型解析/OCR |
| LangGraph | ⚠️ 可选 | LLM pipeline 并行 + 流式，架构改造 |
| C/C++ | ❌ 不建议 | 现有热点已是 C 实现，自研收益低 |

---

## 5. 建议落地顺序

1. 阶段 0 基线测量（先做，不猜）
2. 阶段 1（Hikari + 虚拟线程 + psycopg 连接池）—— 预计最大单步收益
3. 阶段 2（Worker CPU/IO 拆分 + 限流）
4. 阶段 3（检索 HyDE 并行 + HTTP 治理）
5. 阶段 4（可选 LangGraph，answer 流式试点）

每阶段结束回归压测，与基线对比，用数据决定是否进入下一阶段。

---

## 6. 实测结果（2026-07-18，阶段 1–3 实施后）

工具：`tests/benchmarks/benchmark_api_latency.py`（stdlib 并发压测，p50/p95/p99）。
对比对象：同机同库，旧代码（实施前已运行的实例，:8080）vs 新代码（:8081，已 JIT 预热）。
环境：embedding/HyDE/reranker 均 disabled（本地默认），故 retrieval 走三路 fan-out + 融合但
无外部 LLM 调用；有外部调用时 HyDE 并行化的收益会更大。

### 32 并发（轻载）
两侧基本持平——轻载打不到旧代码的瓶颈（固定检索线程池、Hikari 默认 10 连接），符合预期。

### 128 并发（高载，每场景 2560 请求）

| 场景 | 指标 | 旧代码 | 新代码 | 变化 |
|---|---|---|---|---|
| POST /retrieval | **错误数** | **962 (37.6% 失败, HTTP 500)** | **0** | ✅ 消除 |
| POST /retrieval | 吞吐 | 1331 rps | 3466 rps | **+160%** |
| POST /retrieval | p50 / p95 / p99 | 59 / 130 / 173 ms | 34 / 61 / 77 ms | −43% / −53% / −56% |
| GET /health | p50 / p95 | 14.6 / 30.8 ms | 8.8 / 19.7 ms | −40% / −36% |
| GET /tasks | p50 / 吞吐 | 54.6 ms / 2190 rps | 41.9 ms / 2391 rps | −23% / +9% |

旧代码 /retrieval 高并发失败的根因：固定 `ThreadPoolTaskExecutor`（12 线程，
queueCapacity=96），128 并发 × 3 通道 = 384 个任务超出队列容量触发
`RejectedExecutionException` → 500。虚拟线程执行器移除了这个人为上限。

Worker 侧（连接池 + 解析进程池）为功能性验证：池化连接对真实 Postgres 复用正常、
spawn 进程池任务往返正常、120 个单测全绿；吞吐收益需在真实文档解析负载下量化。

### 压测中顺带发现并修复的正确性 bug：lexical/exact 通道零召回

LEXICAL 与 EXACT 通道的 SQL 按 `embedding_provider = 当前配置` 过滤
`document_embeddings`，但这两个通道读取的 `search_vector` / `exact_search_text`
与 embedding provider 无关。只要 API 的 embedding 配置与建库时的 provider 不一致
（例如本地 `EMBEDDING_PROVIDER=disabled`，而数据由 gemini 生成），两通道永远返回
0 候选——RAG 检索在该配置下完全失效。

修复（`LexicalCandidateRetriever` / `ExactSignalCandidateRetriever` /
`RetrievalCandidateMapper.dedupedSelectAndJoins`）：去掉 provider/model 过滤，改用
`DISTINCT ON (source_object_type, source_object_id)` 按来源去重（多 provider 并存时
防重复行）。VECTOR 通道保留过滤（向量必须匹配查询 embedding 模型）。
修复后实测：`linked list` lex=30、`probability distribution` lex=26、引号短语
`"linked list"` exact=15，均返回相关内容。
