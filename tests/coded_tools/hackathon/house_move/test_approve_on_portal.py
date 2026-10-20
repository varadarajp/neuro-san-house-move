import fcntl

from coded_tools.hackathon.house_move.approve_on_portal import ApproveOnPortal
from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.case_session import CaseSession
from coded_tools.hackathon.house_move.case_status import CaseStatus
from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.send_notification import SendNotification
from coded_tools.hackathon.house_move.session_tokens import SessionTokens
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase
from tests.coded_tools.hackathon.house_move.house_move_test_state import HouseMoveTestState


class TestApproveOnPortal(HouseMoveTestCase):
    """
    Unit tests for approving on the portal: only the wording the customer opened, legal requirements one at a time,
    only the owner's own case, and the owner's case locked against the agent server's process.
    """

    NO_FILE: AuditLog = AuditLog(None)

    def setUp(self):
        """
        A fully drafted plan for C-10042, opened through the real session path.
        """
        super().setUp()
        self.token: str = HouseMoveTestState.drafted_session()
        self.awaiting: list = CaseStatus().build(self.token).get("awaiting_approval")

    def opened(self, legal: bool) -> dict:
        """
        :return: one draft awaiting approval, as the portal shows it (with its draft_hash).
        """
        line: dict = next(t for t in self.awaiting if t.get("legal_requirement") == legal)
        return CaseStatus().draft(self.token, line.get("task_id"))

    def approve(self, items: list, token: str | None = None):
        """
        Approves through the session-wrapped entry point, as the portal does.
        """
        sly_data: dict = {"session_token": token or self.token}
        return ApproveOnPortal(audit_log=self.NO_FILE).invoke({"approve": items}, sly_data)

    def test_approves_the_opened_wording_and_it_can_be_sent(self):
        """
        An approval for the wording the customer opened is saved, recorded in the activity, and lets that draft be
        sent; nothing else is approved.
        """
        draft: dict = self.opened(legal=False)
        result = self.approve([{"task_id": draft.get("task_id"), "draft_hash": draft.get("draft_hash")}])
        self.assertEqual({"approved": [draft.get("task_id")], "not_applied": []}, result)

        status: dict = CaseStatus().build(self.token)
        self.assertEqual(len(self.awaiting) - 1, len(status.get("awaiting_approval")))
        self.assertEqual("Approved", status.get("activity")[0].get("event"))
        sent = SendNotification(audit_log=self.NO_FILE).invoke({"task_ids": ["all"]}, {"session_token": self.token})
        went: list = sent.get("sent_to_organization") + sent.get("packs_for_customer")
        self.assertEqual([draft.get("task_id")], [item.get("task_id") for item in went])

    def test_changed_wording_is_not_approved(self):
        """
        A draft_hash that is not the current wording's (rewritten since it was opened, or made up) approves nothing.
        """
        draft: dict = self.opened(legal=False)
        result = self.approve([{"task_id": draft.get("task_id"), "draft_hash": "0" * 64}])
        self.assertEqual([], result.get("approved"))
        self.assertIn("changed since you opened it", result.get("not_applied")[0])
        self.assertEqual(len(self.awaiting), len(CaseStatus().build(self.token).get("awaiting_approval")))

    def test_legal_requirements_one_at_a_time(self):
        """
        A legal-requirement draft is refused as part of a batch and approved on its own.
        """
        legal: dict = self.opened(legal=True)
        other: dict = self.opened(legal=False)
        items: list = [{"task_id": d.get("task_id"), "draft_hash": d.get("draft_hash")} for d in (legal, other)]
        batch = self.approve(items)
        self.assertEqual([other.get("task_id")], batch.get("approved"))
        self.assertIn("one at a time", batch.get("not_applied")[0])
        self.assertEqual([legal.get("task_id")], self.approve(items[:1]).get("approved"))

    def test_only_the_owner_can_approve(self):
        """
        A forged token, or another customer's session, approves nothing in C-10042's case.
        """
        draft: dict = self.opened(legal=False)
        item: dict = {"task_id": draft.get("task_id"), "draft_hash": draft.get("draft_hash")}
        self.assertTrue(self.approve([item], token="forged").startswith("Error: not signed in"))
        other: str = SessionTokens().issue("bank:C-20001", ["C-20001"])
        self.assertTrue(self.approve([item], token=other).startswith("Error: no case is open"))
        self.assertEqual(len(self.awaiting), len(CaseStatus().build(self.token).get("awaiting_approval")))

    def test_owner_lock_is_shared_with_other_processes(self):
        """
        While another process holds the owner's file lock, a session cannot take it; once released, it can.
        """
        session: CaseSession = CaseSession()
        session.verify({"session_token": self.token})
        with CaseStore().lock_path(HouseMoveTestState.OWNER).open("a", encoding="utf-8") as other_process:
            fcntl.flock(other_process, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(session.try_lock())
        self.assertTrue(session.try_lock())
        session.close({})
