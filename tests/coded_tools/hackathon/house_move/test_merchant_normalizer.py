from coded_tools.hackathon.house_move.merchant_normalizer import MerchantNormalizer
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase


class TestMerchantNormaliser(HouseMoveTestCase):
    """
    Unit tests for MerchantNormalizer.
    """

    def test_strips_references_and_payment_markers(self):
        """
        Reference numbers and DD/SO markers are removed so repeat payments group together.
        """
        normaliser = MerchantNormalizer()
        self.assertEqual("BRIGHTWATT EN", normaliser.normalize("BRIGHTWATT EN DD 0034X1274"))
        self.assertEqual("PAWSURE LTD PETPOL", normaliser.normalize("PAWSURE LTD 4110415 PETPOL"))
        self.assertEqual("LITTLE ACORNS NURSERY", normaliser.normalize("LITTLE ACORNS NURSERY SO"))

    def test_upper_cases_and_handles_empty(self):
        """
        Output is upper case, and a descriptor made only of references comes back empty.
        """
        normaliser = MerchantNormalizer()
        self.assertEqual("STREAMBOX.TV", normaliser.normalize("streambox.tv 1234"))
        self.assertEqual("", normaliser.normalize("DD 998877"))
