# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    DUALSIGHT_HOST=0.0.0.0 \
    DUALSIGHT_PORT=8000

WORKDIR /app

RUN groupadd --system dualsight \
    && useradd --system --gid dualsight --create-home dualsight

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install .

COPY --chown=dualsight:dualsight . .

USER dualsight

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]

CMD ["dualsight", "serve", "--host", "0.0.0.0", "--port", "8000"]
