"""
Local upload page for the House Move standalone app.

nsflow's paperclip pastes a file's text into the chat message, which would send a bank statement through the LLM.
This page stands in for the real app's secure upload: the file goes straight into the uploads/ folder, where the
import_statement tool reads it once and deletes it. The chat only ever sees the file name.

Run from the repository root:
    python -m apps.house_move_upload.upload_server
then open http://127.0.0.1:8090
"""

import html
import logging
import re
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi import File
from fastapi import UploadFile
from fastapi.responses import HTMLResponse

from coded_tools.hackathon.house_move.import_statement import ImportStatement
from coded_tools.hackathon.house_move.statement_parser import StatementParser

logger = logging.getLogger(__name__)


class StatementUploadServer:
    """
    A one-page FastAPI app: choose a CSV, OFX or QFX statement, check it can be read, and save it to uploads/.
    It listens on 127.0.0.1 only, never lists or returns uploaded files, and stores each file readable by its owner
    only. Use synthetic statements only.
    """

    HOST: str = "127.0.0.1"
    PORT: int = 8090
    EXTENSIONS: tuple[str, ...] = (".csv", ".ofx", ".qfx")
    UNSAFE_CHARACTERS: re.Pattern = re.compile(r"[^A-Za-z0-9._-]+")
    PAGE: str = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Upload a statement</title>
<style>
  :root {{ color-scheme: light dark; --accent: #1f6feb; }}
  body {{ font-family: system-ui, sans-serif; max-width: 34rem; margin: 3rem auto; padding: 0 1rem;
         line-height: 1.5; }}
  .card {{ border: 1px solid #8884; border-radius: 12px; padding: 1.5rem; }}
  button {{ background: var(--accent); color: #fff; border: 0; border-radius: 8px; padding: .6rem 1.2rem;
           font-size: 1rem; cursor: pointer; }}
  .ok {{ border-left: 4px solid #2da44e; padding: .5rem 1rem; }}
  .error {{ border-left: 4px solid #cf222e; padding: .5rem 1rem; }}
  code {{ background: #8882; padding: .1rem .3rem; border-radius: 4px; }}
  small {{ opacity: .75; }}
</style></head>
<body>
<h1>Upload a bank statement</h1>
{message}
<div class="card">
<form action="/upload" method="post" enctype="multipart/form-data">
  <p><input type="file" name="file" accept=".csv,.ofx,.qfx" required></p>
  <p><button type="submit">Upload</button></p>
</form>
<small>CSV, OFX or QFX, up to 5&nbsp;MB. The file is read once to find who you pay, then deleted.
It never goes into the chat. Demo: use synthetic statements only.</small>
</div>
</body></html>"""

    def __init__(self, upload_dir: Path | None = None):
        """
        :param upload_dir: where files are saved. Defaults to the folder import_statement reads from.
        """
        self._upload_dir: Path = upload_dir or ImportStatement.DEFAULT_UPLOAD_DIR
        self._parser: StatementParser = StatementParser()

    def create_app(self) -> FastAPI:
        """
        :return: the FastAPI app with the form page and the upload endpoint.
        """
        app: FastAPI = FastAPI(title="House Move statement upload", docs_url=None, redoc_url=None)

        # Route handlers are nested so FastAPI can register them against this instance's settings.
        @app.get("/", response_class=HTMLResponse)
        async def form() -> str:
            return self._page("")

        @app.post("/upload", response_class=HTMLResponse)
        async def upload(file: UploadFile = File(...)) -> HTMLResponse:
            content: bytes = await file.read(ImportStatement.MAX_BYTES + 1)
            message, status = self._save(file.filename or "", content)
            return HTMLResponse(self._page(message), status_code=status)

        return app

    @staticmethod
    def main() -> None:
        """
        Starts the upload page on 127.0.0.1:8090.
        """
        logging.basicConfig(level=logging.INFO)
        server: StatementUploadServer = StatementUploadServer()
        uvicorn.run(server.create_app(), host=StatementUploadServer.HOST, port=StatementUploadServer.PORT)

    def _save(self, original_name: str, content: bytes) -> tuple[str, int]:
        """
        Checks and stores one upload.
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

        self._upload_dir.mkdir(parents=True, exist_ok=True)
        target: Path = self._unique_path(name)
        target.write_bytes(content)
        target.chmod(0o600)
        logger.info("Saved an upload of %d transactions as %s", rows, target.name)
        return (
            f'<p class="ok">Uploaded <strong>{html.escape(target.name)}</strong> ({rows} transactions found).<br>'
            f"Now go back to the chat and say: <code>I've uploaded {html.escape(target.name)}. "
            f"Yes, you can read it.</code></p>",
            200,
        )

    def _safe_name(self, original_name: str) -> str:
        """
        Keeps only the last path part and safe characters, so a name cannot point outside the uploads folder.
        """
        base: str = Path(original_name).name.strip() or "statement.csv"
        return StatementUploadServer.UNSAFE_CHARACTERS.sub("_", base)

    def _unique_path(self, name: str) -> Path:
        """
        Adds -2, -3 ... before the extension if a file of that name is already waiting to be imported.
        """
        target: Path = self._upload_dir / name
        counter: int = 2
        while target.exists():
            target = self._upload_dir / f"{Path(name).stem}-{counter}{Path(name).suffix}"
            counter += 1
        return target

    def _page(self, message: str) -> str:
        """
        :return: the page with an optional result message.
        """
        return StatementUploadServer.PAGE.format(message=message)

    @staticmethod
    def _error(text: str) -> str:
        """
        :return: an escaped error message block.
        """
        return f'<p class="error">{html.escape(text)}</p>'


if __name__ == "__main__":
    StatementUploadServer.main()
