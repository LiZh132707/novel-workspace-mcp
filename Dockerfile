FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
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
CMD ["uvicorn", "ui.app:app", "--host", "0.0.0.0", "--port", "8765"]
