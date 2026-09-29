# syntax=docker/dockerfile:1

FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data /app/static/uploads && \
    chown -R appuser:appuser /app/data /app/static/uploads

ENV FLASK_APP=app.py
ENV DATABASE_PATH=/app/data/groceries.db
ENV PYTHONUNBUFFERED=1
ENV PORT=5000

USER appuser

EXPOSE ${PORT}

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
