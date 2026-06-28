# NoteFlow PDF Converter V2 技术规范

状态：当前实现源（2026-06-28）

本文档描述已经落地的 PDF → raw Markdown 转换器。它替代按整份文档字符密度二选一的旧路线，目标是可恢复、可扩展、可审计地处理原生文字、扫描件、手写、复杂公式、表格、图片和多栏文档，并为后续 RAG 保存结构索引与证据来源。

## 1. 需求完成矩阵

| 需求 | 当前实现 | 主要证据 |
|---|---|---|
| 分池并发与 GPU | 文档、MuPDF、OCR、VLM 四类池隔离；GPU OCR 并发按显存计算 | `runtime/resource_pools.py`, `pdf/ocr.py` |
| MCP 与多厂商 API key | Gemini/OpenAI/MCP router；多 key 轮转、故障转移和熔断 | `vision/providers.py` |
| 失败重试与保存 | 区域级指数退避；每块完成立即 upsert；按输入 hash 复用；stale task 恢复 | `pdf/regions.py`, `db/repository.py`, `main.py` |
| 不武断判断来源 | 每页文本质量 profile + 文档分布/置信度；实际路由逐页执行 | `pdf/parser.py`, `pdf/router.py` |
| 使用 document type | 手写强制整页；幻灯片视觉优先；不同类型使用不同 chunk 和 RAG 场景 | `pdf/router.py`, `pdf/strategies.py`, `pdf/layout.py`, `pdf/markdown.py` |
| 公式/手写/图片/表格/多栏 | 公式优先级、cases 修复、结构化 VLM、OCR fallback、两栏阅读顺序 | `pdf/layout.py`, `pdf/markdown.py`, `vision/providers.py` |
| AI 批处理/不同并发池 | VLM 微批 + 独立并发上限；不与 CPU/GPU 池互相阻塞 | `pdf/regions.py` |
| 无用信息过滤 | 多证据保守评分、公式/代码/表格硬保护、重复图片 aHash、小区域、装饰图、Markdown 去重 | `pdf/layout.py`, `pdf/regions.py`, `pdf/markdown.py` |
| Markdown 索引/描述/场景 | document/page structure v2，含描述、标签、适用问答场景、质量报告 | `pdf/markdown.py` |
| 图片 transcription | 精确转录 schema、content kind、importance、reading order、language、上下文 | `vision/providers.py` |
| 中间文件清理 | 成功后删除未被 page asset/region 引用的孤儿生成文件 | `pdf/artifacts.py` |
| Router、测试、迭代 | 逐页 Router；32 个 worker 测试、API 测试、PostgreSQL、完整 Pipeline 与真实 PDF 审计 | `tests/test_pdf_converter_v2.py` |

## 2. 总体架构

```text
Redis PARSE_DOCUMENT
  -> document worker pool
  -> pypdf page text profiling
  -> MuPDF render pool
  -> optional GPU/CPU OCR pool
  -> page evidence router
       NATIVE_TEXT | HYBRID | FULL_PAGE_VLM
  -> visual region discovery/filtering
  -> VLM micro-batches (Gemini/OpenAI/MCP, multi-key router)
       immediate per-region checkpoint
  -> layout merge + two-column order
  -> page/document raw Markdown
  -> RAG index + quality gate report
  -> document-type-aware chunks
  -> orphan artifact cleanup
```

主入口是 `ParseDocumentPipeline.run()`。V2 不再维护“扫描件一套完全独立代码、文字 PDF 另一套代码”的双流水线；所有页面进入统一流程，由逐页 Router 决定是否保留原生文字、是否分析局部视觉区、是否必须整页 VLM。

## 3. 并发池设计与线程数量依据

### 3.1 四类池

| 池 | 任务 | 默认值/推导 | 为什么独立 |
|---|---|---|---|
| Document pool | 不同上传任务 | `WORKER_MAX_CONCURRENT_TASKS=3` | 控制数据库连接、总内存和跨文档公平性 |
| MuPDF pool | 页面渲染/视觉统计 | `min(2, logical_cpu/4)`，至少 1 | MuPDF 单页已有大量 native work，过多 handle 会争用内存带宽 |
| OCR pool | PaddleOCR/Tesseract | GPU 按显存推导；CPU 使用 PDF CPU 上限 | OCR 是模型/CPU 密集任务，不能占满 VLM 网络线程 |
| VLM pool | 远程模型请求 | `VISION_CONCURRENT_REQUESTS=4` | 主要受供应商配额、429 和网络延迟约束 |

