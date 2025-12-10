"""
Secrets Resolution Service
--------------------------
Resolves secret URIs used in activity configs:
 - secret://ENV/VAR_NAME -> os.environ[VAR_NAME]
 - secret://SYSTEM/key   -> settings_service.get_system_setting(key)
Otherwise returns the input value.
"""
import os
from typing import Any

from services.settings_service import settings_service


async def resolve_secret(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if not value.startswith('secret://'):
        return value
    try:
        scheme, rest = value.split('://', 1)
        parts = rest.split('/', 1)
        if len(parts) != 2:
            return value
        provider, key = parts
        if provider.upper() == 'ENV':
            return os.getenv(key)
        if provider.upper() == 'SYSTEM':
            return await settings_service.get_system_setting(key, None)
        return value
    except Exception:
        return value

