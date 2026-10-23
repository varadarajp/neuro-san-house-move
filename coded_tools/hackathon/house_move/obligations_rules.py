"""Deterministic rules engine that decides which organizations a moving customer must tell."""

import json
import logging
from typing import Any

from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.case_tool import CaseTool
from coded_tools.hackathon.house_move.discovery_records import DiscoveryRecords
from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore

logger = logging.getLogger(__name__)


class ObligationsRules(CaseTool):
    """
    Evaluates data/obligations_rules.json against the customer's facts.
    No LLM judgment happens here: a rule applies, does not apply, or needs review because a fact is unknown.
    Deadlines, penalties and sources are copied from the rules file unchanged, so the LLM never invents them.
    Results are saved in sly_data["obligations"] for the timeline builder.

    The household facts are passed in by the LLM, so the LLM could make a legal duty disappear by setting a fact to
    false. Two checks stop that from happening silently:
    - a false fact that the evidence contradicts (a provider whose category implies it, or a fact the customer gave
      on the start form) is dropped, so its rules become needs_review and the customer is asked;
    - every legal requirement that a household fact rules out is listed in excluded_legal_requirements, so the
      customer sees what was left out and why.
    """

    APPLIES: str = "applies"
    NEEDS_REVIEW: str = "needs_review"
    NOT_APPLICABLE: str = "not_applicable"
    # Facts a provider category is evidence for: someone who pays for pet insurance has a pet, whatever the LLM says.
    IMPLIED_BY_CATEGORY: dict[str, tuple[str, ...]] = {
        "vehicle_tax": ("has_vehicle",),
        "car_insurance": ("has_car_insurance",),
        "home_insurance": ("has_home_insurance",),
        "pet_insurance": ("has_pet",),
        "broadband": ("has_broadband",),
        "tv_license": ("has_tv_license",),
        "childcare": ("has_young_child",),
    }

    def __init__(self, data_store: HouseMoveDataStore | None = None, records: DiscoveryRecords | None = None):
        """
        :param data_store: source of the rules. Defaults to the synthetic data files.
        :param records: providers a standalone customer listed. Defaults to the SQLite database.
        """
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()
        self._records: DiscoveryRecords | None = records

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: "facts", a dict of household facts such as {"has_vehicle": true}; and "providers",
                     a list of {"category", "provider_name"} from discovery.
        :param sly_data: needs "customer_profile" and "councils" (set by council_lookup); receives "obligations".
        :return: {"obligations", "not_applicable", "excluded_legal_requirements", "fact_conflicts",
                 "other_providers", "summary"}, or an "Error: ..." string.
        """
        location_facts: dict[str, Any] | str = self._location_facts(sly_data)
        if isinstance(location_facts, str):
            return location_facts
        providers: list[dict[str, Any]] = args.get("providers") or []
        household, conflicts = self._checked_facts(args.get("facts") or {}, providers, sly_data)
        facts: dict[str, Any] = {**household, **location_facts}

        try:
            rules: list[dict[str, Any]] = self._data_store.get_obligation_rules()
        except (OSError, json.JSONDecodeError) as error:
            logger.error("Could not read the obligations rules: %s", error)
            return f"Error: the obligations rules file could not be read ({error})."

        obligations, not_applicable = self._apply(rules, facts, providers)

        sly_data["obligations"] = obligations
        used_categories: set[str] = {
            category
            for rule in rules
            for category in [rule.get("provider_category"), *rule.get("provider_categories", [])]
        }
        logger.info("Evaluated %d rules: %d obligations", len(rules), len(obligations))
        return {
            "obligations": obligations,
            "not_applicable": not_applicable,
            "excluded_legal_requirements": self._excluded_legal(rules, not_applicable, facts, household),
            "fact_conflicts": conflicts,
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

    def _apply(
        self, rules: list[dict[str, Any]], facts: dict[str, Any], providers: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """
        Evaluates every rule.
        :return: (obligations, IDs of the rules that do not apply).
        """
        obligations: list[dict[str, Any]] = []
        not_applicable: list[str] = []
        for rule in rules:
            status, missing = self._evaluate(rule.get("applies_if", []), facts)
            matching: list[dict[str, Any]] = self._providers_for(rule, providers)
            if status == ObligationsRules.NOT_APPLICABLE or (rule.get("one_task_per_provider") and not matching):
                not_applicable.append(rule.get("id"))
            elif rule.get("one_task_per_provider"):
                obligations.extend(
                    self._to_provider_obligation(rule, status, missing, provider, number)
                    for number, provider in enumerate(matching, start=1)
                )
            else:
                obligations.append(self._to_obligation(rule, status, missing, facts, providers))
        return obligations, not_applicable

    @staticmethod
    def _excluded_legal(
        rules: list[dict[str, Any]], not_applicable: list[str], facts: dict[str, Any], household: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """
        :return: each legal requirement that a household fact ruled out, with that fact, so it is never dropped
                 silently. Rules ruled out by where the customer lives are not listed.
        """
        excluded: list[dict[str, Any]] = []
        for rule in rules:
            fact: str | None = ObligationsRules._excluding_fact(rule.get("applies_if", []), facts)
            if rule.get("id") in not_applicable and rule.get("legal_requirement") and fact in household:
                excluded.append({"id": rule.get("id"), "org": rule.get("org"), "because": f"{fact} is false"})
        return excluded

    def _checked_facts(
        self, stated: dict[str, Any], providers: list[dict[str, Any]], sly_data: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        """
        Checks the LLM's household facts against the evidence held for the case. Facts the customer gave on the start
        form fill any gaps. A false fact that the evidence says is true is dropped, so its rules need review.
        :param stated: the household facts the LLM passed in.
        :param providers: the discovered providers the LLM passed in.
        :return: (checked facts, one line per conflict for the customer to settle).
        """
        profile: dict[str, Any] = sly_data.get("customer_profile") or {}
        on_file: dict[str, bool] = {
            name: value for name, value in (profile.get("facts") or {}).items() if isinstance(value, bool)
        }
        if isinstance(profile.get("has_driving_license"), bool):
            on_file.setdefault("has_driving_license", profile.get("has_driving_license"))
        evidence: dict[str, str] = {name: "you told us on the start form" for name, value in on_file.items() if value}
        for provider in [*providers, *self._listed_providers(sly_data)]:
            for name in ObligationsRules.IMPLIED_BY_CATEGORY.get(provider.get("category"), ()):
                evidence.setdefault(name, f"you pay {provider.get('provider_name')} ({provider.get('category')})")

        facts: dict[str, Any] = {**on_file, **{k: v for k, v in stated.items() if v is not None}}
        conflicts: list[str] = []
        for name, value in list(facts.items()):
            if value is False and name in evidence:
                del facts[name]
                conflicts.append(f"{name} was given as false, but {evidence.get(name)}")
        if conflicts:
            logger.warning("Household facts contradicted by evidence: %s", conflicts)
        return facts, conflicts

    def _listed_providers(self, sly_data: dict[str, Any]) -> list[dict[str, Any]]:
        """
        :return: the providers a standalone customer listed themselves, read from the case store, not from the LLM.
        """
        case_id: str | None = sly_data.get("customer_id")
        if not CaseStore.is_case(case_id):
            return []
        return (self._records or DiscoveryRecords()).get_manual_providers(case_id)

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
            # Moving within one council area is a single change of address there, not a move out and a move in.
            "same_council": ObligationsRules._same_council(old_council, new_council),
        }

    @staticmethod
    def _same_council(old_council: dict[str, Any], new_council: dict[str, Any]) -> bool:
        """
        :return: true if both postcodes are in one council: by the council's code when both lookups give one,
                 otherwise by name and country.
        """
        old_code: str | None = old_council.get("council_code")
        new_code: str | None = new_council.get("council_code")
        if old_code and new_code:
            return old_code == new_code
        return (old_council.get("council_name"), old_council.get("country")) == (
            new_council.get("council_name"),
            new_council.get("country"),
        )

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
    def _excluding_fact(conditions: list[dict[str, Any]], facts: dict[str, Any]) -> str | None:
        """
        :return: the name of the first known fact that fails a condition, or None.
        """
        for condition in conditions:
            value: Any = facts.get(condition.get("fact"))
            if value is not None and not ObligationsRules._holds(condition, value):
                return condition.get("fact")
        return None

    @staticmethod
    def _holds(condition: dict[str, Any], value: Any) -> bool:
        """
        :return: true if a known fact value meets one condition.
        """
        if "equals" in condition and value != condition.get("equals"):
            return False
        if "in" in condition and value not in condition.get("in"):
            return False
        return not ("not_in" in condition and value in condition.get("not_in"))

    @staticmethod
    def _providers_for(rule: dict[str, Any], providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        :return: the discovered providers a one-task-per-provider rule covers, in the order discovery listed them.
        """
        categories: list[str] = rule.get("provider_categories") or []
        return [p for p in providers if p.get("category") in categories and p.get("provider_name")]

    @staticmethod
    def _to_provider_obligation(
        rule: dict[str, Any], status: str, missing: list[str], provider: dict[str, Any], number: int
    ) -> dict[str, Any]:
        """
        One obligation for one provider of a one-task-per-provider rule, for example each lender the customer repays,
        so each gets its own task and letter. The ID "<rule>:<n>" keeps its task under the rule's number (T-25-1).
        """
        category: str = provider.get("category")
        name: str = provider.get("provider_name")
        return {
            **ObligationsRules._to_obligation(rule, status, missing, {}, []),
            "id": f"{rule.get('id')}:{number}",
            "org": f"{name} ({category.replace('_', ' ')})",
            "contact_name": name,
            "provider_category": category,
        }

    @staticmethod
    def _to_obligation(
        rule: dict[str, Any], status: str, missing: list[str], facts: dict[str, Any], providers: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Copies the rule's fixed fields and names the organization, adding the customer's provider when known.
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
