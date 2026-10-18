"""Static directory of official government and NHS services for a house move."""

import asyncio
import json
import logging
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore

logger = logging.getLogger(__name__)


class GovServiceDirectory(CodedTool):
    """
    Looks up the official service for each obligation: its channel, link, what the customer needs and who must do it.
    Data comes from data/gov_services.json; entries marked verified false are placeholders.
    """

    FILE_NAME: str = "gov_services.json"

    def __init__(self, data_store: HouseMoveDataStore | None = None):
        """
        :param data_store: source of the directory. Defaults to the synthetic data files.
        """
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: "obligation_ids", a list such as ["dvla_licence", "council_tax_new"].
        :param sly_data: not used.
        :return: {"services": {id: entry}, "unknown": [ids]}, or an "Error: ..." string.
        """
        try:
            services: dict[str, Any] = self._data_store.get_section(GovServiceDirectory.FILE_NAME, "services") or {}
        except (OSError, json.JSONDecodeError) as error:
            logger.error("Could not read the gov services directory: %s", error)
            return f"Error: the service directory could not be read ({error})."
        requested: list[str] = args.get("obligation_ids") or []
        return {
            "services": {item: services.get(item) for item in requested if item in services},
            "unknown": [item for item in requested if item not in services],
        }

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        Runs invoke in a worker thread because it reads files.
        """
        return await asyncio.to_thread(self.invoke, args, sly_data)
