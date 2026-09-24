# 前端 Demo 与 Docker 部署手册

本文说明 React 前端的本地开发方式，以及在实验室 Linux 服务器上使用 Docker 和 Nginx
部署到 `11451` 端口的过程。当前方案面向受控校园网内的导师演示，不等同于公网生产方案。

## 1. 最终结构

```text
导师浏览器
  │ http://服务器地址:11451
  ▼
Docker 容器中的 Nginx
  ├── /             React 静态页面
  ├── /healthz      前端容器健康检查
  └── /api/*        反向代理到宿主机 8001
                         │
                         ▼
                    FastAPI RAG API
                     ├── 8100：Qwen3.5
                     └── 8101：Qwen3-Embedding
```

浏览器只请求 `11451` 的同源 `/api`，不会直接访问 `8001`，因此不需要在 FastAPI 中放开
CORS。Docker 容器使用 `host.docker.internal` 访问宿主机；Compose 的 `extra_hosts` 会在
Linux 上把它映射为 Docker 的 `host-gateway`。

当前 FastAPI 仍监听 `0.0.0.0:8001`，所以校园网设备依然可能直接访问它。正式部署时还应
增加认证、访问控制、TLS 和限流，或把后端纳入同一个受控容器网络。

## 2. 文件说明

| 文件 | 作用 |
| --- | --- |
| `frontend/Dockerfile` | 第一阶段用 Node 构建 React，第二阶段只保留 Nginx 和静态文件 |
| `frontend/compose.yaml` | 配置容器、11451 端口、环境变量和宿主机映射 |
| `frontend/nginx/default.conf.template` | 静态页面、SPA 回退、API 代理、超时和安全响应头 |
| `frontend/.env.example` | 可提交的本地开发及 Docker 配置示例 |
| `frontend/.env.local` | Mac 本地开发配置，不提交 Git |
| `frontend/.env` | 服务器 Docker 配置，不提交 Git |
| `frontend/.dockerignore` | 排除依赖、构建产物和本地配置，缩小构建上下文 |

## 3. 本地开发

首次安装：

```bash
cd /Users/a-void/Code/service-agent-platform/frontend
npm install
```

本机的 `frontend/.env.local` 当前配置为：

```dotenv
VITE_API_PROXY_TARGET=http://10.98.196.218:8001
```

它表示 Vite 开发服务器把 `/api` 转发到实验室服务器。启动前可先确认直连：

```bash
curl --noproxy '*' http://10.98.196.218:8001/api/health
```

启动前端：

```bash
npm run dev
```

浏览器访问 <http://127.0.0.1:5173>。修改 React 或 CSS 后，Vite 会自动刷新页面；修改
`.env.local` 后必须按 `Control+C` 停止并重新启动。

本地检查：

```bash
npm run lint
npm run build
```

如果 API 无法连接，前端会进入“本地界面预览”模式；该模式只用于修改界面，回答不是知识库
真实结果。

## 4. Docker 配置解释

### 4.1 多阶段构建

`Dockerfile` 使用两个阶段：

1. `node:24.21.0-alpine3.24` 根据 `package-lock.json` 执行 `npm ci`，再生成 `dist/`。
2. `nginx:1.31.6-alpine3.24` 只接收构建结果，不携带 Node.js、源码和开发依赖。

这样运行镜像更小，运行时也不需要执行 `npm run dev`。镜像版本显式固定，升级时应修改
Dockerfile、重新构建并完成回归测试，不能在未验证时自动漂移到新版本。

Compose 将构建阶段的网络模式设为 `host`，使 `npm ci` 使用 Linux 服务器已经验证可用的
宿主机网络；该设置只影响构建步骤，不会改变最终 Nginx 容器的运行网络。`npm ci` 同时使用
BuildKit 缓存保存 npm 下载缓存，并增加有限次数的指数退避重试，以降低校园网瞬时抖动导致
整次构建失败的概率。考虑到校园网和镜像 CDN 在并发连接下容易超时，构建阶段把 npm
单源连接数从默认的 15 降为 1，优先复用缓存，并关闭不影响产物的 audit 和 fund 请求。
依赖版本仍由 `package-lock.json` 锁定；安全审计应作为独立任务执行，不与镜像构建耦合。

### 4.2 环境变量

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `DEMO_BIND_ADDRESS` | `0.0.0.0` | 宿主机监听地址；表示所有网卡均可访问 |
| `DEMO_PORT` | `11451` | 导师浏览器访问的端口 |
| `RAG_API_HOST` | `host.docker.internal` | 容器看到的宿主机名称 |
| `RAG_API_PORT` | `8001` | 宿主机上的 FastAPI 端口 |

`frontend/.env` 只控制部署环境，不进入 Git。React 源代码始终请求相对地址 `/api`，所以不
包含服务器 IP，也不会发生浏览器跨域。

### 4.3 Nginx 代理

官方 Nginx 镜像启动时，会把 `/etc/nginx/templates/*.template` 中已定义的环境变量替换后
写成实际配置。本项目把 `${RAG_API_HOST}` 和 `${RAG_API_PORT}` 注入 `proxy_pass`。

问答接口可能需要等待模型推理，因此连接、发送和读取超时分别配置为 `10`、`610`、`610`
秒。`proxy_buffering off` 避免 Nginx 额外缓存 API 响应。`try_files ... /index.html` 确保
React 单页应用刷新路径时仍能打开。

## 5. 服务器部署前检查

进入服务器仓库：

```bash
cd ~/App/service-agent-platform
git status --short
git pull --ff-only
```

确认 RAG API 和模型服务正常：

```bash
curl --noproxy '*' -sS http://127.0.0.1:8001/api/health | jq
screen -ls
```

