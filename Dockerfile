FROM python:3.12-slim

# Prevent .pyc files and enable unbuffered stdout/stderr for Cloud Run logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System dependencies required by asyncpg, psycopg, and reportlab
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy application code
COPY app/ app/
COPY scripts/ scripts/

# Create reports directory (used by PDF generation)
RUN mkdir -p reports

# Cloud Run injects PORT (default 8080)
ENV PORT=8080

EXPOSE ${PORT}

CMD ["sh", "-c", "exec gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:${PORT:-8080}"]
