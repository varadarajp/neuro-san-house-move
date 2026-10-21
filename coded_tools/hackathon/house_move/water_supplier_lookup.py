"""Which water and sewerage companies serve the new address."""

import asyncio
import json
import logging
from typing import Any

from coded_tools.hackathon.house_move.case_tool import CaseTool
from coded_tools.hackathon.house_move.council_lookup import CouncilLookup
from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore
from coded_tools.hackathon.house_move.ofwat_area_lookup import OfwatAreaLookup

logger = logging.getLogger(__name__)


class WaterSupplierLookup(CaseTool):
    """
    Names the water supplier and sewerage provider for a postcode. In England and Wales it asks Ofwat's appointed
    area maps live, at the postcode's coordinates (see OfwatAreaLookup), and names each company as customers know it
    (data/water_suppliers.json: Ofwat's Northumbrian Water trades as Essex & Suffolk Water in Essex, for example).
    If that service is unavailable it falls back to a small table by postcode district, with the official checker
    link; an unknown district returns the checker link only. Scotland and Northern Ireland have one public water
    company each, and households there do not open a separate water account.
    """

    FILE_NAME: str = "water_suppliers.json"
    NATIONAL: dict[str, tuple[str, str]] = {
        "Scotland": ("scottish", "Charges for unmetered homes are collected with council tax: no separate account."),
        "Northern Ireland": ("ni", "Households in Northern Ireland do not get a separate water bill to set up."),
    }

    def __init__(
        self,
        council_lookup: CouncilLookup | None = None,
        data_store: HouseMoveDataStore | None = None,
        area_lookup: OfwatAreaLookup | None = None,
    ):
        """
        :param council_lookup: locates the postcode. Defaults to the live lookup with cached fallback.
        :param data_store: holds the company names and the fallback table.
        :param area_lookup: finds the appointed companies at a point. Defaults to the live Ofwat areas.
        """
        self._council_lookup: CouncilLookup = council_lookup or CouncilLookup()
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()
        self._area_lookup: OfwatAreaLookup = area_lookup or OfwatAreaLookup()

    async def arun(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: optional "postcode" (defaults to the new address).
        :param sly_data: reads "customer_profile" and "councils".
        :return: {"postcode", "water_supplier", "sewerage_provider", "confirmed", "source", "note", "check_at"}, or
                 an error.
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
            names: dict[str, Any] = await asyncio.to_thread(
                self._data_store.get_section, self.FILE_NAME, "ofwat_names"
            )
            trading: dict[str, Any] = await asyncio.to_thread(
                self._data_store.get_section, self.FILE_NAME, "trading_names"
            )
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
                "source": "national company",
                "note": national[1],
                "check_at": None,
            }

        if place.get("latitude") is not None and place.get("longitude") is not None:
            areas: dict[str, list[dict[str, Any]]] | None = await self._area_lookup.areas_at(
                place.get("latitude"), place.get("longitude")
            )
            if areas and areas.get("water") and areas.get("sewerage"):
                return {
                    "postcode": postcode,
                    "water_supplier": self._company(areas.get("water"), companies, names, trading),
                    "sewerage_provider": self._company(areas.get("sewerage"), companies, names, trading),
                    "confirmed": True,
                    "source": OfwatAreaLookup.SOURCE,
                    "note": "From Ofwat's official area maps for this postcode. A few new-build streets are served by "
                    "a different company: the checker confirms an exact address.",
                    "check_at": checker,
                }
            logger.info("No live Ofwat answer for %s: using the table", postcode)

        entry: dict[str, Any] | None = (table or {}).get(postcode.split()[0])
        if entry is None:
            return {
                "postcode": postcode,
                "water_supplier": None,
                "sewerage_provider": None,
                "confirmed": False,
                "source": "none",
                "note": "Not in our table. Find both companies with the official postcode checker.",
                "check_at": checker,
            }
        return {
            "postcode": postcode,
            "water_supplier": companies.get(entry.get("water")),
            "sewerage_provider": companies.get(entry.get("sewerage")),
            "confirmed": entry.get("confirmed", False),
            "source": "our table (the live Ofwat lookup was unavailable)",
            "note": entry.get("note")
            or "Supply areas can split within a postcode district: confirm with the checker.",
            "check_at": checker,
        }

    @staticmethod
    def _company(
        found: list[dict[str, Any]], companies: dict[str, Any], names: dict[str, Any], trading: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Names the company appointed at the point as customers know it. A new appointee (NAV) site lies inside a
        regional company's area, so when both are found the more specific new appointee is named.
        """
        area: dict[str, Any] = next(
            (a for a in found if not (a.get("company_type") or "").startswith("regional")), found[0]
        )
        key: str | None = trading.get(f"{area.get('company')}|{area.get('area')}") or names.get(area.get("company"))
        return companies.get(key) or {"name": area.get("company"), "website": None}
