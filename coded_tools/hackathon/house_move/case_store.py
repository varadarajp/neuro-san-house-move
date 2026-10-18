"""SQLite case store for the standalone House Move app."""

import json
import logging
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from coded_tools.hackathon.house_move.personal_data_vault import PersonalDataVault

logger = logging.getLogger(__name__)


class CaseStore:
    """
    Persists standalone cases (IDs starting "S-"), so a customer who is not a bank customer can start, leave and
    resume a move. Four kinds of data are kept apart:
    - cases: the non-personal profile (first names, postcodes, dates, facts) and a snapshot of the plan state.
    - vault: personal data (full names, street lines, email), encrypted per field by PersonalDataVault.
    - transactions and manual providers: what the customer gave for discovery. Raw statement files are never kept.
    - consents: the customer's own words agreeing to each use of their data.
    Bank-customer cases (C-...) never touch this store.
    """

    PREFIX: str = "S-"
    DB_ENV: str = "HOUSE_MOVE_CASE_DB"
    # Repository root is three folders up: coded_tools/hackathon/house_move/case_store.py
    DEFAULT_DB: Path = Path(__file__).resolve().parents[3] / "case_data" / "house_move_cases.sqlite"
    # sly_data keys that make up a case's plan state. Personal data is not among them.
    STATE_KEYS: tuple[str, ...] = (
        "councils",
        "obligations",
        "tasks",
        "previous_tasks",
        "plan_dates",
        "drafts",
        "outbox",
        "audit_log",
        "processed_replies",
    )
    PERSONAL_FIELDS: tuple[str, ...] = ("full_names", "email", "old_address_line1", "new_address_line1")
    SCHEMA: str = """
        CREATE TABLE IF NOT EXISTS cases (
            case_id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, profile_json TEXT, state_json TEXT);
        CREATE TABLE IF NOT EXISTS vault (
            case_id TEXT, field TEXT, token BLOB, PRIMARY KEY (case_id, field));
        CREATE TABLE IF NOT EXISTS transactions (
            case_id TEXT, transaction_id TEXT, date TEXT, descriptor TEXT, amount REAL, type TEXT, source TEXT);
        CREATE TABLE IF NOT EXISTS manual_providers (
            case_id TEXT, category TEXT, provider_name TEXT);
        CREATE TABLE IF NOT EXISTS consents (
            case_id TEXT, kind TEXT, words TEXT, at TEXT);
    """

    def __init__(self, db_path: Path | None = None, vault: PersonalDataVault | None = None):
        """
        :param db_path: SQLite file. Defaults to HOUSE_MOVE_CASE_DB, then case_data/house_move_cases.sqlite.
        :param vault: encrypts personal data. Defaults to a vault keyed from the environment or a local key file.
        """
        self._db_path: Path = db_path or Path(os.environ.get(CaseStore.DB_ENV) or CaseStore.DEFAULT_DB)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._vault: PersonalDataVault = vault or PersonalDataVault(self._db_path.with_suffix(".key"))
        with closing(self._connect()) as connection, connection:
            connection.executescript(CaseStore.SCHEMA)

    @staticmethod
    def is_case(customer_id: str | None) -> bool:
        """
        :return: true when the ID belongs to a standalone case rather than a bank customer.
        """
        return bool(customer_id) and customer_id.startswith(CaseStore.PREFIX)

    def create_case(self, profile: dict[str, Any], personal: dict[str, Any]) -> str:
        """
        :param profile: non-personal facts, in the shape of the CRM record minus names, street lines and email.
        :param personal: any of PERSONAL_FIELDS; full_names is a list.
        :return: the new case ID, for example "S-3F9A1C".
        """
        case_id: str = CaseStore.PREFIX + secrets.token_hex(3).upper()
        now: str = self._now()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO cases VALUES (?, ?, ?, ?, ?)", (case_id, now, now, json.dumps(profile), json.dumps({}))
            )
        self.save_personal(case_id, personal)
        logger.info("Created standalone case %s", case_id)
        return case_id

    def save_personal(self, case_id: str, personal: dict[str, Any]) -> list[str]:
        """
        Encrypts and stores the given personal fields, replacing earlier values.
        :return: the field names stored.
        """
        stored: list[str] = []
        with closing(self._connect()) as connection, connection:
            for field in CaseStore.PERSONAL_FIELDS:
                value: Any = personal.get(field)
                if value:
                    token: bytes = self._vault.encrypt(json.dumps(value))
                    connection.execute("INSERT OR REPLACE INTO vault VALUES (?, ?, ?)", (case_id, field, token))
                    stored.append(field)
        return stored

    def get_customer(self, case_id: str) -> dict[str, Any] | None:
        """
        :return: the full record, personal data decrypted, in the same shape as a CRM customer record,
                 or None if the case does not exist. Only code sees this; tools redact it for the LLM.
        """
        with closing(self._connect()) as connection:
            row: tuple | None = connection.execute(
                "SELECT profile_json FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            vault_rows: list[tuple] = connection.execute(
                "SELECT field, token FROM vault WHERE case_id = ?", (case_id,)
            ).fetchall()
        if row is None:
            return None
        record: dict[str, Any] = json.loads(row[0])
        personal: dict[str, Any] = {}
        for field, token in vault_rows:
            plain: str | None = self._vault.decrypt(token)
            if plain is not None:
                personal[field] = json.loads(plain)
        record["customer_id"] = case_id
        record["names"] = personal.get("full_names") or record.get("first_names") or []
        record["email"] = personal.get("email", "")
        record.setdefault("old_address", {})["line1"] = personal.get("old_address_line1")
        record.setdefault("new_address", {})["line1"] = personal.get("new_address_line1")
        return record

    def update_profile(self, case_id: str, updates: dict[str, Any]) -> None:
        """
        Merges non-personal updates (for example new facts or dates) into the stored profile.
        """
        record: dict[str, Any] = self._profile(case_id)
        record.update(updates)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE cases SET profile_json = ?, updated_at = ? WHERE case_id = ?",
                (json.dumps(record), self._now(), case_id),
            )

    def add_transactions(self, case_id: str, rows: list[dict[str, Any]], source: str) -> int:
        """
        Stores parsed statement rows. Rows from the same source replace any earlier import of it.
        :return: the number of rows stored.
        """
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM transactions WHERE case_id = ? AND source = ?", (case_id, source))
            connection.executemany(
                "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        case_id,
                        f"{source[:12]}-{index:04d}",
                        row.get("date"),
                        row.get("descriptor"),
                        row.get("amount"),
                        row.get("type"),
                        source,
                    )
                    for index, row in enumerate(rows, start=1)
                ],
            )
        return len(rows)

    def get_transactions(self, case_id: str) -> list[dict[str, Any]]:
        """
        :return: the case's imported transactions, oldest first.
        """
        with closing(self._connect()) as connection:
            rows: list[tuple] = connection.execute(
                "SELECT transaction_id, date, descriptor, amount, type FROM transactions "
                "WHERE case_id = ? ORDER BY date",
                (case_id,),
            ).fetchall()
        return [
            {"transaction_id": tid, "date": day, "descriptor": descriptor, "amount": amount, "type": kind}
            for tid, day, descriptor, amount, kind in rows
        ]

    def add_manual_providers(self, case_id: str, providers: list[dict[str, Any]]) -> int:
        """
        Adds providers the customer named, skipping duplicates.
        :return: how many providers the case now has.
        """
        existing: set[tuple] = {
            (p.get("category"), p.get("provider_name")) for p in self.get_manual_providers(case_id)
        }
        new_rows: list[tuple] = [
            (case_id, p.get("category"), p.get("provider_name"))
            for p in providers
            if (p.get("category"), p.get("provider_name")) not in existing and p.get("provider_name")
        ]
        with closing(self._connect()) as connection, connection:
            connection.executemany("INSERT INTO manual_providers VALUES (?, ?, ?)", new_rows)
        return len(existing) + len(new_rows)

    def get_manual_providers(self, case_id: str) -> list[dict[str, Any]]:
        """
        :return: the providers the customer named, as {"category", "provider_name"}.
        """
        with closing(self._connect()) as connection:
            rows: list[tuple] = connection.execute(
                "SELECT category, provider_name FROM manual_providers WHERE case_id = ?", (case_id,)
            ).fetchall()
        return [{"category": category, "provider_name": name} for category, name in rows]

    def record_consent(self, case_id: str, kind: str, words: str) -> None:
        """
        Keeps the customer's own words agreeing to a use of their data, for example "statement_import".
        """
        with closing(self._connect()) as connection, connection:
            connection.execute("INSERT INTO consents VALUES (?, ?, ?, ?)", (case_id, kind, words, self._now()))

    def get_consents(self, case_id: str) -> list[dict[str, Any]]:
        """
        :return: the case's consents, oldest first.
        """
        with closing(self._connect()) as connection:
            rows: list[tuple] = connection.execute(
                "SELECT kind, words, at FROM consents WHERE case_id = ? ORDER BY at", (case_id,)
            ).fetchall()
        return [{"kind": kind, "words": words, "at": at} for kind, words, at in rows]

    @classmethod
    def snapshot(cls, sly_data: dict[str, Any]) -> bool:
        """
        Saves the plan state if sly_data belongs to a standalone case. For bank customers it returns at once without
        opening the database, so the tools that change the plan can call it unconditionally.
        :return: true if a snapshot was written.
        """
        if not cls.is_case(sly_data.get("customer_id")):
            return False
        return cls().save_state(sly_data)

    def save_state(self, sly_data: dict[str, Any]) -> bool:
        """
        Snapshots the plan state from sly_data for a standalone case, so the case can be resumed in a new session.
        Called by the tools that change the plan. Does nothing for bank customers.
        :return: true if a snapshot was written.
        """
        case_id: str | None = sly_data.get("customer_id")
        if not CaseStore.is_case(case_id):
            return False
        state: dict[str, Any] = {key: sly_data.get(key) for key in CaseStore.STATE_KEYS if key in sly_data}
        with closing(self._connect()) as connection, connection:
            updated: int = connection.execute(
                "UPDATE cases SET state_json = ?, updated_at = ? WHERE case_id = ?",
                (json.dumps(state), self._now(), case_id),
            ).rowcount
        return updated == 1

    def load_state(self, case_id: str) -> dict[str, Any]:
        """
        :return: the last plan-state snapshot, or an empty dict.
        """
        with closing(self._connect()) as connection:
            row: tuple | None = connection.execute(
                "SELECT state_json FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
        return json.loads(row[0]) if row and row[0] else {}

    def delete_case(self, case_id: str) -> bool:
        """
        Erases everything held for a case (right to erasure). The audit file keeps only IDs and events.
        :return: true if the case existed.
        """
        with closing(self._connect()) as connection, connection:
            existed: int = connection.execute("DELETE FROM cases WHERE case_id = ?", (case_id,)).rowcount
            for table in ("vault", "transactions", "manual_providers", "consents"):
                connection.execute(f"DELETE FROM {table} WHERE case_id = ?", (case_id,))  # noqa: S608 - fixed names
        logger.info("Deleted standalone case %s", case_id)
        return existed == 1

    def purge_moved_before(self, cutoff_iso_date: str) -> list[str]:
        """
        Retention: deletes every case whose completion date is before the cutoff, for example 90 days ago.
        :return: the deleted case IDs.
        """
        with closing(self._connect()) as connection:
            rows: list[tuple] = connection.execute("SELECT case_id, profile_json FROM cases").fetchall()
        expired: list[str] = [
            case_id
            for case_id, profile in rows
            if (json.loads(profile).get("completion_date") or "9999") < cutoff_iso_date
        ]
        for case_id in expired:
            self.delete_case(case_id)
        return expired

    def _profile(self, case_id: str) -> dict[str, Any]:
        """
        :return: the stored non-personal profile. Raises KeyError for an unknown case.
        """
        with closing(self._connect()) as connection:
            row: tuple | None = connection.execute(
                "SELECT profile_json FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"No standalone case {case_id}")
        return json.loads(row[0])

    def _connect(self) -> sqlite3.Connection:
        """
        Opens a new connection per operation: tools run on worker threads, and SQLite connections are not shared
        between threads.
        """
        return sqlite3.connect(self._db_path)

    @staticmethod
    def _now() -> str:
        """
        :return: the current UTC time in ISO format.
        """
        return datetime.now(UTC).isoformat(timespec="seconds")
