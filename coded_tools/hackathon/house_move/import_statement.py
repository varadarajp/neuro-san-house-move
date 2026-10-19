"""Imports an uploaded bank statement into a standalone case, then deletes the file."""

import hashlib
import logging
import os
from pathlib import Path
from typing import Any

from coded_tools.hackathon.house_move.case_session import CaseSession
from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.case_tool import CaseTool
from coded_tools.hackathon.house_move.statement_parser import StatementParser

logger = logging.getLogger(__name__)


class ImportStatement(CaseTool):
    """
    Imports a statement the customer uploaded (CSV, OFX or QFX) to their own uploads folder, in two steps: run()
    checks the file and issues a confirmation code; once the customer types it, confirm_with_code calls complete(),
    which stores the transactions, records the customer's own message as consent and deletes the file. Only counts and
    the date range go back to the LLM; the Discovery agent reads the transactions through get_transactions.
    """

    UPLOAD_ENV: str = "HOUSE_MOVE_UPLOAD_DIR"
    # Repository root is three folders up: coded_tools/hackathon/house_move/import_statement.py
    DEFAULT_UPLOAD_DIR: Path = Path(__file__).resolve().parents[3] / "uploads"
    MAX_BYTES: int = 5 * 1024 * 1024

    @staticmethod
    def owner_folder(owner_id: str) -> str:
        """
        :return: the uploads sub-folder for one signed-in owner. Each person's uploads are kept apart, so a file name
                 alone never reaches someone else's statement. Shared with the upload page.
        """
        return hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:24]

    def __init__(self, case_store: CaseStore | None = None, upload_dir: Path | None = None):
        """
        :param case_store: where transactions are stored. Defaults to the SQLite case store.
        :param upload_dir: where the app places uploaded files. Defaults to HOUSE_MOVE_UPLOAD_DIR, then uploads/.
        """
        self._case_store: CaseStore | None = case_store
        self._upload_dir: Path = (
            upload_dir or Path(os.environ.get(ImportStatement.UPLOAD_ENV) or ImportStatement.DEFAULT_UPLOAD_DIR)
        ).resolve()
        self._parser: StatementParser = StatementParser()

    KIND: str = "import_statement"

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        Step one: checks the uploaded file can be read and issues a confirmation code. Nothing is imported until the
        customer types the code (confirm_with_code then calls complete()).
        :param args: optional "file_name" (or sly_data["uploaded_files"]).
        :param sly_data: reads "customer_id" (a standalone case), the session owner and optional "uploaded_files".
        :return: {"file_name", "transactions_found", "from", "to", "confirmation_code", "expires_at",
                 "instructions"}, or an "Error: ..." string.
        """
        case_id: str | None = sly_data.get("customer_id")
        if not CaseStore.is_case(case_id):
            return "Error: statement import is only for standalone cases. Create the case first."
        owner: str | None = sly_data.get(CaseSession.OWNER_KEY)
        if not owner:
            return "Error: not signed in. Sign in on the House Move portal first."
        file_name: str | None = args.get("file_name") or next(iter(sly_data.get("uploaded_files") or []), None)
        if not file_name:
            return "Error: no uploaded file. Ask the customer to upload a CSV or OFX statement."
        rows: list[dict[str, Any]] | str = self._read(owner, file_name)
        if isinstance(rows, str):
            return rows

        code, expires = (self._case_store or CaseStore()).create_pending(
            owner, case_id, ImportStatement.KIND, {"file_name": file_name}
        )
        return {
            "file_name": file_name,
            "transactions_found": len(rows),
            "from": rows[0].get("date"),
            "to": rows[-1].get("date"),
            "confirmation_code": code,
            "expires_at": expires,
            "instructions": "Nothing has been read into the case yet. Tell the customer the statement will be read "
            "once to find who they pay and then deleted, and ask them to reply with this code if they "
            "agree. Never type or confirm the code yourself.",
        }

    def complete(self, owner: str, case_id: str, file_name: str, consent_message: str) -> dict[str, Any] | str:
        """
        Step two, called by confirm_with_code: imports the file, records the customer's own message as consent and
        deletes the upload.
        :return: {"file_name", "transactions_imported", "from", "to", "file_deleted"}, or an "Error: ..." string.
        """
        rows: list[dict[str, Any]] | str = self._read(owner, file_name)
        if isinstance(rows, str):
            return rows
        store: CaseStore = self._case_store or CaseStore()
        count: int = store.add_transactions(case_id, rows, source=f"statement:{file_name}")
        store.record_consent(case_id, "statement_import", consent_message)
        self._path(owner, file_name).unlink()
        logger.info("Imported %d transactions for %s and deleted the upload", count, case_id)
        return {
            "file_name": file_name,
            "transactions_imported": count,
            "from": rows[0].get("date"),
            "to": rows[-1].get("date"),
            "file_deleted": True,
        }

    def _path(self, owner: str, file_name: str) -> Path:
        """
        :return: the resolved path of a file in the owner's uploads folder.
        """
        owner_dir: Path = (self._upload_dir / ImportStatement.owner_folder(owner)).resolve()
        return (owner_dir / file_name).resolve()

    def _read(self, owner: str, file_name: str) -> list[dict[str, Any]] | str:
        """
        Finds the file in the owner's own folder and parses it.
        :return: the rows, or an "Error: ..." string.
        """
        path: Path = self._path(owner, file_name)
        if path.parent != (self._upload_dir / ImportStatement.owner_folder(owner)).resolve():
            return "Error: the file name must be a file you uploaded."
        if not path.is_file():
            return f"Error: {file_name} has not been uploaded."
        if path.stat().st_size > ImportStatement.MAX_BYTES:
            return "Error: the statement is larger than 5 MB."
        try:
            return self._parser.parse(path.read_text(encoding="utf-8-sig"), file_name)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            logger.warning("Could not read %s: %s", file_name, error)
            return f"Error: the statement could not be read: {error}"
