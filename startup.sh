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
# Run migrations to keep schema up to date (ignore failures to allow containers to start)
if command -v alembic >/dev/null 2>&1; then
  echo "🔄 Applying database migrations..."
  HEAD_REV="add_tenant_optional_fields"
  # Determine whether to stamp directly (pre-existing schema) or run full upgrade
  MIGRATION_ACTION=$(python3 - <<'PY'
import asyncio, os, sys
try:
    import asyncpg
except ImportError:
    print("SKIP")  # no driver; let upgrade attempt handle errors
    raise SystemExit
db = os.environ.get("DATABASE_URL")
if not db:
    print("SKIP")
    raise SystemExit

async def main():
    conn = await asyncpg.connect(db)
    try:
        tenants_exists = await conn.fetchval("select to_regclass('public.tenants')")
        alembic_exists = await conn.fetchval("select to_regclass('public.alembic_version')")
        activity_exec_exists = await conn.fetchval("select to_regclass('public.activity_executions')")
        if not tenants_exists:
            print("RUN_UPGRADE")
            return
        if not alembic_exists:
            print("STAMP_HEAD")
            return
        ver = await conn.fetchval("select version_num from alembic_version limit 1")
        if ver == '001' and activity_exec_exists:
            # Schema was bootstrapped without Alembic migration creating this table; stamp ahead
            print("STAMP_HEAD")
            return
        print("RUN_UPGRADE")
    finally:
        await conn.close()

asyncio.run(main())
PY
)
  if [[ "$MIGRATION_ACTION" == "STAMP_HEAD" ]]; then
    echo "ℹ️ Stamping head revision (${HEAD_REV}) and ensuring tenant metadata columns exist"
    python3 - <<'PY'
import asyncio, os
try:
    import asyncpg
except ImportError:
    raise SystemExit(0)
db = os.environ.get("DATABASE_URL")
if not db:
    raise SystemExit(0)

ALTER_SQL = """
ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS industry VARCHAR(255),
  ADD COLUMN IF NOT EXISTS team_size VARCHAR(100),
  ADD COLUMN IF NOT EXISTS primary_use_case VARCHAR(255),
  ADD COLUMN IF NOT EXISTS ehr_vendor VARCHAR(255),
  ADD COLUMN IF NOT EXISTS region VARCHAR(100),
  ADD COLUMN IF NOT EXISTS security_contact VARCHAR(255),
  ADD COLUMN IF NOT EXISTS onboarding_notes TEXT;
"""

async def main():
    conn = await asyncpg.connect(db)
    try:
        await conn.execute(ALTER_SQL)
    finally:
        await conn.close()

asyncio.run(main())
PY
    PYTHONPATH=/app alembic stamp "${HEAD_REV}" || true
  else
    PYTHONPATH=/app alembic upgrade head || echo "⚠️ Alembic migration failed; continuing startup. Please check logs."
  fi
else
  echo "ℹ️ Alembic not available; skipping migrations."
fi

echo "✅ Database setup completed!"

# Start the FastAPI application without hot reload
echo "🌟 Starting FastAPI server (no hot reload)..."
exec env PYTHONPATH=/app uvicorn server:app --host 0.0.0.0 --port 8001
