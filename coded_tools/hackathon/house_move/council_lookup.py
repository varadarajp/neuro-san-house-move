"""Live postcode to local council lookup via postcodes.io, with a cached fallback for the demo."""

import asyncio
import json
import logging
from typing import Any

import httpx
from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore

logger = logging.getLogger(__name__)


class CouncilLookup(CodedTool):
    """
    Finds the local council and country for a UK postcode by calling postcodes.io live.
    If postcodes.io cannot be reached, it falls back to data/council_cache.json and says so in "source".
    Each result is also saved in sly_data["councils"], keyed by postcode, for the obligations rules engine.
    """

    API_URL: str = "https://api.postcodes.io/postcodes/{postcode}"
    TIMEOUT_SECONDS: float = 5.0

    def __init__(
        self, data_store: HouseMoveDataStore | None = None, transport: httpx.AsyncBaseTransport | None = None
    ):
        """
        :param data_store: holds the fallback cache. Defaults to the synthetic data files.
        :param transport: HTTP transport override, used by unit tests to avoid the network.
        """
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()
        self._transport: httpx.AsyncBaseTransport | None = transport

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        Synchronous entry point for scripts and tests. The server calls async_invoke.
        """
        return asyncio.run(self.async_invoke(args, sly_data))

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: "postcode", a UK postcode in any spacing or case.
        :param sly_data: receives sly_data["councils"][postcode].
        :return: {"postcode", "council_name", "council_code", "country", "source"}, or an "Error: ..." string.
        """
        postcode: str = self._format(args.get("postcode", ""))
        if not postcode:
            return "Error: please give a postcode."

        council: dict[str, Any] | str | None = await self._fetch_live(postcode)
        if council is None:
            council = await asyncio.to_thread(self._from_cache, postcode)
        if isinstance(council, str):
            return council

        sly_data.setdefault("councils", {})[postcode] = council
        return council

    async def locate(self, postcode: str | None, sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        Finds a postcode's council, country and coordinates, reusing an earlier lookup from sly_data["councils"].
        Used by the GP, school and water finders.
        :param postcode: any spacing or case; None means the customer's new postcode.
        :return: the council record, or an "Error: ..." string.
        """
        profile: dict[str, Any] = sly_data.get("customer_profile") or {}
        formatted: str = self._format(postcode or (profile.get("new_address") or {}).get("postcode") or "")
        if not formatted:
            return "Error: no postcode given and no customer profile loaded."
        known: dict[str, Any] | None = (sly_data.get("councils") or {}).get(formatted)
        if known and known.get("latitude") is not None:
            return known
        return await self.async_invoke({"postcode": formatted}, sly_data)

    async def _fetch_live(self, postcode: str) -> dict[str, Any] | str | None:
        """
        :return: the council from postcodes.io, an error string if the postcode is not valid,
                 or None if the service could not be reached so the caller should try the cache.
        """
        url: str = CouncilLookup.API_URL.format(postcode=postcode.replace(" ", ""))
        try:
            async with httpx.AsyncClient(timeout=CouncilLookup.TIMEOUT_SECONDS, transport=self._transport) as client:
                response: httpx.Response = await client.get(url)
        except httpx.HTTPError as error:
            logger.warning("postcodes.io unreachable for %s, using cache: %s", postcode, error)
            return None

        if response.status_code == 404:
            return f"Error: {postcode} is not a valid UK postcode."
        if response.status_code != 200:
            logger.warning("postcodes.io returned %s for %s, using cache", response.status_code, postcode)
            return None

        result: dict[str, Any] = response.json().get("result") or {}
        codes: dict[str, Any] = result.get("codes") or {}
        return {
            "postcode": result.get("postcode", postcode),
            "council_name": result.get("admin_district"),
            "council_code": codes.get("admin_district"),
            "country": result.get("country"),
            "eastings": result.get("eastings"),
            "northings": result.get("northings"),
            "latitude": result.get("latitude"),
            "longitude": result.get("longitude"),
            "source": "postcodes.io (live)",
        }

    def _from_cache(self, postcode: str) -> dict[str, Any] | str:
        """
        :return: the cached council marked as cached, or an error string if it is not cached either.
        """
        try:
            cached: dict[str, Any] | None = self._data_store.get_cached_council(postcode)
        except (OSError, json.JSONDecodeError) as error:
            logger.error("Could not read the council cache: %s", error)
            return f"Error: postcodes.io is unreachable and the cache could not be read ({error})."
        if cached is None:
            return f"Error: postcodes.io is unreachable and {postcode} is not cached."
        return {**cached, "source": "cache (postcodes.io unreachable)"}

    @staticmethod
    def _format(raw_postcode: str) -> str:
        """
        Upper-cases a postcode and puts the single space before the last three characters: "ls13ed" -> "LS1 3ED".
        """
        compact: str = "".join(raw_postcode.split()).upper()
        if len(compact) < 5:
            return compact
        return f"{compact[:-3]} {compact[-3:]}"
