"""Mock CRM lookup for the House Move Agent's Profile agent."""

import json
import logging
from typing import Any

from coded_tools.hackathon.house_move.case_session import CaseSession
from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.case_tool import CaseTool
from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore

logger = logging.getLogger(__name__)


class CustomerProfileLookup(CaseTool):
    """
    Returns what the bank already knows about a moving customer.
    The full record, including street addresses, stays in sly_data. The LLM gets a redacted view
    plus the list of facts the bank does not know yet.
    With no customer ID it looks up who is signed in: the case already open, else the bank customer the session is
    for. A signed-in customer never has to type their ID. A guest has no bank record, which the error says, so the
    coordinator starts a new move instead.
    """

    # Facts the move plan needs. A null value in the CRM record means the bank does not know it.
    PROFILE_FACTS: tuple[str, ...] = (
        "household.children",
        "household.pets",
        "old_property_retained",
        "vehicles",
        "has_driving_license",
    )

    def __init__(self, data_store: HouseMoveDataStore | None = None, case_store: CaseStore | None = None):
        """
        :param data_store: source of bank customer records. Defaults to the synthetic data files.
        :param case_store: source of standalone cases (IDs starting "S-"). Defaults to the SQLite case store.
        """
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()
        self._case_store: CaseStore | None = case_store

    NEEDS_CASE: bool = False  # This is the tool that opens a case.

    def before_run(self, args: dict[str, Any], sly_data: dict[str, Any], session: CaseSession) -> str | None:
        """
        Opens the requested case only if the signed-in caller may: their own bank record, or a standalone case they
        created. Resuming loads the saved plan from the server.
        """
        signed_in: list[str] = session.signed_in_customers()
        case_id: str | None = (
            args.get("customer_id") or sly_data.get("customer_id") or (signed_in[0] if len(signed_in) == 1 else None)
        )
        if not case_id:
            return None
        # run() reads the ID from args, so the default chosen here is the one it looks up.
        args["customer_id"] = case_id
        return session.select_case(sly_data, case_id)

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: optional "customer_id". Falls back to sly_data["customer_id"].
        :param sly_data: receives "customer_id" and the full "customer_profile".
        :return: a redacted profile with a "gaps" list, or an "Error: ..." string.
        """
        customer_id: str | None = args.get("customer_id") or sly_data.get("customer_id")
        if not customer_id:
            return (
                "Error: no bank customer is signed in (this is a guest sign-in) and no case is open. Use the "
                "standalone start: ask for first names, both postcodes and the move date. Do not ask for a customer "
                "ID."
            )

        try:
            if CaseStore.is_case(customer_id):
                customer: dict[str, Any] | None = (self._case_store or CaseStore()).get_customer(customer_id)
            else:
                customer = self._data_store.get_customer(customer_id)
        except (OSError, json.JSONDecodeError) as error:
            logger.error("Could not read customer data: %s", error)
            return f"Error: customer data is unavailable ({error})."
        if customer is None:
            return f"Error: no customer or case found with ID {customer_id}."

        sly_data["customer_id"] = customer_id
        sly_data["customer_profile"] = customer
        logger.info("Loaded profile for %s", customer_id)
        view: dict[str, Any] = self._redacted_view(customer)
        view["saved_plan"] = self._plan_summary(sly_data)
        return view

    @staticmethod
    def _plan_summary(sly_data: dict[str, Any]) -> dict[str, Any] | None:
        """
        Summarizes a plan already in progress (for example a resumed standalone case), so the coordinator carries on
        from where the customer left off instead of starting again. None when there is no plan yet.
        """
        tasks: list[dict[str, Any]] = sly_data.get("tasks") or []
        if not tasks:
            return None
        by_status: dict[str, int] = {}
        for task in tasks:
            by_status[task.get("status")] = by_status.get(task.get("status"), 0) + 1
        open_tasks: list[dict[str, Any]] = [
            t for t in tasks if t.get("status") not in ("sent", "confirmed", "skipped")
        ]
        next_due: dict[str, Any] | None = min(open_tasks, key=lambda t: t.get("due_date"), default=None)
        return {
            "tasks": len(tasks),
            "by_status": by_status,
            "drafts_saved": len(sly_data.get("drafts") or {}),
            "next_due": {"org": next_due.get("org"), "due_date": next_due.get("due_date")} if next_due else None,
        }

    def _redacted_view(self, customer: dict[str, Any]) -> dict[str, Any]:
        """
        Builds the LLM-safe view: first names, town and postcode only, no email or street lines.
        """
        return {
            "customer_id": customer.get("customer_id"),
            "first_names": [name.split()[0] for name in customer.get("names", [])],
            "old_address": self._area_only(customer.get("old_address", {})),
            "new_address": self._area_only(customer.get("new_address", {})),
            "completion_date": customer.get("completion_date"),
            "exchange_date": customer.get("exchange_date"),
            "adults": customer.get("household", {}).get("adults"),
            "employment": customer.get("employment"),
            "products_held": customer.get("products_held", []),
            "gaps": self._unknown_facts(customer),
            "known_facts": customer.get("facts") or {},
            "source": customer.get("source", "bank_customer"),
            "move_date_is_estimate": bool(customer.get("move_date_is_estimate")),
        }

    @staticmethod
    def _area_only(address: dict[str, Any]) -> dict[str, Any]:
        """
        Keeps the town and postcode, which the plan needs, and drops the street line.
        """
        return {"town": address.get("town"), "postcode": address.get("postcode")}

    def _unknown_facts(self, customer: dict[str, Any]) -> list[str]:
        """
        Lists the PROFILE_FACTS whose value is missing or null in the CRM record.
        """
        gaps: list[str] = []
        for fact in CustomerProfileLookup.PROFILE_FACTS:
            value: Any = customer
            for key in fact.split("."):
                value = value.get(key) if isinstance(value, dict) else None
            if value is None:
                gaps.append(fact)
        return gaps
