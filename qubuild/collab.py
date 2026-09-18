"""Shared circuits: several people editing the same thing at once.

**What this is, precisely.**  A shared room holds one circuit in the database.
Everyone in the room reads it, anyone may write to it, and each client polls for
changes on a short interval.  Edits land in about a second.

**What it is not.**  It is not operational transforms or CRDTs, so two people
typing into the same gate in the same second do not merge — the second write is
rejected, not silently dropped, and that person is told to reload.  Calling this
"realtime co-editing" would be overselling it; it is a shared session with
conflict detection, which is what a classroom actually needs: an instructor
broadcasting a circuit, a pair working on one problem, a demonstrator driving
while thirty people watch.

Conflict handling is the honest part.  Every write carries the version it was
based on.  If the room has moved on, the write is refused and the caller gets
the current state back, rather than one person's work quietly overwriting
another's — the failure mode that makes naive shared editing worse than none.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import accounts as AC

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT UNIQUE NOT NULL,
    title      TEXT NOT NULL,
    cohort_id  INTEGER REFERENCES cohorts(id),
    owner_id   INTEGER REFERENCES users(id),
    circuit    TEXT NOT NULL,
    version    INTEGER NOT NULL DEFAULT 1,
    locked     INTEGER NOT NULL DEFAULT 0,
    updated_by INTEGER,
    updated    REAL NOT NULL,
    created    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS presence (
    room_id    INTEGER NOT NULL REFERENCES rooms(id),
    user_id    INTEGER NOT NULL REFERENCES users(id),
    seen       REAL NOT NULL,
    PRIMARY KEY (room_id, user_id)
);
CREATE TABLE IF NOT EXISTS room_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id    INTEGER NOT NULL REFERENCES rooms(id),
    user_id    INTEGER REFERENCES users(id),
    action     TEXT NOT NULL,
    at         REAL NOT NULL
);
"""

PRESENCE_WINDOW = 25.0          # seconds before someone counts as gone


def _con():
    con = AC.connect()          # same database, same dialect translation
    con.executescript(SCHEMA)
    return con


@dataclass
class Room:
    id: int
    code: str
    title: str
    version: int
    locked: bool
    circuit: dict
    updated: float
    updated_by: Optional[int]
    owner_id: Optional[int]


def _row_to_room(row) -> Room:
    try:
        circuit = json.loads(row["circuit"])
    except ValueError:
        circuit = {"qubits": 1, "ops": []}
    return Room(row["id"], row["code"], row["title"], row["version"],
                bool(row["locked"]), circuit, row["updated"], row["updated_by"],
                row["owner_id"])


def _code() -> str:
    """Six characters a person can read out loud without ambiguity."""
    import secrets
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # no I/O/0/1
    return "".join(secrets.choice(alphabet) for _ in range(6))


def create_room(title: str, circuit: dict, owner_id: Optional[int] = None,
                cohort_id: Optional[int] = None) -> Room:
    now = time.time()
    with _con() as con:
        for _ in range(8):                               # retry on code collision
            code = _code()
            try:
                new_id = con.insert_returning_id(
                    "INSERT INTO rooms (code, title, cohort_id, owner_id, circuit,"
                    " version, locked, updated_by, updated, created)"
                    " VALUES (?,?,?,?,?,1,0,?,?,?)",
                    (code, title.strip() or "Shared circuit", cohort_id, owner_id,
                     json.dumps(circuit), owner_id, now, now))
                break
            except Exception:                            # noqa: BLE001
                continue
        else:
            raise RuntimeError("Could not allocate a room code.")
        row = con.execute("SELECT * FROM rooms WHERE id = ?", (new_id,)).fetchone()
    return _row_to_room(row)


def get_room(code: str) -> Optional[Room]:
    with _con() as con:
        row = con.execute("SELECT * FROM rooms WHERE code = ?",
                          (code.strip().upper(),)).fetchone()
    return _row_to_room(row) if row else None


