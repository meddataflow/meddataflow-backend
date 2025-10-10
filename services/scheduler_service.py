"""
Lightweight Workflow Scheduler Service
--------------------------------------
Executes workflows that have a cron-like schedule. Initial support focuses on
minute/hour patterns without external dependencies:

- "* * * * *"         → every minute
- "*/N * * * *"       → every N minutes
- "0 */N * * *"       → every N hours on the hour

This service is opt-in via env var `SCHEDULER_ENABLED=true`.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

from database.connection import fetch_all_dict, execute_dict
from services.workflow_execution_service import workflow_execution_service


class WorkflowSchedulerService:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._poll_seconds: int = int(os.getenv("SCHEDULER_POLL_SECONDS", "60"))
        self._enabled: bool = os.getenv("SCHEDULER_ENABLED", "false").lower() == "true"
        self._running: bool = False

    async def start(self) -> None:
        if not self._enabled or self._task is not None:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception:
                # swallow to keep loop alive; log minimal info without PII
                pass
            await asyncio.sleep(self._poll_seconds)

    async def _tick(self) -> None:
        # Select workflows that have a cron_expression and are due to run
        rows: List[Dict[str, Any]] = await fetch_all_dict(
            """
            SELECT id, tenant_id, name, cron_expression, next_run_at
            FROM workflows
            WHERE cron_expression IS NOT NULL
              AND status IN ('ACTIVE', 'SCHEDULED')
              AND (next_run_at IS NULL OR next_run_at <= NOW())
            LIMIT 50
            """,
            {}
        )

        if not rows:
            return

        now = datetime.now(timezone.utc)
        for wf in rows:
            wf_id = str(wf.get("id"))
            tenant_id = str(wf.get("tenant_id"))
            cron_expr = (wf.get("cron_expression") or "").strip()

            # Execute asynchronously but don't overwhelm; fire-and-forget
            asyncio.create_task(
                self._execute_and_schedule_next(wf_id, tenant_id, cron_expr, now)
            )

    async def _execute_and_schedule_next(self, workflow_id: str, tenant_id: str, cron_expr: str, ref_time: datetime) -> None:
        # Compute next run time first to avoid double-firing under load
        next_at = self._compute_next_run_at(cron_expr, ref_time)
        if next_at:
            await execute_dict(
                "UPDATE workflows SET next_run_at = :ts WHERE id = :id",
                {"ts": next_at, "id": workflow_id}
            )

        # Execute the workflow with a minimal trigger context
        try:
            await workflow_execution_service.execute_workflow(
                workflow_id=workflow_id,
                trigger_data={"source": "scheduler"},
                tenant_id=tenant_id,
                user_id=None,
            )
        except Exception:
            # best effort; next tick will retry per schedule
            pass

    def _compute_next_run_at(self, cron_expr: str, now: datetime) -> Optional[datetime]:
        """Compute the next run time based on a small subset of cron syntax.

        Supported:
        - "* * * * *" every minute
        - "*/N * * * *" every N minutes
        - "0 */N * * *" every N hours on the hour
        Fallback: run 5 minutes later.
        """
        try:
            parts = cron_expr.split()
            if len(parts) != 5:
                return now + timedelta(minutes=5)

            minute, hour, _, _, _ = parts

            if cron_expr == "* * * * *":
                return (now + timedelta(minutes=1)).replace(second=0, microsecond=0)

            if minute.startswith("*/") and hour == "*":
                # every N minutes
                n = int(minute[2:])
                if n <= 0:
                    n = 1
                base = now.replace(second=0, microsecond=0)
                delta = (n - (base.minute % n)) % n
                delta = delta if delta != 0 else n
                return base + timedelta(minutes=delta)

            if minute == "0" and hour.startswith("*/"):
                # every N hours on the hour
                n = int(hour[2:])
                if n <= 0:
                    n = 1
                base = now.replace(minute=0, second=0, microsecond=0)
                delta_h = (n - (base.hour % n)) % n
                delta_h = delta_h if delta_h != 0 else n
                return base + timedelta(hours=delta_h)

            # Fallback: next minute
            return (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        except Exception:
            return now + timedelta(minutes=5)


# Global instance
scheduler_service = WorkflowSchedulerService()

