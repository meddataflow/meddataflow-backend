#!/bin/bash

# Backend startup script - runs database initialization and starts server
set -e

echo "🚀 Starting meddataflow Backend..."

# Wait for PostgreSQL to be ready
echo "⏳ Checking database readiness..."
# If DATABASE_URL points to Postgres, wait briefly for readiness using the URL; otherwise skip
MAX_ATTEMPTS=30
ATTEMPT=1
if [[ -n "$DATABASE_URL" && "$DATABASE_URL" == postgresql* ]]; then
  # Use pg_isready with libpq URI to avoid hardcoded host names
  until pg_isready -d "$DATABASE_URL" >/dev/null 2>&1; do
    echo "Database not ready yet (attempt $ATTEMPT/$MAX_ATTEMPTS). Waiting..."
    if [[ $ATTEMPT -ge $MAX_ATTEMPTS ]]; then
      echo "⚠️ Database not ready after $MAX_ATTEMPTS attempts. Continuing startup anyway."
      break
    fi
    ATTEMPT=$((ATTEMPT+1))
    sleep 2
  done
  echo "✅ Database check complete"
else
  echo "ℹ️ No Postgres DATABASE_URL provided or non-Postgres URL. Skipping DB wait."
fi

# Run database initialization (idempotent)
echo "🔄 Ensuring database schema and seed..."
PYTHONPATH=/app python3 database/init_db.py ensure || true

echo "✅ Database setup completed!"

# Start the FastAPI application without hot reload
echo "🌟 Starting FastAPI server (no hot reload)..."
exec env PYTHONPATH=/app uvicorn server:app --host 0.0.0.0 --port 8001
