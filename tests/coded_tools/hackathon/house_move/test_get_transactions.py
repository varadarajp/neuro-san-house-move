from coded_tools.hackathon.house_move.get_transactions import GetTransactions
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase


class TestGetTransactions(HouseMoveTestCase):
    """
    Unit tests for GetTransactions, run against the synthetic data files.
    """

    def test_groups_payees_and_drops_credits(self):
        """
        Repeat payments group under one payee, and salary and own-account transfers are excluded.
        """
        result = GetTransactions().run({}, {"customer_id": "C-10042"})
        payees: dict = {entry.get("payee"): entry for entry in result.get("payees")}

        self.assertEqual(3, payees.get("BRIGHTWATT EN").get("count"))
        self.assertEqual(["DD"], payees.get("PAWSURE LTD PETPOL").get("payment_types"))
        self.assertIn("MERIDIAN VETS LEEDS", payees)
        self.assertFalse(any("SALARY" in payee or "TFR" in payee for payee in payees))
        self.assertEqual({"from": "2026-07-17", "to": "2026-10-15"}, result.get("window"))

    def test_mandates_go_to_sly_data_only(self):
        """
        Direct Debit references are kept in sly_data for the draft tools and never returned to the LLM.
        """
        sly_data: dict = {"customer_id": "C-10042"}
        result = GetTransactions().run({}, sly_data)
        references: dict = {m.get("category"): m.get("reference") for m in sly_data.get("mandates")}
        self.assertEqual("BW-00347781", references.get("energy"))
        self.assertNotIn("BW-00347781", str(result))

    def test_shorter_window(self):
        """
        A 30-day window keeps only the last month's payments.
        """
        result = GetTransactions().run({"days": 30}, {"customer_id": "C-10042"})
        payees: dict = {entry.get("payee"): entry for entry in result.get("payees")}
        self.assertEqual(1, payees.get("BRIGHTWATT EN").get("count"))

    def test_errors(self):
        """
        A missing or unknown customer ID returns an error string.
        """
        tool = GetTransactions()
        self.assertTrue(tool.run({}, {}).startswith("Error:"))
        self.assertTrue(tool.run({"customer_id": "C-0"}, {}).startswith("Error:"))
