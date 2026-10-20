"""Actions that wait for the customer to type a one-time code, and the message they typed."""

import hashlib
import json
import re
import secrets
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Any

from coded_tools.hackathon.house_move.case_database import CaseDatabase


class PendingActions(CaseDatabase):
    """
    The confirmation-code side of the database:
    - pending_actions: approvals, imports and deletions waiting for the customer to type their 6-digit code. Only a
      hash of each code is kept; a code works once and expires.
    - customer_messages: each owner's latest chat message, captured by CustomerMessageMiddleware (never by the LLM),
      which is what a code is checked against.
    """

    PENDING_LIFETIME: timedelta = timedelta(minutes=30)

    def record_customer_message(self, owner_id: str, text: str) -> None:
        """
        Stores the owner's latest chat message, as typed by the customer. Called only by CustomerMessageMiddleware.
        """
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO customer_messages VALUES (?, ?, ?)", (owner_id, text, self._precise_now())
            )

    def latest_customer_message(self, owner_id: str) -> tuple[str, str] | None:
        """
        :return: (text, captured_at) of the owner's latest chat message, or None.
        """
        with self._reading() as connection:
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
        expires: str = (now + PendingActions.PENDING_LIFETIME).isoformat()
        with self._transaction() as connection:
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
        with self._reading() as connection:
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
        with self._reading() as connection:
            rows: list[tuple] = connection.execute(
                "SELECT kind, created_at, expires_at, payload_json FROM pending_actions "
                "WHERE owner_id = ? AND case_id = ? AND expires_at > ? ORDER BY created_at",
                (owner_id, case_id, self._precise_now()),
            ).fetchall()
        return [
            {"kind": kind, "created_at": created, "expires_at": expires, "payload": json.loads(payload)}
            for kind, created, expires, payload in rows
        ]

    def consume_pending(self, action_id: str) -> None:
        """
        Deletes a pending action once it has been carried out, so a code works only once.
        """
        with self._transaction() as connection:
            connection.execute("DELETE FROM pending_actions WHERE action_id = ?", (action_id,))

    @staticmethod
    def _hash(code: str) -> str:
        """
        :return: the SHA-256 of a confirmation code.
        """
        return hashlib.sha256(code.encode("ascii")).hexdigest()
