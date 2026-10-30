"""Dual-Backend Zero-Cost Persistence for Rowing Coach Agent.

Implements Architectural Blueprint v0.2:
  - Local development: SQLite database (./local_state.db)
  - Cloud Run / Agent Engine: Google Cloud Firestore (Native Mode) Free Tier
    (1 GiB storage, 50,000 reads/day, 20,000 writes/day = $0.00/mo fixed cost floor)
  - Resilient fallback: Falls back to SQLite if Firestore is unavailable or unconfigured.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

SQLITE_PATH = os.environ.get("ROWING_COACH_SQLITE_PATH", "./local_state.db")


class PersistenceStore:
    """Unified storage interface abstracting SQLite and Firestore."""

    def __init__(self, mode: Optional[str] = None):
        self.mode = mode or os.environ.get("PERSISTENCE_MODE", "auto").lower()
        self._firestore_client = None
        self._init_backend()

    def _init_backend(self) -> None:
        if self.mode in ("firestore", "auto") and os.environ.get("APP_URL"):
            try:
                from google.cloud import firestore
                self._firestore_client = firestore.Client()
                self.mode = "firestore"
                log.info("PersistenceStore: Connected to Google Cloud Firestore (Native Mode).")
                return
            except Exception as e:
                log.warning("PersistenceStore: Firestore init failed (%s); falling back to SQLite.", e)

        self.mode = "sqlite"
        self._init_sqlite()
        log.info("PersistenceStore: Using local SQLite database at %s.", SQLITE_PATH)

    def _init_sqlite(self) -> None:
        conn = sqlite3.connect(SQLITE_PATH)
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS coach_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL
                )
            """)
        conn.close()

    def set(self, key: str, value: Dict[str, Any], ttl_seconds: int = 86400) -> None:
        """Stores a JSON-serializable dictionary with TTL (default 24 hours)."""
        now = time.time()
        expires_at = now + ttl_seconds
        val_str = json.dumps(value)

        if self.mode == "firestore" and self._firestore_client:
            try:
                doc_ref = self._firestore_client.collection("rowing_coach_state").document(key)
                doc_ref.set({
                    "data": value,
                    "updated_at": now,
                    "expires_at": expires_at,
                })
                return
            except Exception as e:
                log.warning("Firestore write failed (%s); falling back to SQLite.", e)

        # SQLite fallback
        conn = sqlite3.connect(SQLITE_PATH)
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO coach_state (key, value, updated_at, expires_at) VALUES (?, ?, ?, ?)",
                (key, val_str, now, expires_at),
            )
        conn.close()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Retrieves stored JSON dictionary if not expired."""
        now = time.time()

        if self.mode == "firestore" and self._firestore_client:
            try:
                doc = self._firestore_client.collection("rowing_coach_state").document(key).get()
                if doc.exists:
                    d = doc.to_dict()
                    if d.get("expires_at", 0) > now:
                        return d.get("data")
                    return None
            except Exception as e:
                log.warning("Firestore read failed (%s); falling back to SQLite.", e)

        # SQLite fallback
        conn = sqlite3.connect(SQLITE_PATH)
        row = conn.execute(
            "SELECT value, expires_at FROM coach_state WHERE key = ?", (key,)
        ).fetchone()
        conn.close()

        if row:
            val_str, expires_at = row
            if expires_at and expires_at < now:
                return None
            try:
                return json.loads(val_str)
            except Exception:
                return None
        return None