OCR 与 VLM 上限由进程级 semaphore 执行，所有同时运行的 document task 共享同一个额度；不是“每份文档各开 N 个”。GPU OCR 模型实例同样在进程内共享，避免并发文档重复加载模型并突破显存公式。

### 3.2 GPU worker 公式

CUDA 可报告 free VRAM 时：

```text
gpu_workers = min(
  GPU_WORKER_CAP,
  floor((free_vram_mib - reserve_mib) / estimated_mib_per_task)
)
```

默认：

```text
reserve = 1536 MiB
estimated task = 2048 MiB
cap = 4
```

MPS 无稳定 free-memory API，因此默认串行 1 worker。没有 GPU 时为 0，并明确回退 Tesseract/CPU；不会假装使用 GPU。

GPU 实际用于 OCR：NVIDIA/CUDA 使用 PaddleOCR，Apple Silicon MPS 使用 EasyOCR。`requirements-gpu.txt` 不固定 `paddlepaddle-gpu` wheel，因为 wheel 必须匹配部署 CUDA 版本；`requirements-mps.txt` 提供 Metal/MPS 可选运行时。普通 worker 不被迫安装数 GB 的 GPU runtime。

### 3.3 本机 MuPDF 基准

48 页、每页 18 行文字、144 DPI，2026-06-27 实测：

| Render workers | 耗时 | pages/s |
|---:|---:|---:|
| 1 | 1.1888 s | 40.38 |
| 2 | 1.1847 s | 40.52 |
| 4 | 1.1994 s | 40.02 |
| 8 | 1.2053 s | 39.82 |

因此默认上限选择 2，不选择“CPU 核数越多线程越多”。部署环境可运行 `scripts/benchmark_pdf_pools.py` 后覆盖。

### 3.4 任务队列优先级与防饥饿

Redis 使用三个物理 list：

```text
queue:document-analysis:priority:0  # ASK/EXPORT 等交互任务
queue:document-analysis:priority:1  # PARSE/NOTES/QUIZ 等用户可见任务
queue:document-analysis:priority:2  # EMBEDDINGS 等后台任务
```

worker 采用加权轮转 `interactive, visible, interactive, visible, interactive, background, visible, background`，高优先任务获得主要机会，但后台任务有确定的服务窗口，不会永久饥饿。`WORKER_MAX_BACKGROUND_TASKS=1` 限制后台任务最多占一个 document slot；当总并发大于 1 时，至少保留一个槽给交互/用户可见任务。旧的无后缀 queue 在升级期间仍可读取。

## 4. Page Evidence Router

### 4.1 来源 profile

每页记录：

- native text length；
- word count；
- alphanumeric ratio；
- replacement/private glyph 数；
- 0–1 text quality；
- 图片数量/覆盖率；
- vector drawing 数；
- OCR 是否提取到显著更多内容。

文档仍保存兼容枚举 `TEXT_PDF/MIXED/SCANNED_PDF/HANDWRITTEN_SCAN`，但它只是汇总标签。新增 `source_confidence` 和 `source_distribution_json`，真实处理不再由这一个标签决定。

### 4.2 页面路线

| Route | 条件示例 | 行为 |
|---|---|---|
| `NATIVE_TEXT` | 原生文字可靠，无显著视觉内容 | PyMuPDF layout |
| `HYBRID` | 原生文字可用，同时存在图片/密集 vector drawing | 原生 layout + 局部 VLM/OCR |
| `FULL_PAGE_VLM` | 原生文字几乎为空；OCR 显著优于文本层；用户声明手写 | 整页分析，可抑制垃圾文本层 |

`HANDWRITTEN_NOTES` 是用户明确意图，所有页强制 `FULL_PAGE_VLM` 且失败不可静默。`LECTURE_SLIDES` 中的图片被认为具有较高教学语义；其他 document type 继续影响 chunk 边界与 RAG 场景：论文按章节、作业/试卷按问题、课程笔记按概念单元。

每页决策及 reasons 写入 `document_parse_manifests`，可通过：

```text
GET /documents/{documentId}/parse-manifest
```

审计 Router，不需要从最终 Markdown 反推。

## 5. GPU/CPU OCR

`PDF_OCR_BACKEND=auto`：

1. MPS + EasyOCR 可导入：使用 Apple Metal/MPS。
2. CUDA + PaddleOCR 可导入：使用 PaddleOCR GPU。
3. 否则检测 Tesseract，使用 CPU。
4. 都没有：禁用本地 OCR，后续由 VLM/原生文本承担。

