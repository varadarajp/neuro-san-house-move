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

    def test_steps_list_the_whole_journey_with_the_current_one_marked(self):
        """
        The next step carries the whole journey for the Move tab's overview: short stable titles, each marked done,
        current or upcoming, with exactly one current step.
        """
        waiting: dict = {
            "case": {"reference": "C-10042"},
            "stage_index": 2,
            "awaiting_approval": [{}, {}],
            "groups": [{"status": "awaiting_approval", "tasks": [{}, {}]}],
        }
        steps: list = self.next_step(waiting).get("steps")
        self.assertEqual(
            [
                "Sign in",
                "Tell us you're moving",
                "Get your dated plan",
                "Draft the letters",
                "Approve the letters",
                "Send the approved letters",
                "Send your own letters",
                "Replies and follow-ups",
            ],
            [s.get("title") for s in steps],
        )
        current: list = [s for s in steps if s.get("current")]
        self.assertEqual(["Approve the letters"], [s.get("title") for s in current])
        self.assertEqual(
            ["Sign in", "Tell us you're moving", "Get your dated plan", "Draft the letters"],
            [s.get("title") for s in steps if s.get("done")],
        )

    def test_each_stage_leads_to_the_right_action(self):
        """
        No case: tell the assistant. Plan built but nothing drafted: prepare the letters. Drafts waiting: the review
        sheet. Approved: send. A provider asked for something: answer it, with what they asked for.
        """
        case: dict = {"reference": "C-10042"}
        first: dict = self.next_step({"case": None})
        self.assertEqual(("tell", {"type": "say", "say": "start"}), (first.get("key"), first.get("action")))
        self.assertEqual({"done": 1, "total": 8}, first.get("progress"))
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
            ("Approve your 2 letters", {"type": "sheet", "sheet": "approve_all"}),
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

    def test_an_uploaded_statement_waits_for_the_code(self):
        """
        Issue #7: after a guest uploads a statement, the next step asks for the assistant's code instead of offering
        the upload again. It has no button, because only the customer's own message can confirm.
        """
        guest: dict = {"sub": "guest:x", "customers": []}
        status: dict = {
            "case": {"reference": "S-none"},
            "pending_confirmations": [{"kind": "import_statement", "what": "Reading your uploaded statement"}],
        }
        confirm: dict = self.next_step(status, guest)
        self.assertEqual(
            ("confirm_statement", None, None), (confirm.get("key"), confirm.get("action"), confirm.get("secondary"))
        )
        self.assertIn("code", confirm.get("hint"))

    def test_letters_waiting_come_first_even_with_one_undrafted(self):
        """
        Issue #13: with letters waiting, the next step is to approve them all (or review one by one), even if a task
        still has no letter; the hint says so. With none waiting and one left undrafted, drafting comes back.
        """
        status: dict = {
            "case": {"reference": "C-10042"},
            "stage_index": 3,
            "awaiting_approval": [{"task_id": "T-01"}, {"task_id": "T-02"}],
            "groups": [
                {"status": "awaiting_approval", "tasks": [{}, {}]},
                {"status": "planned", "tasks": [{}]},
            ],
        }
        step: dict = self.next_step(status)
        self.assertEqual(
            ("review", {"type": "sheet", "sheet": "approve_all"}, "Approve all letters"),
            (step.get("key"), step.get("action"), step.get("label")),
        )
        self.assertEqual({"type": "sheet", "sheet": "review", "label": "Review one by one"}, step.get("secondary"))
        self.assertIn("1 task has no letter yet", step.get("hint"))
        leftover: dict = {
            **status,
            "awaiting_approval": [],
            "groups": [{"status": "approved", "tasks": [{}, {}]}, {"status": "planned", "tasks": [{}]}],
        }
        self.assertEqual("draft", self.next_step(leftover).get("key"))

    def test_packs_to_send_yourself_come_before_replies(self):
        """
        Issue #16: once letters have gone out, any pre-filled packs the customer still has to send are the next step,
        opening the packs sheet; with none left, the journey moves on to replies.
        """
        status: dict = {
            "case": {"reference": "C-10042"},
            "stage_index": 4,
            "groups": [{"status": "sent", "tasks": [{}]}, {"status": "with_customer", "tasks": [{}, {}]}],
        }
        step: dict = self.next_step(status)
        self.assertEqual(
            ("packs", "Send 2 letters yourself", {"type": "sheet", "sheet": "packs"}),
            (step.get("key"), step.get("title"), step.get("action")),
        )
        sent: dict = {**status, "groups": [{"status": "sent", "tasks": [{}, {}, {}]}]}
        self.assertEqual("follow", self.next_step(sent).get("key"))
