from datetime import date

from coded_tools.hackathon.house_move.build_timeline import BuildTimeline
from coded_tools.hackathon.house_move.customer_profile_lookup import CustomerProfileLookup
from coded_tools.hackathon.house_move.draft_renderer import DraftRenderer
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

    def test_a_false_fact_cannot_hide_a_legal_requirement_the_payments_show(self):
        """
        Review finding 3: if the LLM says there is no pet while the customer pays for pet insurance, the microchip
        rule is not dropped: it needs review, and the conflict is reported.
        """
        facts: dict = {**self.MORGAN_FACTS, "has_pet": False}
        providers: list = [{"category": "pet_insurance", "provider_name": "Pawsure"}]
        result = ObligationsRules().run({"facts": facts, "providers": providers}, self.sly_data_for("England"))
        by_id: dict = {o.get("id"): o for o in result.get("obligations")}

        self.assertNotIn("pet_microchip", result.get("not_applicable"))
        self.assertEqual("needs_review", by_id.get("pet_microchip").get("status"))
        self.assertEqual(["has_pet"], by_id.get("pet_microchip").get("missing_facts"))
        self.assertEqual(1, len(result.get("fact_conflicts")))
        self.assertIn("Pawsure", result.get("fact_conflicts")[0])

    def test_a_false_fact_cannot_override_what_the_customer_put_on_the_form(self):
        """
        A fact the customer gave on the start form outweighs a false one from the LLM, and fills a fact the LLM left
        out.
        """
        sly_data: dict = self.sly_data_for("England")
        sly_data.get("customer_profile")["facts"] = {"has_driving_license": True, "has_vehicle": True}
        facts: dict = {k: v for k, v in self.MORGAN_FACTS.items() if k != "has_vehicle"}
        facts["has_driving_license"] = False
        result = ObligationsRules().run({"facts": facts}, sly_data)
        by_id: dict = {o.get("id"): o for o in result.get("obligations")}

        self.assertEqual("needs_review", by_id.get("dvla_license").get("status"))
        self.assertEqual("applies", by_id.get("dvla_v5c").get("status"))
        self.assertIn("start form", result.get("fact_conflicts")[0])

    def test_legal_requirements_ruled_out_by_a_fact_are_listed(self):
        """
        A legal requirement left out because of a household fact is reported with the reason, never dropped silently.
        Rules left out for location reasons (Scotland, Northern Ireland) are not listed.
        """
        facts: dict = {**self.MORGAN_FACTS, "has_driving_license": False}
        result = ObligationsRules().run({"facts": facts}, self.sly_data_for("England"))
        excluded: dict = {item.get("id"): item for item in result.get("excluded_legal_requirements")}

        self.assertEqual("has_driving_license is false", excluded.get("dvla_license").get("because"))
        self.assertNotIn("ni_rates_old", excluded)
        self.assertEqual([], result.get("fact_conflicts"))

    def test_requires_council_lookup(self):
        """
        Without council lookups for both postcodes, the tool asks for them.
        """
        sly_data: dict = {}
        CustomerProfileLookup().run({"customer_id": "C-10042"}, sly_data)
        result = ObligationsRules().run({"facts": self.MORGAN_FACTS}, sly_data)
        self.assertTrue(result.startswith("Error: call council_lookup"))

    def test_each_lender_gets_its_own_task(self):
        """
        Issue #8: every mortgage, credit card or loan lender in the customer's payments gets its own dated task and
        letter, numbered under the lenders rule, and none is left in other_providers.
        """
        lenders: list = [
            {"category": "mortgage", "provider_name": "Northway Building Society"},
            {"category": "credit_card", "provider_name": "Amberline Card Services"},
            {"category": "credit_card", "provider_name": "Second Card Co"},
        ]
        sly_data: dict = self.sly_data_for("England")
        sly_data["mandates"] = [
            {
                "service_user": "Amberline Card Services",
                "category": "credit_card",
                "placeholder": "account_number",
                "reference": "AMB-1",
            },
            {
                "service_user": "Second Card Co",
                "category": "credit_card",
                "placeholder": "account_number",
                "reference": "SEC-2",
            },
        ]
        result = ObligationsRules().run({"facts": self.MORGAN_FACTS, "providers": lenders}, sly_data)
        self.assertEqual([], result.get("other_providers"))
        BuildTimeline(today=date(2026, 10, 17)).run({}, sly_data)
        tasks: dict = {
            t.get("task_id"): t for t in sly_data.get("tasks") if t.get("obligation_id").startswith("other")
        }
        self.assertEqual(
            {
                "T-25-1": "Northway Building Society (mortgage)",
                "T-25-2": "Amberline Card Services (credit card)",
                "T-25-3": "Second Card Co (credit card)",
            },
            {task_id: task.get("org") for task_id, task in tasks.items()},
        )
        self.assertEqual("2026-11-27", tasks.get("T-25-1").get("due_date"))
        rendered, _, _ = DraftRenderer([]).render("{{org_name}} {{account_number}}", tasks.get("T-25-3"), sly_data)
        self.assertEqual("Second Card Co SEC-2", rendered)

    def test_no_lenders_no_lender_task(self):
        """
        With no lender among the payments, the lenders rule does not apply.
        """
        result = ObligationsRules().run(
            {"facts": self.MORGAN_FACTS, "providers": self.PROVIDERS}, self.sly_data_for("England")
        )
        self.assertIn("other_lenders", result.get("not_applicable"))
