# PDF 转 Raw Markdown 当前策略

审查日期：2026-06-27

> 历史说明：本文记录 Converter V2 重构前的代码审计与缺口。当前实现与运行规范请以 `PDF_CONVERTER_V2_ARCHITECTURE.md` 为准；本文保留用于解释重构动机。

本文档以当前代码实现为准，说明 NoteFlow 如何把 PDF 转成 raw Markdown，重点覆盖纯文字、公式、含重要文字的图片、无用图片和手写内容。本文描述的是**当前行为**，不是目标设计。

## 1. 范围与输出定义

这里的 raw Markdown 指 worker 在解析阶段生成、尚未进入 AI Notes 改写的两级结果：

- `document_markdown_pages`：逐页 Markdown，并保存 `source_type`、质量分和 warnings。
- `document_markdown_documents`：按页拼接的完整 Markdown；页之间带 `<!-- page:N -->` 和 `---`。

它们随后才会被解析为 `document_chunks`。主调用链为：

```text
ParseDocumentPipeline.run
  -> parse_pdf                         # 提取基础文本并判断物理来源类型
  -> resolve_processing_strategy       # 选择整页 VLM 或文本/视觉混合路线
  -> analyze_pdf_visuals               # 每页渲染 PNG，统计图片/绘图，按条件执行本地 OCR
  -> [整页 VLM] 或 [裁剪视觉区 + 选择性 VLM + PyMuPDF 布局提取]
  -> build_markdown_document           # 生成逐页和全文 raw Markdown
  -> build_markdown_chunks             # 从 raw Markdown 生成检索 chunk
```

关键实现：

- `services/worker/noteflow_worker/pipelines/parse_document.py`
- `services/worker/noteflow_worker/pdf/parser.py`
- `services/worker/noteflow_worker/pdf/strategies.py`
- `services/worker/noteflow_worker/pdf/visual.py`
- `services/worker/noteflow_worker/pdf/regions.py`
- `services/worker/noteflow_worker/pdf/layout.py`
- `services/worker/noteflow_worker/pdf/markdown.py`
- `services/worker/noteflow_worker/vision/providers.py`

## 2. 总体路由

### 2.1 物理来源类型判定

`parse_pdf()` 先用 pypdf 提取文本，再按全文字符数除以页数判断：

| 条件 | `content_source_type` |
|---|---|
| 0 页 | `UNKNOWN` |
| 用户类型为 `HANDWRITTEN_NOTES`，且每页少于 100 字符 | `HANDWRITTEN_SCAN` |
| 每页少于 100 字符 | `SCANNED_PDF` |
| 每页少于 300 字符 | `MIXED` |
| 每页至少 300 字符 | `TEXT_PDF` |

这是文档级平均值，不是逐页分类。稀疏的原生文字 PDF 可能被当作扫描件；一份大部分有文本、但夹有扫描页的 PDF 也可能整体落入 `MIXED` 或 `TEXT_PDF`。

### 2.2 两条实际处理路线

| 触发条件 | Markdown 路线 | VLM 要求 |
|---|---|---|
| 用户类型为 `HANDWRITTEN_NOTES` | 每页整图 VLM | 每页必须成功 |
| 来源为 `SCANNED_PDF` 或 `HANDWRITTEN_SCAN` | 每页整图 VLM | 每页必须成功 |
| 其他情况 | PyMuPDF 原生文本 + 裁剪视觉区 VLM | 视觉区允许失败，文字流程继续 |

`LECTURE_SLIDES`、`COURSE_NOTES`、`RESEARCH_PAPER`、`ASSIGNMENT` 等 `markdown_strategy` 名称主要记录在策略与 metadata 中。除整页 VLM 与混合路线的分叉外，它们目前更多影响后续 chunk 边界，而不是采用不同的 PDF-to-Markdown 引擎。

## 3. 五类内容的当前策略

### 3.1 纯文字

适用：带可靠文本层的 `TEXT_PDF` 或 `MIXED` 页面。

处理步骤：

1. PyMuPDF 使用 `page.get_text("dict", sort=True)` 读取文字 block 和 bbox。
2. span 按行拼接，清理控制字符和部分数学私有区字形。
3. 每行通过启发式规则识别 `CODE`、`FORMULA`、`TABLE`、`LIST`、`HEADING` 或 `PARAGRAPH`。
4. 小段落会合并；标题维护简单的 heading path。
5. 重复至少 3 页、短于约 35 tokens 的普通段落/标题可能标为 `BOILERPLATE`，不进入 Markdown。
6. Markdown 中标题统一渲染成二级标题 `##`；段落保留抽取后的文本。

