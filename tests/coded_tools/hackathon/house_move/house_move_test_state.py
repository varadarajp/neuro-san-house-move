from datetime import date

import httpx

from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.build_timeline import BuildTimeline
from coded_tools.hackathon.house_move.case_session import CaseSession
from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.confirm_with_code import ConfirmWithCode
from coded_tools.hackathon.house_move.council_lookup import CouncilLookup
from coded_tools.hackathon.house_move.customer_profile_lookup import CustomerProfileLookup
from coded_tools.hackathon.house_move.list_tasks import ListTasks
from coded_tools.hackathon.house_move.obligations_rules import ObligationsRules
from coded_tools.hackathon.house_move.request_approval import RequestApproval
from coded_tools.hackathon.house_move.save_drafts import SaveDrafts
from coded_tools.hackathon.house_move.session_tokens import SessionTokens
from coded_tools.hackathon.house_move.update_tasks import UpdateTasks


class HouseMoveTestState:
    """
    Builds the sly_data a real run has after planning, by running the real tools for the synthetic Morgan household.
    """

    OWNER: str = "bank:C-10042"
    FACTS: dict = {
        "has_driving_license": True,
        "has_vehicle": True,
        "has_broadband": True,
        "has_tv_license": True,
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
    def approve(sly_data: dict, approve: list, skip: list | None = None, message: str = "Yes, {code}") -> dict:
        """
        Approves the way the customer does: request a code, the customer types it, confirm.
        :param message: what the customer types; "{code}" is replaced with the issued code.
        :return: the confirmation result.
        """
        sly_data.setdefault(CaseSession.OWNER_KEY, HouseMoveTestState.OWNER)
        request: dict = RequestApproval().run({"approve": approve, "skip": skip or []}, sly_data)
        CaseStore().record_customer_message(
            sly_data.get(CaseSession.OWNER_KEY), message.replace("{code}", request.get("confirmation_code", ""))
        )
        return ConfirmWithCode(audit_log=AuditLog(None)).run({}, sly_data)

    @staticmethod
    def approve_everything(sly_data: dict) -> dict:
        """
        Approves every draft awaiting approval, naming legal-requirement drafts individually as the rules require.
        """
        waiting: list = [t.get("task_id") for t in sly_data.get("tasks") if t.get("status") == "awaiting_approval"]
        return HouseMoveTestState.approve(sly_data, waiting)

    @staticmethod
    def offline_lookup() -> CouncilLookup:
        """
        :return: a council lookup whose network calls fail, so it answers from the cache.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            # Nested to simulate an outage; MockTransport needs a plain callable.
            raise httpx.ConnectError("offline", request=request)

        return CouncilLookup(transport=httpx.MockTransport(handler))

    @staticmethod
    def drafted_session() -> str:
        """
        Signs in as C-10042 and, through the real session path (invoke), builds a plan with every task drafted and
        awaiting approval.
        :return: the session token.
        """
        token: str = SessionTokens().issue(HouseMoveTestState.OWNER, ["C-10042"])
        sly: dict = {"session_token": token}
        CustomerProfileLookup().invoke({"customer_id": "C-10042"}, sly)
        for postcode in ("LS1 3ED", "HG1 1SY"):
            HouseMoveTestState.offline_lookup().invoke({"postcode": postcode}, sly)
        providers: list = [{"category": "energy", "provider_name": "Brightwatt Energy"}]
        ObligationsRules().invoke({"facts": HouseMoveTestState.FACTS, "providers": providers}, sly)
        BuildTimeline(today=date(2026, 10, 18)).invoke({}, sly)
        tasks: list = ListTasks().invoke({}, sly).get("tasks")
        updates: list = [
            {
                "task_id": t.get("task_id"),
                "action": "tell them",
                "channel": "direct" if t.get("obligation_id") == "bank_address" else "email",
            }
            for t in tasks
        ]
        UpdateTasks().invoke({"updates": updates}, sly)
        drafts: list = [
            {"task_id": t.get("task_id"), "subject": "Moving", "body": "We move to {{new_address}}."} for t in tasks
        ]
        SaveDrafts().invoke({"drafts": drafts}, sly)
        return token

    @staticmethod
    def task_for(sly_data: dict, obligation_id: str) -> dict:
        """
        :return: the planned task for one obligation.
        """
        return next(task for task in sly_data.get("tasks") if task.get("obligation_id") == obligation_id)
