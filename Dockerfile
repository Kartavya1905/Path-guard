# Single-container image for free public hosting (Render). The API serves the built
# web app, so there is one URL and no cross-origin setup. Local development keeps using
# compose.yaml (separate api and web containers).

FROM node:24-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATHGUARD_STATIC_DIR=/app/static
WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/app ./app
COPY backend/pathguard_engine ./pathguard_engine
RUN pip install --no-cache-dir .
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY --from=web /web/dist ./static
RUN useradd --create-home pathguard
USER pathguard

# Render sets PORT; migrations and the expiry purge run on every start.
CMD ["sh", "-c", "alembic upgrade head && python -m app.purge && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
