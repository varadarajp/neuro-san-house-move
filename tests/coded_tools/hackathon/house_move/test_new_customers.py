from datetime import date

from coded_tools.hackathon.house_move.build_timeline import BuildTimeline
from coded_tools.hackathon.house_move.customer_profile_lookup import CustomerProfileLookup
from coded_tools.hackathon.house_move.get_transactions import GetTransactions
from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore
from coded_tools.hackathon.house_move.obligations_rules import ObligationsRules
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase


class TestNewCustomers(HouseMoveTestCase):
    """
    Cross-border test customers: each move produces the obligations its countries call for.
    Councils come from the fallback cache, which holds the same values postcodes.io returned.
    """

    FACTS: dict = {
        "has_driving_license": True,
        "has_vehicle": True,
        "has_broadband": True,
        "has_tv_license": True,
        "wants_mail_redirection": False,
        "has_home_insurance": True,
        "has_car_insurance": True,
        "has_pet": False,
        "has_young_child": False,
    }

    @staticmethod
    def plan_for(customer_id: str, facts: dict, providers: list | None = None) -> dict:
        """
        Runs profile, transactions, cached council lookups, rules and timeline for one customer.
        :return: sly_data plus "ids" (applicable obligation IDs) and "orgs" (task org by obligation ID).
        """
        sly_data: dict = {}
        store = HouseMoveDataStore()
        profile: dict = store.get_customer(customer_id)
        CustomerProfileLookup().run({"customer_id": customer_id}, sly_data)
        GetTransactions().run({}, sly_data)
        sly_data["councils"] = {
            address.get("postcode"): store.get_cached_council(address.get("postcode"))
            for address in (profile.get("old_address"), profile.get("new_address"))
        }
        result: dict = ObligationsRules().run({"facts": facts, "providers": providers or []}, sly_data)
        BuildTimeline(today=date(2026, 10, 18)).run({}, sly_data)
        sly_data["ids"] = {o.get("id") for o in result.get("obligations") if o.get("status") == "applies"}
        sly_data["orgs"] = {t.get("obligation_id"): t for t in sly_data.get("tasks")}
        return sly_data

    def test_every_new_customer_has_data(self):
        """
        Each new customer has a profile, transactions and Direct Debit mandates.
        """
        for customer_id in ("C-20001", "C-20002", "C-20003", "C-20004"):
            sly_data: dict = {}
            CustomerProfileLookup().run({"customer_id": customer_id}, sly_data)
            summary: dict = GetTransactions().run({}, sly_data)
            self.assertTrue(summary.get("payees"), customer_id)
            self.assertTrue(sly_data.get("mandates"), customer_id)

    def test_halifax_to_chelmsford(self):
        """
        England to England: both councils, old and new water companies, no Scotland or NI rules.
        """
        ids: set = self.plan_for("C-20001", {**self.FACTS, "has_vehicle": False, "has_car_insurance": False}).get(
            "ids"
        )
        self.assertTrue({"council_tax_old", "council_tax_new", "water_company", "water_company_new"} <= ids)
        self.assertFalse(ids & {"dvla_v5c", "scotland_water_via_council_tax", "ni_rates_old", "ni_license_to_gb"})

    def test_glasgow_to_darlington(self):
        """
        Scotland to England: no old water account, a new English water company, English bank holidays.
        """
        sly_data: dict = self.plan_for("C-20002", self.FACTS)
        ids: set = sly_data.get("ids")
        self.assertIn("water_company_new", ids)
        self.assertNotIn("water_company", ids)
        self.assertIn(
            "Council tax: old council (Glasgow City)", sly_data.get("orgs").get("council_tax_old").get("org")
        )
        # Completion is 30 Nov, a Scottish bank holiday only: energy is due 2 working days before it in England.
        self.assertEqual("2026-11-26", sly_data.get("orgs").get("energy_supplier").get("due_date"))

    def test_cardiff_to_edinburgh(self):
        """
        Wales to Scotland: close the Welsh water account; new water comes with council tax; Scottish holidays apply.
        """
        water: list = [{"category": "water", "provider_name": "Taff Valley Water"}]
        sly_data: dict = self.plan_for("C-20003", {**self.FACTS, "has_tv_license": False}, water)
        ids: set = sly_data.get("ids")
        self.assertTrue({"water_company", "scotland_water_via_council_tax", "council_tax_new"} <= ids)
        self.assertFalse(ids & {"water_company_new", "tv_licensing"})
        self.assertIn("Taff Valley Water", sly_data.get("orgs").get("water_company").get("org"))
        self.assertNotIn("Taff", sly_data.get("orgs").get("scotland_water_via_council_tax").get("org"))
        # Royal Mail is not wanted; energy is 2 working days before 8 Jan 2027: 6 Jan.
        self.assertEqual("2027-01-06", sly_data.get("orgs").get("energy_supplier").get("due_date"))

    def test_belfast_to_leeds(self):
        """
        Northern Ireland to England: LPS rates instead of old council tax, an NI license route instead of DVLA's
        change of address, no old water account, and new council tax and water in England.
        """
        ids: set = self.plan_for("C-20004", self.FACTS).get("ids")
        self.assertTrue(
            {"ni_rates_old", "ni_license_to_gb", "council_tax_new", "water_company_new", "dvla_v5c"} <= ids
        )
        self.assertFalse(ids & {"council_tax_old", "dvla_license", "water_company", "ni_rates_new"})
