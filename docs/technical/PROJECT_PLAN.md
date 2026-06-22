# NoteFlow / StudyForge 项目计划

## 1. 项目定位

**产品名称：** NoteFlow  
**概念名称：** StudyForge  
**英文一句话：** A full-stack AI study workspace that transforms technical PDFs into citation-grounded notes, quizzes, and review checklists, then lets students refine them in a Notion-style editor with LaTeX math support.

NoteFlow 面向大学生和技术学习者，提供从 PDF 上传、AI 解析、结构化笔记生成、题库生成、RAG 问答、数学公式编辑到 Markdown / PDF 导出的完整学习资料工作流。

项目的核心不是做一个简单的 PDF summarizer，而是做一个可以真实使用、可以展示工程能力的学习资料处理平台。

## 2. 项目背景

学生在学习数学、统计、计算机科学、工程、经济学、机器学习等课程时，经常需要处理大量 lecture notes、slides、论文和 PDF 材料。现有工具通常存在以下问题：

1. PDF 阅读器只能阅读，不能自动整理知识点。
2. 普通 AI 总结工具容易漏重点、格式混乱，且无法追溯来源。
3. Notion / Word 对数学公式、AI 生成内容、PDF 原文追溯支持不够自然。
4. 学生需要在 PDF 阅读器、ChatGPT、Notion、Word、Markdown 编辑器之间频繁切换。
5. AI 生成内容通常只是初稿，用户仍需要人工编辑、补充、插入公式、整理结构并导出。

NoteFlow 希望把这些动作整合成一个闭环：

**PDF 上传 -> AI 生成笔记和题库 -> 原文 citation 校验 -> 富文本编辑 -> LaTeX 公式整理 -> Markdown / PDF 导出**

## 3. 目标用户

主要用户：

1. 大学生
2. CS / Math / Stats / Engineering 学生
3. 阅读论文的本科生、研究助理或自学者
4. 需要把 PDF 转成 Markdown / Notion 笔记的学习者
5. 需要整理公式、定理、证明、题库和复习材料的用户

典型场景：

1. STAT 学生上传 lecture PDF，生成中文笔记、公式总结和练习题。
2. CS 学生上传逻辑证明 notes，生成概念解释、证明步骤和 quiz。
3. ML 学生上传论文，生成 method breakdown、algorithm summary 和 reproduction checklist。
4. 用户在内置编辑器中修改 AI 笔记，并导出 Markdown。
5. 用户插入公式，例如 `E[X] = \sum_x xP(X=x)` 或 `\operatorname{Var}(X)=E[X^2]-E[X]^2`。

## 4. 核心目标

1. 支持用户上传课程 PDF、slides、论文或 lecture notes。
2. 自动解析文档并生成结构化学习笔记。
3. 自动生成题库、答案解析和复习 checklist。
4. 上传文档后为每个 chunk 生成 embedding，建立可搜索的语义索引。
5. 用户可以用一句自然语言搜索 PDF / 笔记中的相关内容。
6. 支持 RAG 问答，避免直接把整篇 PDF 喂给模型。
7. AI 生成内容尽量绑定 PDF 原文页码或文本片段。
8. 提供类似 Notion / Word 的富文本编辑器。
9. 编辑器支持 inline LaTeX 和 block LaTeX。
10. 支持自动保存编辑内容。
11. 支持 Markdown、Notion-friendly Markdown 和后续 PDF 导出。
12. 最终可部署上线，作为 portfolio 项目展示。

## 5. MVP 范围

### MVP 必做

1. 用户登录
2. PDF 上传
3. 文档列表 dashboard
4. 异步任务状态展示
5. PDF 文本解析
6. Chunk embedding 生成与 pgvector 存储
7. 用户自然语言语义搜索
8. AI 生成结构化笔记
9. AI 生成题库
10. Tiptap 编辑器
11. Inline LaTeX 公式
12. Block LaTeX 公式
13. 自动保存
14. Markdown 导出
15. Docker Compose 本地启动
16. GitHub README 和在线 demo

