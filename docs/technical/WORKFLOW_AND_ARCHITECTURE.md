# NoteFlow 工作流与架构说明

这份文档说明 NoteFlow 的完整工作流、每一步要做什么、系统架构是什么，以及前端、后端、Worker、数据库、Redis、对象存储和 LLM API 之间如何连接。

## 1. 总体目标

NoteFlow 的核心闭环是：

```text
用户上传 PDF
  -> 系统保存文件和文档记录
  -> 创建异步分析任务
  -> Worker 解析 PDF
  -> Worker 切分 chunk 并生成 embedding
  -> Worker 调用 LLM 生成笔记和题库
  -> 后端保存结果
  -> 用户用一句话搜索 PDF / 笔记中的相关内容
  -> 用户基于搜索结果向 AI 提问
  -> 用户在编辑器中修改
  -> 用户导出 Markdown / PDF
```

第一版最重要的是跑通这个闭环。其中 embedding 和自然语言搜索属于核心能力：上传时建立文档语义索引，搜索时把用户的一句话转成 query embedding，再用 pgvector 找到相关 PDF 原文和笔记内容。

## 2. 系统架构

### 2.1 服务组成

```text
Frontend
  Next.js + TypeScript + Tiptap + KaTeX

Desktop Shell
  Electron + TypeScript
  Reuses the Web App UI after the hosted demo is working

Backend API
  Java 21 + Spring Boot + Spring Security + Spring Data JPA

Database
  PostgreSQL + pgvector

Queue / Cache
  Redis

AI Worker
  Python + PyMuPDF/pdfplumber + LLM API + embedding model

Object Storage
  Local storage for MVP
  Cloudflare R2 / AWS S3 / Supabase Storage for production
```

### 2.2 架构图

```text
                               +------------------+
                               |     LLM API      |
                               | OpenAI/Gemini/...|
                               +---------^--------+
                                         |
                                         |
+--------+      HTTPS       +-----------+----------+
|  User  +----------------->|   Next.js Frontend   |
+--------+                  +-----------+----------+
                                         |
                                         | REST API
                                         v
                              +----------+-----------+
                              | Spring Boot Backend  |
                              +----+-----------+-----+
                                   |           |
                         SQL/JPA   |           | Redis commands
                                   v           v
                         +---------+--+     +--+---------+
                         | PostgreSQL |     |   Redis    |
                         | + pgvector |     |   Queue    |
                         +-----+------+     +--+---------+
                               ^               |
                               |               | task pop / status update
                               |               v
                         +-----+---------------+-----+
                         |      Python AI Worker     |
                         +-----+---------------+-----+
                               |
                               | read/write files
                               v
                         +-----+---------------+
                         |   Object Storage    |
                         +---------------------+
```

Desktop App 不是自研浏览器内核，而是 Electron shell：

```text
User
  -> Electron Desktop App
  -> bundled Next.js UI
  -> Cloud Spring Boot Backend
  -> Cloud Worker / PostgreSQL / Redis / Object Storage
```

第一版桌面端使用 Cloud Mode。Local Mode 后期再考虑。

## 3. 服务职责

### 3.1 Frontend

前端负责用户能看见和操作的部分。

主要职责：

1. 登录和会话状态
2. Dashboard 文档列表
3. PDF 上传表单
4. 任务进度展示
5. 文档详情页
6. 笔记编辑器
7. 题库页面
8. 自然语言搜索框
9. RAG 问答入口
10. Citation / source snippet 展示
11. Markdown 导出按钮

前端只调用 Backend API，不直接操作数据库、Redis 或 Worker。

### 3.1.1 Desktop Shell

桌面端负责把 Web App 包装成可安装软件。

主要职责：

1. 创建桌面窗口。
2. 加载打包后的 Next.js UI。
3. 提供菜单、文件选择器和下载位置选择。
4. 调用同一套云端 Backend API。
5. 后期支持自动更新。

第一版桌面端不负责：

