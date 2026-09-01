# ── Social Leaker — production image ────────────────────────────────
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000

WORKDIR /app

# System deps + Node.js (for the optional Claude Code ACP agent) + the adapter.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g @zed-industries/claude-code-acp \
 && apt-get purge -y curl gnupg \
 && apt-get autoremove -y \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Python dependencies (cached layer).
COPY requirements.txt .
RUN pip install -r requirements.txt

# Application code.
COPY . .

# Data dir is a mount point so the SQLite DB lives OUTSIDE the container.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000

# Create schema + admin, then serve.
CMD ["sh", "-c", "python -m socialleaker.cli init && python -m socialleaker.cli serve --host 0.0.0.0 --port ${APP_PORT}"]
