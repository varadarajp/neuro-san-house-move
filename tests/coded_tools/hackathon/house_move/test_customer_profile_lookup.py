from coded_tools.hackathon.house_move.customer_profile_lookup import CustomerProfileLookup
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase


class TestCustomerProfileLookup(HouseMoveTestCase):
    """
    Unit tests for CustomerProfileLookup, run against the synthetic data files.
    """

    def test_known_customer_is_redacted_and_stored(self):
        """
        The LLM view has no street lines or email, and the full record goes into sly_data.
        """
        sly_data: dict = {}
        result = CustomerProfileLookup().run({"customer_id": "C-10042"}, sly_data)

        self.assertEqual(["Sam", "Alex"], result.get("first_names"))
        self.assertEqual({"town": "Leeds", "postcode": "LS1 3ED"}, result.get("old_address"))
        self.assertNotIn("Wharf", str(result))
        self.assertNotIn("example.com", str(result))
        self.assertIn("household.pets", result.get("gaps"))
        self.assertIn("vehicles", result.get("gaps"))
        self.assertEqual("C-10042", sly_data.get("customer_id"))
        self.assertEqual("14 Wharf Approach", sly_data.get("customer_profile").get("old_address").get("line1"))

    def test_customer_id_falls_back_to_sly_data(self):
        """
        With no argument, the customer ID already in sly_data is used.
        """
        result = CustomerProfileLookup().run({}, {"customer_id": "C-10042"})
        self.assertEqual("C-10042", result.get("customer_id"))

    def test_errors(self):
        """
        A missing or unknown customer ID returns an error string.
        """
        lookup = CustomerProfileLookup()
        self.assertTrue(lookup.run({}, {}).startswith("Error:"))
        self.assertTrue(lookup.run({"customer_id": "C-0"}, {}).startswith("Error:"))
