import json
import os
import re
import stat
import time
from pathlib import Path

from fastapi.testclient import TestClient

from apps.house_move_upload.chat_relay import ChatRelay
from apps.house_move_upload.upload_server import StatementUploadServer
from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.create_case import CreateCase
from coded_tools.hackathon.house_move.import_statement import ImportStatement
from coded_tools.hackathon.house_move.personal_details import PersonalDetails
from coded_tools.hackathon.house_move.resume_codes import ResumeCodes
from coded_tools.hackathon.house_move.session_tokens import SessionTokens
from tests.apps.house_move_upload.fake_agent_session import FakeAgentSession
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase
from tests.coded_tools.hackathon.house_move.house_move_test_state import HouseMoveTestState


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
        FakeAgentSession.requests = []
        self.client: TestClient = TestClient(
            StatementUploadServer(
                self.uploads,
                chat=ChatRelay(session_factory=FakeAgentSession),
                case_creator=CreateCase(council_lookup=HouseMoveTestState.offline_lookup()),
            ).create_app()
        )

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
        self.assertIn("session_token", self.client.get("/account").text)
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

    def test_move_tab_and_its_feeds_need_sign_in_and_show_own_case(self):
        """
        The Move tab's feeds refuse signed-out visitors; the old "My move" address now leads to the Move tab;
        signed in, the feed shows the owner's own case.
        """
        self.assertEqual(401, self.client.get("/status.json").status_code)
        self.assertEqual(401, self.client.get("/next.json").status_code)
        old = self.client.get("/status", follow_redirects=False)
        self.assertEqual((307, "/"), (old.status_code, old.headers.get("location")))

        self.sign_in("C-10042")
        self.assertIsNone(self.client.get("/status.json").json().get("case"))
        HouseMoveTestState.drafted_session()
        page = self.client.get("/")
        self.assertIn('id="move"', page.text)
        self.assertIn("Hi Sam", page.text)
        feed = self.client.get("/status.json")
        self.assertEqual("no-store", feed.headers.get("cache-control"))
        self.assertEqual("Drafts ready", feed.json().get("stage"))
        self.assertEqual("C-10042", feed.json().get("case").get("reference"))

    def test_approve_from_the_status_page(self):
        """
        Drafts open only for their signed-in owner; approving needs a same-origin JSON request and then shows up in
        the status.
        """
        self.assertEqual(404, self.client.get("/draft/T-01.json").status_code)
        self.sign_in("C-10042")
        HouseMoveTestState.drafted_session()
        waiting: list = self.client.get("/status.json").json().get("awaiting_approval")
        task_id: str = next(t.get("task_id") for t in waiting if not t.get("legal_requirement"))
        draft: dict = self.client.get(f"/draft/{task_id}.json").json()
        self.assertEqual("Moving", draft.get("subject"))
        body: dict = {"approve": [{"task_id": task_id, "draft_hash": draft.get("draft_hash")}]}

        self.assertEqual(403, self.client.post("/approve", data={"approve": task_id}).status_code)
        self.assertEqual(
            403, self.client.post("/approve", json=body, headers={"Origin": "https://evil.test"}).status_code
        )
        approved = self.client.post("/approve", json=body)
        self.assertEqual({"approved": [task_id], "not_applied": []}, approved.json())
        self.assertEqual(404, self.client.get(f"/draft/{task_id}.json").status_code)
        groups: dict = {g.get("status"): g for g in self.client.get("/status.json").json().get("groups")}
        self.assertEqual([task_id], [t.get("task_id") for t in groups.get("approved").get("tasks")])

        self.client.post("/signout")
        self.client.cookies.clear()
        self.assertEqual(401, self.client.post("/approve", json=body).status_code)

    def test_details_form_starts_a_guests_move_and_echoes_nothing(self):
        """
        A guest's first save of Details (names, email, both full addresses, move date) starts their case, with the
        personal fields in the vault; afterwards the app shows their name and case reference, and later saves update
        the personal fields only. Mistakes come back field by field. A bank customer gets no form.
        """
        self.assertEqual(401, self.client.get("/details").status_code)
        owner: str = SessionTokens().verify(self.sign_in("guest")).get("sub")
        self.assertIn("Save and start my move", self.client.get("/details").text)
        form: dict = {
            "full_names": "Jordan Lee",
            "email": "jordan.lee@example.com",
            "old_address_line1": "8 Cotham Vale",
            "old_town": "Bristol",
            "old_postcode": "bs6 6rb",
            "old_property": "sold",
            "new_address_line1": "21 Lenton Boulevard",
            "new_town": "Nottingham",
            "new_postcode": "NG7 2NF",
            "move_date": "2026-12-11",
        }
        bad = self.client.post("/details", data={**form, "email": "nope", "new_postcode": "12345"})
        self.assertEqual(400, bad.status_code)
        self.assertIn("Enter an email address", bad.text)
        self.assertIn("Enter a UK postcode", bad.text)
        self.assertIn("21 Lenton Boulevard", bad.text)

        saved = self.client.post("/details", data=form, follow_redirects=False)
        self.assertEqual(201, saved.status_code, saved.text)
        self.assertIn('href="/chat?do=start"', saved.text)
        store: CaseStore = CaseStore()
        case_id: str = store.get_active(owner)
        self.assertTrue(case_id.startswith("S-"))
        self.assertIn(case_id, saved.text)
        self.assertIn("Resume code", saved.text)
        self.assertEqual(sorted(PersonalDetails.LABELS), store.personal_on_file(case_id))
        profile: dict = store.get_customer(case_id)
        self.assertEqual(
            ("Bristol", "BS6 6RB"),
            (profile.get("old_address").get("town"), profile.get("old_address").get("postcode")),
        )
        self.assertEqual("2026-12-11", profile.get("completion_date"))

        home: str = self.client.get("/").text
        self.assertIn(f"{case_id} · Jordan Lee", home)
        self.assertIn("Hi Jordan", home)
        page: str = self.client.get("/details").text
        self.assertIn("Update my details", page)
        self.assertIn("Bristol, BS6 6RB", page)
        self.assertNotIn("Cotham", page)
        updated = self.client.post("/details", data={**form, "email": "jordan@example.org"})
        self.assertIn("Saved, encrypted", updated.text)
        self.assertEqual("jordan@example.org", store.get_customer(case_id).get("email"))

        self.client.post("/signout")
        self.sign_in("C-10042")
        self.assertIn("Already on file", self.client.get("/details").text)
        self.assertEqual(400, self.client.post("/details", data=form).status_code)

    def start_guest_move(self) -> tuple[str, str, str]:
        """
        Signs in as a new guest and starts their move on the start form.
        :return: (owner, case reference, resume code shown once).
        """
        owner: str = SessionTokens().verify(self.sign_in("guest")).get("sub")
        form: dict = {
            "full_names": "Jordan Lee",
            "email": "jordan.lee@example.com",
            "old_address_line1": "8 Cotham Vale",
            "old_town": "Bristol",
            "old_postcode": "BS6 6RB",
            "old_property": "sold",
            "new_address_line1": "21 Lenton Boulevard",
            "new_town": "Nottingham",
            "new_postcode": "NG7 2NF",
            "move_date": "2026-12-11",
        }
        started: str = self.client.post("/details", data=form).text
        case_id: str = CaseStore().get_active(owner)
        code: str = re.search(r"Resume code <code class=\"ref\">([A-Z2-9-]{9})</code>", started).group(1)
        return owner, case_id, code

    def test_a_guest_comes_back_with_their_reference_and_resume_code(self):
        """
        Issue #10: after signing out, a guest gets back to their own move only with its reference and resume code.
        A new guest sign-in cannot see it, wrong codes are refused, and a new code from Account replaces the old one.
        """
        owner, case_id, code = self.start_guest_move()
        self.assertIn("Continue my move", self.client.post("/signout").text)
        self.sign_in("guest")
        self.assertNotIn(case_id, self.client.get("/").text)
        self.client.post("/signout")

        wrong = self.client.post("/resume", data={"case_reference": case_id, "resume_code": "AAAA-AAAA"})
        self.assertEqual(400, wrong.status_code)
        self.assertIsNone(self.client.cookies.get(StatementUploadServer.COOKIE))
        back = self.client.post(
            "/resume", data={"case_reference": case_id, "resume_code": code}, follow_redirects=False
        )
        self.assertEqual((303, "/"), (back.status_code, back.headers.get("location")))
        self.assertEqual(
            owner, SessionTokens().verify(self.client.cookies.get(StatementUploadServer.COOKIE)).get("sub")
        )
        self.assertIn(f"{case_id} · Jordan Lee", self.client.get("/").text)

        account: str = self.client.post("/resume-code").text
        fresh: str = re.search(r"Resume code <code class=\"ref\">([A-Z2-9-]{9})</code>", account).group(1)
        self.assertIsNone(ResumeCodes().redeem(case_id, code))
        self.assertEqual(owner, ResumeCodes().redeem(case_id, fresh))

    def test_resume_is_refused_from_another_site_and_for_bank_customers(self):
        """
        A resume posted from another site is refused, and a bank customer has no resume code to make.
        """
        _, case_id, code = self.start_guest_move()
        self.client.post("/signout")
        cross: dict = {"origin": "https://evil.example"}
        refused = self.client.post("/resume", data={"case_reference": case_id, "resume_code": code}, headers=cross)
        self.assertEqual(403, refused.status_code)
        self.sign_in("C-10042")
        self.assertEqual(400, self.client.post("/resume-code").status_code)

    def test_form_posts_from_another_site_are_refused(self):
        """
        Sign-in, upload, details and approval refuse a request a browser says came from another site.
        """
        cross: dict = {"Origin": "https://evil.test", "Sec-Fetch-Site": "cross-site"}
        self.assertEqual(403, self.client.post("/signin", data={"who": "guest"}, headers=cross).status_code)
        self.sign_in("guest")
        csv_bytes: bytes = (self.SAMPLES / "jordan_lee_bristol.csv").read_bytes()
        files: dict = {"file": ("s.csv", csv_bytes, "text/csv")}
        self.assertEqual(403, self.client.post("/upload", files=files, headers=cross).status_code)
        self.assertEqual(
            403, self.client.post("/upload", files=files, headers={"Sec-Fetch-Site": "cross-site"}).status_code
        )
        self.assertEqual(403, self.client.post("/details", data={"email": "x"}, headers=cross).status_code)
        self.assertFalse(any(self.uploads.iterdir()))

    def test_uploads_never_imported_are_deleted(self):
        """
        An upload older than a day that was never imported is deleted, with its empty folder.
        """
        owner: str = SessionTokens().verify(self.sign_in("guest")).get("sub")
        self.post("old.csv", (self.SAMPLES / "jordan_lee_bristol.csv").read_bytes())
        folder: Path = self.uploads / ImportStatement.owner_folder(owner)
        old: Path = folder / "old.csv"
        stale: float = time.time() - ImportStatement.MAX_AGE_SECONDS - 60
        os.utime(old, (stale, stale))
        self.assertEqual(1, ImportStatement.purge_stale(self.uploads))
        self.assertFalse(folder.exists())

    def test_chat_tab_attaches_the_sign_in_and_streams_the_reply(self):
        """
        The Chat tab needs sign-in; a message goes to the agents with this sign-in's own session, the reply streams
        back as JSON lines, history survives a reload, and sign-out forgets the conversation.
        """
        self.assertEqual(401, self.client.get("/chat").status_code)
        self.assertEqual(401, self.client.post("/chat/send", json={"text": "hi"}).status_code)
        token: str = self.sign_in("C-10042")
        page = self.client.get("/chat")
        self.assertIn("I&#x27;m moving house", page.text)
        self.assertNotIn("I&#x27;m customer", page.text)

        cross = self.client.post("/chat/send", json={"text": "hi"}, headers={"Origin": "https://evil.test"})
        self.assertEqual(403, cross.status_code)
        self.assertEqual(400, self.client.post("/chat/send", json={"text": "  "}).status_code)
        sent = self.client.post("/chat/send", json={"text": "I'm moving house"})
        events: list = [json.loads(line) for line in sent.text.splitlines()]
        self.assertEqual(["progress", "reply"], [event.get("type") for event in events])
        self.assertEqual({"session_token": token}, FakeAgentSession.requests[0].get("sly_data"))

        history: dict = self.client.get("/chat/history.json").json()
        self.assertEqual(["you", "assistant"], [m.get("role") for m in history.get("messages")])
        self.assertEqual(200, self.client.post("/chat/new", json={}).status_code)
        self.assertEqual([], self.client.get("/chat/history.json").json().get("messages"))

        self.client.post("/chat/send", json={"text": "again"})
        self.client.post("/signout")
        self.sign_in("C-10042")
        self.assertEqual([], self.client.get("/chat/history.json").json().get("messages"))

    def test_sign_in_and_next_step_take_you_where_input_is_needed(self):
        """
        A new guest lands on the start form; a bank customer lands on Move. The next step follows the case: a
        drafted plan's next step is the review sheet, and the chat may only send the set phrases.
        """
        landing = self.client.post("/signin", data={"who": "guest"}, follow_redirects=False)
        self.assertEqual((303, "/start"), (landing.status_code, landing.headers.get("location")))
        self.assertEqual("start", self.client.get("/next.json").json().get("key"))
        self.assertIn("Save and start my move", self.client.get("/start").text)

        self.client.post("/signout")
        landing = self.client.post("/signin", data={"who": "C-10042"}, follow_redirects=False)
        self.assertEqual((303, "/"), (landing.status_code, landing.headers.get("location")))
        self.assertEqual(303, self.client.get("/start", follow_redirects=False).status_code)
        first: dict = self.client.get("/next.json").json()
        self.assertEqual(("tell", {"type": "say", "say": "start"}), (first.get("key"), first.get("action")))
        HouseMoveTestState.drafted_session()
        step: dict = self.client.get("/next.json").json()
        self.assertEqual(("review", {"type": "sheet", "sheet": "review"}), (step.get("key"), step.get("action")))
        self.assertIn("Please send my approved letters.", self.client.get("/chat").text)

    def test_upload_sheet_stores_the_statement_and_names_it(self):
        """
        The upload sheet's endpoint stores the file in the owner's folder and returns its name and row count;
        it refuses signed-out and cross-site requests and unreadable files, as JSON.
        """
        csv_bytes: bytes = (self.SAMPLES / "jordan_lee_bristol.csv").read_bytes()
        files: dict = {"file": ("jordan_lee_bristol.csv", csv_bytes, "text/csv")}
        self.assertEqual(401, self.client.post("/upload.json", files=files).status_code)
        owner: str = SessionTokens().verify(self.sign_in("guest")).get("sub")
        cross = self.client.post("/upload.json", files=files, headers={"Origin": "https://evil.test"})
        self.assertEqual(403, cross.status_code)
        saved = self.client.post("/upload.json", files=files)
        self.assertEqual({"file_name": "jordan_lee_bristol.csv", "rows": 34}, saved.json())
        self.assertTrue((self.uploads / ImportStatement.owner_folder(owner) / "jordan_lee_bristol.csv").exists())
        bad = self.client.post("/upload.json", files={"file": ("s.pdf", b"%PDF", "application/pdf")})
        self.assertEqual(400, bad.status_code)
        self.assertIn("PDF", bad.json().get("error"))

    def test_customer_id_is_shown_after_sign_in(self):
        """
        A bank customer sees their customer ID in the app bar on every tab and on Account, and the first step says
        they need not type it; a guest has none to show.
        """
        self.sign_in("C-20001")
        home: str = self.client.get("/").text
        self.assertIn("C-20001 · Priya Shah", home)
        account: str = self.client.get("/account").text
        self.assertIn("Customer ID", account)
        self.assertIn("C-20001", account)
        self.assertIn("already knows who you are", self.client.get("/next.json").json().get("hint"))

        self.client.post("/signout")
        self.sign_in("guest")
        self.assertIn("Signed in as a guest", self.client.get("/account").text)

    def test_send_them_now_sends_only_what_was_approved(self):
        """
        "Send them now" sends the approved letter and nothing else, through the same gate as the agents; it needs
        the sign-in and a same-origin JSON request.
        """
        self.assertEqual(401, self.client.post("/send", json={}).status_code)
        self.sign_in("C-10042")
        HouseMoveTestState.drafted_session()
        waiting: list = self.client.get("/status.json").json().get("awaiting_approval")
        task_id: str = next(t.get("task_id") for t in waiting if not t.get("legal_requirement"))
        draft: dict = self.client.get(f"/draft/{task_id}.json").json()
        self.client.post("/approve", json={"approve": [{"task_id": task_id, "draft_hash": draft.get("draft_hash")}]})

        self.assertEqual(403, self.client.post("/send", data={"x": "1"}).status_code)
        sent = self.client.post("/send", json={})
        self.assertEqual(200, sent.status_code, sent.text)
        went: list = sent.json().get("sent_to_organization") + sent.json().get("packs_for_customer")
        self.assertEqual([task_id], [item.get("task_id") for item in went])
        groups: dict = {g.get("status"): g for g in self.client.get("/status.json").json().get("groups")}
        self.assertNotIn("approved", groups)
        self.assertEqual(len(waiting) - 1, len(groups.get("awaiting_approval").get("tasks")))