优先级：原生文本是混合路线的主要内容源。它不经过大模型润色，因此顺序、断行和符号质量取决于 PDF 的文本层及 PyMuPDF block 顺序。

当前限制：

- 多栏页面按 `(y, x, block_index)` 排序，复杂排版可能出现阅读顺序错误。
- boilerplate 只按短文本的跨页指纹过滤，没有严格利用页眉/页脚坐标。
- `LIST` 在部分分类/合并路径中会退化成普通段落，不保证生成 Markdown 列表语法。

### 3.2 公式

公式有两条来源。

#### A. 文本层公式

`is_formula_like()` 依据 `\sum`、`\int`、`\frac`、常见数学符号、等号、幂号和短表达式等规则识别公式。识别为公式的 block 会渲染为：

```markdown
$$
原始抽取文本
$$
```

`math_normalizer.py` 只做有限修复：

- 替换少量控制字符；
- 把特定私有区 glyph 修成括号或 `\begin{cases}`；
- 为缺失的 `\end{cases}` 补齐结束标记；
- 合并空白。

这不是完整的 Math OCR，也不会把所有 PDF 字形可靠转换成 LaTeX。因此 `$$` 中可能仍是扁平文本、错误字符或顺序混乱的表达式。

#### B. 图片中的公式

裁剪视觉区交给 VLM，结构化结果包含 `transcription` 和 `latex`。Markdown 层若检测到 `latex` 或公式特征，会分类为 `FORMULA_IMAGE`，优先把 `latex` 包进 `$$`。

但当前有一个重要分支：只要 transcription 超过 12 个词，`render_visual_result()` 会提前直接返回 transcription；此时即使 VLM 提供了 `latex`，也可能不会进入最终 Markdown。密集公式图片因此不保证保留结构化 LaTeX。

公式去重：若图片公式与本页原生文字的规范化 token 重合度达到约 70%，或字符串相似度达到 0.82，视觉版本会被过滤，避免同一公式出现两次。

### 3.3 含重要文字内容的图片

典型内容包括截图、扫描文字、代码截图、图表标签和带文字的示意图。

#### 区域发现

1. 所有页面以 144 DPI 渲染为 PNG。
2. 读取 PDF 的 image block bbox。
3. 候选区域小于页面面积 3%、小于 `40x40` 像素，或宽高比超过 `12:1`/`1:12` 时丢弃。
4. 页面有视觉内容但没有可用裁剪时，在满足下列任一条件时补整页区域：
   - 图片覆盖率至少 12%；
   - 页面含图片且原生文本不超过 160 字符。

有至少 8 个 vector drawing 的页面也算“有视觉内容”；若没有 image block，会走整页 fallback。

#### VLM 提取

VLM 被要求返回固定 JSON 字段：

```text
transcription, description, latex, code, uncertainty, search_text
```

提示词要求精确转录可见文字/手写/代码/标签，用 `[unclear]` 标记不可读部分，并解释图、箭头、坐标轴和关系。支持 Gemini 或 OpenAI provider。

VLM 结果再按内容分类：

| 分类 | Raw Markdown 处理 |
|---|---|
| `TEXT_IMAGE` | 插入 transcription |
| `CODE_IMAGE` | 尽可能生成 fenced code block |
| `FORMULA_IMAGE` | 优先生成 `$$...$$`，受上面的长 transcription 分支影响 |
| `TABLE_IMAGE` | 按 `|` 或连续空格猜测列并转成 Markdown table |
| `DIAGRAM` / `UNKNOWN_VISUAL` | 生成 `<figure>`，包含可见文字、解释、LaTeX 和 uncertainty |
| 整页视觉/手写 | 直接以整页 transcription 为主体 |

与原生文字重复的视觉转录会按 token overlap/字符串相似度过滤；同页视觉区之间也以包含关系或 0.82 相似度去重。

#### 长文档与数量上限

- 每份文档最多保留 `VISION_MAX_REGIONS_PER_DOCUMENT` 个区域，默认 24 个，按页顺序达到上限即停止。
- 非手写文档达到 120 页后，只选择最多 8 个高价值区域调用 VLM。高价值包括 `CODE_IMAGE`、`HANDWRITTEN`、低原生文本页面，以及图片覆盖率至少 12% 的 `DIAGRAM`。

