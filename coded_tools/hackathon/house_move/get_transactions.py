"""Mock transaction feed for the House Move Agent's Discovery agent."""

import json
import logging
from datetime import date
from datetime import timedelta
from typing import Any

from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.case_tool import CaseTool
from coded_tools.hackathon.house_move.discovery_records import DiscoveryRecords
from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore
from coded_tools.hackathon.house_move.merchant_normalizer import MerchantNormalizer

logger = logging.getLogger(__name__)


class GetTransactions(CaseTool):
    """
    Summarizes a customer's outgoing payments by payee.
    Raw rows stay in the data store. The LLM sees one line per normalized payee: how often it was paid,
    how, the typical amount, the dates as evidence, and one example descriptor to classify.
    The customer's Direct Debit mandate references go into sly_data["mandates"] for the draft tools, never to the LLM.
    """

    DEFAULT_DAYS: int = 90
    # Incoming money and transfers between the customer's own accounts do not identify a provider.
    IGNORED_TYPES: frozenset[str] = frozenset({"CREDIT", "TRANSFER"})

    def __init__(
        self,
        data_store: HouseMoveDataStore | None = None,
        today: date | None = None,
        records: DiscoveryRecords | None = None,
    ):
        """
        :param data_store: source of transactions. Defaults to the synthetic data files.
        :param today: end of the look-back window. Defaults to the latest transaction date, so the demo is stable.
        """
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()
        self._normalizer: MerchantNormalizer = MerchantNormalizer()
        self._today: date | None = today
        self._records: DiscoveryRecords | None = records

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: optional "days" (look-back window, default 90) and "customer_id", which must be the open case.
        :param sly_data: "customer_id" of the case the session opened (see CaseSession). Only this customer is read.
        :return: {"customer_id", "window", "payees": [...]}, or an "Error: ..." string.
        """
        # The ID in args is written by the LLM, so it never chooses whose transactions are read: only the case the
        # session opened, which CaseSession has checked against the caller's token.
        customer_id: str | None = sly_data.get("customer_id")
        if not customer_id:
            return "Error: no customer ID is known yet. Look up the customer profile first."
        requested: str | None = args.get("customer_id")
        if requested and requested != customer_id:
            logger.warning("Refused transactions for %s: the open case is %s", requested, customer_id)
            return f"Error: no customer or case {requested} is available to you. The open case is {customer_id}."
        days: int = int(args.get("days") or GetTransactions.DEFAULT_DAYS)

        stated: list[dict[str, Any]] = []
        try:
            if CaseStore.is_case(customer_id):
                store: DiscoveryRecords = self._records or DiscoveryRecords()
                transactions: list[dict[str, Any]] = store.get_transactions(customer_id)
                stated = store.get_manual_providers(customer_id)
                sly_data["mandates"] = []
            else:
                transactions = self._data_store.get_transactions(customer_id)
                sly_data["mandates"] = self._data_store.get_mandates(customer_id)
        except (OSError, json.JSONDecodeError) as error:
            logger.error("Could not read transaction data: %s", error)
            return f"Error: transaction data is unavailable ({error})."
        if not transactions and not stated:
            return f"Error: no transactions or customer-stated providers for {customer_id} yet."
        if not transactions:
            return {"customer_id": customer_id, "window": None, "payees": [], "customer_stated_providers": stated}

        end: date = self._today or max(date.fromisoformat(row.get("date")) for row in transactions)
        start: date = end - timedelta(days=days)
        in_window: list[dict[str, Any]] = [
            row
            for row in transactions
            if start <= date.fromisoformat(row.get("date")) <= end
            and row.get("type") not in GetTransactions.IGNORED_TYPES
        ]
        logger.info("Summarized %d transactions for %s", len(in_window), customer_id)
        return {
            "customer_id": customer_id,
            "window": {"from": start.isoformat(), "to": end.isoformat()},
            "payees": self._group_by_payee(in_window),
            "customer_stated_providers": stated,
        }

    def _group_by_payee(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Groups rows by normalized descriptor, most frequent payee first.
        """
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            payee: str = self._normalizer.normalize(row.get("descriptor", ""))
            groups.setdefault(payee, []).append(row)

        payees: list[dict[str, Any]] = []
        for payee, payments in groups.items():
            amounts: list[float] = sorted(abs(payment.get("amount", 0.0)) for payment in payments)
            payees.append(
                {
                    "payee": payee,
                    "example_descriptor": payments[0].get("descriptor"),
                    "payment_types": sorted({payment.get("type") for payment in payments}),
                    "count": len(payments),
                    "typical_amount": amounts[len(amounts) // 2],
                    "evidence": [f"{payment.get('date')} {payment.get('transaction_id')}" for payment in payments],
                }
            )
        payees.sort(key=lambda entry: (-entry.get("count"), entry.get("payee")))
        return payees
