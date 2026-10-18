"""UK working-day date arithmetic for the House Move timeline."""

from datetime import date
from datetime import timedelta


class DateCalc:
    """
    Offsets a date by calendar days or by working days (Monday to Friday, excluding UK bank holidays).
    Bank holidays come from gov.uk via data/bank_holidays.json.
    """

    SATURDAY: int = 5

    def __init__(self, bank_holidays: list[str]):
        """
        :param bank_holidays: ISO dates of the bank holidays that apply where the customer is moving.
        """
        self._bank_holidays: frozenset[date] = frozenset(date.fromisoformat(day) for day in bank_holidays)

    def offset(self, anchor: date, offset_days: int, working_days: bool) -> date:
        """
        :param anchor: the date to count from, for example the completion date.
        :param offset_days: signed number of days; negative counts backwards.
        :param working_days: when true, only working days are counted.
        :return: the resulting date.
        """
        if not working_days:
            return anchor + timedelta(days=offset_days)

        step: timedelta = timedelta(days=1 if offset_days >= 0 else -1)
        result: date = anchor
        remaining: int = abs(offset_days)
        while remaining > 0:
            result += step
            if self.is_working_day(result):
                remaining -= 1
        return result

    def nearest_working_day(self, day: date, forward: bool) -> date:
        """
        :param day: any date.
        :param forward: true to move to the next working day, false to move to the previous one.
        :return: the day itself if it is a working day, otherwise the nearest working day in that direction.
        """
        step: timedelta = timedelta(days=1 if forward else -1)
        while not self.is_working_day(day):
            day += step
        return day

    def is_working_day(self, day: date) -> bool:
        """
        :return: true for Monday to Friday, unless the day is a bank holiday.
        """
        return day.weekday() < DateCalc.SATURDAY and day not in self._bank_holidays
