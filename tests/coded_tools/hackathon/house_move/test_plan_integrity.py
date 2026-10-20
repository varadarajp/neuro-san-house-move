from datetime import date

from coded_tools.hackathon.house_move.build_timeline import BuildTimeline
from coded_tools.hackathon.house_move.case_session import CaseSession
from coded_tools.hackathon.house_move.create_case import CreateCase
from coded_tools.hackathon.house_move.move_dates import MoveDates
from coded_tools.hackathon.house_move.obligations_rules import ObligationsRules
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase
from tests.coded_tools.hackathon.house_move.house_move_test_state import HouseMoveTestState


class TestPlanIntegrity(HouseMoveTestCase):
    """
    Second review, findings 5 to 8: a changed fact must not move one task's progress onto another, home insurance
    must never be planned for after exchange, implausible dates are refused, and a move within one council is one
    council tax task.
    """

    TODAY: date = date(2026, 10, 17)
    PROVIDERS: list = [{"category": "energy", "provider_name": "Brightwatt Energy"}]

    def replan(self, sly_data: dict, **fact_changes) -> dict:
        """
        Re-runs the rules with changed facts and rebuilds the timeline, as the agents do when a fact changes.
        """
        facts: dict = {**HouseMoveTestState.FACTS, **fact_changes}
        ObligationsRules().run({"facts": facts, "providers": self.PROVIDERS}, sly_data)
        return BuildTimeline(today=self.TODAY).run({}, sly_data)

    def test_a_rule_that_stops_applying_moves_no_progress(self):
        """
        "Actually, no car": the vehicle task goes, and every other task keeps its own ID, obligation and progress.
        The broadband task that was sent stays the broadband task; TV Licensing is still planned.
        """
        sly_data: dict = HouseMoveTestState.planned()
        before: dict = {t.get("obligation_id"): t.get("task_id") for t in sly_data.get("tasks")}
        broadband: dict = HouseMoveTestState.task_for(sly_data, "broadband")
        broadband.update({"status": "sent", "sent_ref": "MOCK-BB"})

        result = self.replan(sly_data, has_vehicle=False, has_car_insurance=False)

        after: dict = {t.get("obligation_id"): t.get("task_id") for t in sly_data.get("tasks")}
        self.assertNotIn("dvla_v5c", after)
        self.assertEqual({k: v for k, v in before.items() if k in after}, after)
        sent: list = [t for t in sly_data.get("tasks") if t.get("status") == "sent"]
        self.assertEqual([("broadband", "MOCK-BB")], [(t.get("obligation_id"), t.get("sent_ref")) for t in sent])
        tv: dict = HouseMoveTestState.task_for(sly_data, "tv_licensing")
        self.assertEqual(("planned", "TV Licensing"), (tv.get("status"), tv.get("org")))
        self.assertTrue(any(line.startswith(before.get("dvla_v5c")) for line in result.get("changes").get("removed")))

    def test_a_removed_task_with_progress_is_reported_with_its_status(self):
        """
        If a task that was already sent stops applying, the change report says it had been sent.
        """
        sly_data: dict = HouseMoveTestState.planned()
        HouseMoveTestState.task_for(sly_data, "dvla_v5c")["status"] = "sent"
        removed: list = self.replan(sly_data, has_vehicle=False).get("changes").get("removed")
        self.assertTrue(any("(was sent)" in line for line in removed))

    def test_task_ids_follow_the_rules_file(self):
        """
        A task's ID is its rule's position in the rules file, whatever else applies.
        """
        sly_data: dict = HouseMoveTestState.planned()
        self.assertEqual("T-14", HouseMoveTestState.task_for(sly_data, "home_insurance").get("task_id"))
        fewer: dict = HouseMoveTestState.planned()
        self.replan(fewer, has_driving_license=False, has_vehicle=False, has_broadband=False)
        self.assertEqual("T-14", HouseMoveTestState.task_for(fewer, "home_insurance").get("task_id"))

    def test_plans_saved_before_stable_ids_keep_their_ids(self):
        """
        A plan saved under the old position-based IDs keeps each task's ID on a rebuild; a newly applying task never
        takes an ID another task already has.
        """
        sly_data: dict = HouseMoveTestState.planned()
        for number, task in enumerate(sly_data.get("tasks"), start=1):
            task["task_id"] = f"T-{number:02d}"
        old: dict = {t.get("obligation_id"): t.get("task_id") for t in sly_data.get("tasks")}
        sly_data["tasks"] = [t for t in sly_data.get("tasks") if t.get("obligation_id") != "pet_microchip"]
        self.replan(sly_data)
        ids: list = [t.get("task_id") for t in sly_data.get("tasks")]
        self.assertEqual(len(ids), len(set(ids)))
        for task in sly_data.get("tasks"):
            if task.get("obligation_id") != "pet_microchip":
                self.assertEqual(old.get(task.get("obligation_id")), task.get("task_id"))

    def test_home_insurance_is_never_after_exchange(self):
        """
        With no exchange date, home insurance is dated today (exchange always comes first), with a warning;
        with an exchange date, it is dated on exchange.
        """
        sly_data: dict = HouseMoveTestState.planned()
        sly_data.get("customer_profile")["exchange_date"] = None
        result = BuildTimeline(today=self.TODAY).run({}, sly_data)
        insurance: dict = HouseMoveTestState.task_for(sly_data, "home_insurance")
        self.assertEqual(self.TODAY.isoformat(), insurance.get("due_date"))
        self.assertIn("exchange unknown", insurance.get("date_basis"))
        self.assertTrue(any("Home insurance" in w and "exchange date is unknown" in w for w in result.get("warnings")))

        BuildTimeline(today=self.TODAY).run({"exchange_date": "2026-11-13"}, sly_data)
        insurance = HouseMoveTestState.task_for(sly_data, "home_insurance")
        self.assertEqual(("2026-11-13", "exchange"), (insurance.get("due_date"), insurance.get("date_basis")))

    def test_implausible_dates_are_refused(self):
        """
        Exchange after completion, a completion long past or years away, and an exchange a year early are refused;
        a move a fortnight ago is allowed.
        """
        today: date = self.TODAY
        self.assertIn(
            "after the completion date", MoveDates.check(date(2020, 1, 10), date(2030, 1, 1), date(2020, 1, 1))
        )
        self.assertIn("more than 30 days ago", MoveDates.check(date(2020, 1, 10), None, today))
        self.assertIn("two years away", MoveDates.check(date(2030, 1, 10), None, today))
        self.assertIn("a year before", MoveDates.check(date(2026, 12, 1), date(2025, 6, 1), today))
        self.assertIsNone(MoveDates.check(date(2026, 10, 3), date(2026, 9, 20), today))

        result = BuildTimeline(today=today).run(
            {"completion_date": "2020-01-10", "exchange_date": "2030-01-01"}, HouseMoveTestState.planned()
        )
        self.assertTrue(result.startswith("Error: the completion date 2020-01-10"))

    def test_create_case_refuses_a_past_move_date(self):
        """
        A standalone case cannot be started with a move date long past; nothing is looked up or saved.
        """
        args: dict = {"first_names": ["Jordan"], "old_postcode": "BS6 6RB", "new_postcode": "NG7 2NF"}
        tool = CreateCase(council_lookup=HouseMoveTestState.offline_lookup(), today=self.TODAY)
        result = tool.run({**args, "move_date": "2025-01-10"}, {CaseSession.OWNER_KEY: "guest:dates"})
        self.assertTrue(result.startswith("Error: the completion date 2025-01-10"))
        result = tool.run(
            {**args, "move_date": "2026-12-11", "exchange_date": "2026-12-18"}, {CaseSession.OWNER_KEY: "guest:dates"}
        )
        self.assertIn("after the completion date", result)

    def test_moving_within_one_council_is_one_council_tax_task(self):
        """
        Both postcodes in Leeds: one council tax task for Leeds, not "old council (Leeds)" and "new council (Leeds)".
        Moving between councils still gives both.
        """
        sly_data: dict = HouseMoveTestState.planned()
        sly_data.get("councils")["HG1 1SY"] = dict(sly_data.get("councils").get("LS1 3ED"))
        self.replan(sly_data)
        council_tax: list = [t.get("org") for t in sly_data.get("tasks") if "Council tax" in (t.get("org") or "")]
        self.assertEqual(["Council tax: Leeds (moving within the council area)"], council_tax)

        between: dict = HouseMoveTestState.planned()
        orgs: list = [t.get("org") for t in between.get("tasks") if "Council tax" in (t.get("org") or "")]
        self.assertEqual(2, len(orgs))
