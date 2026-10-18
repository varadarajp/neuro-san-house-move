"""Mock directory of how to contact each provider about a house move."""

import asyncio
import json
import logging
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore

logger = logging.getLogger(__name__)


class ProviderDirectory(CodedTool):
    """
    Finds providers by name or alias (case-insensitive) and returns their contact channel, link and required fields.
    Data comes from data/providers.json.
    """

    FILE_NAME: str = "providers.json"

    def __init__(self, data_store: HouseMoveDataStore | None = None):
        """
        :param data_store: source of the directory. Defaults to the synthetic data files.
        """
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: "names", a list of provider names, for example ["Brightwatt Energy", "TV Licensing"].
        :param sly_data: not used.
        :return: {"providers": {name: entry}, "not_found": [names]}, or an "Error: ..." string.
        """
        try:
            providers: list[dict[str, Any]] = self._data_store.get_section(ProviderDirectory.FILE_NAME, "providers")
        except (OSError, json.JSONDecodeError) as error:
            logger.error("Could not read the provider directory: %s", error)
            return f"Error: the provider directory could not be read ({error})."

        found: dict[str, Any] = {}
        not_found: list[str] = []
        for name in args.get("names") or []:
            match: dict[str, Any] | None = self._find(name, providers or [])
            if match is None:
                not_found.append(name)
            else:
                found[name] = match
        return {"providers": found, "not_found": not_found}

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        Runs invoke in a worker thread because it reads files.
        """
        return await asyncio.to_thread(self.invoke, args, sly_data)

    @staticmethod
    def _find(name: str, providers: list[dict[str, Any]]) -> dict[str, Any] | None:
        """
        Matches the exact name or an alias first, then any provider whose name or alias the query contains.
        """
        query: str = name.upper().strip()
        for provider in providers:
            labels: list[str] = [provider.get("name", "").upper()] + provider.get("aliases", [])
            if query in labels:
                return provider
        for provider in providers:
            labels = [provider.get("name", "").upper()] + provider.get("aliases", [])
            if any(label and label in query for label in labels):
                return provider
        return None
