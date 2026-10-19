import re
import stat
from pathlib import Path

from fastapi.testclient import TestClient

from apps.house_move_upload.upload_server import StatementUploadServer
from coded_tools.hackathon.house_move.import_statement import ImportStatement
from coded_tools.hackathon.house_move.session_tokens import SessionTokens
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase


class TestStatementUploadServer(HouseMoveTestCase):
    """
    Unit tests for the House Move portal: demo sign-in, session tokens and per-owner statement uploads.
    """

    SAMPLES: Path = Path(__file__).resolve().parents[3] / "coded_tools/hackathon/house_move/data/sample_statements"

    def setUp(self):
        """
        A portal using the temporary uploads folder.
        """
        super().setUp()
        self.client: TestClient = TestClient(StatementUploadServer(self.uploads).create_app())

    def sign_in(self, who: str) -> str:
        """
        Signs in through the form and returns the issued token.
        """
        response = self.client.post("/signin", data={"who": who})
        self.assertEqual(200, response.status_code)
        return self.client.cookies.get(StatementUploadServer.COOKIE)

    def post(self, name: str, content: bytes):
        """
        Uploads one file through the form endpoint.
        """
        return self.client.post("/upload", files={"file": (name, content, "application/octet-stream")})

    def test_sign_in_issues_scoped_tokens(self):
        """
        A bank customer's token opens only their record; a guest gets a new identity with no bank records.
        """
        self.assertIn("Sign in", self.client.get("/").text)
        bank = SessionTokens().verify(self.sign_in("C-10042"))
        self.assertEqual(("bank:C-10042", ["C-10042"]), (bank.get("sub"), bank.get("customers")))
        page: str = self.client.get("/").text
        self.assertIn("session_token", page)
        self.client.post("/signout")
        guest = SessionTokens().verify(self.sign_in("guest"))
        self.assertTrue(guest.get("sub").startswith("guest:"))
        self.assertEqual([], guest.get("customers"))
        self.assertEqual(400, self.client.post("/signin", data={"who": "C-99999"}).status_code)

    def test_upload_needs_sign_in_and_goes_to_own_folder(self):
        """
        Uploads are refused when signed out; when signed in they go to the owner's folder, owner-only, and a repeat
        name gets a suffix.
        """
        csv_bytes: bytes = (self.SAMPLES / "jordan_lee_bristol.csv").read_bytes()
        self.assertEqual(401, self.post("jordan_lee_bristol.csv", csv_bytes).status_code)

        owner: str = SessionTokens().verify(self.sign_in("guest")).get("sub")
        first = self.post("jordan_lee_bristol.csv", csv_bytes)
        second = self.post("jordan_lee_bristol.csv", csv_bytes)
        ofx = self.post("my statement.ofx", (self.SAMPLES / "jordan_lee_bristol.ofx").read_bytes())

        self.assertEqual(200, first.status_code)
        self.assertIn("34 transactions found", first.text)
        self.assertIn("jordan_lee_bristol-2.csv", second.text)
        self.assertIn("my_statement.ofx", ofx.text)
        saved: Path = self.uploads / ImportStatement.owner_folder(owner) / "jordan_lee_bristol.csv"
        self.assertEqual(csv_bytes, saved.read_bytes())
        self.assertEqual(0o600, stat.S_IMODE(saved.stat().st_mode))
        self.assertEqual([ImportStatement.owner_folder(owner)], [p.name for p in self.uploads.iterdir()])

    def test_rejections(self):
        """
        PDFs, other types, unreadable CSVs and oversized files are refused and nothing is saved.
        """
        self.sign_in("guest")
        self.assertIn("PDF statements are not supported", self.post("s.pdf", b"%PDF-1.7").text)
        self.assertEqual(400, self.post("s.exe", b"MZ").status_code)
        self.assertIn("could not be read", self.post("s.csv", b"Foo,Bar\n1,2\n").text)
        self.assertIn("larger than 5 MB", self.post("big.csv", b"x" * (5 * 1024 * 1024 + 1)).text)
        self.assertFalse(any(self.uploads.iterdir()))

    def test_path_tricks_stay_in_own_folder(self):
        """
        A name with folders or odd characters is reduced to a safe file name inside the owner's folder.
        """
        owner: str = SessionTokens().verify(self.sign_in("guest")).get("sub")
        content: bytes = b"Date,Description,Amount\n2026-09-01,BRIGHTWATT EN DD 1,-5.00\n"
        response = self.post("../../etc/pass wd.csv", content)
        self.assertIn("pass_wd.csv", response.text)
        folder: Path = self.uploads / ImportStatement.owner_folder(owner)
        self.assertEqual(["pass_wd.csv"], [path.name for path in folder.iterdir()])
        self.assertIsNotNone(re.fullmatch(r"[0-9a-f]{24}", folder.name))