1. 自带 Java 后端。
2. 自带 Python Worker。
3. 自带 PostgreSQL。
4. 自带 Redis。
5. 自研浏览器内核。

推荐顺序：

```text
先做 Web App
  -> 部署在线 demo
  -> 再做 Electron Desktop App
```

### 3.2 Backend API

后端是系统控制中心。

主要职责：

1. 认证和权限校验
2. 接收上传请求
3. 保存文件到对象存储
4. 创建 documents 记录
5. 创建 tasks 记录
6. 把任务推入 Redis queue
7. 提供任务状态查询 API
8. 提供 notes、quiz、chunks 查询 API
9. 提供自然语言语义搜索 API
10. 提供 RAG 问答 API
11. 保存编辑器内容
12. 执行 Markdown / PDF 导出

后端不直接做耗时 AI 分析。耗时任务交给 Worker。

### 3.3 AI Worker

Worker 专门处理耗时任务。

主要职责：

1. 从 Redis queue 拉取任务
2. 根据 task id 查询 document 信息
3. 从对象存储下载 PDF
4. 解析 PDF 文本
5. 清洗文本
6. 切分 chunks
7. 生成 embeddings
8. 写入 document_chunks 表
9. 为用户 query 生成 query embedding
10. 检索相关 chunks
11. 调用 LLM 生成结构化笔记
12. 调用 LLM 生成题库
13. 调用 LLM 生成基于 sources 的问答
14. 保存 notes 和 quiz_questions
15. 更新 task 状态为 COMPLETED 或 FAILED

Worker 不负责用户权限和页面展示。

### 3.4 PostgreSQL + pgvector

数据库负责持久化核心业务数据。

存储内容：

1. 用户信息
2. 文档 metadata
3. 任务状态
4. PDF chunks
5. embeddings
6. semantic search results 的 source chunks
7. 笔记 JSON
8. 笔记 Markdown
9. 题库
10. citation 来源
11. export 记录

pgvector 用来做语义相似度检索。

### 3.5 Redis

Redis 第一版主要用作任务队列。

用途：

1. 存放待处理任务 id
2. Worker 拉取任务
3. 缓存任务进度
4. 防止重复提交
5. 后续支持限流

### 3.6 Object Storage

对象存储保存大文件。

保存内容：

1. 原始 PDF
2. 导出的 Markdown 文件
3. 导出的 PDF 文件
4. 后续可能的图片资源

MVP 可以先用本地文件夹，例如 `storage/uploads`。部署时再换成 R2、S3 或 Supabase Storage。

## 4. 数据如何连接

### 4.1 前端连接后端

前端通过 REST API 连接后端。

示例：

```text
Frontend -> Backend

POST /documents
GET /documents
GET /tasks/{id}
GET /documents/{id}/notes
PUT /notes/{id}
POST /notes/{id}/export/markdown
```

前端请求需要带登录凭证：

```text
Authorization: Bearer <access_token>
```

### 4.2 后端连接数据库

Spring Boot 使用 Spring Data JPA 连接 PostgreSQL。

环境变量示例：

```env
SPRING_DATASOURCE_URL=jdbc:postgresql://localhost:5432/noteflow
SPRING_DATASOURCE_USERNAME=noteflow
SPRING_DATASOURCE_PASSWORD=noteflow
```

后端通过 repository/service 写入：

1. documents
2. tasks
3. notes
4. quiz_questions
5. exports

### 4.3 后端连接 Redis

后端在创建任务后，把任务 id 推入 Redis。

示例队列：

```text
queue:document-analysis
```

入队内容：

```json
{
  "taskId": "task_123",
  "documentId": "doc_456",
  "userId": "user_789",
  "taskType": "ANALYZE_DOCUMENT"
}
```

### 4.4 Worker 连接 Redis

Worker 从 Redis 队列中拉取任务。

流程：

```text
BRPOP queue:document-analysis
  -> parse task payload
  -> mark task PROCESSING
  -> run pipeline
  -> mark task COMPLETED or FAILED
```

### 4.5 Worker 连接数据库

