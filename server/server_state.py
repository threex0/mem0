import json
import logging
import os
import threading
from copy import deepcopy
from typing import Any, Callable, Dict
from urllib.parse import urlparse

from mem0 import Memory

_state_lock = threading.RLock()
_current_config: Dict[str, Any] = {}
_memory_instance: Memory | None = None
_session_factory: Callable | None = None


def set_session_factory(factory: Callable) -> None:
    global _session_factory
    _session_factory = factory


def _load_overrides() -> Dict[str, Any]:
    try:
        if _session_factory is None:
            return {}
        from models import Settings

        session = _session_factory()
        try:
            row = session.get(Settings, "config_overrides")
            if row is None:
                return {}
            return json.loads(row.value)
        finally:
            session.close()
    except Exception:
        logging.warning("Failed to load config overrides from database", exc_info=True)
        return {}


def _save_overrides(overrides: Dict[str, Any]) -> None:
    if _session_factory is None:
        logging.warning("Cannot persist config overrides: _session_factory is None!")
        return

    try:
        from models import Settings
        from sqlalchemy.dialects.postgresql import insert

        session = _session_factory()
        try:
            serialized = json.dumps(overrides)
            stmt = (
                insert(Settings)
                .values(key="config_overrides", value=serialized)
                .on_conflict_do_update(
                    index_elements=[Settings.key],
                    set_={"value": serialized},
                )
            )
            session.execute(stmt)
            session.commit()
            logging.info("Config overrides successfully saved to PostgreSQL.")
        finally:
            session.close()
    except Exception:
        logging.warning("Failed to persist config overrides to database", exc_info=True)


def _merge_config(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)

    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_config(merged[key], value)
        else:
            merged[key] = value

    return merged

def _apply_postgres_enforcement(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enforces PostgreSQL pgvector for vector storage.
    Parses DATABASE_URL if available, or falls back to individual POSTGRES_* env vars.
    """
    db_url = os.getenv("DATABASE_URL")

    if db_url:
        parsed = urlparse(db_url)
        pg_user = parsed.username or "postgres"
        pg_pass = parsed.password or "postgres"
        pg_host = parsed.hostname or "postgres"
        pg_port = parsed.port or 5432
        pg_db = parsed.path.lstrip("/") or "mem0_app"
    else:
        pg_user = os.getenv("POSTGRES_USER", "postgres")
        pg_pass = os.getenv("POSTGRES_PASSWORD", "postgres")
        pg_host = os.getenv("POSTGRES_HOST", "postgres")
        pg_port = int(os.getenv("POSTGRES_PORT", "5432"))
        pg_db = os.getenv("POSTGRES_DB", "mem0_app")

    # 1. Force Vector Store configuration for pgvector
    config["vector_store"] = {
        "provider": "pgvector",
        "config": {
            "dbname": pg_db,
            "user": pg_user,
            "password": pg_pass,
            "host": pg_host,
            "port": pg_port,
            "collection_name": os.getenv("MEM0_COLLECTION_NAME", "memories"),
            "embedding_model_dims": int(os.getenv("EMBEDDING_DIMS", "1024")),
        },
    }

    # 2. Prevent SQLite file-system errors by explicitly forcing in-memory history
    config["history_db_path"] = ":memory:"

    return config


def initialize_state(default_config: Dict[str, Any]) -> None:
    global _current_config, _memory_instance
    with _state_lock:
        _current_config = deepcopy(default_config)
        overrides = _load_overrides()
        if overrides:
            _current_config = _merge_config(_current_config, overrides)

        # Enforce Postgres for pgvector and history tracking
        _current_config = _apply_postgres_enforcement(_current_config)

        _memory_instance = Memory.from_config(_current_config)


def update_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    global _current_config, _memory_instance
    with _state_lock:
        next_config = _merge_config(_current_config, updates)
        _current_config = next_config

        # Preserve Postgres enforcement on runtime updates
        _current_config = _apply_postgres_enforcement(_current_config)

        _memory_instance = Memory.from_config(_current_config)
        overrides = _load_overrides()
        overrides = _merge_config(overrides, updates)
        _save_overrides(overrides)
        return deepcopy(_current_config)


def get_current_config() -> Dict[str, Any]:
    with _state_lock:
        return deepcopy(_current_config)


def get_memory_instance() -> Memory:
    with _state_lock:
        if _memory_instance is None:
            raise RuntimeError("Mem0 runtime has not been initialized.")
        return _memory_instance
