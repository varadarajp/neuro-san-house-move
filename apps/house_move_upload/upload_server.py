"""
House Move portal: demo sign-in and secure statement upload.

Sign-in issues the signed session token the agents require (see CaseSession). In production the bank's own
sign-in would do this; here a demo sign-in stands in for it.

nsflow's paperclip pastes a file's text into the chat message, which would send a bank statement through the LLM.
The upload page stands in for the real app's secure upload: the file goes into the signed-in person's own uploads
folder, where the import_statement tool reads it once and deletes it. The chat only ever sees the file name.

Run from the repository root:
    python -m apps.house_move_upload.upload_server
then open http://127.0.0.1:8090
"""

import asyncio
import html
import json
import logging
import re
from pathlib import Path
from string import Template
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi import File
from fastapi import Form
from fastapi import Request
from fastapi import UploadFile
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from fastapi.responses import Response

from apps.house_move_upload.portal_icons import PortalIcons
from coded_tools.hackathon.house_move.approve_on_portal import ApproveOnPortal
from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.case_status import CaseStatus
from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore
from coded_tools.hackathon.house_move.import_statement import ImportStatement
from coded_tools.hackathon.house_move.personal_details import PersonalDetails
from coded_tools.hackathon.house_move.session_tokens import SessionTokens
from coded_tools.hackathon.house_move.statement_parser import StatementParser

logger = logging.getLogger(__name__)