Worker 需要读写 PostgreSQL。

读取：

1. task
2. document
3. file_url

写入：

1. document_chunks
2. notes
3. quiz_questions
4. task status
5. error_message

### 4.6 Worker 连接对象存储

Worker 根据 document.file_url 下载 PDF。

MVP 本地存储：

```text
storage/uploads/{document_id}.pdf
```

生产对象存储：

```text
s3://noteflow/uploads/{document_id}.pdf
```

### 4.7 Worker 连接 LLM API

Worker 调用模型完成两类任务：

1. Embedding: 把 chunk 转成向量
2. Query embedding: 把用户的一句话搜索请求转成向量
3. Generation: 生成笔记、题库、总结、checklist 和基于来源的回答

所有 LLM 输出都应该尽量使用 JSON schema，方便后端稳定保存。

## 4.8 搜索和问答的连接方式

语义搜索和 RAG 问答共享同一个检索基础，但它们是两个不同产品能力。

Semantic Search:

```text
用户输入一句话
  -> generate query embedding
  -> pgvector similarity search
  -> return matching chunks
  -> 前端展示原文片段、页码、章节和相似度
```

RAG Answer:

```text
用户输入问题
  -> generate query embedding
  -> pgvector similarity search
  -> send top-k chunks + question to LLM
  -> return answer with citations
```

搜索不一定调用 LLM，它的目标是定位资料；问答会调用 LLM，它的目标是基于资料解释。

## 5. 完整用户工作流

### Step 1: 用户登录

用户操作：

1. 打开网站
2. 点击登录
3. 使用邮箱、Google 或第三方 auth 登录

前端做什么：

1. 调用认证服务
2. 保存 session
3. 拿到 access token
4. 跳转 dashboard

后端做什么：

1. 校验 token
2. 创建或更新 user record
3. 返回当前用户信息

相关 API：

```text
GET /auth/me
```

相关表：

```text
users
```

### Step 2: 用户上传 PDF

用户操作：

1. 进入 Upload Page
2. 拖拽或选择 PDF
3. 选择文档类型
4. 选择输出语言
5. 勾选生成 Notes / Quiz / Checklist
6. 点击 Upload

前端做什么：

1. 校验文件类型是 PDF
2. 校验文件大小
3. 创建 multipart/form-data 请求
4. 调用 `POST /documents`
5. 跳转 task progress 页面

后端做什么：

1. 校验用户身份
2. 接收 PDF
3. 保存文件到 storage
4. 创建 documents 记录
5. 创建 tasks 记录
6. 推送任务到 Redis queue
7. 返回 document id 和 task id

相关 API：

```text
POST /documents
```

相关表：

```text
documents
tasks
```

相关 Redis queue：

```text
queue:document-analysis
```

### Step 3: 前端展示任务进度

用户操作：

1. 上传后看到进度页
2. 等待处理
3. 看到当前步骤和百分比

前端做什么：

1. 轮询 `GET /tasks/{id}`
2. 展示 task.status
3. 展示 task.progress
4. COMPLETED 后跳转 Document Detail Page
5. FAILED 时展示错误和 retry 按钮

后端做什么：

1. 查询 tasks 表
2. 返回任务状态

相关 API：

```text
GET /tasks/{id}
```

相关状态：

```text
PENDING
PROCESSING
COMPLETED
FAILED
RETRYING
CANCELLED
```

### Step 4: Worker 解析 PDF

触发方式：

1. 后端已经把任务推入 Redis
2. Worker 从 Redis 拉取任务

Worker 做什么：

1. 读取 task payload
2. 更新 task.status = PROCESSING
3. 查询 document.file_url
4. 下载 PDF
5. 使用 PyMuPDF 或 pdfplumber 解析文本
6. 提取页码
7. 清理页眉、页脚、重复空格
8. 保存中间结果或直接进入 chunking

相关表：

```text
tasks
documents
```

失败处理：

1. 解析失败则写入 error_message
2. retry_count + 1
3. 未超过重试次数则重新入队
4. 超过次数则标记 FAILED

