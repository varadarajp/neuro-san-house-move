import shutil
import stat
import tempfile
from pathlib import Path
from unittest import TestCase

from fastapi.testclient import TestClient

from apps.house_move_upload.upload_server import StatementUploadServer


class TestStatementUploadServer(TestCase):
    """
    Unit tests for the local statement upload page.
    """

    SAMPLES: Path = Path(__file__).resolve().parents[3] / "coded_tools/hackathon/house_move/data/sample_statements"

    def setUp(self):
        """
        Uses a temporary uploads folder.
        """
        self.uploads: Path = Path(tempfile.mkdtemp()) / "uploads"
        self.client: TestClient = TestClient(StatementUploadServer(self.uploads).create_app())

    def tearDown(self):
        """
        Removes the temporary folder.
        """
        shutil.rmtree(self.uploads.parent)

    def post(self, name: str, content: bytes):
        """
        Uploads one file through the form endpoint.
        """
        return self.client.post("/upload", files={"file": (name, content, "application/octet-stream")})

    def test_page_and_valid_uploads(self):
        """
        CSV and OFX samples are saved owner-only, with the file name to say in chat; a repeat name gets a suffix.
        """
        self.assertIn("Upload a bank statement", self.client.get("/").text)
        csv_bytes: bytes = (self.SAMPLES / "jordan_lee_bristol.csv").read_bytes()
        first = self.post("jordan_lee_bristol.csv", csv_bytes)
        second = self.post("jordan_lee_bristol.csv", csv_bytes)
        ofx = self.post("my statement.ofx", (self.SAMPLES / "jordan_lee_bristol.ofx").read_bytes())

        self.assertEqual(200, first.status_code)
        self.assertIn("34 transactions found", first.text)
        self.assertIn("jordan_lee_bristol-2.csv", second.text)
        self.assertIn("my_statement.ofx", ofx.text)
        saved: Path = self.uploads / "jordan_lee_bristol.csv"
        self.assertEqual(csv_bytes, saved.read_bytes())
        self.assertEqual(0o600, stat.S_IMODE(saved.stat().st_mode))

    def test_rejections(self):
        """
        PDFs, other types, unreadable CSVs and oversized files are refused and nothing is saved.
        """
        self.assertIn("PDF statements are not supported", self.post("s.pdf", b"%PDF-1.7").text)
        self.assertEqual(400, self.post("s.exe", b"MZ").status_code)
        self.assertIn("could not be read", self.post("s.csv", b"Foo,Bar\n1,2\n").text)
        self.assertIn("larger than 5 MB", self.post("big.csv", b"x" * (5 * 1024 * 1024 + 1)).text)
        self.assertFalse(self.uploads.exists() and any(self.uploads.iterdir()))

    def test_path_tricks_stay_in_uploads(self):
        """
        A name with folders or odd characters is reduced to a safe file name inside the uploads folder.
        """
        content: bytes = b"Date,Description,Amount\n2026-09-01,BRIGHTWATT EN DD 1,-5.00\n"
        response = self.post("../../etc/pass wd.csv", content)
        self.assertIn("pass_wd.csv", response.text)
        self.assertEqual(["pass_wd.csv"], [path.name for path in self.uploads.iterdir()])