### MVP 暂不做

1. 多人协作
2. 实时共同编辑
3. 评论系统
4. 完整 Word 排版
5. 移动端 App
6. 桌面端本地全套运行模式
7. 自研浏览器内核或自研 WebView
8. Notion database
9. 复杂权限共享
10. 手写 OCR 深度优化

## 6. 功能需求

### 6.1 用户账户系统

用户需要能够：

1. 注册和登录
2. 管理自己的文档
3. 查看历史上传记录
4. 查看 AI 生成的笔记和题库
5. 保存编辑后的笔记
6. 导出自己的学习资料

MVP 可以使用 Clerk 或 NextAuth，后续再根据部署需要调整认证方案。

### 6.2 文档上传系统

MVP 优先支持 PDF。上传后保存：

1. 文件名
2. 文件大小
3. 文件类型
4. 上传用户
5. 上传时间
6. 文件存储地址
7. 处理状态
8. 页数
9. 文档语言
10. 文档类型标签

### 6.3 异步任务系统

PDF 解析、embedding 生成和 AI 生成都可能耗时较长，需要后台任务处理。

任务能力：

1. 创建任务
2. 查询任务状态
3. Worker 后台处理
4. 失败重试
5. 错误记录
6. 完成后保存结果

任务状态：

```text
PENDING
PROCESSING
COMPLETED
FAILED
RETRYING
CANCELLED
```

### 6.4 AI 笔记生成系统

课程笔记模式：

1. Topic Overview
2. Key Definitions
3. Key Formulas
4. Important Theorems
5. Worked Examples
6. Common Mistakes
7. Practice Questions
8. Review Checklist

论文模式：

1. Problem
2. Motivation
3. Main Contribution
4. Method
5. Algorithm Steps
6. Mathematical Formulation
7. Experiment Setup
8. Results
9. Limitations
10. Reproduction Checklist

### 6.5 RAG 检索系统

系统需要：

1. 解析 PDF
2. 清洗文本
3. 按章节、页码和段落切分 chunk
4. 为每个 chunk 生成 embedding
5. 把 chunk 和 embedding 存入 PostgreSQL + pgvector
6. 生成笔记或回答问题前检索相关 chunks
7. 使用检索结果生成结构化答案

这样可以降低 hallucination，并让用户验证 AI 结果。

### 6.6 自然语言语义搜索

用户应该可以输入一句自然语言，在当前 PDF 或当前笔记中搜索相关内容。

示例输入：

```text
为什么 variance 可以写成 E[X^2] - E[X]^2？
```

系统流程：

1. 前端把用户 query 发送给后端。
2. 后端或 Worker 调用 embedding model 生成 query embedding。
3. 系统在 `document_chunks.embedding` 中做 pgvector similarity search。
4. 返回最相关的 chunks。
5. 前端展示 page number、section title、source snippet 和 similarity score。

这个功能不一定调用 LLM。它的目标是“帮用户定位资料里的相关内容”，因此速度应该快、成本应该低、结果应该可验证。

搜索结果需要包含：

1. chunk id
2. document id
3. page number
4. section title
5. snippet
6. similarity score

### 6.7 RAG 问答系统

RAG 问答复用语义搜索能力，但会额外调用 LLM 生成解释。

系统流程：

1. 用户输入问题。
2. 系统生成 query embedding。
3. pgvector 检索 top-k 相关 chunks。
4. LLM 基于 retrieved chunks 生成回答。
5. 回答附带 citations。

搜索和问答应该在产品上分开：

1. Search in document: 找原文片段，不调用 LLM。
2. Ask with sources: 基于原文回答，调用 LLM。

### 6.8 Citation Grounding

AI 生成的重点内容应尽量绑定来源。

每条来源记录包含：

1. note item id
2. source document id
3. source chunk id
4. source page
5. source section
6. source text snippet

前端应允许用户点击 citation 查看原文片段。

### 6.9 笔记编辑器

编辑器目标：