### Step 5: Worker 切分 chunks

Worker 做什么：

1. 按 page 切分文本
2. 尝试识别 section title
3. 按段落或 token 长度切 chunk
4. 为每个 chunk 保留 page_number
5. 保存到 document_chunks 表

chunk 需要包含：

```text
document_id
page_number
section_title
chunk_index
content
```

相关表：

```text
document_chunks
```

### Step 6: Worker 生成 embeddings

Worker 做什么：

1. 遍历 document_chunks
2. 调用 embedding model
3. 得到 vector
4. 保存到 document_chunks.embedding
5. 标记 document 的 semantic index 已建立

相关技术：

```text
pgvector
```

查询示例概念：

```sql
SELECT *
FROM document_chunks
WHERE document_id = :documentId
ORDER BY embedding <-> :queryEmbedding
LIMIT 8;
```

### Step 7: Worker 执行 RAG 检索

Worker 做什么：

1. 根据文档类型构造 generation query
2. 对 query 生成 embedding
3. 从 pgvector 检索相关 chunks
4. 把 chunks 作为 source context
5. 传给 LLM

目的：

1. 降低 hallucination
2. 提高内容相关性
3. 为 citation grounding 做准备
4. 为用户自然语言搜索和问答复用同一套语义索引

### Step 8: Worker 生成结构化笔记

Worker 做什么：

1. 根据文档类型选择 prompt
2. 把 source chunks 发给 LLM
3. 要求 LLM 输出 JSON
4. 校验 JSON schema
5. 转成 Tiptap content_json
6. 生成 content_markdown
7. 保存 notes 表

笔记结构：

```text
Topic Overview
Key Definitions
Key Formulas
Important Theorems
Worked Examples
Common Mistakes
Practice Questions
Review Checklist
```

相关表：

```text
notes
note_blocks
document_chunks
```

### Step 9: Worker 生成题库

Worker 做什么：

1. 基于 chunks 和笔记生成题目
2. 要求题目输出 JSON
3. 每题保存 question、answer、explanation、difficulty、source_page
4. 写入 quiz_questions 表

题目类型：

```text
Conceptual
Calculation
Proof
Multiple Choice
Short Answer
True / False
```

相关表：

```text
quizzes
quiz_questions
document_chunks
```

### Step 10: Worker 完成任务

Worker 做什么：

1. 所有结果保存成功
2. 更新 task.progress = 100
3. 更新 task.status = COMPLETED
4. 写入 completed_at

前端下一次轮询时看到 COMPLETED，就跳转文档详情页。

### Step 11: 用户查看文档详情

用户操作：

1. 打开 Document Detail Page
2. 查看 Overview
3. 查看 Notes
4. 查看 Quiz
5. 查看 Sources
6. 点击 citation 查看原文片段

前端做什么：

1. 调用 document API
2. 调用 notes API
3. 调用 quiz API
4. 调用 chunks API
5. 渲染 tabs

相关 API：

```text
GET /documents/{id}
GET /documents/{id}/notes
GET /documents/{id}/quiz
GET /documents/{id}/chunks
```

### Step 12: 用户用一句话搜索 PDF / 笔记内容

用户操作：

1. 在文档详情页或右侧 assistant 中输入一句自然语言。
2. 例如：`为什么 variance 可以写成 E[X^2] - E[X]^2？`
3. 点击 Search in document。
4. 查看相关页码、章节和 source snippet。

前端做什么：

1. 调用 `POST /documents/{id}/search`。
2. 传入 query 和 limit。
3. 渲染匹配到的 chunks。
4. 显示 page number、section title、snippet 和 score。
5. 用户点击结果时打开对应 source panel。

后端做什么：

1. 校验用户是否有权限访问 document。
2. 为 query 生成 query embedding，或请求 Worker/AI service 生成。
3. 使用 pgvector 在 document_chunks 中查找 top-k 相似 chunks。
4. 返回搜索结果。

请求示例：

