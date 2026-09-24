# React 前端 Demo

当前目录是长江航道公共服务智能助手的本地前端。技术栈为 Vite、React、TypeScript 和
Nginx。详细配置、Docker 部署和排错步骤见
[前端 Demo 与 Docker 部署手册](../docs/前端Demo与Docker部署.md)。

浏览器 favicon 与移动端桌面图标位于 `public/`。它们由原始设计图等比裁切、缩放而来，
保留透明通道；不要把下载目录中的高分辨率原图直接放入生产构建。

## 本地预览

```bash
cd frontend
npm install
npm run dev
```

打开 <http://127.0.0.1:5173>。Vite 从 `.env.local` 读取 `VITE_API_PROXY_TARGET`，当前
Mac 配置会通过校园网直连实验室服务器。如果 API 没有启动，页面会自动进入本地界面预览
模式，使用固定示例回答验证界面和交互，不会访问真实知识库。

需要更换测试 API 时，修改被 Git 忽略的 `.env.local`：

```dotenv
VITE_API_PROXY_TARGET=http://其他地址:8001
```

修改环境文件后需要重新启动 Vite。此变量只由本地开发服务器使用；Docker 部署由 Nginx
在同一域名下代理 `/api`，浏览器不直接访问后端端口。

## 检查生产构建

```bash
npm run build
npm run lint
```

`dist/` 是可重新生成的构建产物，不应提交 Git。

## Docker 部署

服务器进入本目录后执行：

```bash
cp .env.example .env
chmod 600 .env
docker compose config
docker compose up -d --build
```

默认发布到 `11451`，并通过 `host.docker.internal:8001` 访问宿主机 RAG API。完整验收和
安全说明见部署手册。
