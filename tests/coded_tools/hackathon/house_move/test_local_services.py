import httpx

from coded_tools.hackathon.house_move.council_lookup import CouncilLookup
from coded_tools.hackathon.house_move.gp_finder import GpFinder
from coded_tools.hackathon.house_move.school_finder import SchoolFinder
from coded_tools.hackathon.house_move.water_supplier_lookup import WaterSupplierLookup
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase


class TestLocalServices(HouseMoveTestCase):
    """
    Unit tests for the GP, school and water finders. No network: postcodes come from the council cache, and the NHS
    and postcodes.io calls made by the GP finder go to a mock transport.
    """

    @staticmethod
    def offline() -> CouncilLookup:
        """
        :return: a council lookup that answers from the cache.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            # Nested to simulate an outage; MockTransport needs a plain callable.
            raise httpx.ConnectError("offline", request=request)

        return CouncilLookup(transport=httpx.MockTransport(handler))

    @staticmethod
    def nhs_mock(request: httpx.Request) -> httpx.Response:
        """
        Answers the ODS search, postcodes.io bulk lookup and ODS detail calls for two practices in NG7.
        """
        url: str = str(request.url)
        if "organisations?" in url:
            return httpx.Response(
                200,
                json={
                    "Organisations": [
                        {"Name": "FAR SURGERY", "OrgId": "C2", "PostCode": "NG7 5HY"},
                        {"Name": "NEAR ST. LUKE'S SURGERY", "OrgId": "C1", "PostCode": "NG7 2DW"},
                        {"Name": "WRONG DISTRICT", "OrgId": "C3", "PostCode": "NG70 1AA"},
                    ]
                },
            )
        if "postcodes.io" in url:
            return httpx.Response(
                200,
                json={
                    "result": [
                        {"query": "NG7 2DW", "result": {"latitude": 52.9480, "longitude": -1.1740}},
                        {"query": "NG7 5HY", "result": {"latitude": 52.9700, "longitude": -1.1700}},
                    ]
                },
            )
        code: str = url.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json={
                "Organisation": {
                    "GeoLoc": {"Location": {"AddrLn1": "1 HIGH STREET", "Town": "NOTTINGHAM"}},
                    "Contacts": {"Contact": [{"type": "tel", "value": f"0115 000000{code[-1]}"}]},
                }
            },
        )

    def test_gp_finder_ranks_by_distance(self):
        """
        Practices in the district are sorted by distance, with address and phone; other districts are ignored.
        """
        tool = GpFinder(council_lookup=self.offline(), transport=httpx.MockTransport(self.nhs_mock))
        result = tool.run({"postcode": "NG7 2NF"}, {})
        names: list = [p.get("practice_name") for p in result.get("practices")]
        self.assertEqual(["Near St. Luke's Surgery", "Far Surgery"], names)
        self.assertEqual("0115 0000001", result.get("practices")[0].get("phone"))
        self.assertEqual("1 High Street, Nottingham", result.get("practices")[0].get("address"))
        self.assertTrue(result.get("source").endswith("(live)"))
        self.assertIn("nhs.uk/service-search/find-a-gp", result.get("how_to_register"))

    def test_gp_finder_fallback_and_other_nations(self):
        """
        With the NHS service unreachable, demo data is used and labelled; Scotland gets a link, not a list.
        """
        tool = GpFinder(council_lookup=self.offline(), transport=self.offline()._transport)
        fallback = tool.run({"postcode": "HG1 1SY"}, {})
        self.assertTrue(fallback.get("source").startswith("demo data"))
        self.assertEqual(3, len(fallback.get("practices")))
        scotland = tool.run({"postcode": "EH7 4LB"}, {})
        self.assertEqual([], scotland.get("practices"))
        self.assertIn("nhsinform.scot", scotland.get("how_to_register"))

    def test_school_finder(self):
        """
        Schools for a child's age are within the radius, nearest first, with contacts; Scotland gets advice.
        """
        tool = SchoolFinder(council_lookup=self.offline())
        result = tool.run({"postcode": "LS6 3BQ", "child_ages": [4], "phases": ["secondary"], "limit": 3}, {})
        for label in ("for a child aged 4", "secondary"):
            schools: list = result.get("groups").get(label)
            self.assertEqual(3, len(schools), label)
            distances: list = [school.get("distance_km") for school in schools]
            self.assertEqual(sorted(distances), distances)
            self.assertTrue(all(distance <= 5 for distance in distances))
        self.assertIn("Leeds", result.get("admissions"))
        self.assertIn("gov.uk/find-free-early-education", result.get("childcare"))
        scotland = tool.run({"postcode": "EH7 4LB", "child_ages": [6]}, {})
        self.assertEqual({}, scotland.get("groups"))
        self.assertIn("education department", scotland.get("admissions"))

    def test_water_supplier_lookup(self):
        """
        Known districts name both companies; Scotland needs no account; unknown districts get the checker only.
        """
        tool = WaterSupplierLookup(council_lookup=self.offline())
        chelmsford = tool.run({"postcode": "CM1 1FR"}, {})
        self.assertEqual("Essex & Suffolk Water", chelmsford.get("water_supplier").get("name"))
        self.assertEqual("Anglian Water", chelmsford.get("sewerage_provider").get("name"))
        self.assertFalse(chelmsford.get("confirmed"))
        self.assertIn("ccw.org.uk", chelmsford.get("check_at"))
        self.assertEqual("Scottish Water", tool.run({"postcode": "EH7 4LB"}, {}).get("water_supplier").get("name"))
        bristol = tool.run({"postcode": "BS6 6RB"}, {})
        self.assertEqual(
            ("Bristol Water", "Wessex Water", True),
            (
                bristol.get("water_supplier").get("name"),
                bristol.get("sewerage_provider").get("name"),
                bristol.get("confirmed"),
            ),
        )