1. 类似 Notion / Word 的写作体验
2. 支持 AI 生成内容进入编辑器
3. 支持用户手动修改
4. 支持标题、段落、列表、引用、代码块
5. 支持 inline math
6. 支持 block math
7. 支持 Markdown 导出
8. 支持自动保存
9. 后续支持编辑历史

MVP 编辑器功能：

1. Heading 1 / 2 / 3
2. Paragraph
3. Bullet list
4. Numbered list
5. Code block
6. Quote
7. Inline LaTeX
8. Block LaTeX
9. Bold / Italic / Inline code
10. Undo / Redo
11. Autosave
12. Export as Markdown

### 6.10 数学公式支持

公式分为两类：

Inline math:

```latex
E[X] = \sum_x xP(X=x)
```

Block math:

```latex
\operatorname{Var}(X)=E[X^2]-E[X]^2
```

交互方式：

1. 用户输入 `/math`
2. 选择 Inline Math 或 Block Math
3. 输入 LaTeX
4. 编辑器用 KaTeX 渲染
5. 点击公式可以重新编辑
6. 导出 Markdown 时保留 LaTeX 语法

### 6.11 题库生成系统

每道题包含：

1. question
2. question type
3. difficulty
4. topic
5. source page
6. answer
7. explanation
8. related formula
9. common mistake

题目类型：

1. Conceptual
2. Calculation
3. Proof
4. Multiple Choice
5. Short Answer
6. True / False

难度：

```text
Easy
Medium
Hard
```

### 6.12 导出系统

MVP 支持：

1. Markdown
2. Copy as Markdown
3. Notion-friendly Markdown

后续支持：

1. PDF export
2. Notion API export

导出需要保留：

1. 标题层级
2. LaTeX 公式
3. 代码块
4. 列表
5. citation
6. Notion 兼容格式

## 7. 技术栈

### 7.1 前端

技术：

1. Next.js
2. TypeScript
3. Tailwind CSS
4. shadcn/ui
5. Tiptap
6. KaTeX
7. TanStack Query
8. Zustand 可选

职责：

1. 用户界面
2. 文档上传
3. Dashboard
4. 任务状态展示
5. 笔记编辑器
6. 公式编辑
7. 题库页面
8. Markdown 导出
9. 登录状态管理

选择理由：

1. Next.js 适合完整 Web App 和 Vercel 部署。
2. Tiptap 适合做可扩展的 Notion-style editor。
3. KaTeX 渲染速度快，适合 Web 数学公式。

### 7.2 后端

技术：

1. Java 21
2. Spring Boot
3. Spring Security
4. Spring Data JPA
5. PostgreSQL
6. Redis
7. JWT / Clerk integration
8. Docker

职责：

1. 用户权限
2. 文档 metadata 管理
3. 文件上传接口
4. 任务创建
5. 任务状态管理
6. 笔记保存
7. 编辑器内容保存
8. 题库保存
9. 导出接口
10. Worker 通信

选择理由：

1. Spring Boot 能展示传统后端能力。
2. Java 后端适合 co-op / backend 简历定位。
3. REST API、数据库、认证、任务管理都适合用 Spring Boot 实现。

### 7.3 AI Worker

技术：

1. Python
2. FastAPI 可选
3. PyMuPDF
4. pdfplumber
5. OpenAI / Gemini / Claude API
6. sentence-transformers 可选
7. LangChain / LlamaIndex 可选
8. Redis client
9. PostgreSQL client

职责：

1. 下载 PDF
2. 解析文本
3. 清洗文本
4. 按 section / page / paragraph 切 chunk
5. 生成 embedding
6. 存储 chunk 和 embedding
7. 执行 RAG 检索
8. 生成结构化笔记
9. 生成题库
10. 回写数据库

选择理由：

1. Python 的 PDF 和 AI 生态成熟。
2. Worker 与 Java 后端解耦。
3. 架构更接近真实生产系统。

### 7.4 数据库与存储

数据库：

1. PostgreSQL
2. pgvector

