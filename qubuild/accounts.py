"""Accounts, cohorts and instructor assignments, on SQLite.

Progress used to live in a single ``progress.json`` next to the app, which is
fine for one learner on one laptop and useless for a classroom.  This module
replaces it with a real multi-user store: learners and instructors, cohorts
with rosters, work an instructor assigns, and per-learner progress rows.

**On passwords.**  They are stored as scrypt hashes with a per-user 16-byte
salt, never in plain text and never recoverable — a forgotten password is reset
by an instructor, not looked up.  scrypt is deliberately slow and
memory-hard, which is the point: it makes guessing expensive.  The parameters
below (N=2**14, r=8, p=1) are the interactive-login settings from RFC 7914.

This is honest classroom-grade authentication: it protects a roster on a
college server.  It is not a public internet identity system — that wants
e-mail verification, rate limiting, session expiry and TLS termination, none of
which belong in a Streamlit prototype.  The Deliverables page says so.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import db as DB

DB_PATH = os.environ.get(
    "QUBUILD_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "qubuild.db"))

SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=64)

SCHEMA = """
CREATE TABLE IF NOT EXISTS cohorts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT UNIQUE NOT NULL,
    created    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT UNIQUE NOT NULL,
    display    TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('learner', 'instructor')),
    cohort_id  INTEGER REFERENCES cohorts(id),
    salt       BLOB NOT NULL,
    pw_hash    BLOB NOT NULL,
    created    REAL NOT NULL,
    last_seen  REAL
);
CREATE TABLE IF NOT EXISTS progress (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id),
    data       TEXT NOT NULL,
    updated    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS assignments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    cohort_id  INTEGER NOT NULL REFERENCES cohorts(id),
    kind       TEXT NOT NULL CHECK (kind IN ('lesson', 'challenge')),
    item_id    TEXT NOT NULL,
    due        TEXT,
    note       TEXT,
    created_by INTEGER REFERENCES users(id),
    created    REAL NOT NULL,
    UNIQUE (cohort_id, kind, item_id)
);
"""


def connect():
    """Open the active database and make sure the schema is there.

    Which database that is depends on QUBUILD_DB_URL — SQLite by default, a
    shared Postgres when one is configured. Callers see no difference.
    """
    con = DB.connect(None if DB.is_postgres() else DB_PATH)
    con.executescript(SCHEMA)
    return con


def is_configured() -> bool:
    return os.path.exists(DB_PATH)


# --------------------------------------------------------------------------
# passwords
# --------------------------------------------------------------------------

def hash_password(password: str, salt: Optional[bytes] = None) -> Tuple[bytes, bytes]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT)
    return salt, digest


def verify_password(password: str, salt: bytes, expected: bytes) -> bool:
    _, digest = hash_password(password, salt)
    # constant-time compare: a timing difference here leaks the hash prefix
    return secrets.compare_digest(digest, expected)


# --------------------------------------------------------------------------
# users and cohorts
# --------------------------------------------------------------------------

@dataclass
class User:
    id: int
    username: str
    display: str
    role: str
    cohort_id: Optional[int]
    cohort_name: Optional[str] = None

    @property
    def is_instructor(self) -> bool:
        return self.role == "instructor"


def _row_to_user(row) -> User:
    return User(row["id"], row["username"], row["display"], row["role"],
                row["cohort_id"], row.get("cohort_name") if hasattr(row, "get") else (
                    row["cohort_name"] if "cohort_name" in row.keys() else None))


def create_cohort(name: str) -> int:
    with connect() as con:
        row = con.execute("SELECT id FROM cohorts WHERE name = ?",
                          (name.strip(),)).fetchone()
        if row:
            return int(row["id"])
        return int(con.insert_returning_id(
            "INSERT INTO cohorts (name, created) VALUES (?, ?)",
            (name.strip(), time.time())))


def cohorts() -> List[dict]:
    with connect() as con:
        return con.execute("SELECT * FROM cohorts ORDER BY name").fetchall()


def create_user(username: str, password: str, display: str = "", role: str = "learner",
                cohort: Optional[str] = None) -> Optional[User]:
    """Returns None if the username is taken."""
    username = username.strip().lower()
    if not username or not password:
        return None
    cohort_id = create_cohort(cohort) if cohort else None
    salt, digest = hash_password(password)
    try:
        with connect() as con:
            uid = con.insert_returning_id(
                "INSERT INTO users (username, display, role, cohort_id, salt, pw_hash, created)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (username, display or username, role, cohort_id, salt, digest, time.time()))
    except Exception as exc:                                       # noqa: BLE001
        # a duplicate username is the expected failure; anything else is not
        if "uniq" in str(exc).lower() or "duplicate" in str(exc).lower():
            return None
        raise
    return get_user(uid)


def get_user(user_id: int) -> Optional[User]:
    with connect() as con:
        row = con.execute(
            "SELECT u.*, c.name AS cohort_name FROM users u"
            " LEFT JOIN cohorts c ON c.id = u.cohort_id WHERE u.id = ?", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


def authenticate(username: str, password: str) -> Optional[User]:
    with connect() as con:
        row = con.execute(
            "SELECT u.*, c.name AS cohort_name FROM users u"
            " LEFT JOIN cohorts c ON c.id = u.cohort_id WHERE u.username = ?",
            (username.strip().lower(),)).fetchone()
        if row is None:
            # Hash anyway so a missing user and a wrong password take the same
            # time — otherwise the response time enumerates valid usernames.
            hash_password(password)
            return None
        if not verify_password(password, row["salt"], row["pw_hash"]):
            return None
        con.execute("UPDATE users SET last_seen = ? WHERE id = ?", (time.time(), row["id"]))
    return _row_to_user(row)


def set_password(user_id: int, password: str) -> None:
    salt, digest = hash_password(password)
    with connect() as con:
        con.execute("UPDATE users SET salt = ?, pw_hash = ? WHERE id = ?",
                    (salt, digest, user_id))


def members(cohort_id: int) -> List[User]:
    with connect() as con:
        rows = con.execute(
            "SELECT u.*, c.name AS cohort_name FROM users u"
            " LEFT JOIN cohorts c ON c.id = u.cohort_id"
            " WHERE u.cohort_id = ? ORDER BY u.display", (cohort_id,)).fetchall()
    return [_row_to_user(r) for r in rows]


def user_count() -> int:
    with connect() as con:
        return con.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


# --------------------------------------------------------------------------
# roster import
# --------------------------------------------------------------------------

def import_roster(csv_text: str, cohort: str) -> Dict[str, object]:
    """Bulk-create learners from CSV with columns username, display (optional).

    Returns a report rather than raising, because a roster with one bad row
    should still import the other thirty-nine.  Temporary passwords are
    generated here and returned once — they are not recoverable afterwards.
    """
    created, skipped, errors = [], [], []
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    if not reader.fieldnames or "username" not in [f.strip().lower() for f in reader.fieldnames]:
        return {"created": [], "skipped": [], "errors": ["CSV needs a 'username' column"]}

    for i, row in enumerate(reader, 2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        username = row.get("username", "")
        if not username:
            errors.append("row %d: blank username" % i)
            continue
        temporary = secrets.token_urlsafe(9)
        user = create_user(username, temporary, row.get("display", ""), "learner", cohort)
        if user is None:
            skipped.append(username)
        else:
            created.append({"username": username, "password": temporary})
    return {"created": created, "skipped": skipped, "errors": errors}


# --------------------------------------------------------------------------
# progress
# --------------------------------------------------------------------------

def load_progress(user_id: int) -> Optional[dict]:
    with connect() as con:
        row = con.execute("SELECT data FROM progress WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["data"])
    except ValueError:
        return None


def save_progress(user_id: int, data: dict) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO progress (user_id, data, updated) VALUES (?, ?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET data = excluded.data, updated = excluded.updated",
            (user_id, json.dumps(data), time.time()))


def cohort_progress(cohort_id: int) -> List[dict]:
    """Every learner in a cohort with their stored progress, for the instructor view."""
    out = []
    with connect() as con:
        rows = con.execute(
            "SELECT u.id, u.username, u.display, p.data, p.updated FROM users u"
            " LEFT JOIN progress p ON p.user_id = u.id"
            " WHERE u.cohort_id = ? AND u.role = 'learner' ORDER BY u.display",
            (cohort_id,)).fetchall()
    for r in rows:
        try:
            data = json.loads(r["data"]) if r["data"] else {}
        except ValueError:
            data = {}
        quiz = list(data.get("quiz", {}).values())
        right = sum(1 for q in quiz if q.get("correct"))
        out.append({
            "user_id": r["id"], "username": r["username"], "display": r["display"],
            "xp": int(data.get("xp", 0)),
            "lessons": len(data.get("lessons", {})),
            "challenges": len(data.get("challenges", {})),
            "attempted": len(quiz),
            "correct": right,
            "accuracy": (right / len(quiz)) if quiz else 0.0,
            "updated": r["updated"],
        })
    return out


# --------------------------------------------------------------------------
# assignments
# --------------------------------------------------------------------------

def assign(cohort_id: int, kind: str, item_id: str, due: str = "",
           note: str = "", by: Optional[int] = None) -> None:
    with connect() as con:
        # ON CONFLICT rather than INSERT OR REPLACE: the latter is SQLite-only,
        # and re-assigning an item should update the due date, not renumber the row.
        con.execute(
            "INSERT INTO assignments"
            " (cohort_id, kind, item_id, due, note, created_by, created)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (cohort_id, kind, item_id) DO UPDATE SET"
            " due = excluded.due, note = excluded.note,"
            " created_by = excluded.created_by, created = excluded.created",
            (cohort_id, kind, item_id, due, note, by, time.time()))


def unassign(assignment_id: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))


def assignments(cohort_id: int) -> List[dict]:
    with connect() as con:
        return con.execute(
            "SELECT * FROM assignments WHERE cohort_id = ? ORDER BY kind, item_id",
            (cohort_id,)).fetchall()


def bootstrap_demo() -> Optional[str]:
    """Create a first instructor if the database is empty.

    Returns the generated password once, so it can be shown to whoever set the
    platform up.  Does nothing if any user already exists.
    """
    if user_count() > 0:
        return None
    password = secrets.token_urlsafe(9)
    create_user("instructor", password, "Instructor", "instructor", "Demo cohort")
    return password
