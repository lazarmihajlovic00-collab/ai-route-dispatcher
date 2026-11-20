FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY dispatcher ./dispatcher
RUN pip install --no-cache-dir .
COPY --from=web /web/dist ./web/dist
COPY docs ./docs
ENV DISPATCHER_DB=/app/data/dispatcher.db
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["dispatcher", "serve", "--host", "0.0.0.0", "--port", "8000"]