缓存和队列：

1. Redis

文件存储：

1. MVP: local storage 或 Supabase Storage
2. Production: Cloudflare R2 或 AWS S3

### 7.5 桌面端

桌面端推荐路线：

```text
先做 Web App
  -> 部署在线 demo
  -> 再用 Electron 包成桌面软件
```

推荐技术：

1. Electron
2. TypeScript
3. Next.js static/export 或本地渲染入口
4. Electron auto-updater 后期可选

桌面端职责：

1. 提供可安装的 macOS / Windows / Linux App。
2. 复用 Web App 的界面、Tiptap 编辑器、KaTeX 公式和 PDF 工作流。
3. 管理桌面窗口、菜单、文件选择器和本地下载。
4. 第一版默认连接云端 Spring Boot backend。

第一版桌面端不负责：

1. 自带 PostgreSQL。
2. 自带 Redis。
3. 自带 Java 后端。
4. 自带 Python worker。
5. 自研浏览器内核。

选择 Electron 的原因：

1. 对复杂 Web 编辑器、PDF viewer 和 React UI 支持成熟。
2. 可以最大化复用 Web App。
3. 比 Swift 原生重写更适合跨平台。
4. 对 portfolio 展示来说，投入产出比最高。

## 8. 数据模型草案

主要表：

1. users
2. documents
3. tasks
4. document_chunks
5. notes
6. note_blocks
7. quizzes
8. quiz_questions
9. exports

### documents

```text
id
user_id
title
file_url
file_type
file_size
page_count
status
created_at
updated_at
```

### tasks

```text
id
document_id
user_id
task_type
status
progress
error_message
retry_count
created_at
started_at
completed_at
```

### document_chunks

```text
id
document_id
page_number
section_title
chunk_index
content
embedding
created_at
```

### notes

```text
id
document_id
user_id
title
content_json
content_markdown
created_at
updated_at
```

### quiz_questions

```text
id
document_id
question
answer
explanation
topic
difficulty
question_type
source_chunk_id
source_page
created_at
```

## 9. 系统架构

```text
User
  -> Next.js Frontend
  -> Java Spring Boot Backend
  -> PostgreSQL + pgvector
  -> Redis Queue
  -> Object Storage
  -> Python AI Worker
  -> LLM API
```

核心流程：

1. 用户上传 PDF。
2. 前端调用后端 API。
3. 后端保存文件到 object storage。
4. 后端创建 document record。
5. 后端创建 task record。
6. 后端把 task 推入 Redis queue。
7. Python worker 拉取 task。
8. Worker 解析 PDF。
9. Worker 切分 chunks。
10. Worker 生成 embeddings。
11. Worker 存入 pgvector。
12. Worker 生成笔记和题库。
13. Worker 回写 PostgreSQL。
14. 前端显示完成状态。
15. 用户进入编辑器修改笔记。
16. 用户导出 Markdown / PDF。

## 10. 页面设计

### 10.1 Landing Page

1. 项目介绍
2. 上传 PDF 生成笔记的演示
3. LaTeX 编辑器展示
4. RAG citation 展示
5. CTA: Start Studying

### 10.2 Dashboard

1. 最近上传的文档
2. 正在处理的任务
3. 已完成的笔记
4. 最近编辑的文档
5. 题库数量

### 10.3 Upload Page

1. 拖拽上传 PDF
2. 选择文档类型
3. 选择输出语言
4. 选择生成内容

可选文档类型：

1. Course Notes
2. Research Paper
3. Lecture Slides

可选输出：

1. Notes
2. Quiz
3. Formula Summary
4. Review Checklist

### 10.4 Task Progress Page

1. Upload completed
2. Parsing PDF
3. Extracting sections
4. Generating embeddings
5. Generating notes
6. Generating quiz
7. Completed

### 10.5 Document Detail Page

Tabs:

1. Overview
2. Notes
3. Quiz
4. Sources
5. Export

### 10.6 Editor Page

左侧：

1. 文档目录
2. Headings