class StatementUploadServer:
    """
    A small FastAPI app on 127.0.0.1: sign in (as a demo bank customer, or as a new guest), copy the session token
    into the chat client, upload CSV, OFX or QFX statements to your own folder, and follow your move on the
    "My move" page (stage, what waits for your approval, tasks by status, recent activity), where drafts can be
    opened and approved. It never lists or returns uploaded files, and stores each file readable by its owner only.
    Use synthetic statements only.
    """

    HOST: str = "127.0.0.1"
    PORT: int = 8090
    COOKIE: str = "hm_session"
    GUEST: str = "guest"
    EXTENSIONS: tuple[str, ...] = (".csv", ".ofx", ".qfx")
    UNSAFE_CHARACTERS: re.Pattern = re.compile(r"[^A-Za-z0-9._-]+")
    HERE: Path = Path(__file__).parent
    # Every screen is the same app shell (app bar, content, tab bar) styled by portal.css; see portal_shell.html.
    SHELL: Template = Template((HERE / "portal_shell.html").read_text(encoding="utf-8"))
    STYLES: str = (HERE / "portal.css").read_text(encoding="utf-8")
    # The "My move" screen renders the status JSON in the browser; see status_page.html.
    STATUS_PAGE: str = (HERE / "status_page.html").read_text(encoding="utf-8")
    TABS: tuple[tuple[str, str, str], ...] = (
        ("/", "Home", PortalIcons.HOME),
        ("/status", "My move", PortalIcons.ROUTE),
        ("/upload", "Upload", PortalIcons.UPLOAD),
        ("/details", "Details", PortalIcons.USER),
    )
    CURRENT: str = ' aria-current="page"'
    DEMO_NOTE: str = (
        '<p class="fineprint">Demo with synthetic data. In production, sign-in is the bank\'s own and this is part '
        "of the banking app.</p>"
    )

    def __init__(
        self,
        upload_dir: Path | None = None,
        tokens: SessionTokens | None = None,
        data_store: HouseMoveDataStore | None = None,
        audit_log: AuditLog | None = None,
    ):
        """
        :param upload_dir: root of the per-owner upload folders. Defaults to the folder import_statement reads.
        :param tokens: issues and verifies session tokens. Defaults to the shared secret.
        :param data_store: lists the demo bank customers for sign-in.
        :param audit_log: records approvals made on the portal. Defaults to the audit file under logs/.
        """
        self._upload_dir: Path = upload_dir or ImportStatement.DEFAULT_UPLOAD_DIR
        self._tokens: SessionTokens = tokens or SessionTokens()
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()
        self._parser: StatementParser = StatementParser()
        self._status: CaseStatus = CaseStatus(tokens=self._tokens)
        self._audit_log: AuditLog = audit_log or AuditLog()
        # Holds the personal details given on the "Your details" form; opened on first use (see _case_store).
        self._store: CaseStore | None = None

    def create_app(self) -> FastAPI:
        """
        :return: the FastAPI app.
        """
        app: FastAPI = FastAPI(title="House Move portal", docs_url=None, redoc_url=None)
        ImportStatement.purge_stale(self._upload_dir)

        # Route handlers are nested so FastAPI can register them against this instance's settings.
        @app.get("/", response_class=HTMLResponse)
        async def home(request: Request) -> str:
            return self._home(request.cookies.get(StatementUploadServer.COOKIE), "")

        @app.get("/portal.css")
        async def styles() -> Response:
            return Response(StatementUploadServer.STYLES, media_type="text/css")

        @app.post("/signin", response_class=HTMLResponse)
        async def sign_in(request: Request, who: str = Form(...)) -> HTMLResponse:
            if not self._from_this_site(request):
                return HTMLResponse(self._home(None, self._error("Please sign in from this page.")), status_code=403)
            token: str | None = self._issue(who)
            if token is None:
                return HTMLResponse(self._home(None, self._error("Choose a customer or guest.")), status_code=400)
            response: HTMLResponse = HTMLResponse(self._home(token, ""))
            response.set_cookie(
                StatementUploadServer.COOKIE,
                token,
                httponly=True,
                samesite="strict",
                max_age=SessionTokens.LIFETIME_SECONDS,
            )
            return response

        @app.post("/signout", response_class=HTMLResponse)
        async def sign_out(request: Request) -> HTMLResponse:
            if not self._from_this_site(request):
                return HTMLResponse(self._home(None, self._error("Please sign out from this page.")), status_code=403)
            response: HTMLResponse = HTMLResponse(self._home(None, self._ok("Signed out.")))
            response.delete_cookie(StatementUploadServer.COOKIE)
            return response

        @app.get("/status", response_class=HTMLResponse)
        async def status_page(request: Request) -> HTMLResponse:
            if self._tokens.verify(request.cookies.get(StatementUploadServer.COOKIE)) is None:
                return HTMLResponse(self._home(None, self._error("Sign in to see your move.")), status_code=401)
            return HTMLResponse(
                self._page(
                    request.cookies.get(StatementUploadServer.COOKIE), "/status", StatementUploadServer.STATUS_PAGE
                )
            )

        @app.get("/status.json")
        async def status_json(request: Request) -> JSONResponse:
            status: dict[str, Any] | None = self._status.build(request.cookies.get(StatementUploadServer.COOKIE))
            if status is None:
                return JSONResponse({"error": "not signed in"}, status_code=401)
            return JSONResponse(status, headers={"Cache-Control": "no-store"})

        @app.get("/draft/{task_id}.json")
        async def draft_json(request: Request, task_id: str) -> JSONResponse:
            draft: dict[str, Any] | None = self._status.draft(
                request.cookies.get(StatementUploadServer.COOKIE), task_id
            )
            if draft is None:
                return JSONResponse({"error": "no draft waiting for your approval"}, status_code=404)
            return JSONResponse(draft, headers={"Cache-Control": "no-store"})

        @app.post("/approve")
        async def approve(request: Request) -> JSONResponse:
            if not self._same_origin(request):
                return JSONResponse({"error": "refused"}, status_code=403)
            try:
                body: Any = await request.json()
            except json.JSONDecodeError:
                return JSONResponse({"error": "expected JSON"}, status_code=400)
            sly_data: dict[str, Any] = {"session_token": request.cookies.get(StatementUploadServer.COOKIE)}
            args: dict[str, Any] = {"approve": body.get("approve") if isinstance(body, dict) else None}
            result: dict[str, Any] | str = await ApproveOnPortal(audit_log=self._audit_log).async_invoke(
                args, sly_data
            )
            if isinstance(result, str):
                status: int = 401 if "not signed in" in result else 400
                return JSONResponse({"error": result.removeprefix("Error: ")}, status_code=status)
            return JSONResponse(result)

        @app.get("/upload", response_class=HTMLResponse)
        async def upload_page(request: Request) -> HTMLResponse:
            token: str | None = request.cookies.get(StatementUploadServer.COOKIE)
            if self._tokens.verify(token) is None:
                return HTMLResponse(self._home(None, self._error("Sign in before uploading.")), status_code=401)
            return HTMLResponse(self._upload_screen(token, ""))

        @app.post("/upload", response_class=HTMLResponse)
        async def upload(request: Request, file: UploadFile = File(...)) -> HTMLResponse:
            token: str | None = request.cookies.get(StatementUploadServer.COOKIE)
            claims: dict[str, Any] | None = self._tokens.verify(token)
            if claims is None:
                return HTMLResponse(self._home(None, self._error("Sign in before uploading.")), status_code=401)
            if not self._from_this_site(request):
                return HTMLResponse(self._upload_screen(token, self._error("Upload from this page.")), status_code=403)
            content: bytes = await file.read(ImportStatement.MAX_BYTES + 1)
            message, status = self._save(claims.get("sub"), file.filename or "", content)
            return HTMLResponse(self._upload_screen(token, message), status_code=status)

        @app.get("/details", response_class=HTMLResponse)
        async def details_page(request: Request) -> HTMLResponse:
            token: str | None = request.cookies.get(StatementUploadServer.COOKIE)
            if self._tokens.verify(token) is None:
                return HTMLResponse(self._home(None, self._error("Sign in to add your details.")), status_code=401)
            return HTMLResponse(await asyncio.to_thread(self._details_screen, token, "", {}, {}))

        @app.post("/details", response_class=HTMLResponse)
        async def save_details(request: Request) -> HTMLResponse:
            token: str | None = request.cookies.get(StatementUploadServer.COOKIE)
            claims: dict[str, Any] | None = self._tokens.verify(token)
            if claims is None:
                return HTMLResponse(self._home(None, self._error("Sign in to add your details.")), status_code=401)
            if not self._from_this_site(request):
                return HTMLResponse(
                    self._home(token, self._error("Save your details from this page.")), status_code=403
                )
            form: dict[str, Any] = dict(await request.form())
            message, status, errors = await asyncio.to_thread(self._save_details, claims, form)
            # On an error the typed values go back into the form; once saved, nothing personal is echoed back.
            typed: dict[str, Any] = form if errors else {}
            screen: str = await asyncio.to_thread(self._details_screen, token, message, errors, typed)
            return HTMLResponse(screen, status_code=status)

        return app

    @staticmethod
    def main() -> None:
        """
        Starts the portal on 127.0.0.1:8090.
        """
        logging.basicConfig(level=logging.INFO)
        server: StatementUploadServer = StatementUploadServer()
        uvicorn.run(server.create_app(), host=StatementUploadServer.HOST, port=StatementUploadServer.PORT)

    def _issue(self, who: str) -> str | None:
        """
        Demo sign-in. A bank customer's identity stands in for the bank's own login; a guest gets a new random
        identity that only this token carries.
        """
        if who == StatementUploadServer.GUEST:
            return self._tokens.issue(SessionTokens.new_guest_id(), [])
        if who in self._demo_customers():
            return self._tokens.issue(f"bank:{who}", [who])
        return None

    def _demo_customers(self) -> dict[str, str]:
        """
        :return: demo bank customer IDs and names, for the sign-in list.
        """
        try:
            customers: list[dict[str, Any]] = self._data_store.get_section("customers.json", "customers") or []
        except (OSError, json.JSONDecodeError) as error:
            logger.error("Could not read demo customers: %s", error)
            return {}
        return {c.get("customer_id"): " and ".join(c.get("names", [])) for c in customers}

    def _home(self, token: str | None, message: str) -> str:
        """
        The welcome screen with sign-in when signed out; the home screen when signed in: a summary of the move
        (filled in from /status.json), quick actions, and the session to give the chat.
        """
        claims: dict[str, Any] | None = self._tokens.verify(token)
        if claims is None:
            return self._welcome(message)
        sly: str = html.escape(json.dumps({"session_token": token}))
        name: str = html.escape(self._first_name(claims) or "there")
        body: str = f"""<section class="welcome" style="padding-top:8px"><h1>Hi {name}</h1>
<p>Here is where your move stands.</p></section>
<a class="card" id="summary" href="/status" style="display:block;text-decoration:none;color:inherit">
  <div class="skeleton" style="height:64px;margin:0"></div></a>
<div class="quick">
  <a href="/status"><span class="avatar">{PortalIcons.CHECK}</span>Review and approve
    <span class="muted">Read each letter first</span></a>
  <a href="/upload"><span class="avatar">{PortalIcons.UPLOAD}</span>Upload a statement
    <span class="muted">Find who you pay</span></a>
</div>
<div class="section-head"><h2>Connect your chat</h2></div>
<div class="card">
  <div style="display:flex;gap:12px;align-items:flex-start">
    <span class="avatar">{PortalIcons.CHAT}</span>
    <div><h2 class="title">Give the assistant your session</h2>
    <p class="small muted">Once, at the start. In nsflow, paste it into the SlyData tab and tick
    <em>Use Edited Sly Data</em>; on the command line, pass it with <code>--sly_data</code>.</p></div>
  </div>
  <pre class="token" id="token">{sly}</pre>
  <button class="btn secondary block" type="button" id="copy">{PortalIcons.COPY} Copy session</button>
  <p class="small muted" style="margin-top:10px">Never share it: it opens your case.</p>
</div>
{StatementUploadServer.DEMO_NOTE}"""
        script: str = """<script>
document.getElementById("copy").onclick = async () => {
  const text = document.getElementById("token").textContent;
  try { await navigator.clipboard.writeText(text); toast("Session copied. Paste it into the chat's SlyData."); }
  catch (e) { getSelection().selectAllChildren(document.getElementById("token")); toast("Selected: press Copy."); }
};
// The move summary, from the same feed as the My move screen. Built with textContent only.
(async () => {
  const box = document.getElementById("summary");
  const el = (tag, text, cls) => { const e = document.createElement(tag); if (text) e.textContent = text;
    if (cls) e.className = cls; return e; };
  try {
    const s = await (await fetch("/status.json", {cache: "no-store"})).json();
    box.textContent = "";
    if (!s.case) {
      box.appendChild(el("h2", "No move started yet", "title"));
      box.appendChild(el("p", s.hint, "small muted"));
      return;
    }
    const label = el("div", undefined, "progress");
    const head = el("div", undefined, "label");
    head.appendChild(el("b", s.stage));
    head.appendChild(el("span", "Step " + (s.stage_index + 1) + " of " + s.stages.length, "small muted"));
    label.appendChild(head);
    const seg = el("div", undefined, "segments");
    s.stages.forEach((_, i) =>
      seg.appendChild(el("i", "", i < s.stage_index ? "done" : i === s.stage_index ? "now" : "")));
    label.appendChild(seg);
    box.appendChild(el("div", s.case.from + "  →  " + s.case.to, "small muted"));
    box.appendChild(label);
    const waiting = s.awaiting_approval.length;
    const action = (s.groups.find(g => g.status === "needs_action") || {tasks: []}).tasks.length;
    box.appendChild(el("p", waiting ? waiting + " letters waiting for your approval"
      : action ? action + " replies need something from you" : s.hint, "small"));
  } catch (e) { box.textContent = "Open My move to see your plan."; }
})();
</script>"""
        return self._page(token, "/", message + body, script)

    def _welcome(self, message: str) -> str:
        """
        Signed out: what the app does, and the demo sign-in.
        """
        options: str = "".join(
            f'<option value="{html.escape(cid)}">{html.escape(name)} · {html.escape(cid)}</option>'
            for cid, name in self._demo_customers().items()
        )
        features: str = "".join(
            f'<li><span class="avatar">{icon}</span>{text}</li>'
            for icon, text in (
                (PortalIcons.SEARCH, "Finds everyone you need to tell, from who you pay"),
                (PortalIcons.CHECK, "Dated plan and ready-made letters you approve"),
                (PortalIcons.SHIELD, "Nothing is sent without your say-so"),
            )
        )
        body: str = f"""<section class="welcome">
  <div class="logo">{PortalIcons.HOME}</div>
  <h1>Moving home,<br>sorted.</h1>
  <p>Tell everyone you're moving: on time, in the right order, and only with your approval.</p>
  <ul class="features">{features}</ul>
</section>
<div class="card">
  <h2 class="title">Sign in</h2>
  <form action="/signin" method="post">
    <label class="field" for="who">Who are you?</label>
    <select name="who" id="who" required>
      <option value="{StatementUploadServer.GUEST}">I'm not a customer (guest)</option>
      <optgroup label="Demo bank customers">{options}</optgroup>
    </select>
    <button class="btn block" type="submit" style="margin-top:14px">Sign in</button>
  </form>
  <p class="small muted" style="margin-top:12px">A bank customer can open only their own record; a guest can open
  only cases they create.</p>
</div>
{StatementUploadServer.DEMO_NOTE}"""
        return self._page(None, "", message + body)

    def _upload_screen(self, token: str | None, message: str) -> str:
        """
        Upload a bank statement into the signed-in person's own folder.
        """
        body: str = f"""<div class="section-head"><h2>Upload a bank statement</h2></div>
<div class="card">
  <form action="/upload" method="post" enctype="multipart/form-data" id="upload-form">
    <label class="drop" id="drop">
      {PortalIcons.FILE}
      <span class="file-name" id="file-name">Choose a CSV, OFX or QFX file</span>
      <span class="small muted">Up to 5&nbsp;MB, downloaded from your online banking</span>
      <input type="file" name="file" id="file" accept=".csv,.ofx,.qfx" required>
    </label>
    <button class="btn block" type="submit" id="send" style="margin-top:14px" disabled>Upload</button>
  </form>
</div>
<ul class="list">
  <li class="row"><span class="avatar">{PortalIcons.SHIELD}</span><div class="main"><div class="name">Never in the
  chat</div><div class="small muted">It goes to your own private folder, not to the assistant.</div></div></li>
  <li class="row"><span class="avatar">{PortalIcons.SEARCH}</span><div class="main"><div class="name">Read once,
  then deleted</div><div class="small muted">Only to find the organisations you pay.</div></div></li>
  <li class="row"><span class="avatar">{PortalIcons.CHAT}</span><div class="main"><div class="name">Then tell the
  assistant</div><div class="small muted">Say the file name; it asks your permission before reading it.</div>
  </div></li>
</ul>
{StatementUploadServer.DEMO_NOTE}"""
        script: str = """<script>
const input = document.getElementById("file"), send = document.getElementById("send");
input.onchange = () => {
  const f = input.files[0];
  document.getElementById("file-name").textContent = f ? f.name : "Choose a CSV, OFX or QFX file";
  document.getElementById("drop").classList.toggle("has-file", !!f);
  send.disabled = !f;
};
document.getElementById("upload-form").onsubmit = () => { send.disabled = true; send.textContent = "Uploading…"; };
</script>"""
        return self._page(token, "/upload", message + body, script)

    def _page(self, token: str | None, tab: str, body: str, script: str = "") -> str:
        """
        Fills the app shell: app bar with the signed-in name and sign-out, the screen, and the tab bar.
        :param tab: the screen's tab path ("" when signed out), which also gives the page title.
        :param body: the screen's HTML, including any message to show above it.
        """
        title: str = next((label for href, label, _ in StatementUploadServer.TABS if href == tab), "Welcome")
        claims: dict[str, Any] | None = self._tokens.verify(token)
        account: str = ""
        nav: str = ""
        subheading: str = "Your move, step by step"
        if claims is not None:
            account = (
                '<form action="/signout" method="post" style="margin:0">'
                f'<button class="icon-btn" type="submit" aria-label="Sign out" title="Sign out">'
                f"{PortalIcons.SIGN_OUT}</button></form>"
            )
            subheading = html.escape(self._display_name(claims))
            links: str = "".join(
                f'<a href="{href}"{StatementUploadServer.CURRENT if href == tab else ""}>'
                f'<span class="pill">{icon}</span>{label}</a>'
                for href, label, icon in StatementUploadServer.TABS
            )
            nav = f'<nav class="tabbar" aria-label="Main">{links}</nav>'
        return StatementUploadServer.SHELL.safe_substitute(
            title=html.escape(title),
            heading="House Move",
            subheading=subheading,
            icon_home=PortalIcons.HOME,
            account=account,
            body=body,
            nav=nav,
            app_class="" if nav else " no-nav",
            script=script,
        )

    def _display_name(self, claims: dict[str, Any]) -> str:
        """
        :return: who is signed in, as shown in the app bar.
        """
        if claims.get("sub", "").startswith("guest:"):
            return "Signed in as a guest"
        names: str = ", ".join(self._demo_customers().get(cid, cid) for cid in claims.get("customers", []))
        return f"Signed in as {names}"

    def _first_name(self, claims: dict[str, Any]) -> str | None:
        """
        :return: the signed-in customer's first name for the greeting, or None for a guest.
        """
        customers: list[str] = claims.get("customers") or []
        if not customers:
            return None
        names: str = self._demo_customers().get(customers[0], "")
        return names.split()[0] if names else None

    def _case_store(self) -> CaseStore:
        """
        :return: the case store, opened on first use so the portal starts without touching the database.
        """
        if self._store is None:
            self._store = CaseStore()
        return self._store

    def _own_standalone_case(self, owner: str) -> str | None:
        """
        :return: the owner's active case if it is a standalone case they own, else None.
        """
        store: CaseStore = self._case_store()
        case_id: str | None = store.get_active(owner)
        if CaseStore.is_case(case_id) and store.case_owner(case_id) == owner:
            return case_id
        return None

    def _save_details(self, claims: dict[str, Any], form: dict[str, Any]) -> tuple[str, int, dict[str, str]]:
        """
        Checks the "Your details" form and stores it encrypted: in the vault of the owner's standalone case, or held
        for create_case if the case does not exist yet.
        :return: (HTML message, HTTP status, errors by field).
        """
        if claims.get("customers"):
            return self._error("Your details come from your bank record, so there is nothing to add here."), 400, {}
        clean, errors = PersonalDetails.check(form)
        if errors:
            return self._error("Please check the highlighted fields."), 400, errors
        owner: str = claims.get("sub")
        case_id: str | None = self._own_standalone_case(owner)
        if case_id is None:
            self._case_store().save_pending_intake(owner, clean)
            where: str = "They will be added to your case when you start it in the chat."
        else:
            self._case_store().save_personal(case_id, clean)
            where = "They are on your case now; letters drafted from here on will use them."
        logger.info("Saved personal details for one owner (%s)", "case" if case_id else "pending")
        return self._ok(f"Saved, encrypted. {where}"), 200, {}

    def _details_screen(self, token: str | None, message: str, errors: dict[str, str], typed: dict[str, Any]) -> str:
        """
        The secure form for a guest's full names, street addresses and email. Shows which details are on file but
        never their values. A bank customer's details come from the bank record, so they get no form.
        """
        claims: dict[str, Any] = self._tokens.verify(token) or {}
        head: str = '<div class="section-head"><h2>Your details</h2></div>'
        if claims.get("customers"):
            body: str = f"""{head}<div class="card"><div style="display:flex;gap:12px;align-items:flex-start">
  <span class="avatar">{PortalIcons.SHIELD}</span><div><h2 class="title">Already on file</h2>
  <p class="small muted">As a bank customer, your name, addresses and email come from your bank record. Letters use
  them automatically when they are sent.</p></div></div></div>
{StatementUploadServer.DEMO_NOTE}"""
            return self._page(token, "/details", message + body)

        owner: str = claims.get("sub", "")
        case_id: str | None = self._own_standalone_case(owner)
        store: CaseStore = self._case_store()
        on_file: set[str] = set(store.personal_on_file(case_id) if case_id else store.pending_intake_fields(owner))
        status: str = "".join(
            f'<span class="chip{" soon" if field not in on_file else ""}">'
            f"{'✓' if field in on_file else '•'} {html.escape(label)}</span> "
            for field, label in PersonalDetails.LABELS.items()
        )

        def field(name: str, label: str, kind: str, hint: str, autocomplete: str) -> str:
            # Nested: renders one labelled input with its error, escaping anything typed back into it.
            value: str = html.escape(str(typed.get(name) or ""))
            error: str = errors.get(name, "")
            control: str = (
                f'<textarea name="{name}" id="{name}" rows="2" autocomplete="{autocomplete}" '
                f"required>{value}</textarea>"
                if kind == "textarea"
                else f'<input type="{kind}" name="{name}" id="{name}" value="{value}" '
                f'autocomplete="{autocomplete}" required>'
            )
            return (
                f'<label class="field" for="{name}">{label}</label>{control}'
                f'<p class="small {"error-text" if error else "muted"}">{html.escape(error or hint)}</p>'
            )

        body = f"""{head}
<div class="card">
  <div style="display:flex;gap:12px;align-items:flex-start;margin-bottom:6px">
    <span class="avatar">{PortalIcons.SHIELD}</span>
    <div><h2 class="title">For your letters, never the chat</h2>
    <p class="small muted">Stored encrypted and filled into letters only when they are sent. The assistant never
    sees them.</p></div>
  </div>
  <div class="meta" style="display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 4px">{status}</div>
</div>
<form class="card form" action="/details" method="post" autocomplete="on">
  {field("full_names", "Full names", "textarea", "Each adult who is moving, one per line.", "name")}
  {field("email", "Email", "email", "Where organisations can reply to you.", "email")}
  {field("old_address_line1", "Current street address", "text", "House number and street.", "address-line1")}
  {field("new_address_line1", "New street address", "text", "House number and street.", "address-line1")}
  <p class="small muted">Towns and postcodes come from your move in the chat.</p>
  <button class="btn block" type="submit">Save my details</button>
</form>
{StatementUploadServer.DEMO_NOTE}"""
        return self._page(token, "/details", message + body)

    def _save(self, owner: str, original_name: str, content: bytes) -> tuple[str, int]:
        """
        Checks and stores one upload in the owner's folder.
        :return: (HTML message, HTTP status).
        """
        name: str = self._safe_name(original_name)
        if name.lower().endswith(".pdf"):
            return self._error("PDF statements are not supported yet. Download a CSV or OFX from online banking."), 400
        if not name.lower().endswith(StatementUploadServer.EXTENSIONS):
            return self._error("Please choose a CSV, OFX or QFX file."), 400
        if len(content) > ImportStatement.MAX_BYTES:
            return self._error("The file is larger than 5 MB."), 400
        try:
            rows: int = len(self._parser.parse(content.decode("utf-8-sig"), name))
        except (UnicodeDecodeError, ValueError) as error:
            return self._error(f"That file could not be read as a statement: {error}"), 400

        ImportStatement.purge_stale(self._upload_dir)
        folder: Path = self._upload_dir / ImportStatement.owner_folder(owner)
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        target: Path = self._unique_path(folder, name)
        target.write_bytes(content)
        target.chmod(0o600)
        logger.info("Saved an upload of %d transactions", rows)
        return (
            f'<p class="ok">Uploaded <strong>{html.escape(target.name)}</strong> ({rows} transactions found).<br>'
            f"Now go back to the chat and say: <code>I've uploaded {html.escape(target.name)}. "
            f"Yes, you can read it.</code></p>",
            200,
        )

    @staticmethod
    def _same_origin(request: Request) -> bool:
        """
        Cross-site request forgery guard for approving: the body must be JSON (a cross-site form cannot send that
        without the browser asking first), and the request must come from this portal (see _from_this_site).
        """
        if not request.headers.get("content-type", "").startswith("application/json"):
            return False
        return StatementUploadServer._from_this_site(request)

    @staticmethod
    def _from_this_site(request: Request) -> bool:
        """
        Cross-site request forgery guard for every form post (sign-in, sign-out, upload, details, approve), on top of
        the SameSite=strict cookie: when the browser says where a request comes from (Origin, or Sec-Fetch-Site), it
        must be this portal. Tools without a browser send neither, and still need the session cookie.
        """
        origin: str | None = request.headers.get("origin")
        if origin is not None and origin != f"{request.url.scheme}://{request.url.netloc}":
            return False
        return request.headers.get("sec-fetch-site") in (None, "same-origin", "none")

    @staticmethod
    def _safe_name(original_name: str) -> str:
        """
        Keeps only the last path part and safe characters, so a name cannot point outside the owner's folder.
        """
        base: str = Path(original_name).name.strip() or "statement.csv"
        return StatementUploadServer.UNSAFE_CHARACTERS.sub("_", base)

    @staticmethod
    def _unique_path(folder: Path, name: str) -> Path:
        """
        Adds -2, -3 ... before the extension if a file of that name is already waiting to be imported.
        """
        target: Path = folder / name
        counter: int = 2
        while target.exists():
            target = folder / f"{Path(name).stem}-{counter}{Path(name).suffix}"
            counter += 1
        return target

    @staticmethod
    def _ok(text: str) -> str:
        """
        :return: an escaped success message block.
        """
        return f'<p class="ok">{html.escape(text)}</p>'

    @staticmethod
    def _error(text: str) -> str:
        """
        :return: an escaped error message block.
        """
        return f'<p class="error">{html.escape(text)}</p>'


if __name__ == "__main__":
    StatementUploadServer.main()
