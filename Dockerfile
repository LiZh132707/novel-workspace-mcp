FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NOVEL_WORKSPACE_HOME=/app \
    NOVEL_LLM_PROVIDER=api \
    NOVEL_LLM_BASE_URL=http://host.docker.internal:1234/v1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY core ./core
COPY plugins ./plugins
COPY skills ./skills
COPY ui ./ui
COPY *.py ./

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

EXPOSE 8765
VOLUME ["/app/storage"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3).read()"]
CMD ["novel-workspace", "serve", "--host", "0.0.0.0", "--port", "8765"]
