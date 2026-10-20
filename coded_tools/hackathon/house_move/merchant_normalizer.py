"""Deterministic clean-up of bank transaction descriptors before an LLM classifies them."""

import re


class MerchantNormalizer:  # pylint: disable=too-few-public-methods  # one job: normalize
    """
    Strips reference numbers and payment-type markers from a raw descriptor,
    so "BRIGHTWATT EN DD 0034X1274" and "BRIGHTWATT EN DD 0034X2384" group as one payee.
    """

    # Payment-type markers that say how a payment was made, not who it was paid to.
    PAYMENT_MARKERS: frozenset[str] = frozenset({"DD", "SO", "FP", "BGC", "CHQ"})
    HAS_DIGIT: re.Pattern = re.compile(r"\d")

    def normalize(self, descriptor: str) -> str:
        """
        :param descriptor: a raw transaction descriptor, for example "PAWSURE LTD 4110415 PETPOL".
        :return: the cleaned payee string, for example "PAWSURE LTD PETPOL". Empty if nothing is left.
        """
        kept: list[str] = [
            token
            for token in descriptor.upper().split()
            if token not in MerchantNormalizer.PAYMENT_MARKERS and not MerchantNormalizer.HAS_DIGIT.search(token)
        ]
        return " ".join(kept)
