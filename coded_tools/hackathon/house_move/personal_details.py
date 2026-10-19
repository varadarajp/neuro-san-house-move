"""Checks the personal details a customer types into the portal's secure form."""

import re
from typing import Any


class PersonalDetails:  # pylint: disable=too-few-public-methods  # one check
    """
    The portal's "Your details" form is the only way personal details (full names, street lines, email) reach a
    standalone case: never the chat, so never the LLM. This checks and tidies what was typed, field by field.
    The result uses CaseStore.PERSONAL_FIELDS names, with full_names as a list (one adult per line).
    """

    EMAIL: re.Pattern = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,}$")
    NAME: re.Pattern = re.compile(r"^[^\W\d_][^\d_<>@{}]{1,79}$")
    MAX_ADULTS: int = 4
    STREET_LENGTH: tuple[int, int] = (3, 120)
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