中间：

1. Tiptap 编辑器

右侧：

1. AI assistant
2. Citations
3. Source snippets

### 10.7 Quiz Page

1. 按 topic 分组
2. 按 difficulty 筛选
3. 展示答案
4. 查看解析
5. 查看 source page

## 11. 编辑器数据结构

编辑器核心数据结构建议使用 JSON document tree。

```json
{
  "type": "doc",
  "content": [
    {
      "type": "heading",
      "attrs": { "level": 2 },
      "content": [{ "type": "text", "text": "Expected Value" }]
    },
    {
      "type": "paragraph",
      "content": [
        {
          "type": "text",
          "text": "Expected value measures the long-run average."
        }
      ]
    },
    {
      "type": "mathBlock",
      "attrs": {
        "latex": "E[X] = \\sum_x xP(X=x)"
      }
    }
  ]
}
```

保存策略：

1. 编辑器内容保存为 `content_json`。
2. 同步生成 `content_markdown`。
3. 每隔 5 到 10 秒 autosave。
4. 用户离开页面前保存。
5. 后期可以加入 version history。

公式插入方式：

1. Slash command: `/math`
2. Toolbar button
3. Keyboard shortcut
4. Paste LaTeX 自动识别可选

## 12. API 草案

Auth:

```text
POST /auth/register
POST /auth/login
GET /auth/me
```

Documents:

```text
POST /documents
GET /documents
GET /documents/{id}
DELETE /documents/{id}
```

Tasks:

```text
POST /documents/{id}/analyze
GET /tasks/{id}
GET /documents/{id}/tasks
```

Notes:

```text
GET /documents/{id}/notes
POST /documents/{id}/notes
PUT /notes/{id}
GET /notes/{id}
```

Quiz:

```text
GET /documents/{id}/quiz
POST /documents/{id}/quiz/generate
```

Chunks / Sources:

```text
GET /documents/{id}/chunks
GET /chunks/{id}
```

Semantic Search:

```text
POST /documents/{id}/search
POST /documents/{id}/ask
```

Export:

```text
POST /notes/{id}/export/markdown
POST /notes/{id}/export/pdf
```

## 13. 10 周开发路线图

### Week 1: 项目设计与基础搭建

目标：

1. 确定产品范围
2. 搭建 Next.js 前端
3. 搭建 Spring Boot 后端
4. 搭建 PostgreSQL
5. 搭建 Docker Compose
6. 设计数据库 schema

产出：

1. 基础项目结构
2. 前后端能跑通
3. 数据库能连接
4. README 初稿

### Week 2: 用户系统与文档上传

目标：

1. 实现登录
2. 实现 PDF 上传
3. 保存文档 metadata
4. 文件存储到本地或 object storage
5. Dashboard 显示文档列表

产出：

1. 用户可以上传 PDF
2. 用户可以看到文档记录
3. 后端有 documents API

### Week 3: 异步任务系统

目标：

1. 设计 tasks 表
2. 后端创建 analysis task
3. Redis queue 接入
4. Python worker 拉取任务
5. 前端显示任务状态

产出：

1. 上传 PDF 后自动创建任务
2. Worker 可以处理任务
3. 前端可以看到任务状态

### Week 4: PDF 解析与 chunking

目标：

1. Worker 下载 PDF
2. 使用 PyMuPDF / pdfplumber 解析文本
3. 清洗文本
4. 按页码和段落切 chunk
5. 存入 document_chunks 表

产出：

1. PDF 可以被解析成文本块
2. 每个 chunk 有 page number
3. 后端可以查询 chunks

### Week 5: Embedding 与 RAG

目标：

1. 接入 embedding model
2. 安装 pgvector
3. 存储 chunk embeddings
4. 实现 similarity search
5. 为生成笔记准备相关 chunks

产出：

1. 支持语义检索
2. 用户可以用一句话搜索 PDF 中的相关内容
3. 用户问题或生成任务可以检索相关原文
4. RAG pipeline 初步跑通