OCR 触发：原生文字少于 160 字符，或图片覆盖率至少 12%。结果至少 20 字符才接受，最多保存 12000 字符。

与 V1 不同，VLM 跳过或失败时，有效 OCR 会以低置信度 `<figure data-type="ocr-fallback">` 写入 raw Markdown，不再只停留在 page asset metadata。

Apple Silicon 实机验证使用 PyTorch 2.12.1 + EasyOCR：`torch.backends.mps.is_available()` 为 true，Reader 设备为 `mps`，并完成了包含公式和 Python 代码的测试图片推理。CUDA/PaddleOCR 仍需在目标 NVIDIA 主机按实际 CUDA wheel 和显存重新 benchmark。

## 6. VLM Router、MCP 与多 key

### 6.1 Provider

```text
VISION_PROVIDER=gemini | openai | mcp | router | auto | comma-separated providers
```

多 key 配置使用逗号或换行：

```text
GEMINI_API_KEYS=key1,key2
OPENAI_API_KEYS=key1,key2
MCP_VISION_API_KEYS=key1,key2
```

Router 在 provider/key 实例间轮转。429、timeout、5xx 触发指数 cooldown；401/403/key 错误进入更长 cooldown。一个实例失败会尝试下一个，而不是立即让区域失败。

### 6.2 MCP Streamable HTTP

实现遵循稳定的 `2025-11-25` 生命周期：

1. `initialize` 协商协议版本；
2. 保存响应 `Mcp-Session-Id`；
3. 发送 `notifications/initialized`；
4. 后续 `tools/call` 带 `MCP-Protocol-Version` 和 session header；
5. 同时解析 JSON 与 `text/event-stream` 响应。

默认工具名 `analyze_pdf_region`，参数包括 prompt、PNG base64、MIME 和 response schema。远程端可以接任意公司的模型。官方传输规范：<https://modelcontextprotocol.io/specification/2025-11-25/basic/transports>。

### 6.3 图片结构化输出

每块必须返回：

```text
transcription, description, latex, code, uncertainty, search_text,
content_kind, importance, reading_order, language
```

Prompt 明确禁止臆造；native text context 只能辅助，不得复制不可见内容；要求保留多栏顺序、表格行列、代码缩进、手写箭头/改写、cases/matrix/alignment 公式结构。

## 7. 微批、重试、断点保存

### 7.1 微批

区域按 `VISION_BATCH_SIZE` 分批，每批最多 `VISION_CONCURRENT_REQUESTS` 个并发请求。它控制内存和瞬时配额；不同供应商原生 batch API 并不适合交互式低延迟解析，因此当前采用 provider-neutral micro-batch，而不是等待离线 batch job。

### 7.2 Retry

- 默认最多 3 次；
- 408/409/429/500/502/503/504、timeout、连接重置可重试；
- exponential backoff + jitter；
- 最大退避 30 秒；
- Router 内部还会跨 provider/key failover。

### 7.3 Exactly-resume 语义

每个区域计算：

```text
sha256(image bytes + region type + bbox + prompt version)
```

每块完成后立即 `upsert_vlm_result()`，保存 fingerprint 和 attempt count。任务崩溃或某个必需页最终失败时，已经成功的块仍在数据库；重跑只调用缺失/失败/fingerprint 变化的块。

worker 启动时会重新入队超过 10 分钟未更新、重试次数少于 3 的 stale parse task。这样不会因为一块失败反复生成正常内容。

## 8. 内容转换优化

### 8.1 复杂公式

- 文本层公式优先于“连续空格表格”规则，避免公式误转 table；
- 修复少量 PDF private glyph 和控制字符；
- 自动补齐 `cases` 结束标记和行分隔；
- 图片公式使用 VLM `latex`；即使 transcription 很长也不再丢弃 LaTeX；
- 整页扫描/手写去重只移除重复 transcription，结构化 LaTeX、代码、图解与 uncertainty 作为 supplement 保留；
- 相同原生/视觉公式按 token overlap 和字符串相似度去重。
- `aligned`、`cases`、`matrix/pmatrix/bmatrix`、积分、求和、乘积、极限、行列式和 Unicode 数学符号均具有语义保护，不能被页眉页脚过滤器覆盖。

这仍不是符号计算验证器。quality report 只检查 Markdown fence 结构，精确数学正确性需要带标注公式集继续评估。

### 8.2 手写