健康检查中应包含：

```json
{
  "status": "ok",
  "chat_model": "qwen3.5",
  "embedding_model": "qwen3-embedding",
  "indexed_chunks": 33723
}
```

确认 `11451` 未被使用：

```bash
ss -lntp | grep -E ':11451\b' || echo '11451 未被占用'
```

先记录操作系统和 CPU 架构：

```bash
cat /etc/os-release
uname -m
```

确认 Docker 和 Compose：

```bash
docker --version
docker compose version
sudo systemctl status docker --no-pager
```

如果未安装，请先核对操作系统是否仍在 Docker 官方支持列表，再按照 Docker 官方 Ubuntu
安装文档添加官方软件源并安装 Docker Engine、Buildx 和 Compose 插件。截至本手册编写日，
官方列出的 Ubuntu 版本为 26.04、24.04 和 22.04；如果服务器仍是 20.04，不要直接套用最新
仓库命令，应先由管理员确定升级系统还是使用经过验证的离线版本。不要同时混装发行版的
`docker.io` 与官方 `docker-ce`。安装后是否把用户加入 `docker` 组也应由服务器管理员决定；
`docker` 组近似拥有 root 权限。

## 6. 首次部署

进入前端目录并创建服务器私有配置：

```bash
cd ~/App/service-agent-platform/frontend
cp .env.example .env
chmod 600 .env
```

打开 `.env` 检查：

```dotenv
DEMO_BIND_ADDRESS=0.0.0.0
DEMO_PORT=11451
RAG_API_HOST=host.docker.internal
RAG_API_PORT=8001
```

默认值适合当前实验室服务器。`VITE_API_PROXY_TARGET` 只影响 `npm run dev`，Docker 中的
Nginx 不读取它。

先展开 Compose 最终配置，确认端口和变量：

```bash
docker compose config
```

构建并后台启动：

```bash
docker compose up -d --build
```

首次构建需要下载 Node 和 Nginx 基础镜像以及 npm 依赖。网络中断后可重复执行同一命令；
已经缓存的层通常不需要重新下载。

## 7. 部署验收

检查容器：

```bash
docker compose ps
docker compose logs --tail=100 frontend
```

只检查 Nginx 是否运行：

```bash
curl --noproxy '*' -sS http://127.0.0.1:11451/healthz
```

预期输出：

```text
ok
```

检查“前端 Nginx → 宿主机 RAG API”的完整代理：

```bash
curl --noproxy '*' -sS http://127.0.0.1:11451/api/health | jq
```

然后在同一校园网的 Mac 浏览器访问：

```text
http://10.98.196.218:11451
```

右上角应显示“知识库服务正常”，再完成普通知识问答、实时问题拦截和来源展开三类测试。

## 8. 日常操作

查看状态：

```bash
cd ~/App/service-agent-platform/frontend
docker compose ps
```

实时日志：

```bash
docker compose logs -f --tail=100 frontend
```

退出日志按 `Control+C`，不会停止容器。

重启前端：

```bash
docker compose restart frontend
```

拉取新代码后重新构建并替换容器：

```bash
cd ~/App/service-agent-platform
git pull --ff-only
cd frontend
docker compose up -d --build
```

停止并删除前端容器和 Compose 网络：

```bash
docker compose down
```

该操作不会关闭宿主机上的 `rag-api`、`vllm-chat` 和 `vllm-embedding`，也不会删除模型或
知识库索引。不要随意增加 `-v` 或执行全局镜像清理命令。

## 9. 常见问题

### 页面能打开，但显示“本地界面预览”

先检查代理：

```bash
curl --noproxy '*' -v http://127.0.0.1:11451/api/health
```

若返回 `502 Bad Gateway`，检查宿主机 API 和容器内宿主机解析：

```bash
curl --noproxy '*' http://127.0.0.1:8001/api/health
docker compose exec frontend getent hosts host.docker.internal
docker compose exec frontend wget -qO- http://host.docker.internal:8001/api/health
```

### 11451 端口被占用

查看占用进程：

```bash
ss -lntp | grep -E ':11451\b'
```

确认可以更换后，在 `frontend/.env` 修改 `DEMO_PORT`，再执行：

```bash
docker compose up -d
```

### 修改了 `.env` 但没有生效

`docker compose restart` 不会重新创建容器环境。执行：

```bash
docker compose up -d --force-recreate
```

### Docker 拉取基础镜像失败

Shell 中的临时代理不一定会被 Docker daemon 使用。应由管理员配置 Docker daemon 代理或
镜像源，然后重启 Docker；不要把代理地址或认证信息提交到仓库。

## 10. 安全边界

- 当前前端没有登录认证，只能用于已确认边界的校园网演示。
- `0.0.0.0:11451` 会监听服务器所有网卡；不要映射到公网。
- Docker 发布端口可能绕过部分 UFW/firewalld 规则，网络限制应在 `DOCKER-USER` 链、上游
  防火墙或交换网络中明确配置。
- 浏览器页面不保存模型 API key；生成和向量服务仍只监听服务器回环地址。
- 前端镜像不包含原始资料、处理后的 Markdown、向量索引或模型权重。
- 正式部署前必须增加身份认证、TLS、访问日志脱敏、速率限制和审计留存。

## 11. 官方参考

- Docker Engine for Ubuntu：<https://docs.docker.com/engine/install/ubuntu/>
- Docker Compose plugin：<https://docs.docker.com/compose/install/linux/>
- Compose networking：<https://docs.docker.com/compose/how-tos/networking/>
- Nginx 官方 Docker 镜像：<https://github.com/nginx/docker-nginx>