```json
{
  "query": "explain the variance shortcut formula",
  "limit": 8
}
```

响应示例：

```json
{
  "results": [
    {
      "chunkId": "chunk_123",
      "pageNumber": 4,
      "sectionTitle": "Variance",
      "snippet": "The variance can be computed using E[X^2] - E[X]^2...",
      "score": 0.83
    }
  ]
}
```

相关 API：

```text
POST /documents/{id}/search
```

相关表：

```text
document_chunks
```

### Step 13: 用户基于来源向 AI 提问

用户操作：

1. 在 assistant 中输入问题。
2. 点击 Ask with sources。
3. 查看 AI 回答。
4. 点击回答中的 citation 查看原文。

前端做什么：

1. 调用 `POST /documents/{id}/ask`。
2. 展示 answer。
3. 展示 citations。
4. 允许用户把回答插入编辑器。

后端或 Worker 做什么：

1. 校验用户权限。
2. 为 question 生成 query embedding。
3. 从 pgvector 检索 top-k source chunks。
4. 把 question 和 source chunks 发给 LLM。
5. 要求 LLM 只基于 sources 回答。
6. 返回 answer 和 citations。

请求示例：

```json
{
  "question": "为什么 variance 等于 E[X^2] - E[X]^2？",
  "limit": 8
}
```

响应示例：

```json
{
  "answer": "Variance is defined as E[(X - E[X])^2]. Expanding the square gives E[X^2] - E[X]^2...",
  "citations": [
    {
      "chunkId": "chunk_123",
      "pageNumber": 4,
      "snippet": "..."
    }
  ]
}
```

相关 API：

```text
POST /documents/{id}/ask
```

相关表：

```text
document_chunks
```

### Step 14: 用户进入编辑器

用户操作：

1. 点击 Edit Notes
2. 修改 AI 生成内容
3. 插入标题、列表、引用、代码块
4. 插入 inline math
5. 插入 block math

前端做什么：

1. 加载 note.content_json
2. 初始化 Tiptap editor
3. 用 KaTeX 渲染公式
4. 用户编辑时更新本地 editor state
5. 每 5 到 10 秒 autosave

后端做什么：

1. 接收 `PUT /notes/{id}`
2. 校验 note 属于当前用户
3. 保存 content_json
4. 同步保存 content_markdown
5. 更新 updated_at

相关 API：

```text
GET /notes/{id}
PUT /notes/{id}
```

相关表：

```text
notes
```

### Step 15: 用户导出 Markdown

用户操作：

1. 点击 Export
2. 选择 Markdown
3. 下载或复制 Markdown

前端做什么：

1. 调用 export API
2. 显示下载按钮或复制结果

后端做什么：

1. 读取 note.content_json 或 content_markdown
2. 转换成 Markdown
3. 保留标题、列表、代码块、LaTeX、citation
4. 返回 Markdown 文本或文件 URL
5. 创建 export record

相关 API：

```text
POST /notes/{id}/export/markdown
```

相关表：

```text
exports
```

## 6. 后端内部分层

Spring Boot 后端建议分层：

```text
controller
  接收 HTTP 请求，处理参数和响应

service
  业务逻辑，例如创建文档、创建任务、保存笔记

repository
  数据库访问

entity
  JPA 实体

dto
  请求和响应对象

security
  认证和权限

storage
  文件存储接口

queue
  Redis 队列接口

export
  Markdown / PDF 导出逻辑
```

示例模块：

```text
com.noteflow.auth
com.noteflow.documents
com.noteflow.tasks
com.noteflow.notes
com.noteflow.quiz
com.noteflow.chunks
com.noteflow.storage
com.noteflow.queue
com.noteflow.export
```

## 7. Worker 内部分层

Python Worker 建议分层：

```text
worker/
  main.py
  config.py
  queue/
    redis_client.py
    task_consumer.py
  db/
    postgres.py
    repositories.py
  storage/
    local_storage.py
    s3_storage.py
  pdf/
    parser.py
    cleaner.py
    chunker.py
  ai/
    embeddings.py
    prompts.py
    generator.py
    schemas.py
  pipelines/
    analyze_document.py
```

