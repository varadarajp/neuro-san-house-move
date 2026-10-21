from coded_tools.hackathon.house_move.approve_on_portal import ApproveOnPortal
from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.case_status import CaseStatus
from coded_tools.hackathon.house_move.mark_sent_on_portal import MarkSentOnPortal
from coded_tools.hackathon.house_move.save_drafts import SaveDrafts
from coded_tools.hackathon.house_move.send_notification import SendNotification
from coded_tools.hackathon.house_move.session_tokens import SessionTokens
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase
from tests.coded_tools.hackathon.house_move.house_move_test_state import HouseMoveTestState


class TestCustomerPacks(HouseMoveTestCase):
    """
    Unit tests for the packs a customer sends themselves (issue #16): CaseStatus.pack shows one filled in with their
    details and what to add, and MarkSentOnPortal records that they sent it.
    """

    NO_FILE: AuditLog = AuditLog(None)
    BODY: str = "Please move my account to {{new_address}} from {{move_date}}. Meter readings: {{meter_readings}}."

    def setUp(self):
        """
        A drafted plan for C-10042 in which one letter needs the customer's meter readings, approved and sent: that
        letter goes back to the customer as a pack, and a complete one goes to its organization.
        """
        super().setUp()
        self.token: str = HouseMoveTestState.drafted_session()
        self.sly: dict = {"session_token": self.token}
        waiting: list = CaseStatus().build(self.token).get("awaiting_approval")
        routine: list = [t.get("task_id") for t in waiting if not t.get("legal_requirement")]
        self.pack_id, self.complete_id = routine[0], routine[1]
        SaveDrafts().invoke(
            {"drafts": [{"task_id": self.pack_id, "subject": "Moving home", "body": TestCustomerPacks.BODY}]}, self.sly
        )
        items: list = [
            {"task_id": task_id, "draft_hash": CaseStatus().draft(self.token, task_id).get("draft_hash")}
            for task_id in (self.pack_id, self.complete_id)
        ]
        for item in items:
            ApproveOnPortal(audit_log=self.NO_FILE).invoke({"approve": [item]}, self.sly)
        SendNotification(audit_log=self.NO_FILE).invoke({"task_ids": ["all"]}, self.sly)

    def test_a_pack_is_filled_in_for_its_owner(self):
        """
        The pack shows the letter with the customer's details filled in and what they must add; a letter that went
        to its organization is not a pack; nothing filled in is saved in the case.
        """
        pack: dict = CaseStatus().pack(self.token, self.pack_id)
        self.assertIn("7 Mayfield Grove, Harrogate, HG1 1SY", pack.get("body"))
        self.assertIn("27 November 2026", pack.get("body"))
        self.assertIn("[add your meter readings]", pack.get("body"))
        self.assertEqual(["meter readings"], pack.get("to_add"))
        self.assertIsNone(CaseStatus().pack(self.token, self.complete_id))
        self.assertNotIn("Mayfield", str(CaseStatus().build(self.token)))

    def test_only_the_owner_sees_their_pack(self):
        """
        Another customer's session, or none, gets nothing.
        """
        other: str = SessionTokens().issue("bank:C-20001", ["C-20001"])
        self.assertIsNone(CaseStatus().pack(other, self.pack_id))
        self.assertIsNone(CaseStatus().pack(None, self.pack_id))

    def test_marking_a_pack_sent(self):
        """
        "I've sent it" moves the pack from waiting for the customer to sent, records it in the activity, and works
        only once and only for a pack.
        """
        groups: dict = {g.get("status"): g for g in CaseStatus().build(self.token).get("groups")}
        self.assertEqual([self.pack_id], [t.get("task_id") for t in groups.get("with_customer").get("tasks")])

        mark = MarkSentOnPortal(audit_log=self.NO_FILE)
        self.assertEqual({"task_id": self.pack_id, "status": "sent"}, mark.invoke({"task_id": self.pack_id}, self.sly))
        status: dict = CaseStatus().build(self.token)
        groups = {g.get("status"): g for g in status.get("groups")}
        self.assertNotIn("with_customer", groups)
        self.assertIn(self.pack_id, [t.get("task_id") for t in groups.get("sent").get("tasks")])
        self.assertEqual("Sent by you", status.get("activity")[0].get("event"))
        self.assertIsNone(CaseStatus().pack(self.token, self.pack_id))

        again = mark.invoke({"task_id": self.pack_id}, self.sly)
        self.assertEqual(f"Error: {self.pack_id} is not a pack waiting for you to send.", again)
        self.assertTrue(mark.invoke({"task_id": self.complete_id}, self.sly).startswith("Error:"))
