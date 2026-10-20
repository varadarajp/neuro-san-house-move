import os
import sqlite3
import stat
from contextlib import closing
from datetime import date

from cryptography.fernet import Fernet

from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.build_timeline import BuildTimeline
from coded_tools.hackathon.house_move.case_session import CaseSession
from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.check_inbox import CheckInbox
from coded_tools.hackathon.house_move.create_case import CreateCase
from coded_tools.hackathon.house_move.discovery_records import DiscoveryRecords
from coded_tools.hackathon.house_move.draft_renderer import DraftRenderer
from coded_tools.hackathon.house_move.key_folder import KeyFolder
from coded_tools.hackathon.house_move.obligations_rules import ObligationsRules
from coded_tools.hackathon.house_move.personal_data_vault import PersonalDataVault
from coded_tools.hackathon.house_move.personal_details import PersonalDetails
from coded_tools.hackathon.house_move.save_drafts import SaveDrafts
from coded_tools.hackathon.house_move.send_notification import SendNotification
from coded_tools.hackathon.house_move.update_tasks import UpdateTasks
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase
from tests.coded_tools.hackathon.house_move.house_move_test_state import HouseMoveTestState
from tests.coded_tools.hackathon.house_move.recording_mail_gateway import RecordingMailGateway


class TestDataProtection(HouseMoveTestCase):
    """
    Third review, findings 9 to 13: personal details reach a standalone case only through the portal's form; a blank
    detail is a gap, never "filled"; canned replies reach only the demo customer, from the right provider, for
    messages that reached the organization; and data at rest is encrypted, owner-only and kept apart from its keys.
    (Upload hardening, finding 11, is tested with the portal in test_upload_server.py.)
    """

    TODAY: date = date(2026, 10, 19)
    OWNER: str = "guest:data-protection"
    NO_FILE: AuditLog = AuditLog(None)
    ARGS: dict = {
        "first_names": ["Jordan"],
        "old_postcode": "BS6 6RB",
        "new_postcode": "NG7 2NF",
        "move_date": "2026-12-11",
        "facts": {"has_driving_license": False, "has_vehicle": False},
    }
    FORM: dict = {
        "full_names": "Jordan Lee",
        "email": "jordan.lee@example.com",
        "old_address_line1": "8 Cotham Vale",
        "new_address_line1": "21 Lenton Boulevard",
    }
    BODY: str = (
        "Dear {{org_name}}, {{full_names}} moves to {{new_address}} on {{move_date}}. Reply to {{customer_email}}."
    )

    def standalone(self, details: bool) -> dict:
        """
        :param details: true if the guest filled in the portal's "Your details" form first.
        :return: sly_data for a standalone case with a dated plan and an energy task drafted as an email.
        """
        if details:
            clean, errors = PersonalDetails.check(self.FORM)
            self.assertEqual({}, errors)
            CaseStore().save_pending_intake(self.OWNER, clean)
        sly_data: dict = {CaseSession.OWNER_KEY: self.OWNER}
        created = CreateCase(council_lookup=HouseMoveTestState.offline_lookup(), today=self.TODAY).run(
            dict(self.ARGS), sly_data
        )
        self.assertTrue(created.get("case_id").startswith("S-"), created)
        providers: list = [{"category": "energy", "provider_name": "Octavia Power"}]
        ObligationsRules().run({"facts": self.ARGS.get("facts"), "providers": providers}, sly_data)
        BuildTimeline(today=self.TODAY).run({}, sly_data)
        energy: dict = HouseMoveTestState.task_for(sly_data, "energy_supplier")
        UpdateTasks().run({"updates": [{"task_id": energy.get("task_id"), "channel": "email"}]}, sly_data)
        SaveDrafts().run(
            {"drafts": [{"task_id": energy.get("task_id"), "subject": "Moving", "body": self.BODY}]}, sly_data
        )
        return sly_data

    def send_energy(self, sly_data: dict, gateway: RecordingMailGateway) -> dict:
        """
        Approves the energy draft the customer's way (a code they type) and sends it.
        """
        energy: dict = HouseMoveTestState.task_for(sly_data, "energy_supplier")
        HouseMoveTestState.approve(sly_data, [energy.get("task_id")])
        return SendNotification(audit_log=self.NO_FILE, gateway=gateway).run({"task_ids": ["all"]}, sly_data)

    # ----- 9. The secure form -----

    def test_portal_details_reach_the_vault_and_nothing_else_does(self):
        """
        Details given on the portal before the case exists are moved into its encrypted vault; details a client
        slips into sly_data as "intake_form" are discarded.
        """
        sly_data: dict = self.standalone(details=True)
        store: CaseStore = CaseStore()
        case_id: str = sly_data.get("customer_id")
        self.assertEqual(sorted(PersonalDetails.LABELS), store.personal_on_file(case_id))
        self.assertEqual([], store.pending_intake_fields(self.OWNER))
        self.assertEqual("21 Lenton Boulevard", sly_data.get("customer_profile").get("new_address").get("line1"))

        smuggled: dict = {CaseSession.OWNER_KEY: "guest:other", "intake_form": {"full_names": ["Eve Smuggler"]}}
        result = CreateCase(council_lookup=HouseMoveTestState.offline_lookup(), today=self.TODAY).run(
            dict(self.ARGS), smuggled
        )
        self.assertEqual([], store.personal_on_file(result.get("case_id")))
        self.assertIn("Your details", result.get("next"))

    def test_details_form_checks_each_field(self):
        """
        Missing surnames, a malformed email, a too-short street and placeholder braces are refused, field by field.
        """
        _, errors = PersonalDetails.check(
            {"full_names": "Jordan", "email": "jordan@", "old_address_line1": "8", "new_address_line1": "{{x}} Road"}
        )
        self.assertEqual(sorted(PersonalDetails.LABELS), sorted(errors))
        clean, errors = PersonalDetails.check({**self.FORM, "full_names": "Jordan Lee\n\n  Sam   Lee  "})
        self.assertEqual({}, errors)
        self.assertEqual(["Jordan Lee", "Sam Lee"], clean.get("full_names"))

        move, errors = PersonalDetails.check_move(
            {
                "old_postcode": "LS1",
                "old_town": "Leeds",
                "new_postcode": "hg1 1sy",
                "new_town": "",
                "move_date": "soon",
            }
        )
        self.assertEqual(["move_date", "new_town", "old_postcode", "old_property"], sorted(errors))
        self.assertEqual("HG1 1SY", move.get("new_postcode"))
        move, errors = PersonalDetails.check_move(
            {
                "old_postcode": "LS1 3ED",
                "old_town": "Leeds",
                "new_postcode": "HG1 1SY",
                "new_town": "Harrogate",
                "move_date": "2026-11-27",
                "move_date_is_estimate": "on",
                "old_property": "let",
            }
        )
        self.assertEqual({}, errors)
        self.assertTrue(move.get("move_date_is_estimate"))
        self.assertNotIn("exchange_date", move)

    # ----- 10. Blank values are gaps -----

    def test_a_letter_without_details_goes_to_the_customer_not_the_organization(self):
        """
        With no details on file, the name, street address and email are gaps, so the letter becomes a pack for the
        customer. With details, the same letter goes to the organization, filled in.
        """
        gateway: RecordingMailGateway = RecordingMailGateway()
        bare: dict = self.standalone(details=False)
        draft: dict = next(iter(bare.get("drafts").values()))
        self.assertEqual(["customer email", "full names", "new address"], draft.get("gaps_for_customer"))
        sent = self.send_energy(bare, gateway)
        self.assertEqual([], sent.get("sent_to_organization"))
        self.assertEqual(
            ["customer email", "full names", "new address"],
            sent.get("packs_for_customer")[0].get("customer_to_complete"),
        )

        CaseStore().delete_case(bare.get("customer_id"))
        full: dict = self.standalone(details=True)
        self.assertEqual([], next(iter(full.get("drafts").values())).get("gaps_for_customer"))
        sent = self.send_energy(full, gateway)
        self.assertEqual(1, len(sent.get("sent_to_organization")))
        self.assertIn("Jordan Lee moves to 21 Lenton Boulevard, Nottingham, NG7 2NF", gateway.messages[-1].get("body"))

    def test_blank_and_street_less_values_are_gaps(self):
        """
        An empty string, spaces, or an address with a town and postcode but no street is a gap, not a value.
        """
        sly_data: dict = {
            "customer_profile": {
                "names": [],
                "full_names": [],
                "email": "   ",
                "new_address": {"line1": None, "town": "Nottingham", "postcode": "NG7 2NF"},
            }
        }
        text, gaps, unknown = DraftRenderer([]).render(
            "{{full_names}} {{customer_email}} {{new_address}}", {}, sly_data
        )
        self.assertEqual(["customer email", "full names", "new address"], gaps)
        self.assertEqual([], unknown)
        self.assertNotIn("Nottingham", text)

    # ----- 12. Canned replies -----

    def test_canned_replies_reach_only_the_demo_customer_from_their_own_provider(self):
        """
        A standalone customer never gets the demo's scripted replies; a bank customer whose energy supplier is
        someone else does not get Brightwatt's.
        """
        standalone: dict = self.standalone(details=True)
        self.send_energy(standalone, RecordingMailGateway())
        self.assertEqual([], CheckInbox().run({}, standalone).get("replies"))

        bank: dict = HouseMoveTestState.drafted()
        bank[CaseSession.OWNER_KEY] = HouseMoveTestState.OWNER
        energy: dict = HouseMoveTestState.task_for(bank, "energy_supplier")
        energy["contact_name"] = "Octavia Power"
        HouseMoveTestState.approve_everything(bank)
        SendNotification(audit_log=self.NO_FILE, gateway=RecordingMailGateway()).run({"task_ids": ["all"]}, bank)
        self.assertNotIn("R-1", [reply.get("reply_id") for reply in CheckInbox().run({}, bank).get("replies")])

    # ----- 13. Data at rest -----

    def test_database_is_owner_only_and_transactions_are_encrypted(self):
        """
        The database and its folder are owner-only, and statement rows are not readable in the file.
        """
        store: DiscoveryRecords = DiscoveryRecords()
        db_path = self.folder / "cases.sqlite"
        store.add_transactions("S-x", [{"date": "2026-09-01", "descriptor": "OCTAVIA POWER DD", "amount": -42.0}], "s")
        self.assertEqual(0o600, stat.S_IMODE(db_path.stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(self.folder.stat().st_mode))
        self.assertNotIn(b"OCTAVIA", db_path.read_bytes())
        self.assertEqual("OCTAVIA POWER DD", store.get_transactions("S-x")[0].get("descriptor"))

    def test_plain_transactions_from_earlier_versions_are_encrypted(self):
        """
        Rows an earlier version stored in plain text are encrypted when the store opens, and the plain copy is gone.
        """
        CaseStore()
        db_path = self.folder / "cases.sqlite"
        with closing(sqlite3.connect(db_path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO transactions VALUES "
                    "('S-y', 'old-0001', '2026-08-01', 'LEGACYCO DD', -9.5, 'debit', 'old')"
                )
        self.assertIn(b"LEGACYCO", db_path.read_bytes())
        store: DiscoveryRecords = DiscoveryRecords()
        self.assertNotIn(b"LEGACYCO", db_path.read_bytes())
        self.assertEqual("LEGACYCO DD", store.get_transactions("S-y")[0].get("descriptor"))

    def test_saved_plan_holds_no_filled_in_letters(self):
        """
        Drafts keep their placeholders and the outbox keeps fingerprints; filled-in text saved by an earlier version
        is dropped on the next save.
        """
        sly_data: dict = self.standalone(details=True)
        self.send_energy(sly_data, RecordingMailGateway())
        text: str = str({key: sly_data.get(key) for key in CaseSession.STATE_KEYS})
        for personal in ("Jordan Lee", "Lenton", "jordan.lee@example.com"):
            self.assertNotIn(personal, text)

        legacy: dict = {
            "drafts": {"D-01": {"body": "{{full_names}}", "rendered_body": "Jordan Lee", "rendered_subject": "x"}},
            "outbox": [{"ref": "MSG-1", "subject": "x", "body": "Jordan Lee, 21 Lenton Boulevard"}],
        }
        clean: dict = CaseSession._without_rendered_text(legacy)  # pylint: disable=protected-access
        self.assertNotIn("Jordan Lee", str(clean))
        self.assertEqual({"body": "{{full_names}}"}, clean.get("drafts").get("D-01"))

    def test_vault_key_lives_apart_from_the_data(self):
        """
        Without a key in the environment, the vault key is kept in the key folder, and a key an earlier version
        left next to the database is moved there.
        """
        legacy = self.folder / "cases.key"
        legacy.write_bytes(Fernet.generate_key())
        saved: str | None = os.environ.pop(PersonalDataVault.KEY_ENV)
        try:
            CaseStore()
        finally:
            os.environ[PersonalDataVault.KEY_ENV] = saved
        self.assertFalse(legacy.exists())
        moved = KeyFolder.path(PersonalDataVault.KEY_NAME)
        self.assertTrue(moved.is_file())
        self.assertEqual(0o600, stat.S_IMODE(moved.stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(moved.parent.stat().st_mode))
        self.assertNotEqual(self.folder, moved.parent)
