"""Sanity checks on a move's completion and exchange dates."""

from datetime import date
from datetime import timedelta


class MoveDates:  # pylint: disable=too-few-public-methods  # one shared check
    """
    One set of date rules for every tool that takes a move date (create_case, build_timeline), so a mistyped year
    is caught before it becomes a plan.
    - Exchange comes before completion (or on the same day), and not more than a year before it.
    - Completion may be up to 30 days in the past: someone who has just moved still has things to do (register with
      a GP, join the electoral register), and some tasks are flagged as overdue. Anything older is refused.
    - Completion is not more than two years ahead.
    """

    MAX_DAYS_PAST: int = 30
    MAX_DAYS_AHEAD: int = 730
    MAX_EXCHANGE_LEAD_DAYS: int = 365

    @staticmethod
    def check(completion: date, exchange: date | None, today: date) -> str | None:
        """
        :return: None if the dates are plausible, otherwise an "Error: ..." string saying what to check.
        """
        if completion < today - timedelta(days=MoveDates.MAX_DAYS_PAST):
            return (
                f"Error: the completion date {completion.isoformat()} is more than {MoveDates.MAX_DAYS_PAST} days "
                "ago. Check the date with the customer."
            )
        if completion > today + timedelta(days=MoveDates.MAX_DAYS_AHEAD):
            return f"Error: the completion date {completion.isoformat()} is more than two years away. Check the year."
        if exchange is None:
            return None
        if exchange > completion:
            return (
                f"Error: the exchange date {exchange.isoformat()} is after the completion date "
                f"{completion.isoformat()}. Exchange comes first: check both dates."
            )
        if exchange < completion - timedelta(days=MoveDates.MAX_EXCHANGE_LEAD_DAYS):
            return (
                f"Error: the exchange date {exchange.isoformat()} is more than a year before completion. "
                "Check both dates."
            )
        return None
