from datetime import date

from coded_tools.hackathon.house_move.build_timeline import BuildTimeline
from coded_tools.hackathon.house_move.date_calc import DateCalc
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase


class TestBuildTimeline(HouseMoveTestCase):
    """
    Unit tests for BuildTimeline and DateCalc.
    """

    TODAY: date = date(2026, 10, 17)

    @staticmethod
    def obligation(rule_id: str, offset_days: int, working_days: bool = False, anchor: str = "completion") -> dict:
        """
        Builds a minimal applicable obligation.
        """
        return {
            "id": rule_id,
            "org": rule_id,
            "status": "applies",
            "legal_requirement": False,
            "timing": {"anchor": anchor, "offset_days": offset_days, "working_days": working_days},
        }

    def sly_data(self) -> dict:
        """
        Builds sly_data for a 27 Nov 2026 completion and 13 Nov exchange in England.
        """
        return {
            "customer_profile": {
                "completion_date": "2026-11-27",
                "exchange_date": "2026-11-13",
                "new_address": {"postcode": "HG1 1SY"},
            },
            "councils": {"HG1 1SY": {"country": "England"}},
            "obligations": [
                self.obligation("broadband", -21),
                self.obligation("royal_mail", -5, working_days=True),
                self.obligation("home_insurance", 0, anchor="exchange"),
                {"id": "mail_question", "org": "Royal Mail", "status": "needs_review", "missing_facts": ["x"]},
            ],
        }

    def test_dates_and_order(self):
        """
        Tasks are dated from the right anchor, sorted by date, and needs-review items are listed apart.
        """
        result = BuildTimeline(today=self.TODAY).run({}, self.sly_data())
        due: dict = {t.get("obligation_id"): t.get("due_date") for t in result.get("tasks")}

        self.assertEqual("2026-11-06", due.get("broadband"))
        self.assertEqual("2026-11-20", due.get("royal_mail"))
        self.assertEqual("2026-11-13", due.get("home_insurance"))
        self.assertEqual(
            ["broadband", "home_insurance", "royal_mail"], [t.get("obligation_id") for t in result.get("tasks")]
        )
        self.assertEqual("mail_question", result.get("needs_review")[0].get("obligation_id"))
        self.assertEqual(["2026-11-02", "2026-11-09", "2026-11-16"], list(result.get("by_week")))

    def test_replan_keeps_ids_and_previous_plan(self):
        """
        Rebuilding with a new completion date keeps task IDs and saves the old plan.
        """
        sly_data: dict = self.sly_data()
        first = BuildTimeline(today=self.TODAY).run({}, sly_data)
        second = BuildTimeline(today=self.TODAY).run({"completion_date": "2026-12-11"}, sly_data)

        first_ids: dict = {t.get("obligation_id"): t.get("task_id") for t in first.get("tasks")}
        second_ids: dict = {t.get("obligation_id"): t.get("task_id") for t in second.get("tasks")}
        self.assertEqual(first_ids, second_ids)
        self.assertEqual(first.get("tasks"), sly_data.get("previous_tasks"))
        self.assertEqual("2026-12-11", sly_data.get("plan_dates").get("completion_date"))

    def test_replan_reports_changes(self):
        """
        A rebuild for a new completion date lists each moved task with its old and new date.
        """
        sly_data: dict = self.sly_data()
        BuildTimeline(today=self.TODAY).run({}, sly_data)
        result = BuildTimeline(today=self.TODAY).run({"completion_date": "2026-12-11"}, sly_data)
        moved: dict = {m.get("org"): (m.get("from"), m.get("to")) for m in result.get("changes").get("moved")}

        self.assertEqual(("2026-11-06", "2026-11-20"), moved.get("broadband"))
        self.assertEqual(["T-03 home_insurance"], result.get("changes").get("unchanged"))

    def test_due_dates_avoid_weekends_and_bank_holidays(self):
        """
        An "after" date on Christmas Day rolls forward; a "before" date on a Sunday rolls back.
        """
        sly_data: dict = self.sly_data()
        sly_data["obligations"] = [
            {**self.obligation("gp", 14), "timing": {"anchor": "completion", "offset_days": 14, "window": "after"}},
            {**self.obligation("tv", -12), "timing": {"anchor": "completion", "offset_days": -12, "window": "before"}},
        ]
        result = BuildTimeline(today=self.TODAY).run({"completion_date": "2026-12-11"}, sly_data)
        due: dict = {t.get("obligation_id"): t.get("due_date") for t in result.get("tasks")}
        self.assertEqual("2026-12-29", due.get("gp"))
        self.assertEqual("2026-11-27", due.get("tv"))

    def test_overdue_warning_and_missing_obligations(self):
        """
        A due date before today is flagged, and an empty plan asks for the rules step first.
        """
        result = BuildTimeline(today=date(2026, 11, 10)).run({}, self.sly_data())
        self.assertTrue(any("broadband" in warning for warning in result.get("warnings")))
        self.assertTrue(BuildTimeline().run({}, {}).startswith("Error:"))

    def test_working_days_skip_weekends_and_bank_holidays(self):
        """
        Working-day offsets skip weekends and bank holidays in both directions.
        """
        date_calc = DateCalc(["2026-12-25", "2026-12-28"])
        self.assertEqual(date(2026, 12, 29), date_calc.offset(date(2026, 12, 24), 1, working_days=True))
        self.assertEqual(date(2026, 12, 24), date_calc.offset(date(2026, 12, 29), -1, working_days=True))
        self.assertEqual(date(2026, 12, 26), date_calc.offset(date(2026, 12, 24), 2, working_days=False))
