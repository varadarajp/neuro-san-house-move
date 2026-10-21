"""Cases, their owners, personal details and plan state, for the House Move app."""

import json
import logging
import secrets
from typing import Any

from coded_tools.hackathon.house_move.case_database import CaseDatabase

logger = logging.getLogger(__name__)


class CaseStore(CaseDatabase):
    """
    Server-side store behind every session (see CaseSession). The client never holds case data: it holds a signed
    session token, and everything else lives here.
    - cases: standalone cases (IDs starting "S-") with their owner and non-personal profile.
    - vault: a standalone customer's personal data (full names, street lines, email), encrypted per field.
    - pending_intake: personal details a signed-in guest entered on the portal before their case existed, encrypted,
      until create_case moves them into the vault.
    - case_state: the plan state of every case, bank or standalone, per owner, encrypted as one blob.
    - active_cases: which case each signed-in owner is working on.
    Confirmation codes are in PendingActions; statements, providers and consents in DiscoveryRecords; the database
    itself in CaseDatabase. Erasure (delete_case, forget_owner) covers every table.
    """

    PREFIX: str = "S-"
    PERSONAL_FIELDS: tuple[str, ...] = ("full_names", "email", "old_address_line1", "new_address_line1")
    # 16 random bytes, URL-safe: a case reference cannot be guessed, and ownership is checked anyway.
    ID_BYTES: int = 16
    # Every table that holds something about one case, for erasure.
    CASE_TABLES: tuple[str, ...] = (
        "vault",
        "transactions",
        "sealed_transactions",
        "manual_providers",
        "consents",
        "case_state",
        "active_cases",
        "pending_actions",
        "resume_codes",
    )
    OWNER_TABLES: tuple[str, ...] = (
        "case_state",
        "active_cases",
        "customer_messages",
        "pending_actions",
        "pending_intake",
    )

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
        with self._transaction() as connection:
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
        with self._transaction() as connection:
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
        with self._reading() as connection:
            rows: list[tuple] = connection.execute("SELECT field FROM vault WHERE case_id = ?", (case_id,)).fetchall()
        return sorted(row[0] for row in rows)

    def save_pending_intake(self, owner_id: str, personal: dict[str, Any]) -> None:
        """
        Holds a guest's personal details, encrypted, until their case is created. A later save replaces it.
        """
        token: bytes = self._vault.encrypt(json.dumps(personal))
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO pending_intake VALUES (?, ?, ?)", (owner_id, token, self._now())
            )

    def take_pending_intake(self, owner_id: str) -> dict[str, Any]:
        """
        :return: the owner's pending personal details (empty if none), removing them from the holding table.
        """
        with self._transaction() as connection:
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
        with self._reading() as connection:
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
        with self._reading() as connection:
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
        with self._transaction() as connection:
            connection.execute(
                "UPDATE cases SET profile_json = ?, updated_at = ? WHERE case_id = ?",
                (json.dumps(record), self._now(), case_id),
            )

    def case_owner(self, case_id: str) -> str | None:
        """
        :return: the owner of a standalone case, or None if it does not exist or has no owner.
        """
        with self._reading() as connection:
            row: tuple | None = connection.execute(
                "SELECT owner_id FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
        return row[0] if row else None

    def set_active(self, owner_id: str, case_id: str | None) -> None:
        """
        Records which case the owner is working on, or clears it with None.
        """
        with self._transaction() as connection:
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
        with self._reading() as connection:
            row: tuple | None = connection.execute(
                "SELECT case_id FROM active_cases WHERE owner_id = ?", (owner_id,)
            ).fetchone()
        return row[0] if row else None

    def save_case_state(self, owner_id: str, case_id: str, state: dict[str, Any]) -> None:
        """
        Stores a case's plan state for its owner, encrypted as one blob.
        """
        token: bytes = self._vault.encrypt(json.dumps(state))
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO case_state VALUES (?, ?, ?, ?)", (owner_id, case_id, token, self._now())
            )

    def load_case_state(self, owner_id: str, case_id: str) -> dict[str, Any]:
        """
        :return: the owner's saved plan state for the case, or an empty dict.
        """
        with self._reading() as connection:
            row: tuple | None = connection.execute(
                "SELECT token FROM case_state WHERE owner_id = ? AND case_id = ?", (owner_id, case_id)
            ).fetchone()
        plain: str | None = self._vault.decrypt(row[0]) if row else None
        return json.loads(plain) if plain else {}

    def forget_owner(self, owner_id: str) -> int:
        """
        Removes everything held for one signed-in owner, so their journey starts again: the standalone cases they
        created (deleted in full), their saved plan state, active case, captured chat message, waiting codes and
        held details. Other owners are untouched.
        :return: the number of standalone cases deleted.
        """
        with self._reading() as connection:
            owned: list[str] = [
                row[0] for row in connection.execute("SELECT case_id FROM cases WHERE owner_id = ?", (owner_id,))
            ]
        for case_id in owned:
            self.delete_case(case_id)
        with self._transaction() as connection:
            for table in CaseStore.OWNER_TABLES:
                connection.execute(f"DELETE FROM {table} WHERE owner_id = ?", (owner_id,))  # noqa: S608 - fixed names
        logger.info("Forgot owner %s and %d standalone cases", owner_id, len(owned))
        return len(owned)

    def delete_case(self, case_id: str) -> bool:
        """
        Erases everything held for a case (right to erasure). The audit file keeps only IDs and events.
        :return: true if the case existed.
        """
        with self._transaction() as connection:
            existed: int = connection.execute("DELETE FROM cases WHERE case_id = ?", (case_id,)).rowcount
            for table in CaseStore.CASE_TABLES:
                connection.execute(f"DELETE FROM {table} WHERE case_id = ?", (case_id,))  # noqa: S608 - fixed names
        logger.info("Deleted standalone case %s", case_id)
        return existed == 1

    def purge_moved_before(self, cutoff_iso_date: str) -> list[str]:
        """
        Retention: deletes every case whose completion date is before the cutoff, for example 90 days ago.
        :return: the deleted case IDs.
        """
        with self._reading() as connection:
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
        with self._reading() as connection:
            row: tuple | None = connection.execute(
                "SELECT profile_json FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"No standalone case {case_id}")
        return json.loads(row[0])
