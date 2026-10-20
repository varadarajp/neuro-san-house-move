"""Checks the personal details a customer types into the portal's secure form."""

import re
from typing import Any


class PersonalDetails:
    """
    The portal's "Your details" form is the only way personal details (full names, street lines, email) reach a
    standalone case: never the chat, so never the LLM. This checks and tidies what was typed, field by field.
    check() covers the personal fields, in CaseStore.PERSONAL_FIELDS names with full_names as a list (one adult per
    line). check_move() covers the move itself (towns, postcodes, dates, the old home), which a guest gives on the
    same form when they first start, as create_case arguments.
    """

    EMAIL: re.Pattern = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,}$")
    NAME: re.Pattern = re.compile(r"^[^\W\d_][^\d_<>@{}]{1,79}$")
    MAX_ADULTS: int = 4
    STREET_LENGTH: tuple[int, int] = (3, 120)
    POSTCODE: re.Pattern = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}$")
    TOWN: re.Pattern = re.compile(r"^[^\W\d_][^\d_<>@{}]{1,59}$")
    ISO_DATE: re.Pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    OLD_PROPERTY: dict[str, str] = {
        "sold": "Sold",
        "let": "Let out",
        "rented_ended": "Rented, and the tenancy ends",
        "kept": "Kept",
    }
    LABELS: dict[str, str] = {
        "full_names": "Full names",
        "email": "Email",
        "old_address_line1": "Current street address",
        "new_address_line1": "New street address",
    }

    @staticmethod
    def check(form: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        """
        :param form: the submitted form fields as strings.
        :return: (clean values, errors by field). Every field is required, so a letter is never sent with a gap.
        """
        clean: dict[str, Any] = {}
        errors: dict[str, str] = {}

        names: list[str] = [" ".join(line.split()) for line in str(form.get("full_names") or "").splitlines()]
        names = [name for name in names if name]
        if not names:
            errors["full_names"] = "Enter the full name of each adult moving, one per line."
        elif len(names) > PersonalDetails.MAX_ADULTS:
            errors["full_names"] = f"Enter up to {PersonalDetails.MAX_ADULTS} adults."
        elif not all(PersonalDetails.NAME.match(name) and " " in name for name in names):
            errors["full_names"] = "Enter each person's first name and surname, letters only."
        else:
            clean["full_names"] = names

        email: str = str(form.get("email") or "").strip()
        if not PersonalDetails.EMAIL.match(email):
            errors["email"] = "Enter an email address like name@example.com."
        else:
            clean["email"] = email

        low, high = PersonalDetails.STREET_LENGTH
        for field in ("old_address_line1", "new_address_line1"):
            street: str = " ".join(str(form.get(field) or "").split())
            if not low <= len(street) <= high or not any(ch.isalpha() for ch in street) or "{" in street:
                errors[field] = "Enter the house number and street, for example 12 Park Road."
            else:
                clean[field] = street
        return clean, errors

    @staticmethod
    def check_move(form: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        """
        :param form: the submitted form fields as strings.
        :return: (create_case arguments without first_names, errors by field). The postcodes are checked again
                 against postcodes.io, and the dates against MoveDates, when the case is created.
        """
        move: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for side in ("old", "new"):
            postcode: str = " ".join(str(form.get(f"{side}_postcode") or "").upper().split())
            if not PersonalDetails.POSTCODE.match(postcode):
                errors[f"{side}_postcode"] = "Enter a UK postcode, for example LS1 3ED."
            else:
                move[f"{side}_postcode"] = postcode
            town: str = " ".join(str(form.get(f"{side}_town") or "").split())
            if not PersonalDetails.TOWN.match(town):
                errors[f"{side}_town"] = "Enter the town or city, for example Leeds."
            else:
                move[f"{side}_town"] = town
        move_date: str = str(form.get("move_date") or "").strip()
        if not PersonalDetails.ISO_DATE.match(move_date):
            errors["move_date"] = "Choose your move date, or your best estimate."
        else:
            move["move_date"] = move_date
        exchange_date: str = str(form.get("exchange_date") or "").strip()
        if exchange_date and not PersonalDetails.ISO_DATE.match(exchange_date):
            errors["exchange_date"] = "Choose a date, or leave it empty if you do not know it yet."
        elif exchange_date:
            move["exchange_date"] = exchange_date
        old_property: str = str(form.get("old_property") or "")
        if old_property not in PersonalDetails.OLD_PROPERTY:
            errors["old_property"] = "Say what happens to your current home."
        else:
            move["old_property"] = old_property
        move["move_date_is_estimate"] = str(form.get("move_date_is_estimate") or "") in ("on", "true", "1")
        return move, errors
