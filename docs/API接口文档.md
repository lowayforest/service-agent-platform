# 航道对外服务智能体 API 接口文档

> 接口版本：0.1.0
>
> 更新日期：2026-09-24
>
> 适用范围：当前知识库问答 Demo 与内网测试环境

## 1. 接口概览

### 1.1 基本信息

| 项目 | 说明 |
| --- | --- |
| 协议 | HTTP |
| 直接访问根地址 | `http://127.0.0.1:8001` |
| 实验室局域网根地址 | `http://10.98.196.218:8001` |
| 前端反向代理地址 | `http://服务器地址:11451/api/*` |
| 请求与响应格式 | `application/json; charset=utf-8` |
| Swagger 文档 | `http://服务器地址:8001/docs` |
| OpenAPI 描述 | `http://服务器地址:8001/openapi.json` |
| 身份认证 | 当前版本未启用，仅允许在受控内网中测试 |

下文统一以 `http://127.0.0.1:8001` 为示例。通过前端容器访问时，只需将主机和端口
替换为 `http://服务器地址:11451`，接口路径保持不变。

### 1.2 接口清单

| HTTP 方法 | 接口路径 | 用途 | 是否需要请求体 |
| --- | --- | --- | --- |
| `GET` | `/api/health` | 检查 RAG API、模型配置和知识库索引状态 | 否 |
| `POST` | `/api/chat` | 提交自然语言问题并返回答案和引用来源 | 是，JSON |

`GET` 用于读取状态，不提交 JSON 请求体；`POST` 用于提交问答数据，请求头必须声明
`Content-Type: application/json`。

## 2. 健康检查

### 2.1 接口定义

| 项目 | 内容 |
| --- | --- |
| 接口名称 | 健康检查 |
| HTTP 方法 | `GET` |
| 接口路径 | `/api/health` |
| 请求参数 | 无 |
| 请求体 | 无 |
| 成功状态码 | `200 OK` |

### 2.2 请求示例

```bash
curl --noproxy '*' -sS \
  http://127.0.0.1:8001/api/health | jq
```

### 2.3 成功响应示例

```json
{
  "status": "ok",
  "chat_backend": "openai",
  "chat_model": "qwen3.5",
  "embedding_backend": "openai",
  "embedding_model": "qwen3-embedding",
  "indexed_chunks": 33723
}
```

示例中的模型名称和索引条目数会随服务器配置及当前索引变化，不应在前端写死。

### 2.4 响应字段

| 字段 | 类型 | 必有 | 说明 |
| --- | --- | --- | --- |
| `status` | `string` | 是 | API 状态；正常时为 `ok` |
| `chat_backend` | `string` | 是 | 生成模型后端，例如 `openai` 或 `ollama` |
| `chat_model` | `string` | 是 | 当前生成模型名称 |
| `embedding_backend` | `string` | 是 | 向量模型后端，例如 `openai` 或 `ollama` |
| `embedding_model` | `string` | 是 | 当前向量模型名称 |
| `indexed_chunks` | `integer` | 是 | 当前已加载的知识片段数量；应大于 0 |

## 3. 知识库问答

### 3.1 接口定义

| 项目 | 内容 |
| --- | --- |
| 接口名称 | 知识库问答 |
| HTTP 方法 | `POST` |
| 接口路径 | `/api/chat` |
| 请求格式 | `application/json` |
| 响应格式 | `application/json` |
| 成功状态码 | `200 OK` |

### 3.2 请求头

| 请求头 | 必需 | 值 |
| --- | --- | --- |
| `Content-Type` | 是 | `application/json` |

当前版本没有登录鉴权，因此不需要 `Authorization` 请求头。正式部署前必须在反向代理或应用层
增加身份认证、访问控制和限流。

### 3.3 请求字段

| 字段 | 类型 | 必需 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `question` | `string` | 是 | 长度为 1～2000 个字符 | 用户提交的自然语言问题 |
| `top_k` | `integer` 或 `null` | 否 | 1～10 | 返回给生成模型的检索结果数量；省略时使用服务器 `RAG_TOP_K` 配置 |

### 3.4 请求 JSON 示例

```json
{
  "question": "内河航道公共服务信息发布指南的标准编号是什么？",
  "top_k": 4
}
```

`top_k` 可以省略：

```json
{
  "question": "内河航道公共服务信息发布指南的标准编号是什么？"
}
```

### 3.5 curl 请求示例

```bash
curl --noproxy '*' -sS \
  -X POST http://127.0.0.1:8001/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"内河航道公共服务信息发布指南的标准编号是什么？","top_k":4}' \
  | jq
```

### 3.6 正常响应 JSON 示例

