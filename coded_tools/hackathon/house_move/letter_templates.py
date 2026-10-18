"""Draft templates for the Comms agent."""

import asyncio
import json
import logging
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore

logger = logging.getLogger(__name__)


class LetterTemplates(CodedTool):
    """
    Returns the draft template for each requested channel, plus the placeholders a draft may use.
    Returning every needed channel in one call keeps the Comms agent to a few tool calls.
    """

    FILE_NAME: str = "letter_templates.json"

    def __init__(self, data_store: HouseMoveDataStore | None = None):
        """
        :param data_store: source of templates. Defaults to the synthetic data files.
        """
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: optional "channels", for example ["email", "online_form"]. Defaults to all channels.
        :param sly_data: not used.
        :return: {"templates", "profile_placeholders", "customer_supplied_placeholders", "task_placeholders"},
                 or an "Error: ..." string.
        """
        try:
            templates: dict[str, Any] = self._data_store.get_section(LetterTemplates.FILE_NAME, "templates") or {}
            profile_placeholders: list[str] = self._data_store.get_section(
                LetterTemplates.FILE_NAME, "profile_placeholders"
            )
            customer_placeholders: list[str] = self._data_store.get_section(
                LetterTemplates.FILE_NAME, "customer_supplied_placeholders"
            )
        except (OSError, json.JSONDecodeError) as error:
            logger.error("Could not read letter templates: %s", error)
            return f"Error: the letter templates could not be read ({error})."

        channels: list[str] = args.get("channels") or list(templates)
        unknown: list[str] = [channel for channel in channels if channel not in templates]
        if unknown:
            return f"Error: no template for {unknown}. Known channels: {sorted(templates)}."
        return {
            "templates": {channel: templates.get(channel) for channel in channels},
            "profile_placeholders": profile_placeholders,
            "customer_supplied_placeholders": customer_placeholders,
            "task_placeholders": ["org_name", "url"],
        }

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        Runs invoke in a worker thread because it reads files.
        """
        return await asyncio.to_thread(self.invoke, args, sly_data)
