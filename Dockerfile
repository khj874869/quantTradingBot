FROM python:3.11-slim

WORKDIR /app

# System deps for psycopg2 + timezones
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    tzdata \
  && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY quantbot ./quantbot

RUN pip install --no-cache-dir --upgrade pip \
  && pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1

# Default: demo pipeline (override via docker compose env BOT_*)
CMD ["python","-m","quantbot.main","--mode","demo"]