```json
{
  "answer": "《内河航道公共服务信息发布指南》的标准编号是 JTS/T 321—2022 [S1]。",
  "sources": [
    {
      "id": "S1",
      "chunk_id": "7c5f9b8e4f83a921",
      "source": "材料二/技术标准等（第二部分）/内河航道公共服务信息发布指南JTS-T+321-2022.pdf",
      "locator": "第 1 页",
      "score": 0.7962,
      "excerpt": "现发布《内河航道公共服务信息发布指南》。该指南的标准代码为 JTS/T 321—2022。"
    }
  ],
  "blocked_realtime": false
}
```

### 3.7 响应字段

| 字段 | 类型 | 必有 | 说明 |
| --- | --- | --- | --- |
| `answer` | `string` | 是 | 模型依据检索证据生成的回答，或系统给出的拒答说明 |
| `sources` | `array` | 是 | 本次回答使用的引用来源；无有效证据或实时问题被拦截时为空数组 |
| `blocked_realtime` | `boolean` | 是 | 是否因当前未接入权威实时数据接口而被拦截 |

`sources` 数组中的对象结构：

| 字段 | 类型 | 必有 | 说明 |
| --- | --- | --- | --- |
| `id` | `string` | 是 | 引用编号，与回答中的 `[S1]`、`[S2]` 对应 |
| `chunk_id` | `string` | 是 | 知识片段唯一标识 |
| `source` | `string` | 是 | 原始资料或标准化资料的来源路径 |
| `locator` | `string` | 是 | PDF 页码、Markdown 章节或 Excel 工作表等定位信息 |
| `score` | `number` | 是 | 混合检索得分，仅用于同一次检索结果的相对比较，不代表正确率 |
| `excerpt` | `string` | 是 | 支撑回答的来源片段摘要 |

### 3.8 实时问题拦截响应示例

请求：

```json
{
  "question": "今天某航段的实时水深是多少？"
}
```

响应：

```json
{
  "answer": "当前版本尚未接入权威实时数据接口，因此无法确认实时水深、水位、气象、航道管制或事项进度。请以航道管理、海事等权威系统的最新发布为准；待接口接入后，回答还必须同时展示数据来源和更新时间。",
  "sources": [],
  "blocked_realtime": true
}
```

## 4. 错误响应

### 4.1 参数校验失败：422

当 `question` 缺失、为空、超过 2000 个字符，或 `top_k` 超出 1～10 时，FastAPI 返回
`422 Unprocessable Entity`。

```json
{
  "detail": [
    {
      "type": "string_too_short",
      "loc": [
        "body",
        "question"
      ],
      "msg": "String should have at least 1 character",
      "input": "",
      "ctx": {
        "min_length": 1
      }
    }
  ]
}
```

错误明细由 FastAPI/Pydantic 生成，具体英文文本可能随依赖版本变化；调用方应主要依据 HTTP
状态码和 `detail` 字段处理。

### 4.2 模型服务不可用：503

生成模型或向量模型无法连接、超时或返回异常时，接口返回 `503 Service Unavailable`。

```json
{
  "detail": "模型服务暂时不可用，请检查模型进程、地址、密钥和服务日志。"
}
```

### 4.3 服务内部错误：500

索引与向量模型配置不一致或服务内部出现数据错误时，接口可能返回
`500 Internal Server Error`。

```json
{
  "detail": "索引使用的向量模型与当前配置不一致，请重新构建索引或修改配置。"
}
```

错误示例用于说明响应结构，`detail` 的具体内容以服务实际返回为准。前端不得向普通用户直接
展示可能包含内部路径或配置细节的错误内容；生产环境应统一转换为安全错误信息并记录服务端日志。

## 5. 前端调用约定

React 前端统一请求同源相对路径：

```text
GET  /api/health
POST /api/chat
```

本地 Vite 开发服务器和 Docker 中的 Nginx 负责把 `/api/*` 转发到 FastAPI 的 `8001` 端口。
浏览器不需要直接拼接 `8001`，也不应把服务器 IP 写死在 React 源码中。具体代理配置见
[前端 Demo 与 Docker 部署](前端Demo与Docker部署.md)。

## 6. 联调检查顺序

1. 访问 `GET /api/health`，确认 HTTP 状态码为 200、`status` 为 `ok`，且
   `indexed_chunks` 大于 0。
2. 调用 `POST /api/chat` 提交一个知识库中有明确答案的问题，确认 `sources` 非空，回答中的
   引用编号能在 `sources[].id` 中找到。
3. 提交一个实时问题，确认 `blocked_realtime` 为 `true` 且 `sources` 为空。
4. 提交空字符串或非法 `top_k`，确认接口返回 422，而不是生成模型编造答案。
5. 前端部署后，再通过 `11451` 端口重复健康检查和问答测试，确认 Nginx 代理正常。
