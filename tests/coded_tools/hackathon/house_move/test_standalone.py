import shutil
import sqlite3
from datetime import date
from pathlib import Path

import httpx

from coded_tools.hackathon.house_move.add_manual_providers import AddManualProviders
from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.build_timeline import BuildTimeline
from coded_tools.hackathon.house_move.case_session import CaseSession
from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.confirm_with_code import ConfirmWithCode
from coded_tools.hackathon.house_move.council_lookup import CouncilLookup
from coded_tools.hackathon.house_move.create_case import CreateCase
from coded_tools.hackathon.house_move.customer_profile_lookup import CustomerProfileLookup
from coded_tools.hackathon.house_move.delete_case import DeleteCase
from coded_tools.hackathon.house_move.get_transactions import GetTransactions
from coded_tools.hackathon.house_move.import_statement import ImportStatement
from coded_tools.hackathon.house_move.obligations_rules import ObligationsRules
from coded_tools.hackathon.house_move.session_tokens import SessionTokens
from coded_tools.hackathon.house_move.statement_parser import StatementParser
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase


class TestStandalone(HouseMoveTestCase):
    """
    The standalone app path: case store and vault, intake, statement import, manual providers, resume and erasure.
    Every test uses a temporary database, keys and uploads folder (HouseMoveTestCase). postcodes.io is replaced by
    an offline mock, so councils come from the fallback cache.
    """

    SAMPLES: Path = Path(__file__).resolve().parents[4] / "coded_tools/hackathon/house_move/data/sample_statements"
    FORM: dict = {
        "full_names": ["Jordan Lee"],
        "email": "jordan.lee@example.com",
        "old_address_line1": "8 Cotham Vale",
        "new_address_line1": "21 Lenton Boulevard",
    }
    OWNER: str = "guest:test-owner"
    CASE_ARGS: dict = {
        "first_names": ["Jordan"],
        "old_postcode": "bs66rb",
        "new_postcode": "NG7 2NF",
        "move_date": "2026-12-11",
        "move_date_is_estimate": True,
        "old_property": "sold",
        "facts": {"has_driving_licence": True, "has_vehicle": True, "has_pet": True, "not_a_fact": True},
    }

    def offline_lookup(self) -> CouncilLookup:
        """
        :return: a council lookup whose network calls fail, so it answers from the cache.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            # Nested to simulate an outage; MockTransport needs a plain callable.
            raise httpx.ConnectError("offline", request=request)

        return CouncilLookup(transport=httpx.MockTransport(handler))

    def new_case(self) -> dict:
        """
        :return: sly_data for a freshly created case, with the secure form already submitted.
        """
        sly_data: dict = {"intake_form": dict(self.FORM), CaseSession.OWNER_KEY: self.OWNER}
        result = CreateCase(council_lookup=self.offline_lookup()).run(dict(self.CASE_ARGS), sly_data)
        self.assertTrue(result.get("case_id").startswith("S-"), result)
        return sly_data

    def upload(self, sample: str) -> str:
        """
        Copies a sample statement into the owner's uploads folder, as the portal's upload would.
        """
        folder: Path = self.uploads / ImportStatement.owner_folder(self.OWNER)
        folder.mkdir(exist_ok=True)
        shutil.copy(self.SAMPLES / sample, folder / sample)
        return sample

    def test_create_case_keeps_personal_data_encrypted(self):
        """
        The form is moved into the vault and out of sly_data; the database file holds no personal data in plain text.
        """
        sly_data: dict = self.new_case()
        profile: dict = sly_data.get("customer_profile")

        self.assertNotIn("intake_form", sly_data)
        self.assertEqual("21 Lenton Boulevard", profile.get("new_address").get("line1"))
        self.assertEqual("NG7 2NF", profile.get("new_address").get("postcode"))
        self.assertEqual({"has_driving_licence": True, "has_vehicle": True, "has_pet": True}, profile.get("facts"))
        self.assertEqual("Nottingham", sly_data.get("councils").get("NG7 2NF").get("council_name"))
        database: bytes = (self.folder / "cases.sqlite").read_bytes()
        for secret in ("Jordan Lee", "Cotham", "Lenton Boulevard", "jordan.lee@example.com"):
            self.assertNotIn(secret.encode(), database)

    def test_create_case_errors(self):
        """
        Missing names, a bad date or an invalid postcode stop the case being created.
        """
        tool = CreateCase(council_lookup=self.offline_lookup())
        self.assertTrue(tool.run({**self.CASE_ARGS, "first_names": []}, {}).startswith("Error:"))
        self.assertTrue(tool.run({**self.CASE_ARGS, "move_date": "next spring"}, {}).startswith("Error:"))
        not_found = CouncilLookup(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
        result = CreateCase(council_lookup=not_found).run({**self.CASE_ARGS, "new_postcode": "ZZ9 9ZZ"}, {})
        self.assertIn("not a valid UK postcode", result)

    def test_parser_reads_csv_and_ofx_alike(self):
        """
        The CSV (paid out / paid in columns) and OFX samples give the same rows; bank details are dropped.
        """
        parser = StatementParser()
        csv_rows: list = parser.parse((self.SAMPLES / "jordan_lee_bristol.csv").read_text(), "s.csv")
        ofx_rows: list = parser.parse((self.SAMPLES / "jordan_lee_bristol.ofx").read_text(), "s.ofx")

        self.assertEqual(34, len(csv_rows))
        self.assertEqual(
            [(r.get("date"), r.get("amount"), r.get("type")) for r in csv_rows],
            [(r.get("date"), r.get("amount"), r.get("type")) for r in ofx_rows],
        )
        self.assertNotIn("12345678", str(csv_rows))
        energy: dict = next(r for r in csv_rows if r.get("descriptor").startswith("AVONSIDE"))
        self.assertEqual((-91.40, "DD"), (energy.get("amount"), energy.get("type")))

    def test_parser_signed_amount_csv_and_unsupported(self):
        """
        A single signed amount column works; PDFs and unrecognisable CSVs are rejected with a clear message.
        """
        rows: list = StatementParser().parse(
            "Date,Description,Amount\n2026-09-01,BRIGHTWATT EN DD 1234,-50.00\n2026-09-02,SALARY,1000\n", "x.csv"
        )
        self.assertEqual(["DD", "CREDIT"], [row.get("type") for row in rows])
        with self.assertRaises(ValueError):
            StatementParser().parse("%PDF", "x.pdf")
        with self.assertRaises(ValueError):
            StatementParser().parse("Foo,Bar\n1,2\n", "x.csv")

    def test_import_statement(self):
        """
        Import needs a standalone case, stays inside the owner's uploads folder and reads nothing until the customer
        types the code; then it stores rows, records the customer's own message as consent and deletes the file.
        """
        tool = ImportStatement()
        self.assertTrue(tool.run({}, {"customer_id": "C-10042"}).startswith("Error"))

        sly_data: dict = self.new_case()
        name: str = self.upload("jordan_lee_bristol.csv")
        self.assertTrue(tool.run({"file_name": "../cases.sqlite"}, sly_data).startswith("Error"))

        request = tool.run({"file_name": name}, sly_data)
        self.assertEqual(34, request.get("transactions_found"))
        self.assertEqual([], CaseStore().get_transactions(sly_data.get("customer_id")))
        typed: str = f"Yes, you can read it: {request.get('confirmation_code')}"
        CaseStore().record_customer_message(self.OWNER, typed)
        result = ConfirmWithCode(audit_log=AuditLog(None)).run({}, sly_data).get("result")
        self.assertEqual(34, result.get("transactions_imported"))
        self.assertFalse((self.uploads / ImportStatement.owner_folder(self.OWNER) / name).exists())
        self.assertEqual(typed, CaseStore().get_consents(sly_data.get("customer_id"))[0].get("words"))

        payees: dict = {p.get("payee"): p for p in GetTransactions().run({}, sly_data).get("payees")}
        self.assertEqual(3, payees.get("AVONSIDE ENERGY").get("count"))
        self.assertNotIn("SALARY HARBOURSIDE DESIGN", payees)
        self.assertEqual([], sly_data.get("mandates"))

    def test_manual_providers(self):
        """
        Listed providers are stored once each and returned as customer-stated, even with no statement.
        """
        sly_data: dict = self.new_case()
        self.assertTrue(GetTransactions().run({}, sly_data).startswith("Error: no transactions"))
        tool = AddManualProviders()
        tool.run({"providers": [{"category": "energy", "provider_name": "Avonside Energy"}]}, sly_data)
        result = tool.run(
            {
                "providers": [
                    {"category": "energy", "provider_name": "Avonside Energy"},
                    {"category": "broadband", "provider_name": "Fibrely Broadband"},
                    {"category": "spaceships", "provider_name": "X"},
                ]
            },
            sly_data,
        )
        self.assertEqual(2, result.get("total"))
        self.assertEqual(1, len(result.get("rejected")))
        stated: list = GetTransactions().run({}, sly_data).get("customer_stated_providers")
        self.assertEqual({"Avonside Energy", "Fibrely Broadband"}, {p.get("provider_name") for p in stated})

    def test_plan_is_saved_and_resumed_in_a_new_session(self):
        """
        Through the real session path: rules and timeline run as for a bank customer, with "your banks" instead of
        the bank's direct update. A new session for the same owner gets the profile and saved plan back; another
        owner cannot open the case.
        """
        token: str = SessionTokens().issue(self.OWNER, [])
        sly_data: dict = {"session_token": token, "intake_form": dict(self.FORM)}
        created = CreateCase(council_lookup=self.offline_lookup()).invoke(dict(self.CASE_ARGS), sly_data)
        case_id: str = created.get("case_id")
        facts: dict = {
            **self.CASE_ARGS.get("facts"),
            "has_broadband": True,
            "has_tv_licence": True,
            "wants_mail_redirection": False,
            "has_home_insurance": True,
            "has_car_insurance": True,
            "has_young_child": False,
        }
        rules = ObligationsRules().invoke({"facts": facts}, sly_data)
        ids: set = {o.get("id") for o in rules.get("obligations")}
        self.assertIn("own_bank_accounts", ids)
        self.assertNotIn("bank_address", ids)
        planned = BuildTimeline(today=date(2026, 10, 18)).invoke({}, sly_data)

        new_session: dict = {"session_token": token}
        view = CustomerProfileLookup().invoke({"customer_id": case_id}, new_session)
        self.assertEqual(["Jordan"], view.get("first_names"))
        self.assertTrue(view.get("move_date_is_estimate"))
        self.assertEqual(len(planned.get("tasks")), view.get("saved_plan").get("tasks"))
        self.assertNotIn("Lenton", str(view))

        stranger: dict = {"session_token": SessionTokens().issue(SessionTokens.new_guest_id(), [])}
        refused = CustomerProfileLookup().invoke({"customer_id": case_id}, stranger)
        self.assertTrue(refused.startswith("Error: no customer or case"))
        self.assertNotIn("customer_profile", stranger)

    def test_delete_and_retention(self):
        """
        Deleting needs the customer to type the code, then removes everything; the retention purge removes old cases.
        """
        sly_data: dict = self.new_case()
        case_id: str = sly_data.get("customer_id")
        request = DeleteCase(audit_log=AuditLog(None)).run({}, sly_data)
        self.assertIsNotNone(CaseStore().get_customer(case_id))
        CaseStore().record_customer_message(self.OWNER, f"Delete it all {request.get('confirmation_code')}")
        result = ConfirmWithCode(audit_log=AuditLog(None)).run({}, sly_data)
        self.assertEqual({"case_id": case_id, "deleted": True}, result.get("result"))
        self.assertNotIn("customer_id", sly_data)
        self.assertNotIn("customer_profile", sly_data)
        self.assertIsNone(CaseStore().get_customer(case_id))
        with sqlite3.connect(self.folder / "cases.sqlite") as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM vault").fetchone()[0])

        old_case: str = self.new_case().get("customer_id")
        self.assertEqual([old_case], CaseStore().purge_moved_before("2027-03-11"))
