"""What a standalone customer gave for discovery: statements, providers they named, and their consents."""

import json
from typing import Any

from coded_tools.hackathon.house_move.case_database import CaseDatabase


class DiscoveryRecords(CaseDatabase):
    """
    The discovery side of the database:
    - sealed_transactions: statement rows, encrypted as one token per imported statement. Raw files are never kept.
    - manual_providers: providers the customer named instead of uploading a statement.
    - consents: the customer's own words agreeing to each use of their data.
    """

    def add_transactions(self, case_id: str, rows: list[dict[str, Any]], source: str) -> int:
        """
        Stores parsed statement rows, encrypted as one token per statement. Rows from the same source replace any
        earlier import of it.
        :return: the number of rows stored.
        """
        with self._transaction() as connection:
            self._seal_statement(connection, case_id, source, rows)
        return len(rows)

    def get_transactions(self, case_id: str) -> list[dict[str, Any]]:
        """
        :return: the case's imported transactions, decrypted, oldest first.
        """
        with self._reading() as connection:
            tokens: list[tuple] = connection.execute(
                "SELECT token FROM sealed_transactions WHERE case_id = ?", (case_id,)
            ).fetchall()
        rows: list[dict[str, Any]] = []
        for (token,) in tokens:
            plain: str | None = self._vault.decrypt(token)
            if plain is not None:
                rows.extend(json.loads(plain))
        return sorted(rows, key=lambda row: row.get("date") or "")

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
        with self._transaction() as connection:
            connection.executemany("INSERT INTO manual_providers VALUES (?, ?, ?)", new_rows)
        return len(existing) + len(new_rows)

    def get_manual_providers(self, case_id: str) -> list[dict[str, Any]]:
        """
        :return: the providers the customer named, as {"category", "provider_name"}.
        """
        with self._reading() as connection:
            rows: list[tuple] = connection.execute(
                "SELECT category, provider_name FROM manual_providers WHERE case_id = ?", (case_id,)
            ).fetchall()
        return [{"category": category, "provider_name": name} for category, name in rows]

    def record_consent(self, case_id: str, kind: str, words: str) -> None:
        """
        Keeps the customer's own words agreeing to a use of their data, for example "statement_import".
        """
        with self._transaction() as connection:
            connection.execute("INSERT INTO consents VALUES (?, ?, ?, ?)", (case_id, kind, words, self._now()))

    def get_consents(self, case_id: str) -> list[dict[str, Any]]:
        """
        :return: the case's consents, oldest first.
        """
        with self._reading() as connection:
            rows: list[tuple] = connection.execute(
                "SELECT kind, words, at FROM consents WHERE case_id = ? ORDER BY at", (case_id,)
            ).fetchall()
        return [{"kind": kind, "words": words, "at": at} for kind, words, at in rows]
