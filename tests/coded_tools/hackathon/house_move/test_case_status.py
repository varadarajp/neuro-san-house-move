import json

from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.case_status import CaseStatus
from coded_tools.hackathon.house_move.confirm_with_code import ConfirmWithCode
from coded_tools.hackathon.house_move.list_tasks import ListTasks
from coded_tools.hackathon.house_move.pending_actions import PendingActions
from coded_tools.hackathon.house_move.request_approval import RequestApproval
from coded_tools.hackathon.house_move.send_notification import SendNotification
from coded_tools.hackathon.house_move.session_tokens import SessionTokens
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase
from tests.coded_tools.hackathon.house_move.house_move_test_state import HouseMoveTestState


class TestCaseStatus(HouseMoveTestCase):
    """
    Unit tests for the portal's "My move" status: stages, pending approvals and confirmations, task groups, activity,
    and that it shows only the owner's own case without codes or street addresses.
    """

    NO_FILE: AuditLog = AuditLog(None)

    def test_invalid_token_and_no_case(self):
        """
        No status without a valid session; a signed-in owner with no open case gets a prompt to start.
        """
        self.assertIsNone(CaseStatus().build("forged"))
        status = CaseStatus().build(SessionTokens().issue("guest:nobody", []))
        self.assertIsNone(status.get("case"))
        self.assertIn("No case is open", status.get("hint"))

    def test_drafted_case_waits_for_approval(self):
        """
        A fully drafted plan is at "Drafts ready", lists every draft awaiting approval with legal ones flagged, and
        shows first names and towns but no street address.
        """
        token: str = HouseMoveTestState.drafted_session()
        status = CaseStatus().build(token)
        self.assertEqual("Drafts ready", status.get("stage"))
        self.assertEqual(2, status.get("stage_index"))
        awaiting: list = status.get("awaiting_approval")
        self.assertEqual(len(ListTasks().invoke({}, {"session_token": token}).get("tasks")), len(awaiting))
        self.assertTrue(any(line.get("legal_requirement") for line in awaiting))
        self.assertEqual(["Sam", "Alex"], status.get("case").get("first_names"))
        self.assertEqual("Harrogate, HG1 1SY", status.get("case").get("to"))
        text: str = json.dumps(status)
        for private in ("Mayfield", "Wharf", "Morgan", "example.com"):
            self.assertNotIn(private, text)

    def test_pending_confirmation_shown_without_its_code(self):
        """
        A requested approval appears as waiting for the customer's code, listing what it covers, but never the code.
        """
        token: str = HouseMoveTestState.drafted_session()
        request = RequestApproval().invoke({"approve": ["all"]}, {"session_token": token})
        status = CaseStatus().build(token)
        pending: list = status.get("pending_confirmations")
        self.assertEqual(["Approval of drafts"], [item.get("what") for item in pending])
        self.assertEqual(len(request.get("will_approve")), len(pending[0].get("items")))
        self.assertNotIn(request.get("confirmation_code"), json.dumps(status))

    def test_progress_through_sent_with_activity(self):
        """
        After the customer confirms and drafts are sent, the stage moves on and activity lists what happened.
        """
        token: str = HouseMoveTestState.drafted_session()
        sly: dict = {"session_token": token}
        waiting: list = [t.get("task_id") for t in ListTasks().invoke({}, sly).get("tasks")]
        code: str = RequestApproval().invoke({"approve": waiting}, sly).get("confirmation_code")
        PendingActions().record_customer_message(HouseMoveTestState.OWNER, f"yes {code}")
        ConfirmWithCode(audit_log=self.NO_FILE).invoke({}, sly)
        self.assertEqual("Approved", CaseStatus().build(token).get("stage"))

        SendNotification(audit_log=self.NO_FILE).invoke({"task_ids": ["all"]}, sly)
        status = CaseStatus().build(token)
        self.assertIn(status.get("stage"), ("Approved", "Sent"))
        self.assertEqual([], status.get("awaiting_approval"))
        self.assertIn("Sent", [entry.get("event") for entry in status.get("activity")])
        self.assertNotIn(code, json.dumps(status))

    def test_stage_rules(self):
        """
        The stage is the earliest step anything is still waiting at.
        """
        stage = CaseStatus._stage  # pylint: disable=protected-access
        self.assertEqual("Started", stage([]))
        self.assertEqual("Plan ready", stage([{"status": "planned"}]))
        self.assertEqual("Drafts ready", stage([{"status": "planned", "draft_id": "D-1"}, {"status": "sent"}]))
        self.assertEqual("Follow-up", stage([{"status": "sent"}, {"status": "needs_action"}]))
        self.assertEqual("Sent", stage([{"status": "sent"}, {"status": "confirmed"}]))
        self.assertEqual("Done", stage([{"status": "confirmed"}, {"status": "skipped"}]))

    def test_other_owners_see_nothing(self):
        """
        Another customer's session shows no case, even though C-10042's case is open.
        """
        HouseMoveTestState.drafted_session()
        other = CaseStatus().build(SessionTokens().issue("bank:C-20001", ["C-20001"]))
        self.assertIsNone(other.get("case"))
