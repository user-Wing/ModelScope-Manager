from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TransferRecord:
    transfer_id: str
    direction: str
    source: str
    destination: str
    success: bool
    message: str
    started_at: int
    completed_at: int

    @property
    def duration(self) -> int:
        return max(0, self.completed_at - self.started_at)


class TransferHistoryStore:
    def __init__(self, database: Path):
        self.database = database

    def add(self, direction: str, source: str, destination: str, success: bool,
            message: str = "", started_at: float | None = None) -> None:
        completed = int(time.time())
        started = int(started_at or completed)
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                """INSERT INTO transfer_history
                   (transfer_id, direction, source, destination, success, message, started_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (uuid.uuid4().hex, direction, source, destination, int(success), message, started, completed),
            )
            connection.commit()
        finally:
            connection.close()

    def list(self, limit: int = 5000) -> list[TransferRecord]:
        connection = sqlite3.connect(self.database)
        try:
            rows = connection.execute(
                """SELECT transfer_id, direction, source, destination, success, message, started_at, completed_at
                   FROM transfer_history ORDER BY completed_at DESC LIMIT ?""", (max(1, limit),),
            ).fetchall()
        finally:
            connection.close()
        return [TransferRecord(row[0], row[1], row[2], row[3], bool(row[4]), row[5], row[6], row[7]) for row in rows]
