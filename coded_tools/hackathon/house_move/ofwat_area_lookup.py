"""Ofwat's appointed water supply and sewerage areas, looked up live at a point."""

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class OfwatAreaLookup:  # pylint: disable=too-few-public-methods  # one lookup
    """
    Finds which company is appointed to supply water, and which to provide sewerage, at a point in England or Wales.

    The areas are Ofwat's boundaries (version 1.5, April 2024), digitised from the companies' legal appointments with
    Ordnance Survey and published under the Open Government Licence. They include new appointees (NAVs), which serve
    some new-build sites inside a regional company's area. The water industry's Stream open data hub serves them as
    ArcGIS feature layers, queried here by point. Ofwat notes the maps are for analysis: the legal record remains the
    appointments themselves.
    """

    BASE_URL: str = "https://services-eu1.arcgis.com/XxS6FebPX29TRGDJ/arcgis/rest/services"
    LAYERS: dict[str, str] = {
        "water": "WaterSupplyAreas_incNAVsv1_5",
        "sewerage": "SewerageServicesAreas_incNAVsv1_5a",
    }
    SOURCE: str = "Ofwat's appointed water supply and sewerage areas (April 2024), via the Stream open data hub"
    TIMEOUT_SECONDS: float = 8.0

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        """
        :param transport: HTTP transport override, used by unit tests to avoid the network.
        """
        self._transport: httpx.AsyncBaseTransport | None = transport

    async def areas_at(self, latitude: float, longitude: float) -> dict[str, list[dict[str, Any]]] | None:
        """
        :return: {"water": [...], "sewerage": [...]}, each a list of {"company", "area", "company_type"} for every
                 appointed area containing the point (a NAV site lies inside a regional area, so there can be two),
                 or None if the service could not be reached or answered with an error.
        """
        try:
            async with httpx.AsyncClient(timeout=OfwatAreaLookup.TIMEOUT_SECONDS, transport=self._transport) as client:
                water, sewerage = await asyncio.gather(
                    *(self._query(client, layer, latitude, longitude) for layer in OfwatAreaLookup.LAYERS.values())
                )
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("Ofwat area lookup unavailable: %s", error)
            return None
        return {"water": water, "sewerage": sewerage}

    @staticmethod
    async def _query(client: httpx.AsyncClient, layer: str, latitude: float, longitude: float) -> list[dict[str, Any]]:
        """
        Asks one layer which areas contain the point.
        :raises httpx.HTTPError: on a failed request or HTTP error status.
        :raises ValueError: on a reply that is not the expected JSON, including an ArcGIS error body.
        """
        response: httpx.Response = await client.get(
            f"{OfwatAreaLookup.BASE_URL}/{layer}/FeatureServer/0/query",
            params={
                "geometry": f"{longitude},{latitude}",
                "geometryType": "esriGeometryPoint",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "COMPANY,AreaServed,CoType",
                "returnGeometry": "false",
                "f": "json",
            },
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        if "features" not in body:
            raise ValueError(f"unexpected reply from {layer}: {body.get('error') or body}")
        return [
            {
                "company": (feature.get("attributes") or {}).get("COMPANY"),
                "area": (feature.get("attributes") or {}).get("AreaServed"),
                "company_type": (feature.get("attributes") or {}).get("CoType"),
            }
            for feature in body.get("features") or []
            if (feature.get("attributes") or {}).get("COMPANY")
        ]
