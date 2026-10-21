from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.case_status import CaseStatus
from coded_tools.hackathon.house_move.skip_on_portal import SkipOnPortal
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase
from tests.coded_tools.hackathon.house_move.house_move_test_state import HouseMoveTestState


class TestSkipOnPortal(HouseMoveTestCase):
    """
    Unit tests for skipping a letter on the portal (issue #13): only drafts waiting for approval, and a legal
    requirement only when the customer confirms it.
    """

    NO_FILE: AuditLog = AuditLog(None)

    def setUp(self):
        """
        A fully drafted plan for C-10042, opened through the real session path.
        """
        super().setUp()
        self.token: str = HouseMoveTestState.drafted_session()
        self.awaiting: list = CaseStatus().build(self.token).get("awaiting_approval")

    def skip(self, args: dict) -> dict:
        """
        Skips through the session-wrapped entry point, as the portal does.
        """
        return SkipOnPortal(audit_log=self.NO_FILE).invoke(args, {"session_token": self.token})

    def task_id(self, legal: bool) -> str:
        """
        :return: one task waiting for approval, legal or not.
        """
        return next(t.get("task_id") for t in self.awaiting if t.get("legal_requirement") == legal)

    def test_skips_a_routine_letter_and_records_it(self):
        """
        A skipped letter leaves the approval queue, shows as skipped, is in the activity, and cannot be skipped twice.
        """
        task_id: str = self.task_id(legal=False)
        self.assertEqual({"skipped": [task_id], "not_applied": []}, self.skip({"skip": [task_id]}))
        status: dict = CaseStatus().build(self.token)
        self.assertNotIn(task_id, [t.get("task_id") for t in status.get("awaiting_approval")])
        groups: dict = {g.get("status"): g for g in status.get("groups")}
        self.assertEqual([task_id], [t.get("task_id") for t in groups.get("skipped").get("tasks")])
        self.assertEqual("Skipped", status.get("activity")[0].get("event"))
        self.assertEqual([f"{task_id}: not waiting for approval"], self.skip({"skip": [task_id]}).get("not_applied"))

    def test_a_legal_requirement_needs_confirming(self):
        """
        A legal requirement is skipped only with confirm_legal, which must be exactly true.
        """
        task_id: str = self.task_id(legal=True)
        refused: dict = self.skip({"skip": [task_id], "confirm_legal": "yes"})
        self.assertEqual([f"{task_id}: a legal requirement: confirm to skip it"], refused.get("not_applied"))
        self.assertEqual([task_id], self.skip({"skip": [task_id], "confirm_legal": True}).get("skipped"))

    def test_nothing_to_skip_or_no_session(self):
        """
        An empty request and a missing session are refused.
        """
        self.assertEqual("Error: nothing to skip.", self.skip({"skip": []}))
        no_session = SkipOnPortal(audit_log=self.NO_FILE).invoke({"skip": ["T-01"]}, {})
        self.assertTrue(no_session.startswith("Error: not signed in"))