### Week 6: AI 笔记和题库生成

目标：

1. 设计 structured prompt
2. 生成课程笔记
3. 生成题库
4. 保存 notes 和 quiz
5. 绑定 source chunks

产出：

1. 用户上传 PDF 后能生成结构化笔记
2. 能生成题库
3. 每条内容可以显示来源

### Week 7: Notion-style 编辑器

目标：

1. 集成 Tiptap
2. 加入基础富文本功能
3. 支持 inline math
4. 支持 block math
5. 加入 KaTeX 渲染
6. 实现 autosave

产出：

1. 用户可以编辑 AI 生成笔记
2. 用户可以插入数学公式
3. 内容可以保存到数据库

### Week 8: 导出与 UI 优化

目标：

1. Markdown export
2. Notion-friendly Markdown export
3. 页面 UI 优化
4. 加入 loading / error state
5. 优化 dashboard
6. 完善 README

产出：

1. 用户可以导出笔记
2. 项目可以完整演示
3. README 有截图和架构说明

### Week 9: 部署与测试

目标：

1. 部署前端
2. 部署后端
3. 部署 worker
4. 部署数据库
5. 测试完整流程
6. 修复 bug

产出：

1. 在线 demo
2. 可访问项目链接
3. Demo video 可选

### Week 10: 简历化与展示优化

目标：

1. 写技术博客
2. 优化 README
3. 添加架构图
4. 添加简历 bullet points
5. 录制 1 到 2 分钟 demo
6. 准备面试讲解稿

产出：

1. Portfolio-ready 项目
2. Resume-ready bullet points
3. Interview-ready explanation

## 14. 部署方案

推荐交付路线：

```text
Web App MVP
  -> Hosted Online Demo
  -> Electron Desktop App
```

第一阶段先把 Web App 做完整，因为浏览器版本最容易开发、测试、分享和面试展示。第二阶段部署在线 demo，让别人可以直接访问。第三阶段再用 Electron 包成桌面软件，复用同一套 Next.js / TypeScript 前端。

MVP 部署：

1. Frontend: Vercel
2. Backend: Railway / Render / Fly.io
3. Database: Supabase / Neon PostgreSQL
4. Redis: Upstash Redis
5. Storage: Cloudflare R2 / Supabase Storage
6. Worker: Railway / Render background service

Electron 桌面版部署：

1. Desktop Shell: Electron
2. UI: 复用 Next.js 前端构建产物
3. Backend: 连接云端 Spring Boot API
4. AI Pipeline: 继续使用云端 Worker、Redis、PostgreSQL 和对象存储
5. Distribution: GitHub Releases 或项目官网下载

桌面版第一阶段只做 Cloud Mode：

```text
Electron Desktop App
  -> 加载本地打包后的 Web UI
  -> 调用云端 API
  -> 云端处理 PDF、embedding、RAG、AI 生成和导出
```

Local Mode 后期可选。第一版不建议在用户电脑上管理 Java、Python、PostgreSQL、Redis、模型 API key 和后台进程。

进阶部署：

1. Frontend: Vercel
2. Backend: AWS ECS
3. Worker: AWS ECS
4. Database: AWS RDS PostgreSQL
5. Redis: AWS ElastiCache
6. Storage: AWS S3
7. Monitoring: CloudWatch / Prometheus / Grafana

## 15. 项目难点与策略

### 15.1 编辑器复杂度

难点：

1. 光标行为
2. 公式插入和编辑
3. Markdown 转换
4. JSON editor state 存储
5. 自动保存

策略：

1. 使用 Tiptap，不从零写 editor。
2. MVP 只支持必要 block。
3. 暂不做实时协作。

### 15.2 PDF 解析复杂度

难点：

1. 多栏论文
2. 页眉页脚
3. 数学公式
4. 表格
5. 扫描版手写笔记

策略：

1. MVP 优先支持文字型 PDF。
2. 手写 OCR 后期再做。
3. 复杂公式先保留原文附近文本。
4. 表格和图片先不做深度解析。

