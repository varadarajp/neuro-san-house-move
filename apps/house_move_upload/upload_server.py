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

import html
import json
import logging
import re
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi import File
from fastapi import Form
from fastapi import Request
from fastapi import UploadFile
from fastapi.responses import HTMLResponse

from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore
from coded_tools.hackathon.house_move.import_statement import ImportStatement
from coded_tools.hackathon.house_move.session_tokens import SessionTokens
from coded_tools.hackathon.house_move.statement_parser import StatementParser

logger = logging.getLogger(__name__)


class StatementUploadServer:
    """
    A small FastAPI app on 127.0.0.1: sign in (as a demo bank customer, or as a new guest), copy the session token
    into the chat client, and upload CSV, OFX or QFX statements to your own folder. It never lists or returns
    uploaded files, and stores each file readable by its owner only. Use synthetic statements only.
    """

    HOST: str = "127.0.0.1"
    PORT: int = 8090
    COOKIE: str = "hm_session"
    GUEST: str = "guest"
    EXTENSIONS: tuple[str, ...] = (".csv", ".ofx", ".qfx")
    UNSAFE_CHARACTERS: re.Pattern = re.compile(r"[^A-Za-z0-9._-]+")
    PAGE: str = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>House Move portal</title>
<style>
  :root {{ color-scheme: light dark; --accent: #2f7d6d; }}
  body {{ font-family: system-ui, sans-serif; max-width: 40rem; margin: 3rem auto; padding: 0 1rem;
         line-height: 1.5; }}
  .card {{ border: 1px solid #8884; border-radius: 12px; padding: 1.25rem 1.5rem; margin-bottom: 1.25rem; }}
  button {{ background: var(--accent); color: #fff; border: 0; border-radius: 8px; padding: .55rem 1.1rem;
           font-size: 1rem; cursor: pointer; }}
  button.quiet {{ background: transparent; color: inherit; border: 1px solid #8886; }}
  select, input[type=file] {{ font-size: 1rem; }}
  .ok {{ border-left: 4px solid #2da44e; padding: .5rem 1rem; }}
  .error {{ border-left: 4px solid #cf222e; padding: .5rem 1rem; }}
  code, pre {{ background: #8882; padding: .1rem .3rem; border-radius: 4px; }}
  pre {{ padding: .75rem; white-space: pre-wrap; word-break: break-all; }}
  small {{ opacity: .75; }}
</style></head>
<body>
<h1>House Move portal</h1>
{message}
{body}
<small>Demo only: synthetic data. In production, sign-in is the bank's own and uploads are part of the app.</small>
</body></html>"""

    def __init__(
        self,
        upload_dir: Path | None = None,
        tokens: SessionTokens | None = None,
        data_store: HouseMoveDataStore | None = None,
    ):
        """
        :param upload_dir: root of the per-owner upload folders. Defaults to the folder import_statement reads.
        :param tokens: issues and verifies session tokens. Defaults to the shared secret.
        :param data_store: lists the demo bank customers for sign-in.
        """
        self._upload_dir: Path = upload_dir or ImportStatement.DEFAULT_UPLOAD_DIR
        self._tokens: SessionTokens = tokens or SessionTokens()
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()
        self._parser: StatementParser = StatementParser()

    def create_app(self) -> FastAPI:
        """
        :return: the FastAPI app.
        """
        app: FastAPI = FastAPI(title="House Move portal", docs_url=None, redoc_url=None)

        # Route handlers are nested so FastAPI can register them against this instance's settings.
        @app.get("/", response_class=HTMLResponse)
        async def home(request: Request) -> str:
            return self._home(request.cookies.get(StatementUploadServer.COOKIE), "")

        @app.post("/signin", response_class=HTMLResponse)
        async def sign_in(who: str = Form(...)) -> HTMLResponse:
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
        async def sign_out() -> HTMLResponse:
            response: HTMLResponse = HTMLResponse(self._home(None, '<p class="ok">Signed out.</p>'))
            response.delete_cookie(StatementUploadServer.COOKIE)
            return response

        @app.post("/upload", response_class=HTMLResponse)
        async def upload(request: Request, file: UploadFile = File(...)) -> HTMLResponse:
            token: str | None = request.cookies.get(StatementUploadServer.COOKIE)
            claims: dict[str, Any] | None = self._tokens.verify(token)
            if claims is None:
                return HTMLResponse(self._home(None, self._error("Sign in before uploading.")), status_code=401)
            content: bytes = await file.read(ImportStatement.MAX_BYTES + 1)
            message, status = self._save(claims.get("sub"), file.filename or "", content)
            return HTMLResponse(self._home(token, message), status_code=status)

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
        The sign-in form when signed out; the session token and upload form when signed in.
        """
        claims: dict[str, Any] | None = self._tokens.verify(token)
        if claims is None:
            options: str = "".join(
                f'<option value="{html.escape(cid)}">{html.escape(cid)} · {html.escape(name)} (bank customer)</option>'
                for cid, name in self._demo_customers().items()
            )
            body: str = f"""<div class="card"><h2>Sign in</h2>
<form action="/signin" method="post">
  <p><select name="who" required>
    <option value="{StatementUploadServer.GUEST}">I'm not a customer: start as a guest</option>{options}
  </select></p>
  <p><button type="submit">Sign in</button></p>
</form>
<small>Demo sign-in. A bank customer can open only their own record; a guest can open only cases they create.</small>
</div>"""
            return StatementUploadServer.PAGE.format(message=message, body=body)

        who: str = "a guest" if claims.get("sub", "").startswith("guest:") else ", ".join(claims.get("customers", []))
        sly: str = html.escape(json.dumps({"session_token": token}))
        body = f"""<div class="card"><h2>Signed in as {html.escape(who)}</h2>
<p>Give the chat this session, once, at the start:</p>
<ul><li><strong>nsflow:</strong> paste it into the SlyData tab and tick <em>Use Edited Sly Data</em>.</li>
<li><strong>Command line:</strong> pass it with <code>--sly_data</code>.</li></ul>
<pre>{sly}</pre>
<form action="/signout" method="post"><button class="quiet" type="submit">Sign out</button></form>
</div>
<div class="card"><h2>Upload a bank statement</h2>
<form action="/upload" method="post" enctype="multipart/form-data">
  <p><input type="file" name="file" accept=".csv,.ofx,.qfx" required></p>
  <p><button type="submit">Upload</button></p>
</form>
<small>CSV, OFX or QFX, up to 5&nbsp;MB. Read once to find who you pay, then deleted. It never goes into the chat.
</small></div>"""
        return StatementUploadServer.PAGE.format(message=message, body=body)

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
    def _error(text: str) -> str:
        """
        :return: an escaped error message block.
        """
        return f'<p class="error">{html.escape(text)}</p>'


if __name__ == "__main__":
    StatementUploadServer.main()
