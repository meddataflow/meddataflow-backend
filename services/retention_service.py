"""
Data Retention Service
----------------------
Periodic cleanup based on per-tenant retention policies (tenant.settings.retention_days).
Runs daily to delete hl7_messages and workflow_executions older than policy.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from database.connection import fetch_all_dict, execute_dict


class RetentionService:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running: bool = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        # run once on start, then every 24h
        while self._running:
            try:
                await self._cleanup()
            except Exception:
                pass
            await asyncio.sleep(24 * 60 * 60)

    async def _cleanup(self) -> None:
        # Load tenants with a positive retention_days
        tenants = await fetch_all_dict(
            "SELECT id, settings FROM tenants WHERE is_active = true",
            {}
        )
        now = datetime.now(timezone.utc)
        for t in tenants:
            settings = t.get('settings') or {}
            if isinstance(settings, str):
                try:
                    import json as _json
                    settings = _json.loads(settings)
                except Exception:
                    settings = {}
            retention_days = int((settings or {}).get('retention_days') or 0)
            if retention_days and retention_days > 0:
                cutoff = now - timedelta(days=retention_days)
                # Delete old HL7 messages
                await execute_dict(
                    "DELETE FROM hl7_messages WHERE tenant_id = :tid AND created_at < :cutoff",
                    { 'tid': str(t['id']), 'cutoff': cutoff }
                )
                # Delete old workflow executions
                await execute_dict(
                    "DELETE FROM workflow_executions WHERE tenant_id = :tid AND started_at < :cutoff",
                    { 'tid': str(t['id']), 'cutoff': cutoff }
                )


retention_service = RetentionService()

