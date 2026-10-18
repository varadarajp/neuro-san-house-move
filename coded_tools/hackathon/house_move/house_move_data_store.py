"""Read-only access to the House Move Agent's synthetic data files."""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class HouseMoveDataStore:
    """
    Loads the synthetic JSON files in this package's data/ folder.
    Stands in for the bank's CRM and transaction systems during the hackathon.
    """

    DATA_DIR: Path = Path(__file__).parent / "data"
    CUSTOMERS_FILE: str = "customers.json"
    TRANSACTIONS_FILE: str = "transactions.json"
    COUNCIL_CACHE_FILE: str = "council_cache.json"
    RULES_FILE: str = "obligations_rules.json"
    BANK_HOLIDAYS_FILE: str = "bank_holidays.json"
    MANDATES_FILE: str = "direct_debit_mandates.json"

    def __init__(self, data_dir: Path | None = None):
        """
        :param data_dir: folder holding the JSON files. Defaults to this package's data/ folder.
        """
        self._data_dir: Path = data_dir or HouseMoveDataStore.DATA_DIR

    def get_customer(self, customer_id: str) -> dict[str, Any] | None:
        """
        :param customer_id: the bank's customer ID, for example "C-10042".
        :return: the full customer record, or None if the ID is unknown.
        """
        customers: list[dict[str, Any]] = self._load(HouseMoveDataStore.CUSTOMERS_FILE).get("customers", [])
        for customer in customers:
            if customer.get("customer_id") == customer_id:
                return customer
        logger.warning("No customer record for %s", customer_id)
        return None

    def get_transactions(self, customer_id: str) -> list[dict[str, Any]]:
        """
        :param customer_id: the bank's customer ID.
        :return: every transaction on file for that customer, oldest first. Empty if none.
        """
        transactions: list[dict[str, Any]] | None = (
            self._load(HouseMoveDataStore.TRANSACTIONS_FILE).get("customers", {}).get(customer_id)
        )
        if transactions is None:
            logger.warning("No transactions on file for %s", customer_id)
            return []
        return transactions

    def get_mandates(self, customer_id: str) -> list[dict[str, Any]]:
        """
        :param customer_id: the bank's customer ID.
        :return: the customer's Direct Debit mandates, each with the payer reference held by the service user.
        """
        return self._load(HouseMoveDataStore.MANDATES_FILE).get("customers", {}).get(customer_id, [])

    def get_cached_council(self, postcode: str) -> dict[str, Any] | None:
        """
        :param postcode: a formatted postcode, for example "LS1 3ED".
        :return: the cached council record, or None when the postcode is not cached.
        """
        return self._load(HouseMoveDataStore.COUNCIL_CACHE_FILE).get(postcode)

    def get_obligation_rules(self) -> list[dict[str, Any]]:
        """
        :return: every rule in obligations_rules.json, in file order.
        """
        return self._load(HouseMoveDataStore.RULES_FILE).get("rules", [])

    def get_bank_holidays(self, division: str) -> list[str]:
        """
        :param division: "england-and-wales" or "scotland", as named by gov.uk.
        :return: ISO dates of that division's bank holidays.
        """
        return self._load(HouseMoveDataStore.BANK_HOLIDAYS_FILE).get(division, [])

    def get_section(self, file_name: str, key: str) -> Any:
        """
        Reads one top-level key from a data file, for the simple directories
        (gov_services.json, providers.json, gp_practices.json, letter_templates.json).
        :return: the value under key, or None if the key is absent.
        """
        return self._load(file_name).get(key)

    def _load(self, file_name: str) -> dict[str, Any]:
        """
        Reads one JSON file. Raises OSError or json.JSONDecodeError so callers can report the problem.
        """
        path: Path = self._data_dir / file_name
        with path.open(encoding="utf-8") as json_file:
            return json.load(json_file)