核心 pipeline：

```text
consume task
  -> load document
  -> download pdf
  -> parse pdf
  -> clean text
  -> chunk text
  -> store chunks
  -> generate embeddings
  -> retrieve context
  -> generate notes
  -> generate quiz
  -> save results
  -> complete task
```

## 8. 前端页面和 API 对应关系

```text
/login
  GET /auth/me

/dashboard
  GET /documents
  GET /tasks/recent

/upload
  POST /documents

/tasks/[taskId]
  GET /tasks/{id}

/documents/[documentId]
  GET /documents/{id}
  GET /documents/{id}/notes
  GET /documents/{id}/quiz
  POST /documents/{id}/search
  POST /documents/{id}/ask

/documents/[documentId]/editor
  GET /notes/{id}
  PUT /notes/{id}

/documents/[documentId]/sources
  GET /documents/{id}/chunks

/documents/[documentId]/export
  POST /notes/{id}/export/markdown
```

## 9. 本地开发连接方式

### 9.1 本地服务端口建议

```text
Frontend:        http://localhost:3000
Backend API:     http://localhost:8080
PostgreSQL:      localhost:5432
Redis:           localhost:6379
Worker:          background process
Object Storage:  ./storage
```

Electron Cloud Mode 开发时：

```text
Electron App:    local desktop shell
Web UI:          bundled Next.js build
Backend API:     deployed cloud API or http://localhost:8080
AI Worker:       cloud worker or local worker
Database/Redis:  cloud services or local Docker services
```

### 9.2 Docker Compose 目标

第一版 Docker Compose 应启动：

1. postgres
2. redis
3. backend
4. worker
5. frontend 可选

Electron 不需要放进第一版 Docker Compose。桌面端在 Web App 和在线 demo 稳定后单独开发。

本地开发时也可以：

1. Docker 跑 postgres + redis
2. 本机跑 frontend
3. 本机跑 backend
4. 本机跑 worker

### 9.3 环境变量

