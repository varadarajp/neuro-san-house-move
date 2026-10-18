"""Mock indicative home insurance quote for the opt-in cross-sell moment."""

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool


class InsuranceQuoteApi(CodedTool):
    """
    Returns an indicative monthly premium band for the new home, by postcode area.
    Mock only: the result is always labelled indicative and is never a quote or a recommendation.
    """

    BANDS_BY_AREA: dict[str, str] = {"HG": "£22 to £30 a month", "LS": "£26 to £36 a month"}
    DEFAULT_BAND: str = "£25 to £40 a month"
    LABEL: str = "Indicative only. Not a quote. Cover, price and terms depend on a full application."

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: optional "postcode". Falls back to the new address in sly_data["customer_profile"].
        :param sly_data: reads "customer_profile".
        :return: {"postcode_area", "indicative_premium_band", "label"}, or an "Error: ..." string.
        """
        profile: dict[str, Any] = sly_data.get("customer_profile") or {}
        postcode: str | None = args.get("postcode") or (profile.get("new_address") or {}).get("postcode")
        if not postcode:
            return "Error: no postcode given and no customer profile loaded."
        area: str = "".join(character for character in postcode.upper()[:2] if character.isalpha())
        return {
            "postcode_area": area,
            "indicative_premium_band": InsuranceQuoteApi.BANDS_BY_AREA.get(area, InsuranceQuoteApi.DEFAULT_BAND),
            "label": InsuranceQuoteApi.LABEL,
        }

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        Delegates to invoke: no I/O.
        """
        return self.invoke(args, sly_data)
