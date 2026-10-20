"""Fills {{placeholders}} in a draft with the customer's details from sly_data."""

import re
from datetime import date
from typing import Any


class DraftRenderer:
    """
    Replaces profile and task placeholders with real values, and customer-supplied ones with a visible gap
    such as "[add your account number]". Any other placeholder is reported as unknown.
    A profile value that is missing or blank is a gap too, never an empty string: a letter without a name, street
    or email must go to the customer to complete, not to the organization. An address counts only with its street.
    When the bank holds a Direct Debit mandate with the task's provider, the mandate's payer reference fills that
    provider's account, policy or license number, so the customer does not have to look it up.
    Used when a draft is saved, and again by the Tracker when it is sent.
    """

    PLACEHOLDER: re.Pattern = re.compile(r"\{\{\s*(\w+)\s*\}\}")

    def __init__(self, customer_supplied: list[str]):
        """
        :param customer_supplied: placeholder names the customer fills in, from letter_templates.json.
        """
        self._customer_supplied: frozenset[str] = frozenset(customer_supplied)

    def render(self, text: str, task: dict[str, Any], sly_data: dict[str, Any]) -> tuple[str, list[str], list[str]]:
        """
        :param text: draft text containing {{placeholders}}.
        :param task: the task being drafted, for org_name and url.
        :param sly_data: holds "customer_profile" and "plan_dates".
        :return: (rendered text, customer gaps left in it, unknown placeholder names).
        """
        values: dict[str, str] = self._values(task, sly_data)
        gaps: list[str] = []
        unknown: list[str] = []

        def replace(match: re.Match) -> str:
            # Nested so re.sub can collect gaps and unknowns from this call's state.
            name: str = match.group(1)
            if (values.get(name) or "").strip():
                return values.get(name)
            if name in values or name in self._customer_supplied:
                label: str = name.replace("_", " ")
                gaps.append(label)
                return f"[add your {label}]"
            unknown.append(name)
            return match.group(0)

        rendered: str = DraftRenderer.PLACEHOLDER.sub(replace, text)
        return rendered, sorted(set(gaps)), sorted(set(unknown))

    @staticmethod
    def _values(task: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, str]:
        """
        Builds the placeholder values from the full profile, which only code sees.
        """
        profile: dict[str, Any] = sly_data.get("customer_profile") or {}
        plan_dates: dict[str, Any] = sly_data.get("plan_dates") or {}
        names: list[str] = profile.get("names") or []
        old_address: dict[str, Any] = profile.get("old_address") or {}
        new_address: dict[str, Any] = profile.get("new_address") or {}
        values: dict[str, str] = {
            # A standalone record keeps full names apart ("full_names"); a bank record's "names" are full names.
            "full_names": " and ".join(profile.get("full_names") if "full_names" in profile else names),
            "first_names": " and ".join(name.split()[0] for name in names),
            "old_address": DraftRenderer._one_line(old_address),
            "new_address": DraftRenderer._one_line(new_address),
            "old_postcode": old_address.get("postcode", ""),
            "new_postcode": new_address.get("postcode", ""),
            "move_date": DraftRenderer._long_date(plan_dates.get("completion_date") or profile.get("completion_date")),
            "exchange_date": DraftRenderer._long_date(plan_dates.get("exchange_date") or profile.get("exchange_date")),
            "customer_email": profile.get("email", ""),
            "org_name": task.get("contact_name") or task.get("org", ""),
            "url": task.get("contact_url") or "",
        }
        for mandate in sly_data.get("mandates") or []:
            if task.get("provider_category") and mandate.get("category") == task.get("provider_category"):
                values[mandate.get("placeholder")] = mandate.get("reference")
        return values

    @staticmethod
    def _one_line(address: dict[str, Any]) -> str:
        """
        Joins the street line, town and postcode. Without a street line there is no address to write to, so the
        result is blank (a gap) rather than just a town and postcode.
        """
        if not (address.get("line1") or "").strip():
            return ""
        return ", ".join(part for part in (address.get("line1"), address.get("town"), address.get("postcode")) if part)

    @staticmethod
    def _long_date(iso_date: str | None) -> str:
        """
        Formats 2026-11-27 as "27 November 2026".
        """
        if not iso_date:
            return ""
        day: date = date.fromisoformat(iso_date)
        return f"{day.day} {day.strftime('%B %Y')}"
