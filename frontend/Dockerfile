# 构建阶段只负责安装锁定依赖并生成静态文件。
FROM node:24.21.0-alpine3.24 AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY index.html tsconfig.json tsconfig.app.json tsconfig.node.json vite.config.ts eslint.config.js ./
COPY src ./src
COPY public ./public

RUN npm run build

# 运行阶段不包含 Node.js，只使用 Nginx 提供静态页面和 /api 反向代理。
FROM nginx:1.31.6-alpine3.24 AS runtime

COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1
