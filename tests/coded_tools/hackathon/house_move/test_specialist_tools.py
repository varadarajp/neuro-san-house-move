from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.bank_address_api import BankAddressApi
from coded_tools.hackathon.house_move.gov_service_directory import GovServiceDirectory
from coded_tools.hackathon.house_move.insurance_quote_api import InsuranceQuoteApi
from coded_tools.hackathon.house_move.provider_directory import ProviderDirectory
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase
from tests.coded_tools.hackathon.house_move.house_move_test_state import HouseMoveTestState


class TestSpecialistTools(HouseMoveTestCase):
    """
    Unit tests for the specialists' mock tools.
    """

    def test_gov_service_directory(self):
        """
        Known obligations return their service; unknown ones are listed.
        """
        result = GovServiceDirectory().invoke({"obligation_ids": ["dvla_v5c", "broadband"]}, {})
        self.assertEqual("the registered keeper only", result.get("services").get("dvla_v5c").get("who"))
        self.assertEqual(["broadband"], result.get("unknown"))

    def test_provider_directory_matches_names_and_aliases(self):
        """
        Providers are found by full name, alias or a longer name containing an alias.
        """
        result = ProviderDirectory().invoke(
            {"names": ["Brightwatt Energy", "fibrely bb", "ShieldMotor Insurance Ltd", "Acme Gas"]}, {}
        )
        found: dict = result.get("providers")
        self.assertEqual("energy", found.get("Brightwatt Energy").get("category"))
        self.assertEqual("Fibrely Broadband", found.get("fibrely bb").get("name"))
        self.assertEqual("Shieldmotor Insurance", found.get("ShieldMotor Insurance Ltd").get("name"))
        self.assertEqual(["Acme Gas"], result.get("not_found"))

    def test_insurance_quote_is_indicative(self):
        """
        The quote is a band for the postcode area and always carries the indicative label.
        """
        result = InsuranceQuoteApi().run({}, HouseMoveTestState.planned())
        self.assertEqual("HG", result.get("postcode_area"))
        self.assertTrue(result.get("label").startswith("Indicative only"))

    def test_bank_address_needs_approval(self):
        """
        The direct action refuses until the task is approved, then schedules and confirms it.
        """
        sly_data: dict = HouseMoveTestState.planned()
        task: dict = HouseMoveTestState.task_for(sly_data, "bank_address")

        refused = BankAddressApi(AuditLog(None)).run({}, sly_data)
        self.assertTrue(refused.startswith("Error: the customer has not approved"))
        self.assertEqual("planned", task.get("status"))

        task["status"] = "approved"
        result = BankAddressApi(AuditLog(None)).run({}, sly_data)
        self.assertEqual("scheduled", result.get("status"))
        self.assertEqual("2026-11-27", result.get("effective_date"))
        self.assertEqual(
            result, BankAddressApi(AuditLog(None)).run({}, {**sly_data, "tasks": [{**task, "status": "approved"}]})
        )
        self.assertEqual("confirmed", task.get("status"))
