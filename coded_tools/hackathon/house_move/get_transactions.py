"""Mock transaction feed for the House Move Agent's Discovery agent."""

import json
import logging
from datetime import date
from datetime import timedelta
from typing import Any

from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.case_tool import CaseTool
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
        case_store: CaseStore | None = None,
    ):
        """
        :param data_store: source of transactions. Defaults to the synthetic data files.
        :param today: end of the look-back window. Defaults to the latest transaction date, so the demo is stable.
        """
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()
        self._normalizer: MerchantNormalizer = MerchantNormalizer()
        self._today: date | None = today
        self._case_store: CaseStore | None = case_store

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> dict[str, Any] | str:
        """
        :param args: optional "days" (look-back window, default 90) and "customer_id".
        :param sly_data: "customer_id" set by the profile lookup is used when args has none.
        :return: {"customer_id", "window", "payees": [...]}, or an "Error: ..." string.
        """
        customer_id: str | None = args.get("customer_id") or sly_data.get("customer_id")
        if not customer_id:
            return "Error: no customer ID is known yet. Look up the customer profile first."
        days: int = int(args.get("days") or GetTransactions.DEFAULT_DAYS)

        stated: list[dict[str, Any]] = []
        try:
            if CaseStore.is_case(customer_id):
                store: CaseStore = self._case_store or CaseStore()
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