因此，长文档中的普通文字图片可能不会被 VLM 转录。

### 3.4 无用图片

当前有两层过滤。

#### A. VLM 前：重复区域过滤

每个裁剪计算 8x8 grayscale average hash。相同 hash 出现次数严格大于 `max(3, ceil(总页数 * 15%))` 时，区域才被视为重复候选。只有页面级 `imageCoverage < 12%` 的普通区域可被删除。

以下类型不会仅因重复而删除：

- `CODE_IMAGE`
- `HANDWRITTEN`
- `FULL_PAGE_VISUAL`

注意：这里使用的是完全相同的 average hash，不是距离阈值；metadata 中保存的也是页面图片覆盖率而非该裁剪自身的面积占比。因此它能过滤部分 logo/背景，但不是可靠的语义无用图检测。

#### B. VLM 后：装饰图过滤

若没有 transcription，且 VLM 文本为空、只有不超过两个普通词，或描述命中 `background`、`texture`、`wood grain` 等装饰词，同时不含 theorem/code/function 等重要词，则分类为 `DECORATIVE_IMAGE`，不写入 Markdown。

目前装饰词表很窄。照片、广告、头像、课程 logo 等无用内容若 VLM 给出较长描述，仍可能以 `<figure>` 进入 raw Markdown。

### 3.5 手写内容

可靠的手写主路线依赖用户把文档类型标为 `HANDWRITTEN_NOTES`，或文档文本密度低到被识别为 `HANDWRITTEN_SCAN`。

处理方式：

1. 每页渲染整页 PNG，不裁剪局部区域。
2. 每页创建一个 `HANDWRITTEN` 区域。
3. 每页调用 VLM，要求忠实转录、保留布局/关系并输出 uncertainty。
4. layout block 使用 `transcription or description`。
5. Markdown builder 以 layout transcription 为页面正文，并过滤同一 VLM 结果的重复视觉副本。
6. 后续 chunk 使用 `PAGE_AWARE`，尽量保持页边界；短的相邻页面可合并，过长页面可拆分。

失败策略：整页 VLM 最多按配置重试，默认 3 次；超时、429 和常见 5xx 等错误会退避重试。任何必需页面最终失败都会使整份解析任务失败，不会静默生成空白手写页。

当前限制：

- 混在普通 `TEXT_PDF` 中的局部手写批注没有专门的视觉检测器；只有文档类型为 `HANDWRITTEN_NOTES` 时区域才直接标为 `HANDWRITTEN`。
- 本地 Tesseract OCR 不作为手写路线的成功降级；这条路线要求 VLM 成功。
- transcription 的公式结构、阅读顺序和 `[unclear]` 使用情况由模型输出质量决定。

## 4. 本地 OCR 的真实作用

`visual.py` 在以下情况尝试 Tesseract：

- 页面原生文字少于 160 字符；或
- 页面含 image block 且图片覆盖率至少 12%。

OCR 结果少于 20 字符会丢弃，保留时最多 4000 字符。它会进入 page asset 的 `visual_summary`，也会被放进无 VLM 的视觉 `WorkingBlock.summary`。

但是 `build_markdown_page()` 会跳过 `IMAGE`/`MIXED_VISUAL` layout block，只从成功的 `vlm_results` 渲染视觉 Markdown。因此在当前主流程中：

- 本地 OCR 主要用于资产诊断和中间 metadata；
- 对混合路线中失败或未选择的 VLM 区域，OCR **不保证进入最终 raw Markdown**；
- 对强制整页 VLM 路线，OCR 不能替代失败的 VLM。

这是现有文档中容易被误解的一点。

## 5. Raw Markdown 合成与质量标记

逐页合成规则：

1. 跳过 `BOILERPLATE`。
2. 先渲染非视觉 layout block。
3. 忽略带 `error_message` 的 VLM 结果。
4. 分类、过滤和渲染成功的视觉结果。
5. 若页面最终为空，写入 `<!-- No extractable content on page N. -->`。
6. 计算质量分并保存 warnings。

常见 warnings：

- `decorative_visual_filtered`
- `full_page_visual_duplicate_of_text`
- `visual_text_duplicate_of_pdf_text`
- `duplicate_visual_region_filtered`
- `empty_visual_region_filtered`
- `empty_markdown_page`

