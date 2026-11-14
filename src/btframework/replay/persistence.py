from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path


def _encoded(value, encoder):
    return json.dumps(value, default=encoder, ensure_ascii=False, allow_nan=False)


class ReplayStore:
    def __init__(self, path, encode):
        self.path = Path(path)
        self.encode = encode
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS replay_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS replay_commands (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    args TEXT NOT NULL
                );
            """)
        self._secure_files()

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def _secure_files(self):
        for path in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
            if path.exists():
                path.chmod(0o600)

    def load(self):
        with self._connect() as connection:
            meta = dict(connection.execute("SELECT key, value FROM replay_meta"))
            if "definition" not in meta:
                return None, int(meta.get("revision", "0")), None
            commands = [
                {"action": action, "args": json.loads(args)}
                for action, args in connection.execute(
                    "SELECT action, args FROM replay_commands ORDER BY seq"
                )
            ]
            return {
                "version": 1,
                "definition": json.loads(meta["definition"]),
                "commands": commands,
                "journal_records": json.loads(meta.get("journal_records", "[]")),
            }, int(meta.get("revision", "0")), meta.get("receipt")

    def save(self, app, action, args):
        document = app._session_document() if app.session else None
        receipt = app._recovery_receipt() if app.session else None
        replace = action in {"create", "restore", "navigate", "resume"}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            meta = dict(connection.execute("SELECT key, value FROM replay_meta"))
            revision = int(meta.get("revision", "0")) + 1
            if replace:
                connection.execute("DELETE FROM replay_commands")
                if document:
                    connection.execute(
                        "INSERT INTO replay_meta(key, value) VALUES('definition', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (_encoded(document["definition"], self.encode),),
                    )
                    connection.execute(
                        "INSERT INTO replay_meta(key, value) VALUES('journal_records', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (_encoded(document.get("journal_records", []), self.encode),),
                    )
                    connection.executemany(
                        "INSERT INTO replay_commands(action, args) VALUES(?, ?)",
                        [(item["action"], _encoded(item["args"], self.encode)) for item in document["commands"]],
                    )
                else:
                    connection.execute("DELETE FROM replay_meta WHERE key IN ('definition', 'journal_records')")
            elif document:
                last = document["commands"][-1] if document["commands"] else None
                if last and last["action"] == action:
                    connection.execute(
                        "INSERT INTO replay_commands(action, args) VALUES(?, ?)",
                        (action, _encoded(last["args"], self.encode)),
                    )
                connection.execute(
                    "INSERT INTO replay_meta(key, value) VALUES('journal_records', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (_encoded(document.get("journal_records", []), self.encode),),
                )
            connection.execute(
                "INSERT INTO replay_meta(key, value) VALUES('revision', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(revision),),
            )
            if receipt is not None:
                connection.execute(
                    "INSERT INTO replay_meta(key, value) VALUES('receipt', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (receipt,),
                )
            else:
                connection.execute("DELETE FROM replay_meta WHERE key='receipt'")
            self._secure_files()
            connection.commit()
        return revision
