from apps.house_move_upload.journey_checklist import JourneyChecklist
from coded_tools.hackathon.house_move.discovery_records import DiscoveryRecords
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

    def next_step(self, status: dict, claims: dict | None = None) -> dict:
        """
        :return: the next step the app takes the customer to.
        """
        return JourneyChecklist(DiscoveryRecords(), StubStatus(status)).next_step("t", claims or self.BANK)

    def test_each_stage_leads_to_the_right_action(self):
        """
        No case: tell the assistant. Plan built but nothing drafted: prepare the letters. Drafts waiting: the review
        sheet. Approved: send. A provider asked for something: answer it, with what they asked for.
        """
        case: dict = {"reference": "C-10042"}
        first: dict = self.next_step({"case": None})
        self.assertEqual(("tell", {"type": "say", "say": "start"}), (first.get("key"), first.get("action")))
        self.assertEqual({"done": 1, "total": 7}, first.get("progress"))
        planned: dict = {
            "case": case,
            "stage": "Plan ready",
            "stage_index": 1,
            "groups": [{"status": "planned", "tasks": [{}]}],
        }
        self.assertEqual("draft", self.next_step(planned).get("key"))
        waiting: dict = {
            "case": case,
            "stage": "Drafts ready",
            "stage_index": 2,
            "awaiting_approval": [{}, {}],
            "groups": [{"status": "awaiting_approval", "tasks": [{}, {}]}],
        }
        review: dict = self.next_step(waiting)
        self.assertEqual(
            ("Read and approve 2 letters", {"type": "sheet", "sheet": "review"}),
            (review.get("title"), review.get("action")),
        )
        approved: dict = {
            "case": case,
            "stage": "Approved",
            "stage_index": 3,
            "groups": [{"status": "approved", "tasks": [{}]}],
        }
        self.assertEqual({"type": "send"}, self.next_step(approved).get("action"))
        asked: dict = {
            "case": case,
            "stage": "Follow-up",
            "stage_index": 5,
            "groups": [
                {"status": "sent", "tasks": [{}]},
                {
                    "status": "needs_action",
                    "tasks": [{"org": "Energy supplier", "follow_up": ["meter serial number"]}],
                },
            ],
        }
        follow: dict = self.next_step(asked)
        self.assertEqual(("follow", {"type": "reply"}), (follow.get("key"), follow.get("action")))
        self.assertEqual([{"org": "Energy supplier", "asked": "meter serial number"}], follow.get("asks"))
        done: dict = {
            "case": case,
            "stage": "Done",
            "stage_index": 6,
            "groups": [{"status": "confirmed", "tasks": [{}]}],
        }
        self.assertEqual(("done", None), (self.next_step(done).get("key"), self.next_step(done).get("action")))

    def test_a_guest_starts_on_the_form_then_shares_who_they_pay(self):
        """
        A guest with no case goes to the start form; once their case exists, to the upload sheet, with listing
        providers in the chat as the alternative.
        """
        guest: dict = {"sub": "guest:x", "customers": []}
        start: dict = self.next_step({"case": None}, guest)
        self.assertEqual(("start", {"type": "link", "target": "/start"}), (start.get("key"), start.get("action")))
        share: dict = self.next_step({"case": {"reference": "S-none"}}, guest)
        self.assertEqual(("share", {"type": "sheet", "sheet": "upload"}), (share.get("key"), share.get("action")))
        self.assertEqual("list", share.get("secondary").get("say"))
        self.assertIn(share.get("secondary").get("say"), JourneyChecklist.SAY)
