"""SQLite-backed, fail-closed kill switch with an append-only transition audit."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class KillSwitchState(str, Enum):
    STOPPED = "STOPPED"
    MONITORING = "MONITORING"
    ENTRIES_PAUSED = "ENTRIES_PAUSED"
    EMERGENCY_HALTED = "EMERGENCY_HALTED"
    RUNNING = "RUNNING"


@dataclass(frozen=True)
class StateTransition:
    previous_state: KillSwitchState
    new_state: KillSwitchState
    actor: str
    reason: str
    created_at: str


class KillSwitch:
    """Persists state in SQLite. A missing/unreadable database never permits entries."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS kill_switch_state (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    state TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS kill_switch_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    previous_state TEXT NOT NULL,
                    new_state TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                )
            """)
            connection.execute(
                "INSERT OR IGNORE INTO kill_switch_state(singleton_id, state) VALUES (1, ?)",
                (KillSwitchState.STOPPED.value,),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path), timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @property
    def state(self) -> KillSwitchState:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT state FROM kill_switch_state WHERE singleton_id = 1"
                ).fetchone()
            if row is None:
                return KillSwitchState.EMERGENCY_HALTED
            return KillSwitchState(row[0])
        except (sqlite3.Error, ValueError, OSError):
            return KillSwitchState.EMERGENCY_HALTED

    @property
    def can_open_entries(self) -> bool:
        return self.state is KillSwitchState.RUNNING

    def transition(
        self,
        target: KillSwitchState,
        *,
        actor: str,
        reason: str,
        authorized: bool = False,
        confirmed: bool = False,
    ) -> StateTransition:
        if not isinstance(target, KillSwitchState):
            raise ValueError("target must be a KillSwitchState")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor must be non-empty")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be non-empty")
        if not authorized:
            raise PermissionError("authorized operator approval is required")

        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM kill_switch_state WHERE singleton_id = 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("kill switch state is missing; refusing transition")
            try:
                previous = KillSwitchState(row[0])
            except ValueError as exc:
                raise RuntimeError("persisted kill switch state is invalid") from exc
            if previous is KillSwitchState.EMERGENCY_HALTED and target is not previous and not confirmed:
                raise PermissionError("leaving emergency halt requires explicit confirmation")
            if previous is target:
                now = connection.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]
                return StateTransition(previous, target, actor.strip(), reason.strip(), now)
            connection.execute(
                "UPDATE kill_switch_state SET state = ? WHERE singleton_id = 1",
                (target.value,),
            )
            cursor = connection.execute(
                """INSERT INTO kill_switch_events(previous_state, new_state, actor, reason)
                   VALUES (?, ?, ?, ?)""",
                (previous.value, target.value, actor.strip(), reason.strip()),
            )
            created_at = connection.execute(
                "SELECT created_at FROM kill_switch_events WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()[0]
            return StateTransition(previous, target, actor.strip(), reason.strip(), created_at)

    def history(self) -> tuple[StateTransition, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT previous_state, new_state, actor, reason, created_at
                   FROM kill_switch_events ORDER BY id"""
            ).fetchall()
        return tuple(
            StateTransition(KillSwitchState(old), KillSwitchState(new), actor, reason, created_at)
            for old, new, actor, reason, created_at in rows
        )
