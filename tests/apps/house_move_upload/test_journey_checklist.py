from apps.house_move_upload.journey_checklist import JourneyChecklist
from coded_tools.hackathon.house_move.case_store import CaseStore
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase


class StubStatus:  # pylint: disable=too-few-public-methods
    """
    A CaseStatus stand-in returning a fixed status.
    """

    def __init__(self, status: dict):
        """
        :param status: what build() returns.
        """
        self.status: dict = status

    def build(self, token: str) -> dict:  # pylint: disable=unused-argument  # same signature as CaseStatus
        """
        :return: the fixed status, whatever the token.
        """
        return self.status


class TestJourneyChecklist(HouseMoveTestCase):
    """
    Unit tests for the Home tab's "Your steps".
    """

    BANK: dict = {"sub": "bank:C-10042", "customers": ["C-10042"]}

    def next_step(self, status: dict, claims: dict | None = None) -> str:
        """
        :return: the title of the step marked next.
        """
        steps: list = JourneyChecklist(CaseStore(), StubStatus(status)).steps("t", claims or self.BANK)
        return next(step.get("title") for step in steps if step.get("next"))

    def test_each_stage_points_at_the_right_next_step(self):
        """
        No case: tell the assistant. Plan built but nothing drafted: get the letters drafted. Drafts waiting:
        approve. Approved: send. A provider asked for something: follow up.
        """
        case: dict = {"reference": "C-10042"}
        self.assertEqual("Tell the assistant about your move", self.next_step({"case": None}))
        planned: dict = {
            "case": case,
            "stage": "Plan ready",
            "stage_index": 1,
            "groups": [{"status": "planned", "tasks": [{}]}],
        }
        self.assertEqual("Get your letters drafted", self.next_step(planned))
        waiting: dict = {
            "case": case,
            "stage": "Drafts ready",
            "stage_index": 2,
            "awaiting_approval": [{}],
            "groups": [{"status": "awaiting_approval", "tasks": [{}]}],
        }
        self.assertEqual("Approve your letters", self.next_step(waiting))
        approved: dict = {
            "case": case,
            "stage": "Approved",
            "stage_index": 3,
            "groups": [{"status": "approved", "tasks": [{}]}],
        }
        self.assertEqual("Send them", self.next_step(approved))
        asked: dict = {
            "case": case,
            "stage": "Follow-up",
            "stage_index": 5,
            "groups": [{"status": "sent", "tasks": [{}]}, {"status": "needs_action", "tasks": [{}]}],
        }
        self.assertEqual("Follow up on replies", self.next_step(asked))

    def test_a_guest_adds_details_first(self):
        """
        A guest with no case starts on Details (names, addresses and move date) before anything else.
        """
        self.assertEqual("Start your move", self.next_step({"case": None}, {"sub": "guest:x", "customers": []}))
