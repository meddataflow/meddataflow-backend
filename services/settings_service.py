"""
Settings Service - Manage application settings including AI configuration
"""
import base64
import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from pathlib import Path
from database.connection import fetch_one, execute_returning, execute

logger = logging.getLogger(__name__)


class SettingsService:
    """Service for managing application settings"""
    _legacy_config_dir = Path(__file__).resolve().parent.parent / "config"
    _static_dir = Path(__file__).resolve().parent.parent / "static"

    async def get_ai_settings(self) -> Dict[str, Any]:
        """Get AI-related settings"""
        try:
            query = """
            SELECT setting_value FROM system_settings
            WHERE setting_key = $1
            """
            result = await fetch_one(query, "ai_config")

            if result:
                return json.loads(result["setting_value"])
            else:
                # Return default settings
                return {
                    "enabled": False,
                    "openrouter_api_key": None,
                    "model": "anthropic/claude-3.5-sonnet",
                    "max_requests_per_day": 100,
                    "temperature": 0.1,
                    "usage_today": 0,
                    "last_reset_date": datetime.now(timezone.utc).date().isoformat()
                }

        except Exception as e:
            logger.error(f"Error getting AI settings: {e}")
            return {}

    async def update_ai_settings(self, settings: Dict[str, Any]) -> bool:
        """Update AI settings"""
        try:
            # Get current settings
            current_settings = await self.get_ai_settings()

            # Merge with new settings
            updated_settings = {**current_settings, **settings}
            updated_settings["updated_at"] = datetime.now(timezone.utc).isoformat()

            # Upsert settings
            query = """
            INSERT INTO system_settings (setting_key, setting_value, updated_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (setting_key)
            DO UPDATE SET
                setting_value = EXCLUDED.setting_value,
                updated_at = EXCLUDED.updated_at
            """

            await execute(
                query,
                "ai_config",
                json.dumps(updated_settings),
                datetime.now(timezone.utc)
            )

            return True

        except Exception as e:
            logger.error(f"Error updating AI settings: {e}")
            return False

    async def increment_ai_usage(self) -> Dict[str, Any]:
        """Increment AI usage counter and return current stats"""
        try:
            current_settings = await self.get_ai_settings()
            today = datetime.now(timezone.utc).date().isoformat()

            # Reset daily counter if it's a new day
            if current_settings.get("last_reset_date") != today:
                current_settings["usage_today"] = 0
                current_settings["last_reset_date"] = today

            # Increment usage
            current_settings["usage_today"] += 1
            current_settings["total_requests"] = current_settings.get("total_requests", 0) + 1
            current_settings["last_request_at"] = datetime.now(timezone.utc).isoformat()

            # Update settings
            await self.update_ai_settings(current_settings)

            return {
                "usage_today": current_settings["usage_today"],
                "max_requests_per_day": current_settings.get("max_requests_per_day", 100),
                "remaining_today": max(0, current_settings.get("max_requests_per_day", 100) - current_settings["usage_today"])
            }

        except Exception as e:
            logger.error(f"Error incrementing AI usage: {e}")
            return {"usage_today": 0, "max_requests_per_day": 100, "remaining_today": 100}

    async def check_ai_rate_limit(self) -> bool:
        """Check if AI usage is within rate limits"""
        try:
            settings = await self.get_ai_settings()

            if not settings.get("enabled", False):
                return False

            max_requests = settings.get("max_requests_per_day", 100)
            usage_today = settings.get("usage_today", 0)

            return usage_today < max_requests

        except Exception as e:
            logger.error(f"Error checking AI rate limit: {e}")
            return False

    async def get_ai_usage_stats(self) -> Dict[str, Any]:
        """Get comprehensive AI usage statistics"""
        try:
            settings = await self.get_ai_settings()

            # Get request history for the last 30 days
            query = """
            SELECT
                DATE(created_at) as request_date,
                COUNT(*) as request_count
            FROM ai_request_logs
            WHERE created_at >= $1
            GROUP BY DATE(created_at)
            ORDER BY request_date DESC
            """

            thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
            history_result = await fetch_one(query, thirty_days_ago)

            # Calculate stats
            total_requests = settings.get("total_requests", 0)
            requests_today = settings.get("usage_today", 0)

            # Get this month's usage
            this_month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            month_query = """
            SELECT COUNT(*) as count FROM ai_request_logs
            WHERE created_at >= $1
            """
            month_result = await fetch_one(month_query, this_month_start)
            requests_this_month = month_result["count"] if month_result else 0

            # Calculate success rate
            success_query = """
            SELECT
                COUNT(*) as total,
                COUNT(CASE WHEN success = true THEN 1 END) as successful
            FROM ai_request_logs
            WHERE created_at >= $1
            """
            success_result = await fetch_one(success_query, thirty_days_ago)
            success_rate = 0
            if success_result and success_result["total"] > 0:
                success_rate = (success_result["successful"] / success_result["total"]) * 100

            return {
                "total_requests": total_requests,
                "requests_today": requests_today,
                "requests_this_month": requests_this_month,
                "last_request_at": settings.get("last_request_at"),
                "success_rate": round(success_rate, 2),
                "max_requests_per_day": settings.get("max_requests_per_day", 100),
                "cost_estimate": total_requests * 0.01  # Rough estimate
            }

        except Exception as e:
            logger.error(f"Error getting AI usage stats: {e}")
            return {
                "total_requests": 0,
                "requests_today": 0,
                "requests_this_month": 0,
                "success_rate": 0,
                "cost_estimate": 0
            }

    async def log_ai_request(
        self,
        prompt: str,
        response: Dict[str, Any],
        success: bool,
        error_message: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> bool:
        """Log AI request for analytics"""
        try:
            query = """
            INSERT INTO ai_request_logs (
                user_id, prompt, response, success, error_message, created_at
            ) VALUES ($1, $2, $3, $4, $5, $6)
            """

            await execute(
                query,
                user_id,
                prompt[:1000],  # Truncate long prompts
                json.dumps(response) if response else None,
                success,
                error_message,
                datetime.now(timezone.utc)
            )

            return True

        except Exception as e:
            logger.error(f"Error logging AI request: {e}")
            return False

    async def get_system_setting(self, key: str, default_value: Any = None) -> Any:
        """Get a system setting by key"""
        try:
            query = """
            SELECT setting_value FROM system_settings
            WHERE setting_key = $1
            """
            result = await fetch_one(query, key)

            if result:
                try:
                    return json.loads(result["setting_value"])
                except json.JSONDecodeError:
                    return result["setting_value"]
            else:
                return default_value

        except Exception as e:
            logger.error(f"Error getting system setting {key}: {e}")
            return default_value

    async def set_system_setting(self, key: str, value: Any) -> bool:
        """Set a system setting"""
        try:
            if isinstance(value, (dict, list)):
                setting_value = json.dumps(value)
            else:
                setting_value = str(value)

            query = """
            INSERT INTO system_settings (setting_key, setting_value, updated_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (setting_key)
            DO UPDATE SET
                setting_value = EXCLUDED.setting_value,
                updated_at = EXCLUDED.updated_at
            """

            await execute(
                query,
                key,
                setting_value,
                datetime.now(timezone.utc)
            )

            return True

        except Exception as e:
            logger.error(f"Error setting system setting {key}: {e}")
            return False

    async def ensure_settings_tables(self):
        """Ensure required settings tables exist"""
        try:
            # Create system_settings table if not exists
            create_settings_table = """
            CREATE TABLE IF NOT EXISTS system_settings (
                setting_key VARCHAR(255) PRIMARY KEY,
                setting_value TEXT NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
            """

            # Create AI request logs table if not exists
            create_logs_table = """
            CREATE TABLE IF NOT EXISTS ai_request_logs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID,
                prompt TEXT,
                response TEXT,
                success BOOLEAN DEFAULT FALSE,
                error_message TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
            """

            await execute(create_settings_table)
            await execute(create_logs_table)


        except Exception as e:
            logger.error(f"Error ensuring settings tables: {e}")

    async def _read_legacy_file(self, filename: str) -> Optional[Dict[str, Any]]:
        """Load legacy JSON config from disk (used for one-time migration)."""
        try:
            path = self._legacy_config_dir / filename
            if path.exists():
                data = json.loads(path.read_text())
                return data if isinstance(data, dict) else None
        except Exception:
            return None
        return None

    async def get_platform_branding(self) -> Dict[str, Any]:
        """Return platform branding, migrating from legacy file storage if needed."""
        default = {
            "app_name": "meddataflow",
            "company_logo_url": None,
            "favicon_url": None,
            "idle_timeout_minutes": 5,
            "idle_warning_seconds": 60,
        }
        try:
            data = await self.get_system_setting("platform_branding", None)
            if not isinstance(data, dict):
                # Try legacy file as a migration path
                legacy = await self._read_legacy_file("branding.json")
                data = legacy if isinstance(legacy, dict) else {}
                if data:
                    await self.set_system_setting("platform_branding", data)
            merged = {**default, **(data or {})}
            await self._restore_branding_files(merged)
            return merged
        except Exception as e:
            logger.error(f"Error getting platform branding: {e}")
            return default

    async def set_platform_branding(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Persist platform branding settings."""
        current = await self.get_platform_branding()
        merged = {**current, **updates}
        await self.set_system_setting("platform_branding", merged)
        return merged

    async def get_platform_config(self) -> Dict[str, Any]:
        """Return platform config (billing/email/coupons), migrating from legacy file if present."""
        try:
            data = await self.get_system_setting("platform_config", None)
            if not isinstance(data, dict):
                legacy = await self._read_legacy_file("platform_config.json")
                data = legacy if isinstance(legacy, dict) else {}
                if data:
                    await self.set_system_setting("platform_config", data)
            return data or {}
        except Exception as e:
            logger.error(f"Error getting platform config: {e}")
            return {}

    async def set_platform_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Persist platform config (billing/email/coupons)."""
        current = await self.get_platform_config()
        merged = {**current, **updates}
        await self.set_system_setting("platform_config", merged)
        return merged

    async def _restore_branding_files(self, branding: Dict[str, Any]) -> None:
        """Ensure logo/favicon files exist on disk using stored blobs, if present."""
        try:
            assets = branding.get("asset_blobs") or {}
            tasks = [
                ("company_logo_url", assets.get("company_logo")),
                ("favicon_url", assets.get("favicon")),
            ]
            for url_key, blob in tasks:
                url = branding.get(url_key)
                if not url or not blob:
                    continue
                if not str(url).startswith("/static/"):
                    continue
                # Resolve target path within static dir
                rel = url.lstrip("/")
                out_path = self._static_dir / Path(rel).relative_to("static")
                if out_path.exists():
                    continue
                out_path.parent.mkdir(parents=True, exist_ok=True)
                data_b64 = blob.get("base64_data")
                if not data_b64:
                    continue
                try:
                    content = base64.b64decode(data_b64)
                    out_path.write_bytes(content)
                except PermissionError:
                    # Cannot write to disk; leave as data-only
                    continue
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"Failed to restore branding files: {e}")

    async def store_platform_asset(self, key: str, url: str, content: bytes, mime_type: Optional[str]) -> Dict[str, Any]:
        """Persist platform asset blobs inside branding for durability."""
        branding = await self.get_platform_branding()
        assets = branding.get("asset_blobs") or {}
        assets[key] = {
            "mime_type": mime_type or "application/octet-stream",
            "base64_data": base64.b64encode(content).decode("ascii")
        }
        branding["asset_blobs"] = assets
        branding_key = "company_logo" if key == "company_logo" else "favicon" if key == "favicon" else key
        branding[f"{branding_key}_url"] = url
        await self.set_platform_branding(branding)
        return branding


# Global settings service instance
settings_service = SettingsService()