质量分从 1.0 开始，短页面、warnings 和被过滤区域只做简单扣分。它不是 OCR/VLM 正确率评估，不能单独证明内容完整。

## 6. 配置与运行前提

相关默认配置位于 `services/worker/noteflow_worker/config.py`：

| 配置 | 默认值 | 作用 |
|---|---:|---|
| `VISION_PROVIDER` | `disabled` | `gemini`、`openai` 或禁用 |
| `GEMINI_VISION_MODEL` | `gemini-2.5-flash` | Gemini 视觉模型 |
| `OPENAI_VISION_MODEL` | `gpt-4o-mini` | OpenAI 视觉模型 |
| `VISION_MAX_REGIONS_PER_DOCUMENT` | 24 | 每文档视觉区域上限 |
| `VISION_REQUEST_TIMEOUT_SECONDS` | 60 | 单次请求超时 |
| `VISION_REQUEST_MAX_ATTEMPTS` | 3 | 最大尝试次数 |
| `VISION_RETRY_BACKOFF_SECONDS` | 2.0 | 线性退避基数 |

如果 provider 为 `disabled` 或 API key 缺失：

- 扫描/手写路线必然失败，因为它要求 VLM 成功；
- 原生文本路线仍可完成，但图片内容通常不会进入 raw Markdown。

## 7. 现状结论

| 内容类型 | 当前主策略 | 完整性判断 |
|---|---|---|
| 纯文字 | PyMuPDF block 提取 + 启发式结构化 | 一般可用，复杂版式和页眉页脚有风险 |
| 文字层公式 | 启发式识别 + 有限 glyph 修复 + `$$` 包裹 | 可搜索，不等于可靠 LaTeX |
| 图片公式 | 视觉区 VLM + `latex` 字段 | 有条件可用，长 transcription 分支可能丢失 LaTeX |
| 重要文字图片 | 裁剪/整页 fallback + VLM 转录 + 去重 | 中短文档较完整；长文档及区域上限下可能漏内容 |
| 无用图片 | 重复 aHash 过滤 + VLM 装饰词过滤 | 仅启发式，可能误留或误删 |
| 手写 PDF | 每页强制 VLM + page-aware chunk | 路线明确，但完全依赖 VLM 成功和转录质量 |

## 8. 已确认的技术债务与建议优先级

1. **P0：让 OCR fallback 真正进入 raw Markdown。** 对选择性 VLM 失败/未选择的视觉区，当前 OCR 中间结果会在 Markdown 层丢失。
2. **P0：修复公式图片长 transcription 分支。** `FORMULA_IMAGE` 应优先保留 `latex`，再附 transcription，而不是被通用的“超过 12 词”分支截断。
3. **P1：改成逐页来源判定。** 文档级平均字符密度无法可靠处理混合扫描页。
4. **P1：为长文档记录未分析视觉区。** 当前 24 区域上限和 120 页/8 区域策略可能静默漏掉重要图片。
5. **P1：增加局部手写检测。** 不应完全依赖用户选择 `HANDWRITTEN_NOTES`。
6. **P2：使用 bbox 位置增强页眉页脚过滤，并用感知 hash 距离而非完全相等判断重复图片。**
7. **P2：增加按内容类型的回归样本。** 至少覆盖文本、多栏、文本公式、公式图片、代码截图、装饰图、局部手写和整页手写。

## 9. 代码事实索引

| 事实 | 实现位置 |
|---|---|
| 来源类型阈值 | `pdf/parser.py::detect_content_source_type` |
| 路由优先级 | `pdf/strategies.py::resolve_processing_strategy` |
| 两条主流水线 | `pipelines/parse_document.py::ParseDocumentPipeline.run` |
| 页面渲染和 OCR 条件 | `pdf/visual.py::analyze_pdf_visuals`, `should_run_ocr` |
| 区域裁剪、重复过滤、长文档选择 | `pdf/regions.py` |
| 文本 block/公式/table 格式化 | `pdf/layout.py::extract_page_text_blocks`, `format_block_content` |
| 数学字符有限修复 | `pdf/math_normalizer.py` |
| 视觉结构化输出契约与重试 | `vision/providers.py`, `pdf/regions.py::analyze_regions_with_vlm` |
| Markdown 视觉分类、过滤、去重 | `pdf/markdown.py` |
| Raw Markdown 到 chunk | `pdf/layout.py::build_markdown_chunks` |