def rooms_for(cohort_id: Optional[int] = None) -> List[Room]:
    with _con() as con:
        if cohort_id is None:
            rows = con.execute("SELECT * FROM rooms ORDER BY updated DESC LIMIT 25").fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM rooms WHERE cohort_id = ? ORDER BY updated DESC LIMIT 25",
                (cohort_id,)).fetchall()
    return [_row_to_room(r) for r in rows]


def push(code: str, circuit: dict, base_version: int,
         user_id: Optional[int] = None) -> Tuple[bool, Room]:
    """Write a circuit to the room, but only if nobody else got there first.

    Returns (accepted, room).  On rejection the returned room carries the
    current state so the caller can show what actually happened instead of
    losing the edit silently.
    """
    now = time.time()
    with _con() as con:
        row = con.execute("SELECT * FROM rooms WHERE code = ?",
                          (code.strip().upper(),)).fetchone()
        if row is None:
            raise KeyError("No such room: %s" % code)
        if row["locked"] and user_id != row["owner_id"]:
            return False, _row_to_room(row)
        if row["version"] != base_version:
            return False, _row_to_room(row)
        con.execute(
            "UPDATE rooms SET circuit = ?, version = version + 1, updated_by = ?,"
            " updated = ? WHERE id = ?",
            (json.dumps(circuit), user_id, now, row["id"]))
        con.execute("INSERT INTO room_log (room_id, user_id, action, at) VALUES (?,?,?,?)",
                    (row["id"], user_id, "edit", now))
        fresh = con.execute("SELECT * FROM rooms WHERE id = ?", (row["id"],)).fetchone()
    return True, _row_to_room(fresh)


def set_lock(code: str, locked: bool, user_id: Optional[int]) -> bool:
    """Owner-only. A locked room is broadcast: everyone watches, only the owner drives."""
    with _con() as con:
        row = con.execute("SELECT * FROM rooms WHERE code = ?",
                          (code.strip().upper(),)).fetchone()
        if row is None or (row["owner_id"] is not None and row["owner_id"] != user_id):
            return False
        con.execute("UPDATE rooms SET locked = ? WHERE id = ?", (1 if locked else 0, row["id"]))
    return True


def heartbeat(code: str, user_id: int) -> None:
    now = time.time()
    with _con() as con:
        row = con.execute("SELECT id FROM rooms WHERE code = ?",
                          (code.strip().upper(),)).fetchone()
        if row is None:
            return
        con.execute(
            "INSERT INTO presence (room_id, user_id, seen) VALUES (?,?,?)"
            " ON CONFLICT(room_id, user_id) DO UPDATE SET seen = excluded.seen",
            (row["id"], user_id, now))


def who_is_here(code: str) -> List[dict]:
    cutoff = time.time() - PRESENCE_WINDOW
    with _con() as con:
        row = con.execute("SELECT id FROM rooms WHERE code = ?",
                          (code.strip().upper(),)).fetchone()
        if row is None:
            return []
        rows = con.execute(
            "SELECT u.display, u.username, p.seen FROM presence p"
            " JOIN users u ON u.id = p.user_id"
            " WHERE p.room_id = ? AND p.seen > ? ORDER BY u.display",
            (row["id"], cutoff)).fetchall()
    return [{"display": r["display"], "username": r["username"], "seen": r["seen"]}
            for r in rows]


def history(code: str, limit: int = 12) -> List[dict]:
    with _con() as con:
        row = con.execute("SELECT id FROM rooms WHERE code = ?",
                          (code.strip().upper(),)).fetchone()
        if row is None:
            return []
        rows = con.execute(
            "SELECT l.action, l.at, u.display FROM room_log l"
            " LEFT JOIN users u ON u.id = l.user_id"
            " WHERE l.room_id = ? ORDER BY l.id DESC LIMIT ?",
            (row["id"], limit)).fetchall()
    return [{"action": r["action"], "at": r["at"], "display": r["display"] or "someone"}
            for r in rows]
