# ── ERP & RPA Platform frontend ───────────────────────────────────────────────
# Build the Vite bundle in a Node stage, serve it from nginx. The runtime image
# has no Node in it at all.
#
#   docker build -t erp-frontend .
#   docker run --rm -p 8080:80 erp-frontend

FROM node:20-alpine AS build
WORKDIR /app

# Install against the lockfile first so a source-only change reuses this layer
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY . .
# The bundle talks to same-origin /api, so no backend URL is baked in at build
# time — the same image works in dev, staging and production.
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build


FROM nginx:1.27-alpine AS runtime

# `api` resolves to the docker-compose service of the same name. On a plain VM,
# replace the proxy_pass targets with 127.0.0.1:8000.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
