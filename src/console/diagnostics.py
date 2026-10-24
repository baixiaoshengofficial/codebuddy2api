"""Persist metadata about upstream attempts without storing credentials or payloads."""

import logging
import time
from contextlib import contextmanager

from ..request_log_manager import build_response_preview

logger = logging.getLogger(__name__)


class DiagnosticsRepository:
    def __init__(self, connect):
        self.connect = connect

    @contextmanager
    def connection(self):
        with self.connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS upstream_attempts (
                id INTEGER PRIMARY KEY, request_record_id INTEGER NOT NULL,
                number INTEGER NOT NULL, credential TEXT, started_at INTEGER NOT NULL,
                completed_at INTEGER, status_code INTEGER, error TEXT,
                UNIQUE(request_record_id, number))"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS routing_events (
                id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL,
                previous TEXT, current TEXT, reason TEXT NOT NULL,
                request_record_id INTEGER)"""
            )
            yield db

    def start(self, record_id, number, credential):
        with self.connection() as db:
            cursor = db.execute(
                "INSERT INTO upstream_attempts(request_record_id,number,credential,started_at) VALUES(?,?,?,?)",
                (record_id, number, credential, int(time.time() * 1000)),
            )
            return cursor.lastrowid

    def finish(self, attempt_id, status_code, error=None):
        with self.connection() as db:
            db.execute(
                "UPDATE upstream_attempts SET completed_at=?,status_code=?,error=? WHERE id=?",
                (
                    int(time.time() * 1000),
                    status_code,
                    build_response_preview(error),
                    attempt_id,
                ),
            )

    def switch(self, previous, current, reason, record_id=None):
        with self.connection() as db:
            db.execute(
                "INSERT INTO routing_events(created_at,previous,current,reason,request_record_id) VALUES(?,?,?,?,?)",
                (int(time.time() * 1000), previous, current, reason, record_id),
            )
            db.execute(
                "DELETE FROM routing_events WHERE id NOT IN (SELECT id FROM routing_events ORDER BY id DESC LIMIT 1000)"
            )

    def attempts(self, record_id):
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM upstream_attempts WHERE request_record_id=? ORDER BY number",
                    (record_id,),
                )
            ]

    def events(self):
        with self.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM routing_events ORDER BY id DESC LIMIT 30"
                )
            ]

    def prune(self):
        with self.connection() as db:
            db.execute(
                "DELETE FROM upstream_attempts WHERE request_record_id NOT IN (SELECT id FROM request_logs)"
            )
