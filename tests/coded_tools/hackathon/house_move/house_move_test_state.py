from datetime import date

from coded_tools.hackathon.house_move.build_timeline import BuildTimeline
from coded_tools.hackathon.house_move.customer_profile_lookup import CustomerProfileLookup
from coded_tools.hackathon.house_move.obligations_rules import ObligationsRules
from coded_tools.hackathon.house_move.save_drafts import SaveDrafts
from coded_tools.hackathon.house_move.update_tasks import UpdateTasks


class HouseMoveTestState:
    """
    Builds the sly_data a real run has after planning, by running the real tools for the synthetic Morgan household.
    """

    FACTS: dict = {
        "has_driving_licence": True,
        "has_vehicle": True,
        "has_broadband": True,
        "has_tv_licence": True,
        "wants_mail_redirection": True,
        "has_home_insurance": True,
        "has_car_insurance": True,
        "has_pet": True,
        "has_young_child": True,
    }

    @staticmethod
    def planned() -> dict:
        """
        :return: sly_data with a profile, both councils, obligations and a dated plan.
        """
        sly_data: dict = {}
        CustomerProfileLookup().run({"customer_id": "C-10042"}, sly_data)
        sly_data["councils"] = {
            "LS1 3ED": {"council_name": "Leeds", "country": "England"},
            "HG1 1SY": {"council_name": "North Yorkshire", "country": "England"},
        }
        providers: list = [{"category": "energy", "provider_name": "Brightwatt Energy"}]
        ObligationsRules().run({"facts": HouseMoveTestState.FACTS, "providers": providers}, sly_data)
        BuildTimeline(today=date(2026, 10, 17)).run({}, sly_data)
        return sly_data

    @staticmethod
    def drafted() -> dict:
        """
        :return: planned sly_data where every task has a channel and a saved draft awaiting approval.
        """
        sly_data: dict = HouseMoveTestState.planned()
        updates: list = []
        drafts: list = []
        for task in sly_data.get("tasks"):
            channel: str = "direct" if task.get("obligation_id") == "bank_address" else "email"
            updates.append({"task_id": task.get("task_id"), "action": "tell them", "channel": channel})
            body: str = "We move on {{move_date}} to {{new_address}}."
            if task.get("obligation_id") == "broadband":
                body += " Ref {{account_number}}."
            drafts.append({"task_id": task.get("task_id"), "subject": "Moving", "body": body})
        UpdateTasks().run({"updates": updates}, sly_data)
        SaveDrafts().run({"drafts": drafts}, sly_data)
        return sly_data

    @staticmethod
    def task_for(sly_data: dict, obligation_id: str) -> dict:
        """
        :return: the planned task for one obligation.
        """
        return next(task for task in sly_data.get("tasks") if task.get("obligation_id") == obligation_id)