- 用户选择手写后整页 VLM；
- 垃圾 OCR/text layer 被抑制；
- Prompt 要求保留箭头、推导顺序、改写和 uncertainty；
- 任何必需页失败会使本轮任务显式失败，但其他页 checkpoint 保留；
- chunk 使用 page-aware 语义。

### 8.3 表格

- 原生文本按 `|` 或列间空格检测一致列形；
- 图片表格要求 VLM 保留行列关系；
- 转换为 Markdown table；
- 大表可独立 chunk，并标记 `containsTable`。

### 8.4 多栏

当左右窄 block 各至少两个时，按“宽标题/分隔块 → 左栏从上到下 → 右栏从上到下”排序；单栏继续使用 `(y,x)`。layout metadata 记录 `readingOrder=two_column`。

### 8.5 图片 transcription

视觉块 metadata 现在包含相邻 native text context、region area、bbox、document type/page signals。VLM 的 declared `content_kind` 优先于脆弱的字符串猜测；`importance=low + decorative` 可在 Markdown 前过滤。

### 8.6 多语言代码

代码检测覆盖 C/C++、Java、Python、JavaScript/TypeScript、Scheme/Racket、SQL、shell shebang 和 R function 典型结构。宽泛的“符号数量很多就是代码”规则已经删除，避免集合、概率表达式和矩阵被包进 code fence。代码与公式都拥有去噪硬保护。

## 9. 无用信息过滤

### 9.1 文字

禁止用某个课程名、固定页码格式或单条正则直接删除。V2 只允许以下多证据流程：

1. 文档至少 8 页；
2. block 位于顶部 12% 或底部 12%，且不超过 28 tokens/3 行；
3. exact block、numeric family 或 block 内多数行在至少 `max(5, 25%页数)` 页面重复；
4. 位置、重复覆盖率、数字变化和低上下文共同达到 0.84 高置信度才排除；
5. 0.62–0.84 只写 `noiseAssessment`，内容仍保留；
6. 任何 formula/code/table/heading/list、显式 LaTeX/Unicode 数学符号、高符号密度或编程语言信号都会硬保护，重复率和位置不能覆盖保护。

数字替换为 `#` 仅是弱证据之一，绝不是删除决定。被排除内容仍保存在 `document_layout_blocks`，含 score/reasons，可审计和恢复。

### 9.2 图片

- 小于页面 3%、小于 40×40、极端长宽比区域丢弃；
- 相同 average hash 达到跨页阈值、且区域自身小于页面 8% 时可删除；
- `HANDWRITTEN/FULL_PAGE_VISUAL/CODE_IMAGE` 受保护；
- VLM 明确声明 `decorative` 的结果不进入 Markdown；
- 同页视觉结果按包含/0.82 相似度去重。

代码截图不再仅凭“页面文字少”武断分类；只有 OCR 命中代码特征才预标 `CODE_IMAGE`，否则为 `TEXT_IMAGE`，最终由 VLM `content_kind` 判定。

## 10. Raw Markdown 的 RAG 契约

`document_markdown_documents.structure_json`：

```json
{
  "schemaVersion": "raw-markdown-index-v2",
  "documentType": "COURSE_NOTES",
  "headings": [{"page": 1, "text": "..."}],
  "pages": [{
    "page": 1,
    "description": "...",
    "retrievalTags": ["mathematics", "visual"],
    "applicableScenarios": ["semantic_search", "formula_lookup", "theorem_lookup"],
    "sourceType": "hybrid",
    "qualityScore": 0.93
  }]
}
```

场景随内容和用户 document type 合并，例如：

- formula → `formula_lookup`, `derivation_qa`；
- code → `code_search`, `implementation_qa`；
- table → `structured_fact_lookup`；
- diagram → `visual_explanation`；
- research paper → `methodology_lookup`, `evidence_synthesis`；
- assignment/exam → `question_lookup`；
- handwritten → `handwritten_derivation`。

chunk metadata 保存 document type、source type、chunk strategy、heading、content flags、asset IDs 和 bbox refs，便于 RAG 过滤、引用和回到页图。

## 11. Quality Gate

文档质量报告包括：

- page count / empty page count；
- average page score；
- native token coverage；
- failed VLM region count；
- 跨页重复行比率；
- math/code fence 是否平衡；
- `qualityGatePassed` 与 issues。

当前 gate 发现问题会保存并暴露，不会删除已有成功结果。强制 VLM 页失败属于正确性错误，会显式失败。

## 12. 生成文件生命周期

保留：

