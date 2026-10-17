FROM python:3.11-slim

# Install curl for Docker health checks
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements.txt lives at the root of the build context (./backend/)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire build context into /app/backend/ so the package hierarchy
# mirrors the source tree: /app/backend/__init__.py, /app/backend/main.py, …
# With PYTHONPATH=/app, `import backend.config` resolves correctly.
COPY . ./backend/

ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
