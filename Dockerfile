# STRAUSS DRIVE - one image: the React app is built in the first stage and served by the FastAPI service in the
# second, so the client and the API share one origin (the client calls relative /api/... only).
#   docker build -t strauss-drive .
#   docker run -p 10000:10000 -e DATABASE_URL=... strauss-drive

# ---- 1. frontend build (tsc + vite) ----
FROM node:24-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 2. backend + the built frontend ----
FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv/backend
COPY backend/requirements.txt backend/requirements-render.txt ./
RUN pip install -r requirements-render.txt
COPY backend/ ./
COPY --from=web /web/dist /srv/frontend-dist
ENV FRONTEND_DIST_DIR=/srv/frontend-dist \
    SESSION_COOKIE_SECURE=true \
    PORT=10000
RUN useradd --system --create-home --uid 10001 app && chown -R app /srv
USER app
EXPOSE 10000
CMD ["python", "deploy/render_start.py"]
