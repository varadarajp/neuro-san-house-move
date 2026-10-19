from build_scripts.reset_house_move_demo import DemoReset
from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.import_statement import ImportStatement
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase
from tests.coded_tools.hackathon.house_move.house_move_test_state import HouseMoveTestState


class TestDemoReset(HouseMoveTestCase):
    """
    The demo reset leaves every test customer with no case data, and keeps a backup that still holds it.
    """

    def test_reset_clears_cases_and_keeps_a_backup(self):
        """
        After a reset, C-10042 has no plan, the standalone details and uploads are gone, and the backup has them.
        """
        HouseMoveTestState.drafted_session()
        CaseStore().save_pending_intake("guest:someone", {"email": "someone@example.com"})
        upload = self.uploads / ImportStatement.owner_folder("guest:someone")
        upload.mkdir()
        (upload / "s.csv").write_text("Date,Description,Amount\n")
        audit = self.folder / "audit.jsonl"
        audit.write_text("{}\n")

        backup = DemoReset(audit_file=audit).reset()

        store: CaseStore = CaseStore()
        self.assertEqual({}, store.load_case_state(HouseMoveTestState.OWNER, "C-10042"))
        self.assertIsNone(store.get_active(HouseMoveTestState.OWNER))
        self.assertEqual([], store.pending_intake_fields("guest:someone"))
        self.assertFalse(any(self.uploads.iterdir()))
        self.assertFalse(audit.exists())

        saved: CaseStore = CaseStore(db_path=backup / "cases.sqlite")
        self.assertTrue(saved.load_case_state(HouseMoveTestState.OWNER, "C-10042").get("tasks"))
        self.assertTrue((backup / "uploads" / upload.name / "s.csv").exists())
        self.assertTrue((backup / "audit.jsonl").exists())
