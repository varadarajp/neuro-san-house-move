"""The House Move SQLite database: one file, owner-only, shared by the case stores."""

import hashlib
import json
import logging
import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from contextlib import contextmanager
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from coded_tools.hackathon.house_move.personal_data_vault import PersonalDataVault

logger = logging.getLogger(__name__)


class CaseDatabase:  # pylint: disable=too-few-public-methods  # base class: the stores add the methods
    """
    The database behind every session (see CaseSession), and the base of the stores that use it: CaseStore (cases,
    personal details, plan state), PendingActions (confirmation codes), DiscoveryRecords (statements, providers,
    consents) and ResumeCodes (a guest's way back to their case). Each opens the same file; this class owns the
    file, its schema, its encryption and its locks.
    The database file and its folder are owner-only (0600 and 0700), and the keys live elsewhere (KeyFolder).
    Every connection uses SQLite's secure delete, so erased data is overwritten rather than left in free pages.
    """

    DB_ENV: str = "HOUSE_MOVE_CASE_DB"
    # Repository root is three folders up: coded_tools/hackathon/house_move/case_database.py
    DEFAULT_DB: Path = Path(__file__).resolve().parents[3] / "case_data" / "house_move_cases.sqlite"
    SCHEMA: str = """
        CREATE TABLE IF NOT EXISTS cases (
            case_id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, profile_json TEXT, state_json TEXT);
        CREATE TABLE IF NOT EXISTS vault (
            case_id TEXT, field TEXT, token BLOB, PRIMARY KEY (case_id, field));
        CREATE TABLE IF NOT EXISTS transactions (
            case_id TEXT, transaction_id TEXT, date TEXT, descriptor TEXT, amount REAL, type TEXT, source TEXT);
        CREATE TABLE IF NOT EXISTS sealed_transactions (
            case_id TEXT, source TEXT, token BLOB, PRIMARY KEY (case_id, source));
        CREATE TABLE IF NOT EXISTS manual_providers (
            case_id TEXT, category TEXT, provider_name TEXT);
        CREATE TABLE IF NOT EXISTS consents (
            case_id TEXT, kind TEXT, words TEXT, at TEXT);
        CREATE TABLE IF NOT EXISTS case_state (
            owner_id TEXT, case_id TEXT, token BLOB, updated_at TEXT, PRIMARY KEY (owner_id, case_id));
        CREATE TABLE IF NOT EXISTS active_cases (
            owner_id TEXT PRIMARY KEY, case_id TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS customer_messages (
            owner_id TEXT PRIMARY KEY, text TEXT, at TEXT);
        CREATE TABLE IF NOT EXISTS pending_intake (
            owner_id TEXT PRIMARY KEY, token BLOB, at TEXT);
        CREATE TABLE IF NOT EXISTS resume_codes (
            case_id TEXT PRIMARY KEY, salt BLOB, code_hash BLOB, failures INTEGER, created_at TEXT);
        CREATE TABLE IF NOT EXISTS pending_actions (
            action_id TEXT PRIMARY KEY, owner_id TEXT, case_id TEXT, kind TEXT, code_hash TEXT, payload_json TEXT,
            created_at TEXT, expires_at TEXT);
    """

    def __init__(self, db_path: Path | None = None, vault: PersonalDataVault | None = None):
        """
        Opens (and if needed creates or upgrades) the database.
        :param db_path: SQLite file. Defaults to HOUSE_MOVE_CASE_DB, then case_data/house_move_cases.sqlite.
        :param vault: encrypts personal data. Defaults to a vault keyed from the environment or the key folder.
        """
        self._db_path: Path = db_path or Path(os.environ.get(CaseDatabase.DB_ENV) or CaseDatabase.DEFAULT_DB)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path.parent.chmod(0o700)
        self._vault: PersonalDataVault = vault or PersonalDataVault(self._db_path.with_suffix(".key"))
        with self._transaction() as connection:
            connection.executescript(CaseDatabase.SCHEMA)
            columns: set[str] = {row[1] for row in connection.execute("PRAGMA table_info(cases)")}
            if "owner_id" not in columns:
                # Cases created before ownership existed get no owner, so nobody can open them.
                connection.execute("ALTER TABLE cases ADD COLUMN owner_id TEXT")
        self._db_path.chmod(0o600)
        self._seal_plain_transactions()

    def lock_path(self, owner_id: str) -> Path:
        """
        :return: the per-owner lock file next to the database, shared by every process that uses it.
        """
        folder: Path = self._db_path.parent / "locks"
        folder.mkdir(mode=0o700, exist_ok=True)
        return folder / f"{hashlib.sha256(owner_id.encode('utf-8')).hexdigest()[:24]}.lock"

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """
        A connection whose changes are committed together at the end, or rolled back on an error.
        """
        with closing(self._connect()) as connection:
            with connection:
                yield connection

    def _reading(self) -> closing:
        """
        :return: a connection for reading, closed at the end of the with-block.
        """
        return closing(self._connect())

    def _seal_statement(
        self, connection: sqlite3.Connection, case_id: str, source: str, rows: list[dict[str, Any]]
    ) -> None:
        """
        Stores one statement's rows encrypted as a single token, numbering them, and drops any plain copy.
        """
        numbered: list[dict[str, Any]] = [
            {
                "transaction_id": f"{source[:12]}-{index:04d}",
                "date": row.get("date"),
                "descriptor": row.get("descriptor"),
                "amount": row.get("amount"),
                "type": row.get("type"),
            }
            for index, row in enumerate(rows, start=1)
        ]
        token: bytes = self._vault.encrypt(json.dumps(numbered))
        connection.execute("DELETE FROM transactions WHERE case_id = ? AND source = ?", (case_id, source))
        connection.execute("INSERT OR REPLACE INTO sealed_transactions VALUES (?, ?, ?)", (case_id, source, token))

    def _seal_plain_transactions(self) -> None:
        """
        Encrypts statement rows that earlier versions stored in plain text, then deletes the plain rows.
        """
        with self._reading() as connection:
            plain: list[tuple] = connection.execute(
                "SELECT case_id, source, date, descriptor, amount, type FROM transactions ORDER BY rowid"
            ).fetchall()
        if not plain:
            return
        statements: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for case_id, source, day, descriptor, amount, kind in plain:
            statements.setdefault((case_id, source), []).append(
                {"date": day, "descriptor": descriptor, "amount": amount, "type": kind}
            )
        with self._transaction() as connection:
            for (case_id, source), rows in statements.items():
                self._seal_statement(connection, case_id, source, rows)
            connection.execute("DELETE FROM transactions")
        with self._reading() as connection:
            # Rebuilds the file so no copy of the plain rows is left in free pages.
            connection.execute("VACUUM")
        logger.info("Encrypted %d statement rows stored in plain text by an earlier version", len(plain))

    def _connect(self) -> sqlite3.Connection:
        """
        Opens a new connection per operation: tools run on worker threads, and SQLite connections are not shared
        between threads. secure_delete makes SQLite overwrite deleted content (erased cases, consumed intake), so it
        does not linger.
        """
        connection: sqlite3.Connection = sqlite3.connect(self._db_path)
        connection.execute("PRAGMA secure_delete = ON")
        return connection

    @staticmethod
    def _now() -> str:
        """
        :return: the current UTC time in ISO format.
        """
        return datetime.now(UTC).isoformat(timespec="seconds")

    @staticmethod
    def _precise_now() -> str:
        """
        :return: the current UTC time with microseconds, for ordering a message against a code issued moments before.
        """
        return datetime.now(UTC).isoformat()
