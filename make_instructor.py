"""Promote an account to instructor, or reset a password.

Only the FIRST account created on a fresh installation becomes the instructor —
which is right for a single laptop, and useless the moment a second teacher
needs access or the first one forgets the password that was shown once at
setup.  This script is the missing administrative door, run from the same
machine that holds the database, which is the only place it should ever work.

    python make_instructor.py --list
    python make_instructor.py sai_prasanna
    python make_instructor.py sai_prasanna --demote
    python make_instructor.py instructor --set-password

Close QuBuild before running it: SQLite tolerates a second writer, but the
running app holds its own copy of your session in memory and would write it
back over the change.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qubuild import accounts as AC        # noqa: E402


def rows():
    with AC.connect() as con:
        return con.execute(
            "SELECT u.id, u.username, u.display, u.role, c.name AS cohort"
            " FROM users u LEFT JOIN cohorts c ON c.id = u.cohort_id"
            " ORDER BY u.id").fetchall()


def show():
    found = rows()
    if not found:
        print("No accounts yet. Start QuBuild and create one — the first account "
              "on a fresh install becomes the instructor automatically.")
        return
    print("%-4s %-18s %-18s %-11s %s" % ("id", "username", "name", "role", "cohort"))
    print("-" * 66)
    for r in found:
        print("%-4s %-18s %-18s %-11s %s"
              % (r["id"], r["username"], r["display"], r["role"], r["cohort"] or "—"))


def set_role(username: str, role: str) -> int:
    username = username.strip().lower()
    with AC.connect() as con:
        row = con.execute("SELECT id, username, role FROM users WHERE username = ?",
                          (username,)).fetchone()
        if row is None:
            print("No account called %r. Run --list to see what exists." % username)
            return 1
        if row["role"] == role:
            print("%s is already %s — nothing to do." % (username, role))
            return 0
        con.execute("UPDATE users SET role = ? WHERE id = ?", (role, row["id"]))
    print("%s is now %s." % (username, role))
    print("Restart QuBuild and sign in again — the role is read at sign-in.")
    return 0


def reset_password(username: str) -> int:
    username = username.strip().lower()
    with AC.connect() as con:
        row = con.execute("SELECT id FROM users WHERE username = ?",
                          (username,)).fetchone()
    if row is None:
        print("No account called %r." % username)
        return 1
    first = getpass.getpass("New password for %s: " % username)
    if len(first) < 8:
        print("Use at least 8 characters.")
        return 1
    if first != getpass.getpass("Confirm: "):
        print("The two passwords do not match.")
        return 1
    AC.set_password(row["id"], first)
    print("Password changed. It is stored as a salted scrypt hash, not as text.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("username", nargs="?", help="the account to change")
    ap.add_argument("--list", action="store_true", help="show every account and role")
    ap.add_argument("--demote", action="store_true", help="make this account a learner")
    ap.add_argument("--set-password", action="store_true",
                    help="set a new password for this account")
    args = ap.parse_args()

    if args.list or not args.username:
        show()
        if not args.username:
            print("\nGive a username to promote it, e.g.:  "
                  "python make_instructor.py <username>")
        return 0

    if args.set_password:
        return reset_password(args.username)
    return set_role(args.username, "learner" if args.demote else "instructor")


if __name__ == "__main__":
    raise SystemExit(main())