### 15.3 AI 结果可靠性

难点：

1. 漏掉重点
2. 生成错误公式
3. 编造内容
4. 题目质量不稳定

策略：

1. 使用 RAG。
2. 每条内容绑定 source chunk。
3. 允许用户查看原文依据。
4. 使用强制 JSON schema。
5. 后期加入 evaluation。

### 15.4 系统复杂度

难点：

1. 前端
2. Java 后端
3. Python worker
4. Redis
5. PostgreSQL
6. pgvector
7. Object storage
8. LLM API

策略：

1. 分阶段实现。
2. 先用 Docker Compose 跑通本地闭环。
3. 再部署云端版本。
4. 每周只做一个核心模块。

## 16. 项目亮点

1. 完整 full-stack 软件
2. Java Spring Boot 后端
3. Python AI Worker
4. Redis 异步任务队列
5. PostgreSQL + pgvector RAG
6. Citation-grounded generation
7. Notion-style LaTeX 编辑器
8. Markdown / PDF 导出
9. 真实学生学习场景
10. 可以上线给别人使用

## 17. 简历 Bullet Points 初稿

1. Built a full-stack AI study workspace that transforms technical PDFs into citation-grounded notes, quizzes, and review checklists using an asynchronous RAG pipeline.
2. Designed a Java Spring Boot backend with PostgreSQL, Redis queues, and object storage to support PDF upload, task orchestration, status tracking, retries, and result retrieval.
3. Implemented Python AI workers for PDF parsing, section-aware chunking, embedding generation, semantic retrieval with pgvector, and structured LLM generation.
4. Built a Notion-style rich-text editor with Tiptap and KaTeX, supporting inline and block LaTeX formulas, autosave, JSON-based document storage, and Markdown export.
5. Added source-grounded note and quiz generation with page-level citations, allowing users to verify AI-generated study materials against the original PDF content.

## 18. 优先级

最高优先级：

1. PDF 上传
2. 异步任务
3. Chunk embedding 和 pgvector
4. 自然语言语义搜索
5. AI 笔记生成
6. 编辑器
7. LaTeX 公式
8. Markdown 导出

第二优先级：

1. RAG 问答
2. Citation grounding
3. 题库生成
4. 页面 UI 优化

第三优先级：

1. PDF export
2. Notion API export
3. WebSocket 实时进度
4. 复习计划
5. 错题本
6. 多人协作

## 19. MVP 验收标准

第一版完成时，用户应该可以：

1. 打开网站并登录。
2. 上传一份文字型 PDF。
3. 看到异步处理进度。
4. 系统解析 PDF 并生成 chunks。
5. 系统为 chunks 生成 embeddings 并存入 pgvector。
6. 用户可以用一句话搜索 PDF / 笔记中的相关内容。
7. 用户可以基于搜索到的 sources 向 AI 提问。
8. 等待系统生成结构化笔记。
9. 查看题库和答案解析。
10. 在笔记中看到至少页码级 citation。
11. 进入编辑器修改 AI 生成内容。
12. 插入 inline 和 block LaTeX 公式。
13. 自动保存编辑内容。
14. 导出 Markdown。

## 20. 最终建议

这个项目值得作为 8 到 10 周的主项目来做，但必须控制范围。第一版最重要的是跑通完整闭环：

**PDF 上传 -> 解析 chunks -> 生成 embeddings -> 用户一句话搜索 -> AI 生成笔记 -> 编辑器修改 -> 插入公式 -> 导出 Markdown**

闭环完成之后，再逐步强化 RAG 问答、citation、题库质量、worker retry、PDF export 和部署稳定性。

这个项目比普通 AI PDF summarizer 更强，因为它有完整产品体验；比普通 full-stack CRUD 项目更强，因为它包含 AI pipeline、RAG、异步任务和数学编辑器；比纯后端项目更适合作品集，因为用户真的可以打开网页使用。

推荐简历定位：

**Backend / AI Infrastructure-oriented Software Engineer**
