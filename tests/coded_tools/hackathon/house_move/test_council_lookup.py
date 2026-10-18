from unittest import TestCase

import httpx

from coded_tools.hackathon.house_move.council_lookup import CouncilLookup


class TestCouncilLookup(TestCase):
    """
    Unit tests for CouncilLookup. postcodes.io is replaced by a mock transport.
    """

    LEEDS_RESPONSE: dict = {
        "status": 200,
        "result": {
            "postcode": "LS1 3ED",
            "admin_district": "Leeds",
            "country": "England",
            "codes": {"admin_district": "E08000035"},
        },
    }

    def test_live_lookup_saves_to_sly_data(self):
        """
        A live answer is reshaped, marked live, and stored in sly_data under the formatted postcode.
        """
        requested: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            # Nested so the mock can record the request; MockTransport needs a plain callable.
            requested.append(str(request.url))
            return httpx.Response(200, json=TestCouncilLookup.LEEDS_RESPONSE)

        sly_data: dict = {}
        result = CouncilLookup(transport=httpx.MockTransport(handler)).invoke({"postcode": "ls13ed"}, sly_data)

        self.assertEqual(["https://api.postcodes.io/postcodes/LS13ED"], requested)
        self.assertEqual("Leeds", result.get("council_name"))
        self.assertEqual("E08000035", result.get("council_code"))
        self.assertEqual("postcodes.io (live)", result.get("source"))
        self.assertEqual("Leeds", sly_data.get("councils").get("LS1 3ED").get("council_name"))

    def test_falls_back_to_cache_when_unreachable(self):
        """
        A network failure uses the cached record for a demo postcode, marked as cached.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            # Nested to simulate an outage; MockTransport needs a plain callable.
            raise httpx.ConnectError("offline", request=request)

        result = CouncilLookup(transport=httpx.MockTransport(handler)).invoke({"postcode": "HG1 1SY"}, {})
        self.assertEqual("North Yorkshire", result.get("council_name"))
        self.assertTrue(result.get("source").startswith("cache"))

    def test_invalid_postcode(self):
        """
        A 404 from postcodes.io is reported as an invalid postcode, without using the cache.
        """
        transport = httpx.MockTransport(lambda request: httpx.Response(404, json={"status": 404}))
        result = CouncilLookup(transport=transport).invoke({"postcode": "ZZ9 9ZZ"}, {})
        self.assertEqual("Error: ZZ9 9ZZ is not a valid UK postcode.", result)