Frontend:

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8080
```

Backend:

```env
SPRING_DATASOURCE_URL=jdbc:postgresql://localhost:5432/noteflow
SPRING_DATASOURCE_USERNAME=noteflow
SPRING_DATASOURCE_PASSWORD=noteflow
REDIS_URL=redis://localhost:6379
STORAGE_TYPE=local
STORAGE_LOCAL_DIR=./storage
```

Worker:

```env
DATABASE_URL=postgresql://noteflow:noteflow@localhost:5432/noteflow
REDIS_URL=redis://localhost:6379
STORAGE_TYPE=local
STORAGE_LOCAL_DIR=./storage
LLM_API_KEY=replace_me
EMBEDDING_MODEL=replace_me
```

## 10. MVP 实现顺序

### Phase 1: 项目骨架

要做：

1. 创建 frontend、backend、worker 目录
2. 配置 Docker Compose
3. 启动 PostgreSQL 和 Redis
4. 后端连接数据库
5. 前端能调用后端 health check

完成标准：

1. `GET /health` 返回 OK
2. 前端页面能显示 API connected
3. 数据库 migration 能运行

### Phase 2: 文档上传

要做：

1. 前端上传 PDF
2. 后端接收 multipart file
3. 保存到 local storage
4. 写 documents 表
5. 返回 document id

完成标准：

1. 用户可以上传 PDF
2. Dashboard 能看到文档
3. storage 目录出现 PDF 文件

### Phase 3: 任务系统

要做：

1. 创建 tasks 表
2. 上传后创建 analysis task
3. 后端推送 Redis queue
4. Worker 能消费 task
5. Worker 更新 task status

完成标准：

1. 上传 PDF 后出现 PENDING task
2. Worker 消费后状态变 PROCESSING
3. Worker 结束后状态变 COMPLETED

### Phase 4: PDF 解析和 chunks

要做：

1. Worker 下载 PDF
2. 解析文本
3. 按页切分
4. 写 document_chunks 表
5. 后端提供 chunks 查询 API

完成标准：

1. Sources 页面能看到 page number 和 chunk text
2. 一个 PDF 至少能解析出多个 chunks

### Phase 5: Embedding 与 pgvector 语义索引

要做：

1. 接入 embedding model
2. 启用 pgvector
3. 为 chunks 生成 embeddings
4. 存储到 document_chunks.embedding
5. 实现 similarity search

完成标准：

1. 上传 PDF 后每个 chunk 都有 embedding
2. 后端可以按 query embedding 搜索相关 chunks
3. Sources 页面可以按语义相关性返回结果

### Phase 6: 自然语言搜索和 RAG 问答

要做：

1. 实现 `POST /documents/{id}/search`
2. 用户 query 生成 query embedding
3. pgvector 搜索 top-k chunks
4. 前端展示 source snippets
5. 实现 `POST /documents/{id}/ask`
6. 将 top-k chunks 和问题传给 LLM
7. 返回 answer with citations

完成标准：

1. 用户可以用一句话搜索 PDF 中的相关内容
2. 搜索结果包含页码、章节、片段和相似度
3. 用户可以基于文档提问
4. AI 回答带 citations

### Phase 7: AI 笔记生成

要做：

1. Worker 调 LLM
2. 生成结构化 JSON
3. 转换成 note content_json
4. 保存 notes 表
5. 前端展示笔记
6. 笔记重点绑定 source chunks

完成标准：

1. 上传 PDF 后能生成一份结构化笔记
2. Notes 页面能正确展示标题、段落、列表
3. 笔记中的重点内容可以追溯到 source chunks

### Phase 8: 编辑器和 LaTeX

要做：

1. 集成 Tiptap
2. 加基础 rich text extension
3. 加 inline math
4. 加 block math
5. 加 KaTeX 渲染
6. 加 autosave

完成标准：

1. 用户可以编辑 AI 笔记
2. 用户可以插入 inline formula
3. 用户可以插入 block formula
4. 刷新后内容仍然存在

### Phase 9: Markdown 导出

要做：

1. content_json 转 Markdown
2. 保留 LaTeX
3. 保留 citation
4. 提供下载或 copy

完成标准：

1. 用户能导出 Markdown
2. 导出内容可导入 Notion 或 Markdown 编辑器

### Phase 10: Citation 和产品体验增强

要做：

1. 优化 citation UI
2. 每条 note item 绑定 source_chunk_id
3. 前端点击 citation 显示 source snippet
4. 优化 search result ranking
5. 优化 answer prompt 和 citation 格式

完成标准：

1. 笔记重点能看到页码来源
2. 点击 citation 能看到原文片段
3. 搜索和问答体验稳定可演示

### Phase 11: 在线 demo 部署

要做：

1. 部署 Next.js 前端。
2. 部署 Spring Boot backend。
3. 部署 Python worker。
4. 部署 PostgreSQL + pgvector。
5. 部署 Redis。
6. 配置对象存储。
7. 配置环境变量和 CORS。
8. 测试完整线上流程。

完成标准：

1. 用户可以通过 URL 打开项目。
2. 上传、embedding、搜索、问答、笔记生成、编辑和导出都能在线完成。
3. README 有 demo 链接和运行说明。

### Phase 12: Electron 桌面版

要做：

1. 创建 Electron app。
2. 复用 Web App 的构建产物。
3. 配置桌面窗口、菜单和基础应用信息。
4. 让桌面端连接云端 Backend API。
5. 支持本地文件选择和导出保存。
6. 打包 macOS / Windows 可安装版本。

完成标准：

1. 用户可以安装桌面版。
2. 桌面版 UI 与 Web App 功能一致。
3. 桌面版可以连接云端 API 完成上传、搜索、问答、编辑和导出。
4. 桌面版不要求用户本地安装 Java、Python、PostgreSQL 或 Redis。

## 11. 关键接口清单

### Auth

```text
GET /auth/me
```

### Documents

```text
POST /documents
GET /documents
GET /documents/{id}
DELETE /documents/{id}
```

### Tasks

```text
POST /documents/{id}/analyze
GET /tasks/{id}
GET /documents/{id}/tasks
POST /tasks/{id}/retry
```

### Notes

```text
GET /documents/{id}/notes
GET /notes/{id}
PUT /notes/{id}
```

### Quiz

```text
GET /documents/{id}/quiz
POST /documents/{id}/quiz/generate
```

### Sources

```text
GET /documents/{id}/chunks
GET /chunks/{id}
```

### Semantic Search And RAG

```text
POST /documents/{id}/search
POST /documents/{id}/ask
```

### Export

```text
POST /notes/{id}/export/markdown
POST /notes/{id}/export/pdf
```

## 12. 数据状态流转

### Document 状态

```text
UPLOADED
PROCESSING
READY
FAILED
DELETED
```

状态变化：

```text
UPLOADED -> PROCESSING -> READY
UPLOADED -> PROCESSING -> FAILED
READY -> DELETED
```

### Task 状态

```text
PENDING
PROCESSING
COMPLETED
FAILED
RETRYING
CANCELLED
```

状态变化：

```text
PENDING -> PROCESSING -> COMPLETED
PENDING -> PROCESSING -> FAILED
FAILED -> RETRYING -> PROCESSING
PENDING -> CANCELLED
PROCESSING -> CANCELLED
```

## 13. 错误处理

### 上传失败

原因：

1. 文件不是 PDF
2. 文件太大
3. storage 写入失败

处理：

1. 前端展示错误
2. 后端不创建 task
3. 如果 document 已创建但 storage 失败，需要标记 FAILED 或回滚

### Worker 失败

原因：

1. PDF 下载失败
2. PDF 解析失败
3. LLM API 超时
4. JSON schema 校验失败
5. 数据库写入失败

处理：

1. task.status = FAILED
2. 写入 error_message
3. retry_count + 1
4. 允许用户点击 retry

### AI 输出格式错误

处理：

1. 使用 JSON schema
2. 校验失败后自动 retry 一次
3. 仍失败则保存 raw output 到 debug log
4. task 标记 FAILED

## 14. 第一版最小闭环

如果时间有限，只做这条链路：

```text
PDF upload
  -> save document
  -> create task
  -> worker parse PDF
  -> chunk text
  -> generate chunk embeddings
  -> semantic search by user query
  -> worker generate notes
  -> save note
  -> editor edit note
  -> export Markdown
