"""GP practices near the new address, from the NHS Organisation Data Service (live)."""

import asyncio
import json
import logging
import string
from typing import Any

import httpx

from coded_tools.hackathon.house_move.case_tool import CaseTool
from coded_tools.hackathon.house_move.council_lookup import CouncilLookup
from coded_tools.hackathon.house_move.geo_distance import GeoDistance
from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore

logger = logging.getLogger(__name__)


class GpFinder(CaseTool):
    """
    Lists the GP practices nearest a postcode in England or Wales, with address, phone and straight-line distance.
    Practices come from the NHS Organisation Data Service (ODS), searched by postcode district; their locations come
    from postcodes.io. Whether a practice covers the address or is taking new patients is not in ODS, so the result
    points to the NHS service search for that. Falls back to demo data if the services cannot be reached.
    """

    ODS_SEARCH: str = (
        "https://directory.spineservices.nhs.uk/ORD/2-0-0/organisations"
        "?PostCode={district}&PrimaryRoleId=RO177&NonPrimaryRoleId=RO76&Status=Active&Limit=200"
    )
    ODS_DETAIL: str = "https://directory.spineservices.nhs.uk/ORD/2-0-0/organisations/{code}"
    POSTCODES_BULK: str = "https://api.postcodes.io/postcodes"
    FIND_A_GP: dict[str, str] = {
        "England": "https://www.nhs.uk/service-search/find-a-gp",
        "Wales": "https://111.wales.nhs.uk/localservices/",
        "Scotland": "https://www.nhsinform.scot/scotlands-service-directory/gp-practices",
        "Northern Ireland": "https://www.nidirect.gov.uk/articles/your-local-doctor-gp",
    }
    ODS_COUNTRIES: frozenset[str] = frozenset({"England", "Wales"})
    TIMEOUT_SECONDS: float = 10.0
    DEFAULT_LIMIT: int = 5

    def __init__(
        self,
        council_lookup: CouncilLookup | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        data_store: HouseMoveDataStore | None = None,
    ):
        """
        :param council_lookup: locates the postcode. Defaults to the live lookup with cached fallback.
        :param transport: HTTP transport override, used by unit tests to avoid the network.
        :param data_store: holds the demo fallback list.
        """
        self._council_lookup: CouncilLookup = council_lookup or CouncilLookup()
        self._transport: httpx.AsyncBaseTransport | None = transport
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()

    async def arun(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: optional "postcode" (defaults to the new address) and "limit" (default 5).
        :param sly_data: reads "customer_profile" and "councils".
        :return: {"postcode", "practices", "source", "how_to_register"}, or an "Error: ..." string.
        """
        place: dict[str, Any] | str = await self._council_lookup.locate(args.get("postcode"), sly_data)
        if isinstance(place, str):
            return place
        country: str = place.get("country") or ""
        how_to: str = (
            f"Check which practices cover the address and are taking new patients at "
            f"{GpFinder.FIND_A_GP.get(country, GpFinder.FIND_A_GP.get('England'))}."
        )
        if country == "England":
            how_to += " You do not need ID, proof of address or an NHS number to register."
        if country not in GpFinder.ODS_COUNTRIES:
            return {"postcode": place.get("postcode"), "practices": [], "source": None, "how_to_register": how_to}

        limit: int = int(args.get("limit") or GpFinder.DEFAULT_LIMIT)
        try:
            practices: list[dict[str, Any]] = await self._nearest(place, limit)
            source: str = "NHS Organisation Data Service (live)"
        except (httpx.HTTPError, ValueError, KeyError) as error:
            logger.warning("GP lookup failed, using demo data: %s", error)
            practices = await asyncio.to_thread(self._demo_practices, place)
            source = "demo data (NHS service unreachable)"
        return {"postcode": place.get("postcode"), "practices": practices, "source": source, "how_to_register": how_to}

    async def _nearest(self, place: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        """
        Searches ODS by postcode district, measures each practice's distance and adds address and phone for the
        nearest ones.
        """
        district: str = place.get("postcode", "").split()[0]
        async with httpx.AsyncClient(timeout=GpFinder.TIMEOUT_SECONDS, transport=self._transport) as client:
            response: httpx.Response = await client.get(GpFinder.ODS_SEARCH.format(district=district))
            response.raise_for_status()
            found: list[dict[str, Any]] = [
                org
                for org in response.json().get("Organisations", [])
                if (org.get("PostCode") or "").startswith(district + " ")
            ]
            if not found:
                return []
            located: dict[str, dict[str, Any]] = await self._locate_postcodes(
                client, sorted({org.get("PostCode") for org in found})
            )
            for org in found:
                where: dict[str, Any] | None = located.get(org.get("PostCode"))
                org["distance_km"] = (
                    round(
                        GeoDistance.between_lat_lon(
                            place.get("latitude"),
                            place.get("longitude"),
                            where.get("latitude"),
                            where.get("longitude"),
                        ),
                        1,
                    )
                    if where
                    else None
                )
            nearest: list[dict[str, Any]] = sorted(
                found, key=lambda org: org.get("distance_km") if org.get("distance_km") is not None else 999
            )[:limit]
            details: list[dict[str, Any]] = await asyncio.gather(
                *(self._detail(client, org.get("OrgId")) for org in nearest)
            )
        return [
            {
                "practice_name": string.capwords(org.get("Name", "").lower()),
                "ods_code": org.get("OrgId"),
                "address": detail.get("address"),
                "postcode": org.get("PostCode"),
                "phone": detail.get("phone"),
                "distance_km": org.get("distance_km"),
            }
            for org, detail in zip(nearest, details)
        ]

    @staticmethod
    async def _locate_postcodes(client: httpx.AsyncClient, postcodes: list[str]) -> dict[str, dict[str, Any]]:
        """
        Looks up up to 100 postcodes at a time with postcodes.io.
        """
        located: dict[str, dict[str, Any]] = {}
        for start in range(0, len(postcodes), 100):
            response: httpx.Response = await client.post(
                GpFinder.POSTCODES_BULK, json={"postcodes": postcodes[start : start + 100]}
            )
            response.raise_for_status()
            for item in response.json().get("result", []):
                if item.get("result"):
                    located[item.get("query")] = item.get("result")
        return located

    @staticmethod
    async def _detail(client: httpx.AsyncClient, code: str) -> dict[str, Any]:
        """
        :return: the practice's first address line, town and phone number from its ODS record.
        """
        response: httpx.Response = await client.get(GpFinder.ODS_DETAIL.format(code=code))
        if response.status_code != 200:
            return {}
        record: dict[str, Any] = response.json().get("Organisation", {})
        location: dict[str, Any] = (record.get("GeoLoc") or {}).get("Location", {})
        phones: list[str] = [
            contact.get("value")
            for contact in (record.get("Contacts") or {}).get("Contact", [])
            if contact.get("type") == "tel"
        ]
        address: str = ", ".join(
            string.capwords(part.lower()) for part in (location.get("AddrLn1"), location.get("Town")) if part
        )
        return {"address": address, "phone": phones[0] if phones else None}

    def _demo_practices(self, place: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Fictional practices from data/gp_practices.json, used only when the NHS service cannot be reached.
        """
        try:
            practices: dict[str, Any] = self._data_store.get_section("gp_practices.json", "practices") or {}
        except (OSError, json.JSONDecodeError) as error:
            logger.error("Could not read demo GP practices: %s", error)
            return []
        return practices.get(place.get("postcode", "").split()[0], [])
