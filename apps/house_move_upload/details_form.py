"""The guest's "Your details" form on the portal's Details tab."""

import html
from typing import Any

from apps.house_move_upload.portal_icons import PortalIcons
from coded_tools.hackathon.house_move.personal_details import PersonalDetails


class DetailsForm:
    """
    Renders the Details tab for a guest. Before their case exists it asks for everything needed to start it (full
    names, email, both full addresses, the move date and what happens to the current home). Once it exists it shows
    the move, which personal details are on file, and a form to update them. Saved personal values are never shown
    back; only what was just typed is, when a field needs correcting. Everything is HTML-escaped.
    """

    AUTOCOMPLETE: dict[str, str] = {
        "full_names": "name",
        "email": "email",
        "old_address_line1": "address-line1",
        "new_address_line1": "address-line1",
        "old_town": "address-level2",
        "new_town": "address-level2",
        "old_postcode": "postal-code",
        "new_postcode": "postal-code",
    }
    OPTIONAL: frozenset[str] = frozenset({"exchange_date", "move_date_is_estimate"})

    def __init__(self, errors: dict[str, str], typed: dict[str, Any]):
        """
        :param errors: messages by field from the last save, if it failed.
        :param typed: what was typed in that save, put back into the fields so nothing has to be retyped.
        """
        self._errors: dict[str, str] = errors
        self._typed: dict[str, Any] = typed

    def start(self) -> str:
        """
        :return: the intro and the full form that starts a guest's move.
        """
        return f"""{self._intro("")}
<form class="card form" action="/details" method="post" autocomplete="on">
  <h2 class="title">You</h2>
  {self._personal()}
  <h2 class="title" style="margin-top:22px">Current home</h2>
  {self.field("old_address_line1", "House number and street", "text", "For example 12 Park Road.")}
  {self.field("old_town", "Town or city", "text")}
  {self.field("old_postcode", "Postcode", "text")}
  {self.field("old_property", "What happens to it", "select")}
  <h2 class="title" style="margin-top:22px">New home</h2>
  {self.field("new_address_line1", "House number and street", "text")}
  {self.field("new_town", "Town or city", "text")}
  {self.field("new_postcode", "Postcode", "text")}
  <h2 class="title" style="margin-top:22px">Dates</h2>
  {self.field("move_date", "Move date (completion)", "date", "Your best estimate is fine.")}
  {self.field("move_date_is_estimate", "This date is an estimate", "checkbox")}
  {self.field("exchange_date", "Exchange date", "date", "Leave empty if you do not know it yet.")}
  <button class="btn block" type="submit" style="margin-top:16px">Save and start my move</button>
</form>"""

    def update(self, case_id: str, profile: dict[str, Any], on_file: list[str]) -> str:
        """
        :return: the intro with what is on file, the move as it stands, and the form that updates personal details.
        """
        chips: str = "".join(
            f'<span class="chip{"" if field in on_file else " soon"}">'
            f"{'✓' if field in on_file else '•'} {html.escape(label)}</span> "
            for field, label in PersonalDetails.LABELS.items()
        )
        old: dict[str, Any] = profile.get("old_address") or {}
        new: dict[str, Any] = profile.get("new_address") or {}
        estimate: str = " (estimate)" if profile.get("move_date_is_estimate") else ""
        return f"""{self._intro(f'<div class="meta chips-row">{chips}</div>')}
<div class="card">
  <h2 class="title">Your move</h2>
  <p class="small muted">Case <code class="ref">{html.escape(case_id)}</code></p>
  <p>{html.escape(old.get("town") or "")}, {html.escape(old.get("postcode") or "")} →
  {html.escape(new.get("town") or "")}, {html.escape(new.get("postcode") or "")}</p>
  <p class="small muted">Moving on {html.escape(profile.get("completion_date") or "")}{estimate}. To change the date,
  tell the assistant in Chat.</p>
</div>
<form class="card form" action="/details" method="post" autocomplete="on">
  <p class="small muted" style="margin-top:0">Update your details. Fill in every field: saved values are not shown
  again.</p>
  {self._personal()}
  {self.field("old_address_line1", "Current street address", "text", "House number and street.")}
  {self.field("new_address_line1", "New street address", "text", "House number and street.")}
  <button class="btn block" type="submit">Update my details</button>
</form>"""

    def field(self, name: str, label: str, kind: str, hint: str = "") -> str:
        """
        :return: one labelled control (textarea, select, checkbox, or an input of this type) with its error or hint.
        """
        value: str = html.escape(str(self._typed.get(name) or ""))
        need: str = "" if name in DetailsForm.OPTIONAL else " required"
        auto: str = DetailsForm.AUTOCOMPLETE.get(name, "off")
        if kind == "checkbox":
            checked: str = " checked" if self._typed.get(name) else ""
            return (
                f'<label class="check" for="{name}"><input type="checkbox" name="{name}" id="{name}"{checked}> '
                f"{label}</label>"
            )
        if kind == "textarea":
            control: str = (
                f'<textarea name="{name}" id="{name}" rows="2" autocomplete="{auto}"{need}>{value}</textarea>'
            )
        elif kind == "select":
            options: str = "".join(
                f'<option value="{key}"{" selected" if self._typed.get(name) == key else ""}>'
                f"{html.escape(text)}</option>"
                for key, text in PersonalDetails.OLD_PROPERTY.items()
            )
            control = f'<select name="{name}" id="{name}"{need}><option value="">Choose…</option>{options}</select>'
        else:
            control = f'<input type="{kind}" name="{name}" id="{name}" value="{value}" autocomplete="{auto}"{need}>'
        error: str = self._errors.get(name, "")
        return (
            f'<label class="field" for="{name}">{label}</label>{control}'
            f'<p class="small {"error-text" if error else "muted"}">{html.escape(error or hint)}</p>'
        )

    def _personal(self) -> str:
        """
        :return: the names and email fields, common to both forms.
        """
        return self.field(
            "full_names", "Full names", "textarea", "Each adult who is moving, one per line."
        ) + self.field("email", "Email", "email", "Where organisations can reply to you.")

    @staticmethod
    def _intro(extra: str) -> str:
        """
        :return: the card explaining where these details go.
        """
        return f"""<div class="card">
  <div style="display:flex;gap:12px;align-items:flex-start;margin-bottom:6px">
    <span class="avatar">{PortalIcons.SHIELD}</span>
    <div><h2 class="title">For your letters, never the chat</h2>
    <p class="small muted">Stored encrypted and filled into letters only when they are sent. The assistant only sees
    first names, towns, postcodes and dates.</p></div>
  </div>
  {extra}
</div>"""
