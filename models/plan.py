from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import uuid

from database.connection import fetch_one, fetch_all, execute_returning, execute
import json


class PlanRepository:
    @staticmethod
    async def create_plan(code: str, name: str, price_cents: int, billing_period: str = 'monthly', included_messages: int = 0, overage_rate: float = 0.0, features: Optional[List[str]] = None, is_active: bool = True, stripe_price_id: Optional[str] = None, stripe_product_id: Optional[str] = None) -> Dict[str, Any]:
        query = """
        INSERT INTO subscription_plans (code, name, price_cents, billing_period, included_messages, overage_rate, features, is_active, stripe_product_id, stripe_price_id, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11)
        RETURNING *
        """
        features_json = json.dumps(features or [])
        return await execute_returning(query, code, name, price_cents, billing_period, included_messages, overage_rate, features_json, is_active, stripe_product_id, stripe_price_id, datetime.now(timezone.utc))

    @staticmethod
    async def list_plans(active_only: bool = False) -> List[Dict[str, Any]]:
        if active_only:
            query = "SELECT * FROM subscription_plans WHERE is_active = true ORDER BY price_cents ASC"
            return await fetch_all(query)
        else:
            query = "SELECT * FROM subscription_plans ORDER BY created_at DESC"
            return await fetch_all(query)

    @staticmethod
    async def get_plan_by_code(code: str) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM subscription_plans WHERE LOWER(code) = LOWER($1)"
        return await fetch_one(query, code)

    @staticmethod
    async def get_plan_by_price_id(price_id: str) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM subscription_plans WHERE stripe_price_id = $1"
        return await fetch_one(query, price_id)

    @staticmethod
    async def get_plan_by_id(plan_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM subscription_plans WHERE id = $1"
        return await fetch_one(query, plan_id)

    @staticmethod
    async def update_plan(plan_id: uuid.UUID, **updates) -> Optional[Dict[str, Any]]:
        if not updates:
            return None
        set_clauses = []
        values = []
        i = 1
        for k, v in updates.items():
            if k in ("code", "name", "price_cents", "billing_period", "included_messages", "overage_rate", "features", "is_active", "stripe_price_id", "stripe_product_id"):
                if k == 'features' and isinstance(v, list):
                    set_clauses.append(f"{k} = ${i}::jsonb")
                    values.append(json.dumps(v))
                else:
                    set_clauses.append(f"{k} = ${i}")
                    values.append(v)
                i += 1
        if not set_clauses:
            return None
        set_clauses.append(f"updated_at = ${i}")
        values.append(datetime.now(timezone.utc))
        i += 1
        values.append(plan_id)
        query = f"UPDATE subscription_plans SET {', '.join(set_clauses)} WHERE id = ${i} RETURNING *"
        return await execute_returning(query, *values)

    @staticmethod
    async def delete_plan(plan_id: uuid.UUID) -> bool:
        query = "DELETE FROM subscription_plans WHERE id = $1"
        await execute(query, plan_id)
        return True
