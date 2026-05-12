# syntax=docker/dockerfile:1.6
# 多阶段：node 构建 → nginx 静态服务
FROM node:20-alpine AS builder
WORKDIR /app

# 先拷 package*.json 以便缓存 npm install 层
COPY package.json package-lock.json* ./
RUN npm install --no-fund --no-audit

# 拷入源码并构建
COPY tsconfig.json vite.config.ts tailwind.config.js postcss.config.js index.html ./
COPY src ./src
RUN npm run build

FROM nginx:1.27-alpine AS runtime
# 自带 SPA fallback 的 nginx 配置
COPY <<'EOF' /etc/nginx/conf.d/default.conf
server {
    listen 80;
    server_name _;

    root /usr/share/nginx/html;
    index index.html;

    # /api 和 /api/v1/ws 转给后端
    location /api/ {
        proxy_pass http://backend:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # WebSocket 必需
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 600s;
    }

    # SPA fallback
    location / {
        try_files $uri $uri/ /index.html;
    }
}
EOF

COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
