from unittest import TestCase

from coded_tools.hackathon.house_move.merchant_normaliser import MerchantNormaliser


class TestMerchantNormaliser(TestCase):
    """
    Unit tests for MerchantNormaliser.
    """

    def test_strips_references_and_payment_markers(self):
        """
        Reference numbers and DD/SO markers are removed so repeat payments group together.
        """
        normaliser = MerchantNormaliser()
        self.assertEqual("BRIGHTWATT EN", normaliser.normalise("BRIGHTWATT EN DD 0034X1274"))
        self.assertEqual("PAWSURE LTD PETPOL", normaliser.normalise("PAWSURE LTD 4110415 PETPOL"))
        self.assertEqual("LITTLE ACORNS NURSERY", normaliser.normalise("LITTLE ACORNS NURSERY SO"))

    def test_upper_cases_and_handles_empty(self):
        """
        Output is upper case, and a descriptor made only of references comes back empty.
        """
        normaliser = MerchantNormaliser()
        self.assertEqual("STREAMBOX.TV", normaliser.normalise("streambox.tv 1234"))
        self.assertEqual("", normaliser.normalise("DD 998877"))
