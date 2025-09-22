"""
Pure asyncpg database connection and utilities for high-performance async operations
"""
import os
import asyncpg
from typing import Any, Dict, List, Optional, Union
from dotenv import load_dotenv
import logging

load_dotenv()

logger = logging.getLogger(__name__)

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is required")

# Global connection pool
_pool: Optional[asyncpg.Pool] = None

async def create_connection_pool():
    """Create database connection pool"""
    global _pool
    try:
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=10,
            command_timeout=60
        )
    except Exception as e:
        logger.error(f"Failed to create connection pool: {e}")
        raise

async def close_connection_pool():
    """Close database connection pool"""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None

async def get_pool() -> asyncpg.Pool:
    """Get database connection pool"""
    global _pool
    if _pool is None:
        await create_connection_pool()
    return _pool

# Convenience functions
async def connect_database():
    """Connect to the database (alias for create_connection_pool)"""
    await create_connection_pool()

async def disconnect_database():
    """Disconnect from the database (alias for close_connection_pool)"""
    await close_connection_pool()

# SQL execution helpers
async def fetch_one(query: str, *args) -> Optional[Dict]:
    """Execute query and fetch one record"""
    pool = await get_pool()
    async with pool.acquire() as connection:
        row = await connection.fetchrow(query, *args)
        return dict(row) if row else None

async def fetch_all(query: str, *args) -> List[Dict]:
    """Execute query and fetch all records"""
    pool = await get_pool()
    async with pool.acquire() as connection:
        rows = await connection.fetch(query, *args)
        return [dict(row) for row in rows]

async def execute(query: str, *args) -> str:
    """Execute query without returning results"""
    pool = await get_pool()
    async with pool.acquire() as connection:
        return await connection.execute(query, *args)

async def execute_returning(query: str, *args) -> Dict:
    """Execute query and return the inserted/updated record"""
    pool = await get_pool()
    async with pool.acquire() as connection:
        row = await connection.fetchrow(query, *args)
        return dict(row) if row else {}

async def execute_transaction(queries: List[tuple]) -> List[Any]:
    """Execute multiple queries in a transaction"""
    pool = await get_pool()
    async with pool.acquire() as connection:
        async with connection.transaction():
            results = []
            for query, args in queries:
                if 'RETURNING' in query.upper():
                    result = await connection.fetchrow(query, *args)
                    results.append(dict(result) if result else None)
                else:
                    result = await connection.execute(query, *args)
                    results.append(result)
            return results

async def fetch_one_dict(query: str, params: Dict[str, Any]) -> Optional[Dict]:
    """Execute query with named parameters (:name) and fetch one record.

    Safely converts named params to positional ($1, $2, ...) while ignoring
    PostgreSQL type casts like ::int (avoids matching ':int').
    """
    import re

    pattern = re.compile(r'(?<!:):([A-Za-z_][A-Za-z0-9_]*)')
    # Unique names in order of first occurrence
    seen = []
    for m in pattern.finditer(query):
        name = m.group(1)
        if name not in seen:
            seen.append(name)

    converted_query = query
    for i, name in enumerate(seen, 1):
        converted_query = re.sub(rf'(?<!:):{name}\b', f'${i}', converted_query)

    args = [params.get(name) for name in seen]
    return await fetch_one(converted_query, *args)

async def fetch_all_dict(query: str, params: Dict[str, Any]) -> List[Dict]:
    """Execute query with named parameters (:name) and fetch all records.

    Ignores type casts like ::int when converting to positional parameters.
    """
    import re

    pattern = re.compile(r'(?<!:):([A-Za-z_][A-Za-z0-9_]*)')
    seen = []
    for m in pattern.finditer(query):
        name = m.group(1)
        if name not in seen:
            seen.append(name)

    converted_query = query
    for i, name in enumerate(seen, 1):
        converted_query = re.sub(rf'(?<!:):{name}\b', f'${i}', converted_query)

    args = [params.get(name) for name in seen]
    return await fetch_all(converted_query, *args)

async def execute_dict(query: str, params: Dict[str, Any]) -> str:
    """Execute query with named parameters (:name) without returning results.

    Ignores type casts like ::int when converting to positional parameters.
    """
    import re

    pattern = re.compile(r'(?<!:):([A-Za-z_][A-Za-z0-9_]*)')
    seen = []
    for m in pattern.finditer(query):
        name = m.group(1)
        if name not in seen:
            seen.append(name)

    converted_query = query
    for i, name in enumerate(seen, 1):
        converted_query = re.sub(rf'(?<!:):{name}\b', f'${i}', converted_query)

    args = [params.get(name) for name in seen]
    return await execute(converted_query, *args)

async def test_connection():
    """Test database connection"""
    try:
        result = await fetch_one("SELECT 1 as test")
        return result.get('test') == 1 if result else False
    except Exception as e:
        logger.error(f"Database connection test failed: {e}")
        return False
