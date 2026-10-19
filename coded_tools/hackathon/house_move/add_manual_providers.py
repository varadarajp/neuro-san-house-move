"""Manual entry: providers the customer names instead of uploading a statement."""

from typing import Any

from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.case_tool import CaseTool


class AddManualProviders(CaseTool):
    """
    Saves providers the customer tells us about (for example "Octopus for energy, Sky for broadband") to a standalone
    case. get_transactions returns them alongside any statement payees, marked as customer stated.
    """

    CATEGORIES: frozenset[str] = frozenset(
        {
            "energy",
            "water",
            "council_tax",
            "broadband",
            "mobile",
            "tv_licence",
            "vehicle_tax",
            "car_insurance",
            "home_insurance",
            "pet_insurance",
            "mortgage",
            "childcare",
            "gym",
            "subscription",
            "charity",
            "other",
        }
    )

    def __init__(self, case_store: CaseStore | None = None):
        """
        :param case_store: where providers are saved. Defaults to the SQLite case store.
        """
        self._case_store: CaseStore | None = case_store

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: "providers", a list of {"category", "provider_name"}.
        :param sly_data: reads "customer_id" (a standalone case).
        :return: {"added", "rejected", "total"}, or an "Error: ..." string.
        """
        case_id: str | None = sly_data.get("customer_id")
        if not CaseStore.is_case(case_id):
            return "Error: manual providers are only for standalone cases. Create the case first."
        accepted: list[dict[str, Any]] = []
        rejected: list[str] = []
        for provider in args.get("providers") or []:
            if provider.get("category") in AddManualProviders.CATEGORIES and provider.get("provider_name"):
                accepted.append(
                    {"category": provider.get("category"), "provider_name": provider.get("provider_name").strip()}
                )
            else:
                rejected.append(f"{provider}: needs a provider_name and a category from {sorted(self.CATEGORIES)}")
        total: int = (self._case_store or CaseStore()).add_manual_providers(case_id, accepted)
        return {"added": [p.get("provider_name") for p in accepted], "rejected": rejected, "total": total}
