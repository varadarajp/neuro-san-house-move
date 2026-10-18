"""Schools near the new address, from the Department for Education's Get Information About Schools register."""

import asyncio
import csv
import logging
from pathlib import Path
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.hackathon.house_move.council_lookup import CouncilLookup
from coded_tools.hackathon.house_move.geo_distance import GeoDistance

logger = logging.getLogger(__name__)


class SchoolFinder(CodedTool):
    """
    Lists the open schools nearest a postcode in England, for each child's age or for chosen phases, with contact
    details and straight-line distance. Data: data/schools_england.csv, built from GIAS by
    build_scripts/build_house_move_schools.py (Open Government Licence v3.0).
    Distance is not catchment: places are allocated by the council's admissions rules.
    """

    DATA_FILE: Path = Path(__file__).parent / "data" / "schools_england.csv"
    PHASES: dict[str, frozenset[str]] = {
        "nursery": frozenset({"Nursery"}),
        "primary": frozenset({"Primary", "Middle deemed primary", "All-through"}),
        "secondary": frozenset({"Secondary", "Middle deemed secondary", "All-through"}),
        "sixth_form": frozenset({"16 plus", "Secondary", "All-through"}),
    }
    OTHER_NATIONS: dict[str, str] = {
        "Wales": "Contact the new council's school admissions team; Welsh schools are listed at https://mylocalschool.gov.wales",
        "Scotland": (
            "Contact the new council's education department; Scottish schools have catchment areas set by the council."
        ),
        "Northern Ireland": "Apply through the Education Authority: https://www.eani.org.uk",
    }
    DEFAULT_LIMIT: int = 5
    DEFAULT_RADIUS_KM: float = 5.0
    _schools: list[dict[str, Any]] | None = None  # Loaded once per process; the file is read-only at run time.

    def __init__(self, council_lookup: CouncilLookup | None = None):
        """
        :param council_lookup: locates the postcode. Defaults to the live lookup with cached fallback.
        """
        self._council_lookup: CouncilLookup = council_lookup or CouncilLookup()

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        Synchronous entry point for scripts and tests. The server calls async_invoke.
        """
        return asyncio.run(self.async_invoke(args, sly_data))

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: optional "postcode" (defaults to the new address), "child_ages" (list of ages in September),
                     "phases" (nursery, primary, secondary, sixth_form), "limit" (per group, default 5) and
                     "radius_km" (default 5).
        :param sly_data: reads "customer_profile" and "councils".
        :return: {"postcode", "council", "groups": {label: [schools]}, "admissions", "source"}, or an error string.
        """
        place: dict[str, Any] | str = await self._council_lookup.locate(args.get("postcode"), sly_data)
        if isinstance(place, str):
            return place
        country: str = place.get("country") or ""
        if country != "England":
            return {
                "postcode": place.get("postcode"),
                "council": place.get("council_name"),
                "groups": {},
                "admissions": SchoolFinder.OTHER_NATIONS.get(country, "Contact the new council."),
                "source": None,
            }
        try:
            schools: list[dict[str, Any]] = await asyncio.to_thread(self._load)
        except OSError as error:
            logger.error("Could not read the schools file: %s", error)
            return (
                f"Error: the schools list could not be read ({error}). Run build_scripts/build_house_move_schools.py."
            )

        limit: int = int(args.get("limit") or SchoolFinder.DEFAULT_LIMIT)
        radius: float = float(args.get("radius_km") or SchoolFinder.DEFAULT_RADIUS_KM)
        groups: dict[str, list[dict[str, Any]]] = {}
        for age in args.get("child_ages") or []:
            groups[f"for a child aged {age}"] = self._nearest(
                place, [s for s in schools if s.get("low_age") <= int(age) <= s.get("high_age")], limit, radius
            )
        for phase in args.get("phases") or ([] if args.get("child_ages") else ["primary", "secondary"]):
            allowed: frozenset[str] = SchoolFinder.PHASES.get(phase, frozenset())
            groups[phase] = self._nearest(place, [s for s in schools if s.get("phase") in allowed], limit, radius)
        return {
            "postcode": place.get("postcode"),
            "council": place.get("council_name"),
            "groups": groups,
            "admissions": f"Places are allocated by {place.get('council_name')} council's school admissions team, not "
            f"by distance alone.",
            "childcare": "Private nurseries and childminders are not in this list. Find local childcare and free "
            "early education through the council: https://www.gov.uk/find-free-early-education",
            "source": "Get Information About Schools, Department for Education (Open Government Licence v3.0)",
        }

    @staticmethod
    def _nearest(
        place: dict[str, Any], candidates: list[dict[str, Any]], limit: int, radius_km: float
    ) -> list[dict[str, Any]]:
        """
        :return: up to limit candidates within radius_km, nearest first, in a customer-facing shape.
        """
        measured: list[tuple[float, dict[str, Any]]] = [
            (
                GeoDistance.between_grid(
                    place.get("eastings"), place.get("northings"), school.get("easting"), school.get("northing")
                ),
                school,
            )
            for school in candidates
        ]
        ranked: list[tuple[float, dict[str, Any]]] = sorted(
            (pair for pair in measured if pair[0] <= radius_km), key=lambda pair: pair[0]
        )[:limit]
        return [
            {
                "name": school.get("name"),
                "type": school.get("type"),
                "ages": f"{school.get('low_age')} to {school.get('high_age')}",
                "address": ", ".join(
                    p for p in (school.get("street"), school.get("town"), school.get("postcode")) if p
                ),
                "phone": school.get("telephone") or None,
                "website": school.get("website") or None,
                "distance_km": round(distance, 1),
            }
            for distance, school in ranked
        ]

    @classmethod
    def _load(cls) -> list[dict[str, Any]]:
        """
        Reads the extract once, converting ages and grid references to numbers. Rows without ages are skipped.
        """
        if cls._schools is None:
            schools: list[dict[str, Any]] = []
            with cls.DATA_FILE.open(encoding="utf-8") as data_file:
                for row in csv.DictReader(data_file):
                    try:
                        row["low_age"], row["high_age"] = int(row.get("low_age")), int(row.get("high_age"))
                        row["easting"], row["northing"] = float(row.get("easting")), float(row.get("northing"))
                    except (TypeError, ValueError):
                        continue
                    schools.append(row)
            cls._schools = schools
        return cls._schools
