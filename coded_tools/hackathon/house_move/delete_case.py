"""Right to erasure for standalone cases."""

from typing import Any

from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.case_session import CaseSession
from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.case_tool import CaseTool
from coded_tools.hackathon.house_move.pending_actions import PendingActions


class DeleteCase(CaseTool):
    """
    Deletes everything held for a standalone case (profile, encrypted personal data, transactions, providers,
    consents and plan), in two steps: run() issues a confirmation code; once the customer types it,
    confirm_with_code calls complete(). The audit trail keeps one entry with the case ID only.
    """

    def __init__(self, case_store: CaseStore | None = None, audit_log: AuditLog | None = None):
        """
        :param case_store: where the case lives. Defaults to the SQLite case store.
        :param audit_log: records the erasure. Defaults to the audit file under logs/.
        """
        self._case_store: CaseStore | None = case_store
        self._audit_log: AuditLog = audit_log or AuditLog()

    KIND: str = "delete_case"

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        Step one: issues a confirmation code. Nothing is deleted until the customer types it (confirm_with_code then
        calls complete()).
        :param args: not used.
        :param sly_data: reads "customer_id" and the session owner.
        :return: {"case_id", "confirmation_code", "expires_at", "instructions"}, or an "Error: ..." string.
        """
        case_id: str | None = sly_data.get("customer_id")
        owner: str | None = sly_data.get(CaseSession.OWNER_KEY)
        if not CaseStore.is_case(case_id) or not owner:
            return (
                "Error: only your own standalone case can be deleted here. "
                "Bank customers' data is handled by the bank."
            )
        code, expires = PendingActions().create_pending(owner, case_id, DeleteCase.KIND, {})
        return {
            "case_id": case_id,
            "confirmation_code": code,
            "expires_at": expires,
            "instructions": "Tell the customer this permanently deletes everything held for the case, and ask them "
            "to reply with this code if they are sure. Never type or confirm the code yourself.",
        }

    def complete(self, sly_data: dict[str, Any], case_id: str) -> dict[str, Any]:
        """
        Step two, called by confirm_with_code: deletes the case and clears it from sly_data, keeping the session.
        """
        deleted: bool = (self._case_store or CaseStore()).delete_case(case_id)
        self._audit_log.record({"customer_id": case_id}, "case_deleted", None, "customer confirmed deletion with code")
        for key in CaseSession.SERVER_KEYS:
            if key != CaseSession.OWNER_KEY:
                sly_data.pop(key, None)
        return {"case_id": case_id, "deleted": deleted}
