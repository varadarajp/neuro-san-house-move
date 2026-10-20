from coded_tools.hackathon.house_move.customer_profile_lookup import CustomerProfileLookup
from coded_tools.hackathon.house_move.obligations_rules import ObligationsRules
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase


class TestObligationsRules(HouseMoveTestCase):
    """
    Unit tests for ObligationsRules, run against the real rules file and the synthetic customer.
    """

    MORGAN_FACTS: dict = {
        "has_driving_license": True,
        "has_vehicle": True,
        "has_broadband": True,
        "has_tv_license": True,
        "has_home_insurance": True,
        "has_car_insurance": True,
        "has_pet": True,
        "has_young_child": True,
    }
    PROVIDERS: list = [
        {"category": "energy", "provider_name": "Brightwatt Energy"},
        {"category": "gym", "provider_name": "PulseGym"},
    ]

    @staticmethod
    def sly_data_for(new_country: str) -> dict:
        """
        Builds sly_data as it looks after the profile lookup and both council lookups.
        """
        sly_data: dict = {}
        CustomerProfileLookup().run({"customer_id": "C-10042"}, sly_data)
        sly_data["councils"] = {
            "LS1 3ED": {"council_name": "Leeds", "country": "England"},
            "HG1 1SY": {"council_name": "North Yorkshire", "country": new_country},
        }
        return sly_data

    def test_morgan_household(self):
        """
        Known facts make rules apply; the unanswered mail redirection question needs review.
        """
        sly_data: dict = self.sly_data_for("England")
        result = ObligationsRules().run({"facts": self.MORGAN_FACTS, "providers": self.PROVIDERS}, sly_data)
        by_id: dict = {o.get("id"): o for o in result.get("obligations")}

        self.assertEqual("applies", by_id.get("dvla_license").get("status"))
        self.assertEqual("Council tax: old council (Leeds)", by_id.get("council_tax_old").get("org"))
        self.assertEqual("Energy supplier (Brightwatt Energy)", by_id.get("energy_supplier").get("org"))
        self.assertEqual("needs_review", by_id.get("royal_mail_redirection").get("status"))
        self.assertEqual(["wants_mail_redirection"], by_id.get("royal_mail_redirection").get("missing_facts"))
        self.assertIn("scotland_water_via_council_tax", result.get("not_applicable"))
        self.assertEqual([{"category": "gym", "provider_name": "PulseGym"}], result.get("other_providers"))
        self.assertEqual(result.get("obligations"), sly_data.get("obligations"))

    def test_scotland_merges_water_into_council_tax(self):
        """
        Moving from England to Scotland closes the old water account; the new home's water comes with council tax.
        """
        result = ObligationsRules().run({"facts": self.MORGAN_FACTS}, self.sly_data_for("Scotland"))
        ids: set = {o.get("id") for o in result.get("obligations")}
        self.assertIn("scotland_water_via_council_tax", ids)
        self.assertIn("water_company", ids)
        self.assertNotIn("water_company_new", ids)

    def test_false_fact_excludes_rule(self):
        """
        A fact that is known to be false makes the rule not applicable.
        """
        facts: dict = {**self.MORGAN_FACTS, "has_pet": False}
        result = ObligationsRules().run({"facts": facts}, self.sly_data_for("England"))
        self.assertIn("pet_microchip", result.get("not_applicable"))

    def test_requires_council_lookup(self):
        """
        Without council lookups for both postcodes, the tool asks for them.
        """
        sly_data: dict = {}
        CustomerProfileLookup().run({"customer_id": "C-10042"}, sly_data)
        result = ObligationsRules().run({"facts": self.MORGAN_FACTS}, sly_data)
        self.assertTrue(result.startswith("Error: call council_lookup"))
