"""
AI Workflow Router - API endpoints for AI-powered workflow generation
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Dict, Any, Optional
import uuid
import logging

from api.auth_deps import get_current_user, require_super_admin
from models.workflow import WorkflowRepository
from services.ai_service import ai_workflow_service
from services.settings_service import settings_service

router = APIRouter(prefix="/api/ai", tags=["ai-workflows"])
logger = logging.getLogger(__name__)


class GenerateWorkflowRequest(BaseModel):
    prompt: str
    auto_create: bool = False  # If True, automatically create the workflow


class GenerateWorkflowResponse(BaseModel):
    success: bool
    workflow_config: Optional[Dict[str, Any]] = None
    workflow_id: Optional[str] = None
    message: str
    ai_used: bool = True


class TestAIConnectionRequest(BaseModel):
    api_key: Optional[str] = None


@router.post("/workflow/generate", response_model=GenerateWorkflowResponse)
async def generate_workflow_from_prompt(
    request: GenerateWorkflowRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Generate a workflow configuration from natural language prompt using AI
    """
    try:
        # Check if AI is enabled and configured
        ai_settings = await settings_service.get_ai_settings()
        if not ai_settings.get("enabled", False):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI workflow generation is not enabled"
            )

        api_key = ai_settings.get("openrouter_api_key")
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI API key not configured"
            )

        # Set the API key in the AI service
        ai_workflow_service.set_api_key(api_key)

        # Generate workflow using AI
        workflow_config = await ai_workflow_service.generate_workflow_from_prompt(
            user_prompt=request.prompt,
            tenant_id=str(current_user["tenant_id"]),
            user_id=str(current_user["id"])
        )

        workflow_id = None

        # Auto-create workflow if requested
        if request.auto_create:
            try:
                # Create the workflow in the database
                created_workflow = await WorkflowRepository.create_workflow(
                    tenant_id=current_user["tenant_id"],
                    created_by_id=current_user["id"],
                    name=workflow_config["name"],
                    description=workflow_config["description"],
                    version=workflow_config.get("version", "1.0.0"),
                    status="DRAFT"
                )

                workflow_id = str(created_workflow["id"])

                # Create activities using the current schema (order_index, tenant_id)
                from database.connection import fetch_one_dict
                import uuid as _uuid
                import json as _json

                for activity_data in workflow_config["activities"]:
                    act_id = str(_uuid.uuid4())
                    new_act = await fetch_one_dict(
                        """
                        INSERT INTO workflow_activities (
                            id, workflow_id, tenant_id, name, activity_type, order_index, config, error_handling
                        ) VALUES (
                            :id, :workflow_id, :tenant_id, :name, :activity_type, :order_index, :config, :error_handling
                        ) RETURNING *
                        """,
                        {
                            "id": act_id,
                            "workflow_id": str(workflow_id),
                            "tenant_id": str(current_user["tenant_id"]),
                            "name": activity_data["name"],
                            "activity_type": activity_data["activity_type"],
                            "order_index": int(activity_data.get("order", 1)),
                            "config": _json.dumps(activity_data.get("config", {})),
                            "error_handling": _json.dumps({"on_error": "stop", "retry_count": 0})
                        }
                    )

                    # Create transformers if any (optional – storing for parity when table exists)
                    # Note: activity_transformers table in this codebase expects different schema; add when needed.


            except Exception as e:
                logger.error(f"Failed to auto-create workflow: {e}")
                # Don't fail the request, just return the config without creating
                pass

        return GenerateWorkflowResponse(
            success=True,
            workflow_config=workflow_config,
            workflow_id=workflow_id,
            message="Workflow generated successfully" + (" and created" if workflow_id else ""),
            ai_used=True
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating workflow from AI: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate workflow: {str(e)}"
        )


@router.get("/settings")
async def get_ai_settings(
    current_user: Dict[str, Any] = Depends(require_super_admin())
):
    """
    Get AI settings (Super Admin only)
    """
    try:
        settings = await settings_service.get_ai_settings()

        # Don't return the actual API key for security
        safe_settings = {
            "enabled": settings.get("enabled", False),
            "has_api_key": bool(settings.get("openrouter_api_key")),
            "model": settings.get("model", "anthropic/claude-3.5-sonnet"),
            "max_requests_per_day": settings.get("max_requests_per_day", 100),
            "usage_today": settings.get("usage_today", 0)
        }

        return safe_settings

    except Exception as e:
        logger.error(f"Error getting AI settings: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get AI settings"
        )


@router.put("/settings")
async def update_ai_settings(
    settings: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(require_super_admin())
):
    """
    Update AI settings (Super Admin only)
    """
    try:
        # Validate settings
        allowed_fields = [
            "enabled", "openrouter_api_key", "model",
            "max_requests_per_day", "temperature"
        ]

        filtered_settings = {
            k: v for k, v in settings.items()
            if k in allowed_fields
        }

        # Update settings
        await settings_service.update_ai_settings(filtered_settings)


        return {"message": "AI settings updated successfully"}

    except Exception as e:
        logger.error(f"Error updating AI settings: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update AI settings"
        )


@router.post("/test-connection")
async def test_ai_connection(
    request: TestAIConnectionRequest,
    current_user: Dict[str, Any] = Depends(require_super_admin())
):
    """
    Test AI API connection (Super Admin only)
    """
    try:
        # Use provided API key or get from settings
        api_key = request.api_key
        if not api_key:
            ai_settings = await settings_service.get_ai_settings()
            api_key = ai_settings.get("openrouter_api_key")

        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No API key provided or configured"
            )

        # Test the connection with a simple prompt
        ai_workflow_service.set_api_key(api_key)

        test_response = await ai_workflow_service.generate_workflow_from_prompt(
            user_prompt="Create a simple workflow that parses an HL7 message and extracts patient name",
            tenant_id="test",
            user_id="test"
        )

        return {
            "success": True,
            "message": "AI connection successful",
            "test_response_received": bool(test_response)
        }

    except Exception as e:
        logger.error(f"AI connection test failed: {e}")
        return {
            "success": False,
            "message": f"AI connection failed: {str(e)}"
        }


@router.get("/activity-types")
async def get_supported_activity_types(
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Get list of supported activity types for AI workflow generation
    """
    try:
        # Get activity knowledge from AI service
        ai_service_instance = ai_workflow_service
        activity_knowledge = ai_service_instance._get_activity_knowledge()

        return {
            "activity_types": list(activity_knowledge["activity_types"].keys()),
            "transformer_types": list(activity_knowledge["transformer_types"].keys()),
            "common_hl7_fields": activity_knowledge["common_hl7_fields"]
        }

    except Exception as e:
        logger.error(f"Error getting activity types: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get activity types"
        )


@router.get("/usage-stats")
async def get_ai_usage_stats(
    current_user: Dict[str, Any] = Depends(require_super_admin())
):
    """
    Get AI usage statistics (Super Admin only)
    """
    try:
        stats = await settings_service.get_ai_usage_stats()

        return {
            "total_requests": stats.get("total_requests", 0),
            "requests_today": stats.get("requests_today", 0),
            "requests_this_month": stats.get("requests_this_month", 0),
            "last_request_at": stats.get("last_request_at"),
            "average_response_time": stats.get("average_response_time", 0),
            "success_rate": stats.get("success_rate", 0),
            "cost_estimate": stats.get("cost_estimate", 0)
        }

    except Exception as e:
        logger.error(f"Error getting AI usage stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get usage stats"
        )
