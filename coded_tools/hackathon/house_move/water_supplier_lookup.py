"""Which water and sewerage companies serve the new address."""

import asyncio
import json
import logging
from typing import Any

from coded_tools.hackathon.house_move.case_tool import CaseTool
from coded_tools.hackathon.house_move.council_lookup import CouncilLookup
from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore

logger = logging.getLogger(__name__)


class WaterSupplierLookup(CaseTool):
    """
    Names the water supplier and sewerage provider for a postcode. There is no public API for this, so England and
    Wales use a small checked table by postcode district (data/water_suppliers.json) and always include the official
    checker link; an unknown district returns the checker link only. Scotland and Northern Ireland have one public
    water company each, and households there do not open a separate water account.
    """

    FILE_NAME: str = "water_suppliers.json"
    NATIONAL: dict[str, tuple[str, str]] = {
        "Scotland": ("scottish", "Charges for unmetered homes are collected with council tax: no separate account."),
        "Northern Ireland": ("ni", "Households in Northern Ireland do not get a separate water bill to set up."),
    }

    def __init__(self, council_lookup: CouncilLookup | None = None, data_store: HouseMoveDataStore | None = None):
        """
        :param council_lookup: locates the postcode. Defaults to the live lookup with cached fallback.
        :param data_store: holds the supplier table.
        """
        self._council_lookup: CouncilLookup = council_lookup or CouncilLookup()
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()

    async def arun(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: optional "postcode" (defaults to the new address).
        :param sly_data: reads "customer_profile" and "councils".
        :return: {"postcode", "water_supplier", "sewerage_provider", "confirmed", "note", "check_at"}, or an error.
        """
        place: dict[str, Any] | str = await self._council_lookup.locate(args.get("postcode"), sly_data)
        if isinstance(place, str):
            return place
        try:
            table: dict[str, Any] = await asyncio.to_thread(self._data_store.get_section, self.FILE_NAME, "districts")
            companies: dict[str, Any] = await asyncio.to_thread(
                self._data_store.get_section, self.FILE_NAME, "companies"
            )
            checker: str = await asyncio.to_thread(self._data_store.get_section, self.FILE_NAME, "checker_url")
        except (OSError, json.JSONDecodeError) as error:
            logger.error("Could not read the water supplier table: %s", error)
            return f"Error: the water supplier table could not be read ({error})."

        postcode: str = place.get("postcode", "")
        national: tuple[str, str] | None = WaterSupplierLookup.NATIONAL.get(place.get("country"))
        if national:
            company: dict[str, Any] = companies.get(national[0])
            return {
                "postcode": postcode,
                "water_supplier": company,
                "sewerage_provider": company,
                "confirmed": True,
                "note": national[1],
                "check_at": None,
            }

        entry: dict[str, Any] | None = (table or {}).get(postcode.split()[0])
        if entry is None:
            return {
                "postcode": postcode,
                "water_supplier": None,
                "sewerage_provider": None,
                "confirmed": False,
                "note": "Not in our table. Find both companies with the official postcode checker.",
                "check_at": checker,
            }
        return {
            "postcode": postcode,
            "water_supplier": companies.get(entry.get("water")),
            "sewerage_provider": companies.get(entry.get("sewerage")),
            "confirmed": entry.get("confirmed", False),
            "note": entry.get("note")
            or "Supply areas can split within a postcode district: confirm with the checker.",
            "check_at": checker,
        }
