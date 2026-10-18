"""Deterministic rules engine that decides which organisations a moving customer must tell."""

import asyncio
import json
import logging
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore

logger = logging.getLogger(__name__)


class ObligationsRules(CodedTool):
    """
    Evaluates data/obligations_rules.json against the customer's facts.
    No LLM judgment happens here: a rule applies, does not apply, or needs review because a fact is unknown.
    Deadlines, penalties and sources are copied from the rules file unchanged, so the LLM never invents them.
    Results are saved in sly_data["obligations"] for the timeline builder.
    """

    APPLIES: str = "applies"
    NEEDS_REVIEW: str = "needs_review"
    NOT_APPLICABLE: str = "not_applicable"

    def __init__(self, data_store: HouseMoveDataStore | None = None):
        """
        :param data_store: source of the rules. Defaults to the synthetic data files.
        """
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: "facts", a dict of household facts such as {"has_vehicle": true}; and "providers",
                     a list of {"category", "provider_name"} from discovery.
        :param sly_data: needs "customer_profile" and "councils" (set by council_lookup); receives "obligations".
        :return: {"obligations", "not_applicable", "other_providers", "summary"}, or an "Error: ..." string.
        """
        location_facts: dict[str, Any] | str = self._location_facts(sly_data)
        if isinstance(location_facts, str):
            return location_facts
        facts: dict[str, Any] = {**(args.get("facts") or {}), **location_facts}
        providers: list[dict[str, Any]] = args.get("providers") or []

        try:
            rules: list[dict[str, Any]] = self._data_store.get_obligation_rules()
        except (OSError, json.JSONDecodeError) as error:
            logger.error("Could not read the obligations rules: %s", error)
            return f"Error: the obligations rules file could not be read ({error})."

        obligations: list[dict[str, Any]] = []
        not_applicable: list[str] = []
        for rule in rules:
            status, missing = self._evaluate(rule.get("applies_if", []), facts)
            if status == ObligationsRules.NOT_APPLICABLE:
                not_applicable.append(rule.get("id"))
            else:
                obligations.append(self._to_obligation(rule, status, missing, facts, providers))

        sly_data["obligations"] = obligations
        CaseStore.snapshot(sly_data)
        used_categories: set[str] = {rule.get("provider_category") for rule in rules}
        logger.info("Evaluated %d rules: %d obligations", len(rules), len(obligations))
        return {
            "obligations": obligations,
            "not_applicable": not_applicable,
            "other_providers": [p for p in providers if p.get("category") not in used_categories],
            "summary": {
                "applies": sum(1 for o in obligations if o.get("status") == ObligationsRules.APPLIES),
                "needs_review": sum(1 for o in obligations if o.get("status") == ObligationsRules.NEEDS_REVIEW),
                "legal_requirements": sum(1 for o in obligations if o.get("legal_requirement")),
                "unverified_rules": sum(1 for o in obligations if not o.get("verified")),
                "old_council": facts.get("old_council"),
                "new_council": facts.get("new_council"),
            },
        }

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        Runs invoke in a worker thread because it reads files.
        """
        return await asyncio.to_thread(self.invoke, args, sly_data)

    @staticmethod
    def _location_facts(sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        Derives old/new council and country from the profile's postcodes and the council_lookup results.
        """
        profile: dict[str, Any] = sly_data.get("customer_profile") or {}
        councils: dict[str, Any] = sly_data.get("councils") or {}
        old_postcode: str | None = (profile.get("old_address") or {}).get("postcode")
        new_postcode: str | None = (profile.get("new_address") or {}).get("postcode")
        if not old_postcode or not new_postcode:
            return "Error: no customer profile loaded. Look up the customer profile first."
        old_council: dict[str, Any] | None = councils.get(old_postcode)
        new_council: dict[str, Any] | None = councils.get(new_postcode)
        if old_council is None or new_council is None:
            return f"Error: call council_lookup for both {old_postcode} and {new_postcode} first."
        return {
            # A standalone case holds no products with this bank, so its banks are told by the customer instead.
            "is_bank_customer": bool(profile.get("products_held")),
            "old_council": old_council.get("council_name"),
            "new_council": new_council.get("council_name"),
            "old_country": old_council.get("country"),
            "new_country": new_council.get("country"),
        }

    @staticmethod
    def _evaluate(conditions: list[dict[str, Any]], facts: dict[str, Any]) -> tuple[str, list[str]]:
        """
        :return: (status, missing fact names). Any failed condition means not applicable; otherwise any
                 unknown fact means needs review.
        """
        missing: list[str] = []
        for condition in conditions:
            name: str = condition.get("fact")
            value: Any = facts.get(name)
            if value is None:
                missing.append(name)
            elif "equals" in condition and value != condition.get("equals"):
                return ObligationsRules.NOT_APPLICABLE, []
            elif "in" in condition and value not in condition.get("in"):
                return ObligationsRules.NOT_APPLICABLE, []
            elif "not_in" in condition and value in condition.get("not_in"):
                return ObligationsRules.NOT_APPLICABLE, []
        if missing:
            return ObligationsRules.NEEDS_REVIEW, missing
        return ObligationsRules.APPLIES, []

    @staticmethod
    def _to_obligation(
        rule: dict[str, Any], status: str, missing: list[str], facts: dict[str, Any], providers: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Copies the rule's fixed fields and names the organisation, adding the customer's provider when known.
        """
        org: str = rule.get("org", "").format(
            old_council=facts.get("old_council") or "unknown", new_council=facts.get("new_council") or "unknown"
        )
        provider_names: list[str] = [
            p.get("provider_name") for p in providers if p.get("category") == rule.get("provider_category")
        ]
        if provider_names and rule.get("category") != "government":
            org = f"{org} ({', '.join(provider_names)})"
        return {
            "id": rule.get("id"),
            "org": org,
            "contact_name": provider_names[0] if provider_names else org,
            "category": rule.get("category"),
            "provider_category": rule.get("provider_category"),
            "status": status,
            "missing_facts": missing,
            "legal_requirement": rule.get("legal_requirement"),
            "penalty_note": rule.get("penalty_note"),
            "timing": rule.get("timing"),
            "timing_basis": rule.get("timing_basis"),
            "agent": rule.get("agent"),
            "source_url": rule.get("source_url"),
            "verified": rule.get("verified", False),
        }
