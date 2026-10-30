"""Unit tests for Dual-Backend PersistenceStore (Blueprint v0.2)."""

import os
import tempfile
import pytest

from backend.db import PersistenceStore


def test_sqlite_persistence_roundtrip():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        os.environ["ROWING_COACH_SQLITE_PATH"] = db_path
        store = PersistenceStore(mode="sqlite")

        key = "coach_david_preferences"
        data = {"benchmark_2k": "1:58.2", "target_watts": 211.9, "drag_factor": 128}

        store.set(key, data, ttl_seconds=3600)
        retrieved = store.get(key)

        assert retrieved is not None
        assert retrieved["benchmark_2k"] == "1:58.2"
        assert retrieved["target_watts"] == 211.9


def test_sqlite_persistence_expiry():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_exp.db")
        os.environ["ROWING_COACH_SQLITE_PATH"] = db_path
        store = PersistenceStore(mode="sqlite")

        key = "transient_state"
        store.set(key, {"temp": True}, ttl_seconds=-10)  # already expired

        assert store.get(key) is None
