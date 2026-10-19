from datetime import date

from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.bank_address_api import BankAddressApi
from coded_tools.hackathon.house_move.build_timeline import BuildTimeline
from coded_tools.hackathon.house_move.check_inbox import CheckInbox
from coded_tools.hackathon.house_move.get_audit_log import GetAuditLog
from coded_tools.hackathon.house_move.get_transactions import GetTransactions
from coded_tools.hackathon.house_move.plan_corrections import PlanCorrections
from coded_tools.hackathon.house_move.record_reply import RecordReply
from coded_tools.hackathon.house_move.save_drafts import SaveDrafts
from coded_tools.hackathon.house_move.send_notification import SendNotification
from coded_tools.hackathon.house_move.update_tasks import UpdateTasks
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase
from tests.coded_tools.hackathon.house_move.house_move_test_state import HouseMoveTestState


class TestTracker(HouseMoveTestCase):
    """
    Unit tests for the Tracker's tools: approval gate, mock send, replan corrections, replies and the audit view.
    """

    NO_FILE: AuditLog = AuditLog(None)
    TODAY: date = date(2026, 10, 17)

    def approved_and_sent(self) -> dict:
        """
        :return: drafted sly_data with every draft approved, sent, and the bank update done.
        """
        sly_data: dict = HouseMoveTestState.drafted()
        HouseMoveTestState.approve_everything(sly_data)
        SendNotification(audit_log=self.NO_FILE).run({"task_ids": ["all"]}, sly_data)
        BankAddressApi(self.NO_FILE).run({}, sly_data)
        return sly_data

    def test_send_refuses_unapproved_and_marks_sent(self):
        """
        Unapproved tasks are refused. Approved ones are sent, with drafts holding gaps going to the customer.
        """
        sly_data: dict = HouseMoveTestState.drafted()
        energy: dict = HouseMoveTestState.task_for(sly_data, "energy_supplier")
        broadband: dict = HouseMoveTestState.task_for(sly_data, "broadband")
        sender = SendNotification(audit_log=self.NO_FILE)

        refused = sender.run({"task_ids": [energy.get("task_id")]}, sly_data)
        self.assertEqual([], refused.get("sent_to_organisation") + refused.get("packs_for_customer"))
        self.assertIn("not approved", refused.get("refused")[0])

        HouseMoveTestState.approve_everything(sly_data)
        result = sender.run({"task_ids": ["all"]}, sly_data)
        to_org: list = [item.get("task_id") for item in result.get("sent_to_organisation")]
        to_customer: list = [item.get("task_id") for item in result.get("packs_for_customer")]

        self.assertEqual(17, len(to_org) + len(to_customer))
        self.assertIn(energy.get("task_id"), to_org)
        self.assertIn(broadband.get("task_id"), to_customer)
        self.assertEqual("sent", energy.get("status"))
        self.assertIn("27 November 2026", sly_data.get("outbox")[0].get("body"))

    def test_direct_debit_reference_lets_email_go_to_organisation(self):
        """
        With the mandate on file, {{account_number}} is filled, so the broadband email has no gaps and is sent to
        the provider instead of becoming a customer pack.
        """
        sly_data: dict = HouseMoveTestState.drafted()
        GetTransactions().run({}, sly_data)
        broadband: dict = HouseMoveTestState.task_for(sly_data, "broadband")
        saved = SaveDrafts().run(
            {
                "drafts": [
                    {
                        "task_id": broadband.get("task_id"),
                        "subject": "Moving",
                        "body": "Ref {{account_number}}, new address {{new_address}}.",
                    }
                ]
            },
            sly_data,
        )
        self.assertEqual([], saved.get("saved")[0].get("gaps_for_customer"))

        HouseMoveTestState.approve(sly_data, [broadband.get("task_id")])
        result = SendNotification(audit_log=self.NO_FILE).run({"task_ids": [broadband.get("task_id")]}, sly_data)
        self.assertEqual(broadband.get("task_id"), result.get("sent_to_organisation")[0].get("task_id"))
        self.assertIn("Ref FB-55120934", sly_data.get("outbox")[-1].get("body"))

    def test_only_the_bank_address_task_can_be_direct(self):
        """
        update_tasks refuses "direct" for any other task; the approved bank task is reported for bank_address_api.
        """
        sly_data: dict = HouseMoveTestState.drafted()
        tv: dict = HouseMoveTestState.task_for(sly_data, "tv_licensing")
        result = UpdateTasks().run(
            {"updates": [{"task_id": tv.get("task_id"), "action": "x", "channel": "direct"}]}, sly_data
        )
        self.assertEqual([], result.get("updated"))
        self.assertIn("only this bank's own address update can be direct", result.get("rejected")[0])

        HouseMoveTestState.approve_everything(sly_data)
        sent = SendNotification(audit_log=self.NO_FILE).run({"task_ids": ["all"]}, sly_data)
        bank: dict = HouseMoveTestState.task_for(sly_data, "bank_address")
        self.assertEqual([bank.get("task_id")], sent.get("direct_updates_pending"))

    def test_approved_task_is_never_dropped_silently(self):
        """
        A non-bank task already marked "direct" (as happened on a standalone case) is sent as a customer pack, and an
        approved task that cannot go out is reported even when sending "all".
        """
        sly_data: dict = HouseMoveTestState.drafted()
        tv: dict = HouseMoveTestState.task_for(sly_data, "tv_licensing")
        tv["channel"] = "direct"
        HouseMoveTestState.approve_everything(sly_data)
        broadband: dict = HouseMoveTestState.task_for(sly_data, "broadband")
        del sly_data.get("drafts")[broadband.get("draft_id")]

        sent = SendNotification(audit_log=self.NO_FILE).run({"task_ids": ["all"]}, sly_data)
        packs: list = [item.get("task_id") for item in sent.get("packs_for_customer")]
        self.assertIn(tv.get("task_id"), packs)
        self.assertEqual(1, len(sent.get("refused")))
        self.assertIn(broadband.get("task_id"), sent.get("refused")[0])
        self.assertIn("no saved draft", sent.get("refused")[0])

    def test_replan_keeps_progress_and_sorts_corrections(self):
        """
        After a date change, sent tasks need a correction or rebooking, unsent ones update automatically.
        """
        sly_data: dict = self.approved_and_sent()
        energy: dict = HouseMoveTestState.task_for(sly_data, "energy_supplier")
        energy["status"] = "approved"  # pretend this one was approved but not yet sent

        BuildTimeline(today=self.TODAY).run({"completion_date": "2026-12-11"}, sly_data)
        rebuilt_energy: dict = HouseMoveTestState.task_for(sly_data, "energy_supplier")
        self.assertEqual("approved", rebuilt_energy.get("status"))
        self.assertIsNotNone(rebuilt_energy.get("draft_id"))

        groups = PlanCorrections(self.NO_FILE).run({}, sly_data)
        orgs: dict = {name: [line.get("org") for line in lines] for name, lines in groups.items()}
        self.assertIn("Broadband provider", orgs.get("needs_rebooking"))
        self.assertIn("Your bank (direct update)", orgs.get("needs_rebooking"))
        self.assertIn("TV Licensing", orgs.get("needs_correction"))
        self.assertIn("Energy supplier (Brightwatt Energy)", orgs.get("updated_automatically"))
        self.assertIn("Home insurance", orgs.get("unchanged"))
        self.assertEqual("needs_action", HouseMoveTestState.task_for(sly_data, "tv_licensing").get("status"))

        HouseMoveTestState.approve_everything(sly_data)
        resent = SendNotification(audit_log=self.NO_FILE).run({"task_ids": [rebuilt_energy.get("task_id")]}, sly_data)
        self.assertEqual(1, len(resent.get("sent_to_organisation")))
        self.assertIn("11 December 2026", sly_data.get("outbox")[-1].get("body"))

    def test_inbox_and_reply_handling(self):
        """
        Replies arrive only for sent tasks, and recording one updates the task and stops it reappearing.
        """
        self.assertEqual([], CheckInbox().run({}, HouseMoveTestState.drafted()).get("replies"))

        sly_data: dict = self.approved_and_sent()
        replies: list = CheckInbox().run({}, sly_data).get("replies")
        self.assertEqual(["R-1", "R-2", "R-3"], [reply.get("reply_id") for reply in replies])

        energy_reply: dict = replies[0]
        tool = RecordReply(self.NO_FILE)
        self.assertTrue(
            tool.run(
                {"reply_id": "R-1", "task_id": energy_reply.get("task_id"), "outcome": "needs_action"}, sly_data
            ).startswith("Error:")
        )
        result = tool.run(
            {
                "reply_id": "R-1",
                "task_id": energy_reply.get("task_id"),
                "outcome": "needs_action",
                "facts": {"closing_account_number": "88412"},
                "requested_info": ["meter serial number"],
            },
            sly_data,
        )
        self.assertEqual("needs_action", result.get("status"))
        self.assertEqual(["R-2", "R-3"], [r.get("reply_id") for r in CheckInbox().run({}, sly_data).get("replies")])

    def test_replies_still_arrive_after_redraft(self):
        """
        A task sent once and then re-drafted after a date change still receives its provider's reply.
        """
        sly_data: dict = self.approved_and_sent()
        energy: dict = HouseMoveTestState.task_for(sly_data, "energy_supplier")
        energy["status"] = "awaiting_approval"
        replies: list = CheckInbox().run({}, sly_data).get("replies")
        self.assertIn("R-1", [reply.get("reply_id") for reply in replies])

    def test_audit_view(self):
        """
        Every approval, send and direct update is in the audit log, without personal data.
        """
        sly_data: dict = self.approved_and_sent()
        counts: dict = GetAuditLog().run({}, sly_data).get("counts")
        self.assertEqual({"approved": 18, "sent": 17, "direct_update": 1}, counts)
        self.assertNotIn("Mayfield", str(sly_data.get("audit_log")))
