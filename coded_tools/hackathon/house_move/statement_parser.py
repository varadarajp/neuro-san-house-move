"""Parses uploaded bank statements (CSV or OFX/QFX) into transaction rows."""

import csv
import io
import re
from datetime import date
from datetime import datetime
from typing import Any


class StatementParser:  # pylint: disable=too-few-public-methods  # one job: parse
    """
    Turns a statement file's text into {"date", "descriptor", "amount", "type"} rows, the same shape as the bank's
    own transaction feed, so the Discovery agent works unchanged.
    CSV headers vary by bank, so columns are matched by common names: a signed amount column, or separate
    paid-out and paid-in columns. Columns such as sort code, account number and balance are dropped.
    Raises ValueError with a plain message when the file cannot be understood.
    """

    DATE_HEADERS: tuple[str, ...] = ("date", "transaction date", "posting date", "completed date", "posted date")
    DESCRIPTION_HEADERS: tuple[str, ...] = (
        "description",
        "transaction description",
        "details",
        "narrative",
        "merchant",
        "name",
        "payee",
        "memo",
    )
    AMOUNT_HEADERS: tuple[str, ...] = ("amount", "value", "amount (gbp)", "transaction amount")
    OUT_HEADERS: tuple[str, ...] = ("debit amount", "paid out", "money out", "debit", "withdrawals", "out")
    IN_HEADERS: tuple[str, ...] = ("credit amount", "paid in", "money in", "credit", "deposits", "in")
    TYPE_HEADERS: tuple[str, ...] = ("type", "transaction type")
    DATE_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d/%m/%y", "%d %B %Y")
    # Bank type codes and words, mapped to the feed's types.
    TYPE_WORDS: dict[str, str] = {
        "DD": "DD",
        "DIRECT DEBIT": "DD",
        "DIRECTDEBIT": "DD",
        "SO": "SO",
        "STANDING ORDER": "SO",
        "REPEATPMT": "SO",
        "FPI": "CREDIT",
        "BGC": "CREDIT",
        "CREDIT": "CREDIT",
        "DEP": "CREDIT",
        "TFR": "TRANSFER",
        "XFER": "TRANSFER",
        "TRANSFER": "TRANSFER",
        "DEB": "CARD",
        "POS": "CARD",
        "CARD PAYMENT": "CARD",
        "DEBIT": "CARD",
        "FPO": "CARD",
    }
    OFX_TRANSACTION: re.Pattern = re.compile(r"<STMTTRN>(.*?)</STMTTRN>", re.IGNORECASE | re.DOTALL)
    OFX_FIELD: str = r"<{name}>([^<\r\n]*)"

    def parse(self, text: str, file_name: str) -> list[dict[str, Any]]:
        """
        :param text: the file contents.
        :param file_name: used to pick the format by extension (.csv, .ofx or .qfx).
        :return: rows sorted by date.
        """
        lower_name: str = file_name.lower()
        if lower_name.endswith((".ofx", ".qfx")):
            rows: list[dict[str, Any]] = self._parse_ofx(text)
        elif lower_name.endswith(".csv"):
            rows = self._parse_csv(text)
        else:
            raise ValueError("Only CSV, OFX or QFX statements are supported. PDF statements are on the roadmap.")
        if not rows:
            raise ValueError("No transactions were found in the statement.")
        return sorted(rows, key=lambda row: row.get("date"))

    def _parse_csv(self, text: str) -> list[dict[str, Any]]:
        """
        Reads a CSV statement, matching its headers to known column names.
        """
        reader: csv.DictReader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
        headers: dict[str, str] = {(name or "").strip().lower(): name for name in reader.fieldnames or []}
        columns: dict[str, str | None] = {
            "date": self._column(headers, StatementParser.DATE_HEADERS),
            "description": self._column(headers, StatementParser.DESCRIPTION_HEADERS),
            "amount": self._column(headers, StatementParser.AMOUNT_HEADERS),
            "out": self._column(headers, StatementParser.OUT_HEADERS),
            "in": self._column(headers, StatementParser.IN_HEADERS),
            "type": self._column(headers, StatementParser.TYPE_HEADERS),
        }
        if (
            not columns.get("date")
            or not columns.get("description")
            or not (columns.get("amount") or columns.get("out") or columns.get("in"))
        ):
            raise ValueError(
                f"The CSV needs a date, a description and an amount (or paid out / paid in) column. "
                f"Found columns: {list(headers)}"
            )
        rows: list[dict[str, Any]] = []
        for record in reader:
            row: dict[str, Any] | None = self._csv_row(record, columns)
            if row is not None:
                rows.append(row)
        return rows

    def _csv_row(self, record: dict[str, str], columns: dict[str, str | None]) -> dict[str, Any] | None:
        """
        :return: one CSV record as a transaction, or None if its date cannot be read.
        """
        when: date | None = self._date(record.get(columns.get("date"), ""))
        if when is None:
            return None
        if columns.get("amount"):
            amount: float = self._number(record.get(columns.get("amount")))
        else:
            amount = self._number(record.get(columns.get("in"))) - self._number(record.get(columns.get("out")))
        descriptor: str = (record.get(columns.get("description")) or "").strip()
        raw_type: str = (record.get(columns.get("type")) or "").strip() if columns.get("type") else ""
        return {
            "date": when.isoformat(),
            "descriptor": descriptor,
            "amount": amount,
            "type": self._type(raw_type, descriptor, amount),
        }

    def _parse_ofx(self, text: str) -> list[dict[str, Any]]:
        """
        Reads OFX/QFX transactions. Both SGML-style (unclosed) and XML-style tags are accepted.
        """
        rows: list[dict[str, Any]] = []
        for block in StatementParser.OFX_TRANSACTION.findall(text):
            posted: str = self._ofx_field(block, "DTPOSTED")[:8]
            try:
                when: date = datetime.strptime(posted, "%Y%m%d").date()
            except ValueError:
                continue
            descriptor: str = " ".join(
                part for part in (self._ofx_field(block, "NAME"), self._ofx_field(block, "MEMO")) if part
            )
            amount: float = self._number(self._ofx_field(block, "TRNAMT"))
            rows.append(
                {
                    "date": when.isoformat(),
                    "descriptor": descriptor,
                    "amount": amount,
                    "type": self._type(self._ofx_field(block, "TRNTYPE"), descriptor, amount),
                }
            )
        return rows

    @staticmethod
    def _column(headers: dict[str, str], candidates: tuple[str, ...]) -> str | None:
        """
        :return: the original header name of the first candidate present, or None.
        """
        return next((headers.get(name) for name in candidates if name in headers), None)

    @staticmethod
    def _date(text: str) -> date | None:
        """
        :return: the date in any known format, or None for a row that is not a transaction (such as a total).
        """
        for date_format in StatementParser.DATE_FORMATS:
            try:
                return datetime.strptime(text.strip(), date_format).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _number(text: Any) -> float:
        """
        Reads "£1,234.50", "-12.00" or an empty cell (0.0).
        """
        cleaned: str = re.sub(r"[£,\s]", "", str(text or ""))
        try:
            return float(cleaned) if cleaned else 0.0
        except ValueError:
            return 0.0

    @staticmethod
    def _type(raw_type: str, descriptor: str, amount: float) -> str:
        """
        Maps the bank's type code to DD, SO, CREDIT, TRANSFER or CARD, falling back to clues in the descriptor.
        """
        mapped: str | None = StatementParser.TYPE_WORDS.get(raw_type.upper())
        if mapped:
            return mapped
        tokens: set[str] = set(descriptor.upper().split())
        if "DD" in tokens:
            return "DD"
        if "SO" in tokens:
            return "SO"
        return "CREDIT" if amount > 0 else "CARD"

    @staticmethod
    def _ofx_field(block: str, name: str) -> str:
        """
        :return: one OFX field's value from a transaction block, or "".
        """
        match: re.Match | None = re.search(StatementParser.OFX_FIELD.format(name=name), block, re.IGNORECASE)
        return match.group(1).strip() if match else ""
