"""
Builds coded_tools/hackathon/house_move/data/schools_england.csv from the Department for Education's
Get Information About Schools (GIAS) daily extract.

Run from the repository root (downloads about 60 MB):
    python -m build_scripts.build_house_move_schools
Contains public sector information licensed under the Open Government Licence v3.0.
"""

import csv
import io
import logging
from datetime import date
from datetime import timedelta
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


class SchoolExtractBuilder:
    """
    Downloads the latest GIAS establishment file, keeps open schools with a location, and writes only the columns
    the school finder needs.
    """

    URL: str = "https://ea-edubase-api-prod.azurewebsites.net/edubase/downloads/public/edubasealldata{day}.csv"
    TARGET: Path = Path(__file__).resolve().parents[1] / "coded_tools/hackathon/house_move/data/schools_england.csv"
    OPEN_STATUSES: frozenset[str] = frozenset({"Open", "Open, but proposed to close"})
    # Output column -> GIAS column
    COLUMNS: dict[str, str] = {
        "urn": "URN",
        "name": "EstablishmentName",
        "phase": "PhaseOfEducation (name)",
        "type": "TypeOfEstablishment (name)",
        "low_age": "StatutoryLowAge",
        "high_age": "StatutoryHighAge",
        "street": "Street",
        "town": "Town",
        "postcode": "Postcode",
        "la_name": "LA (name)",
        "telephone": "TelephoneNum",
        "website": "SchoolWebsite",
        "easting": "Easting",
        "northing": "Northing",
    }
    DAYS_BACK: int = 7

    def build(self) -> int:
        """
        :return: the number of schools written.
        """
        text: str = self._download()
        reader: csv.DictReader = csv.DictReader(io.StringIO(text))
        written: int = 0
        with SchoolExtractBuilder.TARGET.open("w", newline="", encoding="utf-8") as target:
            writer: csv.DictWriter = csv.DictWriter(target, fieldnames=list(SchoolExtractBuilder.COLUMNS))
            writer.writeheader()
            for row in reader:
                if row.get("EstablishmentStatus (name)") not in SchoolExtractBuilder.OPEN_STATUSES:
                    continue
                if not row.get("Easting") or not row.get("Northing") or row.get("Easting") == "0":
                    continue
                writer.writerow({out: (row.get(source) or "").strip() for out, source in self.COLUMNS.items()})
                written += 1
        logger.info("Wrote %d open schools to %s", written, SchoolExtractBuilder.TARGET)
        return written

    def _download(self) -> str:
        """
        Tries today's file, then each earlier day for a week. The file is published in Windows-1252.
        """
        for back in range(SchoolExtractBuilder.DAYS_BACK):
            day: str = (date.today() - timedelta(days=back)).strftime("%Y%m%d")
            url: str = SchoolExtractBuilder.URL.format(day=day)
            response: httpx.Response = httpx.get(url, timeout=300, follow_redirects=True)
            if response.status_code == 200:
                logger.info("Downloaded GIAS extract for %s", day)
                return response.content.decode("cp1252", errors="replace")
            logger.info("No GIAS extract for %s (%s)", day, response.status_code)
        raise RuntimeError(f"No GIAS extract found in the last {SchoolExtractBuilder.DAYS_BACK} days")

    @staticmethod
    def main() -> None:
        """
        Builds the extract.
        """
        logging.basicConfig(level=logging.INFO)
        SchoolExtractBuilder().build()


if __name__ == "__main__":
    SchoolExtractBuilder.main()