```

这一版可以暂时不做：

1. 高级 citation UI
2. quiz
3. PDF export
4. OCR
5. WebSocket
6. 复杂权限

最小闭环完成后，再逐步补强架构亮点。

## 15. 推荐开发检查清单

每完成一个阶段，检查：

1. 前端是否有页面入口
2. API 是否能独立测试
3. 数据库是否有对应记录
4. 错误状态是否能展示
5. 刷新页面后数据是否仍存在
6. 用户是否只能访问自己的数据
7. README 是否更新当前运行方式

## 16. 最终完成标准

项目达到 portfolio-ready 时，应满足：

1. 用户可以登录。
2. 用户可以上传 PDF。
3. 系统可以异步分析 PDF。
4. 用户可以看到任务进度。
5. 系统可以生成结构化笔记。
6. 系统可以为 chunks 生成 embeddings。
7. 用户可以用一句话搜索 PDF / 笔记中的相关内容。
8. 用户可以基于 sources 向 AI 提问。
9. 系统可以生成题库。
10. 笔记至少有页码级 citation。
11. 用户可以在 Tiptap 编辑器中修改笔记。
12. 用户可以插入 inline 和 block LaTeX。
13. 用户可以导出 Markdown。
14. 项目可以本地 Docker Compose 启动。
15. 项目有在线 demo。
16. 后续可以用 Electron 包成桌面软件。
17. README 有截图、架构图和技术说明。