- 原始上传 PDF；
- `document_page_assets` 引用的页图；
- `document_visual_regions` 引用的裁剪图。

成功完成后扫描该 document 的 `rendered/` 和 `regions/` 目录，只删除未被当前记录引用的旧版本/孤儿文件。失败处理中间文件暂留，用于 retry/resume；下一次成功后统一清理。这样既避免磁盘泄漏，也不会产生数据库指向已删除资产的记录。

## 13. 验证结果

### 13.1 自动测试

```text
Worker unittest: 32 passed
API Gradle test: BUILD SUCCESSFUL
```

覆盖：

- VRAM worker 计算和 CPU fallback；
- 混合页来源分布与逐页 route；
- handwrite/document type；
- 两栏阅读顺序；
- 多行公式/cases；
- 图片公式 LaTeX；
- OCR fallback；
- RAG index/quality report；
- 多 key/provider failover；
- MCP initialize/session/tool call；
- fingerprint resume；
- artifact cleanup；
- 保守多证据去噪、短文档不删除、重复公式/代码硬保护；
- Python/C++/JavaScript/Scheme/SQL 与复杂 matrix/aligned/cases 公式；
- 三级 Redis 优先队列、后台槽预留和防饥饿；
- 合成 PDF 的文字、公式、多栏、文字图片端到端转换。

### 13.2 PostgreSQL 与完整 Pipeline

隔离 PostgreSQL 测试库已验证：

- schema create/alter；
- VLM incremental upsert/load；
- source confidence/distribution；
- parse manifest；
- stale task recovery；
- 合成 PDF → mock VLM → layout → Markdown → chunk → completed task；
- page source `text/vlm`、chunk image provenance 和 quality gate。

测试数据库在验证后已删除。

### 13.3 真实课程 PDF 迭代

离线审计了代码截图课件、数学课件、22 页手写笔记和 359 页课程讲义，不调用付费 VLM：

| 文档 | 页数 | Router 结果 | 关键观察 |
|---|---:|---|---|
| CS116 | 17 | 8 native / 9 hybrid | 图片课件页进入 hybrid；原生代码与代码截图分别进入文本/视觉链路 |
| MATH138L25 | 7 | 4 native / 3 hybrid | 73 个公式 block，错误 code block 从 10 降为 0 |
| STAT230Jun17 | 22 | 22 full-page VLM | 用户手写类型覆盖全部页面 |
| STAT230CourseNote | 359 | 152 native / 207 hybrid | 3394 个公式 block；错误 code block 从 924 降为 0；错误双栏标记从 4345 个 block 降为 0 |

第一轮真实审计暴露了“集合/概率公式被符号数量规则误判为代码”和“并排公式被误判为双栏”两个问题。第二轮取消宽泛符号代码规则，并要求双栏候选具有足够的自然语言比例、左右分离和垂直重叠。以上是修正后的结果。

最终对仓库全部 13 份、合计 878 页 PDF 做逐 block 安全审计：识别 5359 个公式、326 个代码、114 个表格 block；多证据过滤排除 89 个噪声 block；被排除内容中具有公式/代码/表格保护信号的数量为 **0**。

## 14. 配置入口

完整示例见 `services/worker/.env.example`。关键调参原则：

- 先运行 MuPDF benchmark，再改 `PDF_CPU_WORKERS`；
- 用实际 OCR 模型峰值显存设置 `PDF_GPU_MEMORY_PER_TASK_MIB`；
- `VISION_CONCURRENT_REQUESTS` 不得高于供应商稳定配额；默认短文档最多选择 96 个可选区域，长文档最多选择 64 个高价值区域；必需整页不受上限影响；
- 多 key 用于容量和故障隔离，不应规避供应商限制；
- 生产环境推荐 `VISION_PROVIDER=router` 并至少配置两个独立 provider/key。

## 15. 当前边界

- Apple MPS/EasyOCR 已完成真实推理验证；本机没有 CUDA GPU，因此 CUDA/PaddleOCR 路径仍只完成能力探测与单元测试，实际吞吐必须在目标 NVIDIA 主机再次 benchmark。
- VLM 的公式/手写准确率依赖所选模型；结构 gate 不能替代带 ground truth 的字符/公式准确率评测。
- 两栏算法针对常见双栏学术布局；三栏、浮动侧注和复杂跨栏需要在真实失败样本上继续扩充。
- 稳定 MCP 版本默认 `2025-11-25`；协议升级必须通过配置和兼容测试，不应静默改变 wire behavior。
