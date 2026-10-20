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
from collections.abc import AsyncIterator
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
from fastapi.responses import RedirectResponse
from fastapi.responses import Response
from fastapi.responses import StreamingResponse

from apps.house_move_upload.chat_relay import ChatRelay
from apps.house_move_upload.details_form import DetailsForm
from apps.house_move_upload.journey_checklist import JourneyChecklist
from apps.house_move_upload.portal_icons import PortalIcons
from coded_tools.hackathon.house_move.approve_on_portal import ApproveOnPortal
from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.bank_address_api import BankAddressApi
from coded_tools.hackathon.house_move.case_status import CaseStatus
from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.create_case import CreateCase
from coded_tools.hackathon.house_move.discovery_records import DiscoveryRecords
from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore
from coded_tools.hackathon.house_move.import_statement import ImportStatement
from coded_tools.hackathon.house_move.personal_details import PersonalDetails
from coded_tools.hackathon.house_move.send_notification import SendNotification
from coded_tools.hackathon.house_move.session_tokens import SessionTokens
from coded_tools.hackathon.house_move.statement_parser import StatementParser

logger = logging.getLogger(__name__)


class StatementUploadServer:  # pylint: disable=too-many-instance-attributes  # injected collaborators
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
    # The Move tab renders the status and next step in the browser; see move_page.html. Its sheets and the chat's
    # action cards share app.js.
    MOVE_PAGE: str = (HERE / "move_page.html").read_text(encoding="utf-8")
    SCRIPT: str = (HERE / "app.js").read_text(encoding="utf-8")
    # The Chat tab streams replies from /chat/send; see chat_page.html.
    CHAT_PAGE: str = (HERE / "chat_page.html").read_text(encoding="utf-8")
    # Three tabs. Upload, Details and the letter review are not places to go: they open where they are needed.
    TABS: tuple[tuple[str, str, str], ...] = (
        ("/", "Move", PortalIcons.ROUTE),
        ("/chat", "Chat", PortalIcons.CHAT),
        ("/account", "Account", PortalIcons.USER),
    )
    CURRENT: str = ' aria-current="page"'
    DEMO_NOTE: str = (
        '<p class="fineprint">Demo with synthetic data. In production, sign-in is the bank\'s own and this is part '
        "of the banking app.</p>"
    )

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # test seams
        self,
        upload_dir: Path | None = None,
        tokens: SessionTokens | None = None,
        data_store: HouseMoveDataStore | None = None,
        audit_log: AuditLog | None = None,
        chat: ChatRelay | None = None,
        case_creator: CreateCase | None = None,
    ):
        """
        :param upload_dir: root of the per-owner upload folders. Defaults to the folder import_statement reads.
        :param tokens: issues and verifies session tokens. Defaults to the shared secret.
        :param data_store: lists the demo bank customers for sign-in.
        :param audit_log: records approvals made on the portal. Defaults to the audit file under logs/.
        :param chat: relays the Chat tab to the agent network. Defaults to the Neuro SAN server on localhost:8080.
        :param case_creator: starts a guest's case from the Details form. Defaults to create_case with live postcodes.
        """
        self._upload_dir: Path = upload_dir or ImportStatement.DEFAULT_UPLOAD_DIR
        self._tokens: SessionTokens = tokens or SessionTokens()
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()
        self._parser: StatementParser = StatementParser()
        self._status: CaseStatus = CaseStatus(tokens=self._tokens)
        self._audit_log: AuditLog = audit_log or AuditLog()
        self._chat: ChatRelay = chat or ChatRelay()
        self._case_creator: CreateCase = case_creator or CreateCase()
        # Holds the personal details given on the "Your details" form; opened on first use (see _case_store).
        self._store: CaseStore | None = None

    def create_app(self) -> FastAPI:
        """
        :return: the FastAPI app.
        """
        app: FastAPI = FastAPI(title="House Move portal", docs_url=None, redoc_url=None)
        ImportStatement.purge_stale(self._upload_dir)

        self._add_account_routes(app)
        self._add_move_routes(app)
        self._add_upload_routes(app)
        self._add_chat_routes(app)
        return app

    def _add_account_routes(self, app: FastAPI) -> None:
        """
        Sign-in, sign-out, the Home tab and the stylesheet.
        """

        # Route handlers are nested so FastAPI can register them against this instance's settings.
        @app.get("/", response_class=HTMLResponse)
        async def home(request: Request) -> str:
            return self._home(request.cookies.get(StatementUploadServer.COOKIE), "")

        @app.get("/portal.css")
        async def styles() -> Response:
            return Response(StatementUploadServer.STYLES, media_type="text/css")

        @app.get("/app.js")
        async def script() -> Response:
            return Response(StatementUploadServer.SCRIPT, media_type="text/javascript")

        @app.get("/next.json")
        async def next_step(request: Request) -> JSONResponse:
            token: str | None = request.cookies.get(StatementUploadServer.COOKIE)
            claims: dict[str, Any] | None = self._tokens.verify(token)
            if claims is None:
                return JSONResponse({"error": "not signed in"}, status_code=401)
            step: dict[str, Any] = await asyncio.to_thread(self._journey().next_step, token, claims)
            return JSONResponse(step, headers={"Cache-Control": "no-store"})

        @app.get("/account", response_class=HTMLResponse)
        async def account(request: Request) -> HTMLResponse:
            token: str | None = request.cookies.get(StatementUploadServer.COOKIE)
            claims: dict[str, Any] | None = self._tokens.verify(token)
            if claims is None:
                return HTMLResponse(self._home(None, self._error("Sign in first.")), status_code=401)
            return HTMLResponse(await asyncio.to_thread(self._account_screen, token, claims))

        @app.get("/start", response_class=HTMLResponse)
        async def start(request: Request) -> Response:
            token: str | None = request.cookies.get(StatementUploadServer.COOKIE)
            claims: dict[str, Any] | None = self._tokens.verify(token)
            if claims is None:
                return HTMLResponse(self._home(None, self._error("Sign in first.")), status_code=401)
            if claims.get("customers") or self._own_standalone_case(claims.get("sub", "")):
                return RedirectResponse("/", status_code=303)
            return HTMLResponse(await asyncio.to_thread(self._details_screen, token, "", {}, {}))

        @app.post("/signin", response_class=HTMLResponse)
        async def sign_in(request: Request, who: str = Form(...)) -> Response:
            if not self._from_this_site(request):
                return HTMLResponse(self._home(None, self._error("Please sign in from this page.")), status_code=403)
            token: str | None = self._issue(who)
            if token is None:
                return HTMLResponse(self._home(None, self._error("Choose a customer or guest.")), status_code=400)
            # Straight to where input is needed: a new guest's start form, otherwise the Move tab and its next step.
            claims: dict[str, Any] = self._tokens.verify(token) or {}
            landing: str = (
                "/" if claims.get("customers") or self._own_standalone_case(claims.get("sub", "")) else "/start"
            )
            response: RedirectResponse = RedirectResponse(landing, status_code=303)
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
            claims: dict[str, Any] | None = self._tokens.verify(request.cookies.get(StatementUploadServer.COOKIE))
            if claims is not None:
                self._chat.forget(claims.get("sub"))
            response: HTMLResponse = HTMLResponse(self._home(None, self._ok("Signed out.")))
            response.delete_cookie(StatementUploadServer.COOKIE)
            return response

    def _add_move_routes(self, app: FastAPI) -> None:
        """
        The My move tab: status, drafts to read, and approving.
        """

        # Route handlers are nested so FastAPI can register them against this instance's settings.
        @app.get("/status")
        async def status_page() -> RedirectResponse:
            # The old "My move" address: the Move tab now shows the status.
            return RedirectResponse("/", status_code=307)

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

        @app.post("/send")
        async def send_approved(request: Request) -> JSONResponse:
            if self._tokens.verify(request.cookies.get(StatementUploadServer.COOKIE)) is None:
                return JSONResponse({"error": "not signed in"}, status_code=401)
            if not self._same_origin(request):
                return JSONResponse({"error": "refused"}, status_code=403)
            result, error = await self._send_approved(request.cookies.get(StatementUploadServer.COOKIE))
            if result is None:
                return JSONResponse({"error": error}, status_code=400)
            return JSONResponse(result)

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

    async def _send_approved(self, token: str | None) -> tuple[dict[str, Any] | None, str | None]:
        """
        "Send them now": sends every letter the customer approved, in its approved wording, and makes the bank's own
        address change if that was approved. The same tools the agents use, so the same gate applies; the click
        comes from the customer's own signed-in browser, so no LLM decides.
        :return: (send_notification's groups plus "bank_update", None), or (None, the reason).
        """
        sly_data: dict[str, Any] = {"session_token": token}
        sent: dict[str, Any] | str = await SendNotification(audit_log=self._audit_log).async_invoke(
            {"task_ids": ["all"]}, sly_data
        )
        if isinstance(sent, str):
            return None, sent.removeprefix("Error: ")
        bank: dict[str, Any] | str | None = None
        if sent.get("direct_updates_pending"):
            bank = await BankAddressApi(self._audit_log).async_invoke({}, {"session_token": token})
        return {**sent, "bank_update": bank if isinstance(bank, dict) else None}, None

    def _add_upload_routes(self, app: FastAPI) -> None:
        """
        The Upload and Details tabs.
        """

        # Route handlers are nested so FastAPI can register them against this instance's settings.
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

        @app.post("/upload.json")
        async def upload_json(request: Request, file: UploadFile = File(...)) -> JSONResponse:
            claims: dict[str, Any] | None = self._tokens.verify(request.cookies.get(StatementUploadServer.COOKIE))
            if claims is None:
                return JSONResponse({"error": "not signed in"}, status_code=401)
            if not self._from_this_site(request):
                return JSONResponse({"error": "refused"}, status_code=403)
            content: bytes = await file.read(ImportStatement.MAX_BYTES + 1)
            saved, error, status = self._store_upload(claims.get("sub"), file.filename or "", content)
            return JSONResponse(saved if saved else {"error": error}, status_code=status)

        @app.get("/details", response_class=HTMLResponse)
        async def details_page(request: Request) -> HTMLResponse:
            token: str | None = request.cookies.get(StatementUploadServer.COOKIE)
            if self._tokens.verify(token) is None:
                return HTMLResponse(self._home(None, self._error("Sign in to add your details.")), status_code=401)
            return HTMLResponse(await asyncio.to_thread(self._details_screen, token, "", {}, {}))

        @app.post("/details", response_class=HTMLResponse)
        async def save_details(request: Request) -> Response:
            token: str | None = request.cookies.get(StatementUploadServer.COOKIE)
            claims: dict[str, Any] | None = self._tokens.verify(token)
            if claims is None:
                return HTMLResponse(self._home(None, self._error("Sign in to add your details.")), status_code=401)
            if not self._from_this_site(request):
                return HTMLResponse(
                    self._home(token, self._error("Save your details from this page.")), status_code=403
                )
            form: dict[str, Any] = dict(await request.form())
            message, status, errors = await self._save_details(claims, token, form)
            if status == 201:
                # The guest's move has just started: the chat carries on from it, without asking anything again.
                return RedirectResponse("/chat?do=start", status_code=303)
            # On an error the typed values go back into the form; once saved, nothing personal is echoed back.
            typed: dict[str, Any] = form if errors else {}
            screen: str = await asyncio.to_thread(self._details_screen, token, message, errors, typed)
            return HTMLResponse(screen, status_code=status)

    def _add_chat_routes(self, app: FastAPI) -> None:
        """
        The Chat tab and its relay to the agent network.
        """

        # Route handlers are nested so FastAPI can register them against this instance's settings.
        @app.get("/chat", response_class=HTMLResponse)
        async def chat_page(request: Request) -> HTMLResponse:
            token: str | None = request.cookies.get(StatementUploadServer.COOKIE)
            claims: dict[str, Any] | None = self._tokens.verify(token)
            if claims is None:
                return HTMLResponse(self._home(None, self._error("Sign in to chat.")), status_code=401)
            return HTMLResponse(self._chat_screen(token, claims))

        @app.get("/chat/history.json")
        async def chat_history(request: Request) -> JSONResponse:
            claims: dict[str, Any] | None = self._tokens.verify(request.cookies.get(StatementUploadServer.COOKIE))
            if claims is None:
                return JSONResponse({"error": "not signed in"}, status_code=401)
            owner: str = claims.get("sub")
            return JSONResponse(
                {"messages": self._chat.history(owner), "busy": self._chat.is_busy(owner)},
                headers={"Cache-Control": "no-store"},
            )

        @app.post("/chat/send")
        async def chat_send(request: Request) -> Response:
            token: str | None = request.cookies.get(StatementUploadServer.COOKIE)
            claims: dict[str, Any] | None = self._tokens.verify(token)
            if claims is None:
                return JSONResponse({"error": "not signed in"}, status_code=401)
            if not self._same_origin(request):
                return JSONResponse({"error": "refused"}, status_code=403)
            try:
                body: Any = await request.json()
            except json.JSONDecodeError:
                return JSONResponse({"error": "expected JSON"}, status_code=400)
            text: str = str((body or {}).get("text") or "").strip() if isinstance(body, dict) else ""
            if not text:
                return JSONResponse({"error": "type a message"}, status_code=400)
            if self._chat.is_busy(claims.get("sub")):
                return JSONResponse({"error": "still answering your last message"}, status_code=409)

            async def events() -> AsyncIterator[bytes]:
                # Nested: streams this request's events as JSON lines.
                async for event in self._chat.send(claims.get("sub"), token, text):
                    yield (json.dumps(event) + "\n").encode("utf-8")

            return StreamingResponse(
                events(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"}
            )

        @app.post("/chat/new")
        async def chat_new(request: Request) -> JSONResponse:
            claims: dict[str, Any] | None = self._tokens.verify(request.cookies.get(StatementUploadServer.COOKIE))
            if claims is None:
                return JSONResponse({"error": "not signed in"}, status_code=401)
            if not self._same_origin(request):
                return JSONResponse({"error": "refused"}, status_code=403)
            self._chat.forget(claims.get("sub"))
            return JSONResponse({"messages": []})

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
            return self._tokens.issue(SessionTokens.bank_owner_id(who), [who])
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
        The welcome screen with sign-in when signed out; the Move tab when signed in: progress, the one next step
        (which runs from here: a sheet, a screen, or the chat), the plan by date and recent activity.
        """
        claims: dict[str, Any] | None = self._tokens.verify(token)
        if claims is None:
            return self._welcome(message)
        greeting: str = html.escape(f"Hi {self._first_name(claims) or 'there'}")
        return self._page(token, "/", message + StatementUploadServer.MOVE_PAGE.replace("__GREETING__", greeting))

    def _journey(self) -> JourneyChecklist:
        """
        :return: the journey over this portal's store and status.
        """
        return JourneyChecklist(DiscoveryRecords(), self._status)

    def _account_screen(self, token: str, claims: dict[str, Any]) -> str:
        """
        The Account tab: who is signed in, their details and statements, the nsflow session, and sign-out.
        """
        customers: list[str] = claims.get("customers") or []
        case_id, names = (
            (customers[0] if customers else None, []) if customers else self._guest_case(claims.get("sub"))
        )
        who: str = (
            html.escape(self._demo_customers().get(customers[0], customers[0]))
            if customers
            else html.escape(" and ".join(names) or "Guest")
        )
        reference: str = (
            f'<p class="small muted">{"Customer ID" if customers else "Case"} '
            f'<code class="ref">{html.escape(case_id)}</code></p>'
            if case_id
            else '<p class="small muted">No move started yet.</p>'
        )
        details: str = (
            '<li><div class="row"><span class="avatar">' + PortalIcons.SHIELD + '</span><div class="main">'
            '<div class="name">Your details</div><div class="small muted">From your bank record</div></div></div></li>'
            if customers
            else '<li><a class="row" href="/details"><span class="avatar">' + PortalIcons.USER + "</span>"
            '<div class="main"><div class="name">Your details</div><div class="small muted">Names, addresses, email'
            "</div></div></a></li>"
        )
        sly: str = html.escape(json.dumps({"session_token": token}))
        body: str = f"""<div class="section-head"><h2>Account</h2></div>
<div class="card"><h2 class="title">{who}</h2>{reference}</div>
<ul class="list">
  {details}
  <li><a class="row" href="/upload"><span class="avatar">{PortalIcons.UPLOAD}</span><div class="main">
    <div class="name">Bank statements</div><div class="small muted">Upload a CSV, OFX or QFX file</div></div></a></li>
</ul>
<details class="card dev">
  <summary><span class="avatar">{PortalIcons.CHAT}</span><span><b>Chatting in nsflow instead?</b>
    <span class="small muted">For developers and judges</span></span></summary>
  <p class="small muted">The Chat tab needs nothing extra. To use nsflow instead, paste this session into its SlyData
  tab (replacing anything there) and tick <em>Use Edited Sly Data</em>; on the command line, pass it with
  <code>--sly_data</code>. Copy it again after each sign-in. Never share it: it opens your case.</p>
  <pre class="token" id="token">{sly}</pre>
  <button class="btn secondary block" type="button" id="copy">{PortalIcons.COPY} Copy session</button>
</details>
<form action="/signout" method="post"><button class="btn ghost block" type="submit">Sign out</button></form>
{StatementUploadServer.DEMO_NOTE}"""
        script: str = """<script>
document.getElementById("copy").onclick = async () => {
  const text = document.getElementById("token").textContent;
  try { await navigator.clipboard.writeText(text); toast("Session copied. Paste it into nsflow's SlyData."); }
  catch (e) { getSelection().selectAllChildren(document.getElementById("token")); toast("Selected: press Copy."); }
};
</script>"""
        return self._page(token, "/account", body, script)

    def _chat_screen(self, token: str | None, claims: dict[str, Any]) -> str:  # pylint: disable=unused-argument
        """
        The Chat tab: talks to the assistant with this sign-in attached on the server. The next step shows as a card
        under the conversation; its "say" actions may send only JourneyChecklist.SAY's set phrases.
        """
        body: str = StatementUploadServer.CHAT_PAGE.replace(
            "__SAY__", html.escape(json.dumps(JourneyChecklist.SAY), quote=True)
        )
        return self._page(token, "/chat", body)

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
        return self._page(token, "/account", message + body, script)

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
        if not claims.get("customers"):
            # A guest has a name and a reference once their case is started (on Details, or in the chat).
            case_id, names = self._guest_case(claims.get("sub", ""))
            return f"{case_id} · {' and '.join(names)}" if case_id else "Signed in as a guest"
        # The customer ID comes first so it stays visible when a long name is cut off on a phone.
        customers: list[str] = claims.get("customers", [])
        names: str = ", ".join(self._demo_customers().get(cid, cid) for cid in customers)
        return f"{', '.join(customers)} · {names}"

    def _first_name(self, claims: dict[str, Any]) -> str | None:
        """
        :return: the signed-in customer's first name for the greeting, or None for a guest with no case yet.
        """
        customers: list[str] = claims.get("customers") or []
        if not customers:
            _, names = self._guest_case(claims.get("sub", ""))
            return names[0].split()[0] if names else None
        names: str = self._demo_customers().get(customers[0], "")
        return names.split()[0] if names else None

    def _guest_case(self, owner: str) -> tuple[str | None, list[str]]:
        """
        :return: a guest's own open case and the names on it (full names from the vault, else first names), or
                 (None, []) before they have started one.
        """
        case_id: str | None = self._own_standalone_case(owner) if owner else None
        if case_id is None:
            return None, []
        profile: dict[str, Any] = self._case_store().get_customer(case_id) or {}
        return case_id, list(profile.get("full_names") or profile.get("first_names") or [])

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

    async def _save_details(
        self, claims: dict[str, Any], token: str, form: dict[str, Any]
    ) -> tuple[str, int, dict[str, str]]:
        """
        Checks the "Your details" form and stores it encrypted. A guest's first save also starts their case, through
        create_case (the same tool the chat uses: postcodes checked, dates checked, details moved into the vault), so
        the assistant already knows their move and never asks for it again. Later saves update the personal details.
        :return: (HTML message, HTTP status, errors by field).
        """
        if claims.get("customers"):
            return self._error("Your details come from your bank record, so there is nothing to add here."), 400, {}
        owner: str = claims.get("sub")
        personal, errors = PersonalDetails.check(form)
        case_id: str | None = await asyncio.to_thread(self._own_standalone_case, owner)
        if case_id is not None:
            if errors:
                return self._error("Please check the highlighted fields."), 400, errors
            await asyncio.to_thread(self._case_store().save_personal, case_id, personal)
            logger.info("Updated personal details on an existing case")
            return self._ok("Saved, encrypted. Letters drafted from now on will use them."), 200, {}

        move, move_errors = PersonalDetails.check_move(form)
        errors.update(move_errors)
        if errors:
            return self._error("Please check the highlighted fields."), 400, errors
        await asyncio.to_thread(self._case_store().save_pending_intake, owner, personal)
        args: dict[str, Any] = {**move, "first_names": [name.split()[0] for name in personal.get("full_names")]}
        created: dict[str, Any] | str = await self._case_creator.async_invoke(args, {"session_token": token})
        if isinstance(created, str):
            # The details stay held (encrypted) for the next attempt; the reason goes back to the form.
            return self._error(created.removeprefix("Error: ")), 400, {}
        logger.info("Started a standalone case from the details form")
        # 201 tells the route to carry straight on to the chat.
        return self._ok(f"Your move is set up. Case reference {created.get('case_id')}."), 201, {}

    def _details_screen(self, token: str | None, message: str, errors: dict[str, str], typed: dict[str, Any]) -> str:
        """
        The secure form for a guest. Before their case exists it asks for everything needed to start it: full names,
        email, both full addresses, the move date and what happens to the current home. Once the case exists it
        shows the move and lets them update their personal details. Saved personal values are never shown back.
        A bank customer's details come from the bank record, so they get no form.
        """
        claims: dict[str, Any] = self._tokens.verify(token) or {}
        head: str = '<div class="section-head"><h2>Your details</h2></div>'
        if claims.get("customers"):
            body: str = f"""{head}<div class="card"><div style="display:flex;gap:12px;align-items:flex-start">
  <span class="avatar">{PortalIcons.SHIELD}</span><div><h2 class="title">Already on file</h2>
  <p class="small muted">As a bank customer, your name, addresses and email come from your bank record. Letters use
  them automatically when they are sent.</p></div></div></div>
{StatementUploadServer.DEMO_NOTE}"""
            return self._page(token, "/account", message + body)

        case_id: str | None = self._own_standalone_case(claims.get("sub", ""))
        form: DetailsForm = DetailsForm(errors, typed)
        if case_id is None:
            head = '<div class="section-head"><h2>Start your move · about 2 minutes</h2></div>'
            body = head + form.start()
        else:
            store: CaseStore = self._case_store()
            body = head + form.update(case_id, store.get_customer(case_id) or {}, store.personal_on_file(case_id))
        return self._page(token, "/account", f"{message}{body}{StatementUploadServer.DEMO_NOTE}")

    def _save(self, owner: str, original_name: str, content: bytes) -> tuple[str, int]:
        """
        Checks and stores one upload from the Upload page.
        :return: (HTML message, HTTP status).
        """
        saved, error, status = self._store_upload(owner, original_name, content)
        if saved is None:
            return self._error(error), status
        return (
            f'<p class="ok">Uploaded <strong>{html.escape(saved.get("file_name"))}</strong> ({saved.get("rows")} '
            f"transactions found).<br>Now go back to the chat and say: <code>I've uploaded "
            f"{html.escape(saved.get('file_name'))}. Yes, you can read it.</code></p>",
            200,
        )

    def _store_upload(
        self, owner: str, original_name: str, content: bytes
    ) -> tuple[dict[str, Any] | None, str | None, int]:
        """
        Checks one upload and stores it in the owner's own folder, readable by them only.
        :return: ({"file_name", "rows"}, None, 200), or (None, a plain-text reason, HTTP status).
        """
        name: str = self._safe_name(original_name)
        if name.lower().endswith(".pdf"):
            return None, "PDF statements are not supported yet. Download a CSV or OFX from online banking.", 400
        if not name.lower().endswith(StatementUploadServer.EXTENSIONS):
            return None, "Please choose a CSV, OFX or QFX file.", 400
        if len(content) > ImportStatement.MAX_BYTES:
            return None, "The file is larger than 5 MB.", 400
        try:
            rows: int = len(self._parser.parse(content.decode("utf-8-sig"), name))
        except (UnicodeDecodeError, ValueError) as error:
            return None, f"That file could not be read as a statement: {error}", 400

        ImportStatement.purge_stale(self._upload_dir)
        folder: Path = self._upload_dir / ImportStatement.owner_folder(owner)
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        target: Path = self._unique_path(folder, name)
        target.write_bytes(content)
        target.chmod(0o600)
        logger.info("Saved an upload of %d transactions", rows)
        return {"file_name": target.name, "rows": rows}, None, 200

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
