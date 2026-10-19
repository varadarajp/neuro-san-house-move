"""Starts a standalone move case for someone who is not (or not yet) a bank customer."""

import asyncio
import logging
from datetime import date
from typing import Any

from coded_tools.hackathon.house_move.case_session import CaseSession
from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.case_tool import CaseTool
from coded_tools.hackathon.house_move.council_lookup import CouncilLookup
from coded_tools.hackathon.house_move.move_dates import MoveDates

logger = logging.getLogger(__name__)


class CreateCase(CaseTool):
    """
    Creates a case from the few facts gathered in chat: first names, both postcodes and the move date.
    Postcodes are checked with council_lookup, which also records both councils.
    Personal details (full names, street lines, email) never come through chat: the customer types them into the
    portal's "Your details" form, which holds them encrypted (CaseStore.save_pending_intake) until this tool moves them
    into the new case's vault. Anything a client puts in sly_data["intake_form"] is discarded, since it did not come
    through that form. The case belongs to the signed-in owner and looks like a CRM record to every other tool.
    """

    FACT_NAMES: tuple[str, ...] = (
        "has_driving_licence",
        "has_vehicle",
        "has_broadband",
        "has_tv_licence",
        "wants_mail_redirection",
        "has_home_insurance",
        "has_car_insurance",
        "has_pet",
        "has_young_child",
    )

    NEEDS_CASE: bool = False  # This is the tool that creates a case.

    def __init__(
        self,
        case_store: CaseStore | None = None,
        council_lookup: CouncilLookup | None = None,
        today: date | None = None,
    ):
        """
        :param case_store: where the case is saved. Defaults to the SQLite case store.
        :param council_lookup: validates postcodes and finds councils. Defaults to the live lookup.
        :param today: the date the move date is checked against. Defaults to the real date.
        """
        self._case_store: CaseStore | None = case_store
        self._council_lookup: CouncilLookup = council_lookup or CouncilLookup()
        self._today: date | None = today

    async def arun(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: "first_names" (list), "old_postcode", "new_postcode", "move_date" (YYYY-MM-DD), optional
                     "move_date_is_estimate", "exchange_date", "old_property" (sold, let, rented_ended, kept) and
                     "facts" (true/false household facts).
        :param sly_data: discards any "intake_form"; receives "customer_id", "customer_profile" and "councils".
        :return: the case summary, without personal data, or an "Error: ..." string.
        """
        first_names: list[str] = [name.strip() for name in args.get("first_names") or [] if name.strip()]
        if not first_names:
            return "Error: ask for the first name of each adult who is moving."
        try:
            move_date: date = date.fromisoformat(args.get("move_date") or "")
            exchange_date: date | None = (
                date.fromisoformat(args.get("exchange_date")) if args.get("exchange_date") else None
            )
        except ValueError:
            return "Error: give the move date (or best estimate) as YYYY-MM-DD."
        date_error: str | None = MoveDates.check(move_date, exchange_date, self._today or date.today())
        if date_error is not None:
            return date_error

        councils: dict[str, dict[str, Any]] = {}
        for key in ("old_postcode", "new_postcode"):
            council: dict[str, Any] | str = await self._council_lookup.lookup(args.get(key, ""))
            if isinstance(council, str):
                return f"Error with the {key.replace('_', ' ')}: {council}"
            councils[key] = council
            sly_data.setdefault("councils", {})[council.get("postcode")] = council

        profile: dict[str, Any] = self._profile(args, first_names, councils, move_date, exchange_date)
        if sly_data.pop("intake_form", None) is not None:
            logger.warning("Discarded intake_form sent by the client: personal details come only from the portal")
        store: CaseStore = self._case_store or await asyncio.to_thread(CaseStore)
        owner: str | None = sly_data.get(CaseSession.OWNER_KEY)
        if not owner:
            return "Error: not signed in. Sign in on the House Move portal first."
        form: dict[str, Any] = await asyncio.to_thread(store.take_pending_intake, owner)
        case_id: str = await asyncio.to_thread(store.create_case, profile, form, owner)

        sly_data["customer_id"] = case_id
        sly_data["customer_profile"] = await asyncio.to_thread(store.get_customer, case_id)
        return {
            "case_id": case_id,
            "first_names": first_names,
            "old_council": councils.get("old_postcode"),
            "new_council": councils.get("new_postcode"),
            "move_date": move_date.isoformat(),
            "move_date_is_estimate": bool(args.get("move_date_is_estimate")),
            "facts": profile.get("facts"),
            "personal_details_from_form": sorted(key for key in form if key in CaseStore.PERSONAL_FIELDS),
            "next": "Ask how they want to share who they pay: upload a statement, or list providers."
            + (
                ""
                if form
                else " Their name, street addresses and email are not on file yet: ask them to add them under "
                "'Your details' on the portal (never in the chat), or letters will come back to them to complete."
            ),
        }

    def after_run(self, result: Any, sly_data: dict[str, Any], session: CaseSession) -> None:
        """
        Makes the new case the owner's active case, keeping the councils just looked up.
        """
        if isinstance(result, dict) and result.get("case_id"):
            session.select_case(sly_data, result.get("case_id"), load=False)

    @staticmethod
    def _profile(
        args: dict[str, Any],
        first_names: list[str],
        councils: dict[str, dict[str, Any]],
        move_date: date,
        exchange_date: date | None,
    ) -> dict[str, Any]:
        """
        Builds the non-personal profile in the same shape as a CRM record.
        """
        facts: dict[str, Any] = {
            name: value
            for name, value in (args.get("facts") or {}).items()
            if name in CreateCase.FACT_NAMES and isinstance(value, bool)
        }
        old: dict[str, Any] = councils.get("old_postcode") or {}
        new: dict[str, Any] = councils.get("new_postcode") or {}
        return {
            "first_names": first_names,
            "household": {"adults": len(first_names), "children": None, "pets": None},
            "old_address": {"town": old.get("council_name"), "postcode": old.get("postcode")},
            "new_address": {"town": new.get("council_name"), "postcode": new.get("postcode")},
            "completion_date": move_date.isoformat(),
            "exchange_date": exchange_date.isoformat() if exchange_date else None,
            "move_date_is_estimate": bool(args.get("move_date_is_estimate")),
            "old_property_retained": args.get("old_property"),
            "has_driving_licence": facts.get("has_driving_licence"),
            "vehicles": None,
            "employment": None,
            "products_held": [],
            "facts": facts,
            "source": "standalone",
        }
