"""
Admin plan management and public plan listing
"""
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
import uuid

from api.auth_deps import require_super_admin
from models.plan import PlanRepository
import os
try:
    import stripe
except Exception:
    stripe = None
from api.settings_router import Path as _Path  # placeholder; we'll use local reader
import json

from pathlib import Path as FsPath

CONFIG_PATH = FsPath(__file__).resolve().parent.parent / "config" / "platform_config.json"

def _read_platform_config() -> Dict[str, Any]:
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text())
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}

router = APIRouter(prefix="/api", tags=["plans"])


class PlanCreate(BaseModel):
    code: str
    name: str
    price_cents: int
    billing_period: str = 'monthly'
    included_messages: int = 0
    overage_rate: float = 0.0
    features: Optional[List[str]] = None
    is_active: bool = True
    stripe_price_id: Optional[str] = None
    create_stripe: Optional[bool] = True


class PlanUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    price_cents: Optional[int] = None
    billing_period: Optional[str] = None
    included_messages: Optional[int] = None
    overage_rate: Optional[float] = None
    features: Optional[List[str]] = None
    is_active: Optional[bool] = None
    stripe_price_id: Optional[str] = None


@router.get("/public/plans")
async def public_plans() -> List[Dict[str, Any]]:
    return await PlanRepository.list_plans(active_only=True)


@router.get("/admin/plans", dependencies=[Depends(require_super_admin())])
async def list_plans_admin() -> List[Dict[str, Any]]:
    return await PlanRepository.list_plans(active_only=False)


@router.post("/admin/plans", dependencies=[Depends(require_super_admin())])
async def create_plan_admin(body: PlanCreate):
    existing = await PlanRepository.get_plan_by_code(body.code)
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Plan code already exists")
    plan = await PlanRepository.create_plan(
        code=body.code,
        name=body.name,
        price_cents=body.price_cents,
        billing_period=body.billing_period,
        included_messages=body.included_messages,
        overage_rate=body.overage_rate,
        features=body.features,
        is_active=body.is_active,
        stripe_price_id=body.stripe_price_id,
    )
    # If no explicit price provided and Stripe configured, create Product+Price
    if (not plan.get('stripe_price_id')) and (body.create_stripe is not False):
        cfg = _read_platform_config().get('stripe', {})
        secret = cfg.get('secret_key') or os.getenv('STRIPE_SECRET_KEY')
        if not secret:
            # no stripe configured; return plan as-is
            return plan
        if stripe is None:
            return plan
        try:
            stripe.api_key = secret
            prod = stripe.Product.create(name=body.name)
            price = stripe.Price.create(
                unit_amount=body.price_cents,
                currency='usd',
                recurring={'interval': 'month' if body.billing_period == 'monthly' else 'year'},
                product=prod['id']
            )
            updated = await PlanRepository.update_plan(plan['id'], stripe_product_id=prod['id'], stripe_price_id=price['id'])
            return updated or plan
        except Exception:
            return plan
    return plan


@router.patch("/admin/plans/{plan_id}", dependencies=[Depends(require_super_admin())])
async def update_plan_admin(plan_id: str, body: PlanUpdate):
    try:
        pid = uuid.UUID(plan_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid plan id")
    updated = await PlanRepository.update_plan(pid, **{k: v for k, v in body.dict(exclude_unset=True).items()})
    if not updated:
        raise HTTPException(status_code=404, detail="Plan not found or no changes")
    return updated


@router.delete("/admin/plans/{plan_id}", dependencies=[Depends(require_super_admin())])
async def delete_plan_admin(plan_id: str):
    try:
        pid = uuid.UUID(plan_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid plan id")
    await PlanRepository.delete_plan(pid)
    return {"deleted": True}
