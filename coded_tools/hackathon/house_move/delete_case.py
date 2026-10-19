"""Right to erasure for standalone cases."""

from typing import Any

from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.case_session import CaseSession
from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.case_tool import CaseTool


class DeleteCase(CaseTool):
    """
    Deletes everything held for a standalone case (profile, encrypted personal data, transactions, providers,
    consents and plan) and clears it from sly_data. The audit trail keeps one entry with the case ID only.
    """

    def __init__(self, case_store: CaseStore | None = None, audit_log: AuditLog | None = None):
        """
        :param case_store: where the case lives. Defaults to the SQLite case store.
        :param audit_log: records the erasure. Defaults to the audit file under logs/.
        """
        self._case_store: CaseStore | None = case_store
        self._audit_log: AuditLog = audit_log or AuditLog()

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: "customer_words", the customer's request to delete their data, quoted.
        :param sly_data: reads "customer_id"; the case's data is cleared after deletion, the session is kept.
        :return: {"case_id", "deleted"}, or an "Error: ..." string.
        """
        case_id: str | None = sly_data.get("customer_id")
        if not CaseStore.is_case(case_id):
            return "Error: only standalone cases can be deleted here. Bank customers' data is handled by the bank."
        words: str = (args.get("customer_words") or "").strip()
        if not words:
            return "Error: quote the customer's request to delete their data. Nothing was deleted."
        deleted: bool = (self._case_store or CaseStore()).delete_case(case_id)
        self._audit_log.record({"customer_id": case_id}, "case_deleted", None, "customer asked to delete their data")
        for key in CaseSession.SERVER_KEYS:
            if key != CaseSession.OWNER_KEY:
                sly_data.pop(key, None)
        return {"case_id": case_id, "deleted": deleted}
