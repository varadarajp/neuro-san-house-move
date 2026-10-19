"""SQLite case store for the standalone House Move app."""

import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

from coded_tools.hackathon.house_move.personal_data_vault import PersonalDataVault

logger = logging.getLogger(__name__)


class CaseStore:
    """
    Server-side store behind every session (see CaseSession). The client never holds case data: it holds a signed
    session token, and everything else lives here.
    - cases: standalone cases (IDs starting "S-") with their owner and non-personal profile.
    - vault: a standalone customer's personal data (full names, street lines, email), encrypted per field.
    - case_state: the plan state of every case, bank or standalone, per owner, encrypted as one blob.
    - active_cases: which case each signed-in owner is working on.
    - sealed_transactions and manual providers: what a standalone customer gave for discovery. Statement rows are
      encrypted, one token per imported statement; raw files are never kept. (The plain "transactions" table is
      only read to encrypt rows saved by earlier versions.)
    The database file and its folder are owner-only (0600 and 0700), and the keys live elsewhere (KeyFolder).
    - consents: the customer's own words agreeing to each use of their data.
    - customer_messages: each owner's latest chat message, captured by CustomerMessageMiddleware (not by the LLM).
    - pending_actions: approvals, imports and deletions waiting for the customer to type their confirmation code.
    - pending_intake: personal details a signed-in guest entered on the portal before their case existed, encrypted,
      until create_case moves them into the vault.
    """

    PREFIX: str = "S-"
    DB_ENV: str = "HOUSE_MOVE_CASE_DB"
    # Repository root is three folders up: coded_tools/hackathon/house_move/case_store.py
    DEFAULT_DB: Path = Path(__file__).resolve().parents[3] / "case_data" / "house_move_cases.sqlite"
    PERSONAL_FIELDS: tuple[str, ...] = ("full_names", "email", "old_address_line1", "new_address_line1")
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
        CREATE TABLE IF NOT EXISTS pending_actions (
            action_id TEXT PRIMARY KEY, owner_id TEXT, case_id TEXT, kind TEXT, code_hash TEXT, payload_json TEXT,
            created_at TEXT, expires_at TEXT);
    """
    # 16 random bytes, URL-safe: a case reference cannot be guessed, and ownership is checked anyway.
    ID_BYTES: int = 16
    PENDING_LIFETIME: timedelta = timedelta(minutes=30)

    def __init__(self, db_path: Path | None = None, vault: PersonalDataVault | None = None):
        """
        :param db_path: SQLite file. Defaults to HOUSE_MOVE_CASE_DB, then case_data/house_move_cases.sqlite.
        :param vault: encrypts personal data. Defaults to a vault keyed from the environment or a local key file.
        """
        self._db_path: Path = db_path or Path(os.environ.get(CaseStore.DB_ENV) or CaseStore.DEFAULT_DB)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path.parent.chmod(0o700)
        self._vault: PersonalDataVault = vault or PersonalDataVault(self._db_path.with_suffix(".key"))
        with closing(self._connect()) as connection, connection:
            connection.executescript(CaseStore.SCHEMA)
            columns: set[str] = {row[1] for row in connection.execute("PRAGMA table_info(cases)")}
            if "owner_id" not in columns:
                # Cases created before ownership existed get no owner, so nobody can open them.
                connection.execute("ALTER TABLE cases ADD COLUMN owner_id TEXT")
        self._db_path.chmod(0o600)
        self._seal_plain_transactions()

    @staticmethod
    def is_case(customer_id: str | None) -> bool:
        """
        :return: true when the ID belongs to a standalone case rather than a bank customer.
        """
        return bool(customer_id) and customer_id.startswith(CaseStore.PREFIX)

    def create_case(self, profile: dict[str, Any], personal: dict[str, Any], owner_id: str) -> str:
        """
        :param profile: non-personal facts, in the shape of the CRM record minus names, street lines and email.
        :param personal: any of PERSONAL_FIELDS; full_names is a list.
        :param owner_id: the signed-in owner, from the verified session token. Only they can open the case.
        :return: the new case ID, for example "S-Hk3x...".
        """
        if not owner_id:
            raise ValueError("A standalone case needs an owner")
        case_id: str = CaseStore.PREFIX + secrets.token_urlsafe(CaseStore.ID_BYTES)
        now: str = self._now()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO cases (case_id, created_at, updated_at, profile_json, state_json, owner_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (case_id, now, now, json.dumps(profile), json.dumps({}), owner_id),
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

    def personal_on_file(self, case_id: str) -> list[str]:
        """
        :return: which personal fields the vault holds for a case, without decrypting them.
        """
        with closing(self._connect()) as connection:
            rows: list[tuple] = connection.execute("SELECT field FROM vault WHERE case_id = ?", (case_id,)).fetchall()
        return sorted(row[0] for row in rows)

    def save_pending_intake(self, owner_id: str, personal: dict[str, Any]) -> None:
        """
        Holds a guest's personal details, encrypted, until their case is created. A later save replaces it.
        """
        token: bytes = self._vault.encrypt(json.dumps(personal))
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO pending_intake VALUES (?, ?, ?)", (owner_id, token, self._now())
            )

    def take_pending_intake(self, owner_id: str) -> dict[str, Any]:
        """
        :return: the owner's pending personal details (empty if none), removing them from the holding table.
        """
        with closing(self._connect()) as connection, connection:
            row: tuple | None = connection.execute(
                "SELECT token FROM pending_intake WHERE owner_id = ?", (owner_id,)
            ).fetchone()
            connection.execute("DELETE FROM pending_intake WHERE owner_id = ?", (owner_id,))
        plain: str | None = self._vault.decrypt(row[0]) if row else None
        return json.loads(plain) if plain else {}

    def pending_intake_fields(self, owner_id: str) -> list[str]:
        """
        :return: which personal fields a guest has given before their case exists.
        """
        with closing(self._connect()) as connection:
            row: tuple | None = connection.execute(
                "SELECT token FROM pending_intake WHERE owner_id = ?", (owner_id,)
            ).fetchone()
        plain: str | None = self._vault.decrypt(row[0]) if row else None
        return sorted(key for key, value in (json.loads(plain) if plain else {}).items() if value)

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
        # Kept apart from "names" (which falls back to first names for display), so a letter that needs full names
        # shows a gap until the customer has given them.
        record["full_names"] = personal.get("full_names") or []
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
        Stores parsed statement rows, encrypted as one token per statement. Rows from the same source replace any
        earlier import of it.
        :return: the number of rows stored.
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
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM transactions WHERE case_id = ? AND source = ?", (case_id, source))
            connection.execute("INSERT OR REPLACE INTO sealed_transactions VALUES (?, ?, ?)", (case_id, source, token))
        return len(rows)

    def get_transactions(self, case_id: str) -> list[dict[str, Any]]:
        """
        :return: the case's imported transactions, decrypted, oldest first.
        """
        with closing(self._connect()) as connection:
            tokens: list[tuple] = connection.execute(
                "SELECT token FROM sealed_transactions WHERE case_id = ?", (case_id,)
            ).fetchall()
        rows: list[dict[str, Any]] = []
        for (token,) in tokens:
            plain: str | None = self._vault.decrypt(token)
            if plain is not None:
                rows.extend(json.loads(plain))
        return sorted(rows, key=lambda row: row.get("date") or "")

    def _seal_plain_transactions(self) -> None:
        """
        Encrypts statement rows that earlier versions stored in plain text, then deletes the plain rows.
        """
        with closing(self._connect()) as connection:
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
        for (case_id, source), rows in statements.items():
            self.add_transactions(case_id, rows, source)
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM transactions")
        with closing(self._connect()) as connection:
            # Rebuilds the file so no copy of the plain rows is left in free pages.
            connection.execute("VACUUM")
        logger.info("Encrypted %d statement rows stored in plain text by an earlier version", len(plain))

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

    def case_owner(self, case_id: str) -> str | None:
        """
        :return: the owner of a standalone case, or None if it does not exist or has no owner.
        """
        with closing(self._connect()) as connection:
            row: tuple | None = connection.execute(
                "SELECT owner_id FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
        return row[0] if row else None

    def set_active(self, owner_id: str, case_id: str | None) -> None:
        """
        Records which case the owner is working on, or clears it with None.
        """
        with closing(self._connect()) as connection, connection:
            if case_id is None:
                connection.execute("DELETE FROM active_cases WHERE owner_id = ?", (owner_id,))
            else:
                connection.execute(
                    "INSERT OR REPLACE INTO active_cases VALUES (?, ?, ?)", (owner_id, case_id, self._now())
                )

    def get_active(self, owner_id: str) -> str | None:
        """
        :return: the case the owner was last working on, or None.
        """
        with closing(self._connect()) as connection:
            row: tuple | None = connection.execute(
                "SELECT case_id FROM active_cases WHERE owner_id = ?", (owner_id,)
            ).fetchone()
        return row[0] if row else None

    def save_case_state(self, owner_id: str, case_id: str, state: dict[str, Any]) -> None:
        """
        Stores a case's plan state for its owner, encrypted, because drafts and the outbox hold personal data.
        """
        token: bytes = self._vault.encrypt(json.dumps(state))
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO case_state VALUES (?, ?, ?, ?)", (owner_id, case_id, token, self._now())
            )

    def load_case_state(self, owner_id: str, case_id: str) -> dict[str, Any]:
        """
        :return: the owner's saved plan state for the case, or an empty dict.
        """
        with closing(self._connect()) as connection:
            row: tuple | None = connection.execute(
                "SELECT token FROM case_state WHERE owner_id = ? AND case_id = ?", (owner_id, case_id)
            ).fetchone()
        plain: str | None = self._vault.decrypt(row[0]) if row else None
        return json.loads(plain) if plain else {}

    def record_customer_message(self, owner_id: str, text: str) -> None:
        """
        Stores the owner's latest chat message, as typed by the customer. Called only by CustomerMessageMiddleware.
        """
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO customer_messages VALUES (?, ?, ?)", (owner_id, text, self._precise_now())
            )

    def latest_customer_message(self, owner_id: str) -> tuple[str, str] | None:
        """
        :return: (text, captured_at) of the owner's latest chat message, or None.
        """
        with closing(self._connect()) as connection:
            row: tuple | None = connection.execute(
                "SELECT text, at FROM customer_messages WHERE owner_id = ?", (owner_id,)
            ).fetchone()
        return (row[0], row[1]) if row else None

    def create_pending(self, owner_id: str, case_id: str, kind: str, payload: dict[str, Any]) -> tuple[str, str]:
        """
        Records an action that waits for the customer to type a one-time code. Earlier pending actions of the same
        kind for the case are replaced, so only the latest request can be confirmed.
        :return: (code, expires_at). Only a hash of the code is stored.
        """
        code: str = f"{secrets.randbelow(1_000_000):06d}"
        now: datetime = datetime.now(UTC)
        expires: str = (now + CaseStore.PENDING_LIFETIME).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "DELETE FROM pending_actions WHERE owner_id = ? AND case_id = ? AND kind = ?",
                (owner_id, case_id, kind),
            )
            connection.execute(
                "INSERT INTO pending_actions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    secrets.token_hex(8),
                    owner_id,
                    case_id,
                    kind,
                    self._hash(code),
                    json.dumps(payload),
                    now.isoformat(),
                    expires,
                ),
            )
        return code, expires

    def match_pending(self, owner_id: str, case_id: str, message: str, message_at: str) -> dict[str, Any] | None:
        """
        Finds the pending action whose code appears in the customer's message, if that message was typed after the
        code was issued and before it expired. Codes are matched as whole 6-digit numbers.
        :return: {"action_id", "kind", "payload", "created_at"} or None.
        """
        typed: set[str] = {self._hash(code) for code in re.findall(r"(?<!\d)\d{6}(?!\d)", message or "")}
        if not typed:
            return None
        with closing(self._connect()) as connection:
            rows: list[tuple] = connection.execute(
                "SELECT action_id, kind, code_hash, payload_json, created_at, expires_at FROM pending_actions "
                "WHERE owner_id = ? AND case_id = ?",
                (owner_id, case_id),
            ).fetchall()
        for action_id, kind, code_hash, payload, created_at, expires_at in rows:
            if code_hash in typed and created_at < message_at <= expires_at:
                return {"action_id": action_id, "kind": kind, "payload": json.loads(payload), "created_at": created_at}
        return None

    def list_pending(self, owner_id: str, case_id: str) -> list[dict[str, Any]]:
        """
        :return: unexpired pending actions for the case, without their codes: {"kind", "created_at", "expires_at",
                 "payload"}.
        """
        now: str = self._precise_now()
        with closing(self._connect()) as connection:
            rows: list[tuple] = connection.execute(
                "SELECT kind, created_at, expires_at, payload_json FROM pending_actions "
                "WHERE owner_id = ? AND case_id = ? AND expires_at > ? ORDER BY created_at",
                (owner_id, case_id, now),
            ).fetchall()
        return [
            {"kind": kind, "created_at": created, "expires_at": expires, "payload": json.loads(payload)}
            for kind, created, expires, payload in rows
        ]

    def lock_path(self, owner_id: str) -> Path:
        """
        :return: the per-owner lock file next to the database, shared by every process that uses this store.
        """
        folder: Path = self._db_path.parent / "locks"
        folder.mkdir(mode=0o700, exist_ok=True)
        return folder / f"{hashlib.sha256(owner_id.encode('utf-8')).hexdigest()[:24]}.lock"

    def consume_pending(self, action_id: str) -> None:
        """
        Deletes a pending action once it has been carried out, so a code works only once.
        """
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM pending_actions WHERE action_id = ?", (action_id,))

    def delete_case(self, case_id: str) -> bool:
        """
        Erases everything held for a case (right to erasure). The audit file keeps only IDs and events.
        :return: true if the case existed.
        """
        with closing(self._connect()) as connection, connection:
            existed: int = connection.execute("DELETE FROM cases WHERE case_id = ?", (case_id,)).rowcount
            for table in (
                "vault",
                "transactions",
                "sealed_transactions",
                "manual_providers",
                "consents",
                "case_state",
                "active_cases",
                "pending_actions",
            ):
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
        between threads. secure_delete makes SQLite overwrite deleted content (erased cases, consumed intake), so it
        does not linger.
        """
        connection: sqlite3.Connection = sqlite3.connect(self._db_path)
        connection.execute("PRAGMA secure_delete = ON")
        return connection

    @staticmethod
    def _precise_now() -> str:
        """
        :return: the current UTC time with microseconds, for ordering a message against a code issued moments before.
        """
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _hash(code: str) -> str:
        """
        :return: the SHA-256 of a confirmation code.
        """
        return hashlib.sha256(code.encode("ascii")).hexdigest()

    @staticmethod
    def _now() -> str:
        """
        :return: the current UTC time in ISO format.
        """
        return datetime.now(UTC).isoformat(timespec="seconds")
