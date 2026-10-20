import asyncio
import copy
import re
import shutil
from pathlib import Path

from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.bank_address_api import BankAddressApi
from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.council_lookup import CouncilLookup
from coded_tools.hackathon.house_move.create_case import CreateCase
from coded_tools.hackathon.house_move.customer_profile_lookup import CustomerProfileLookup
from coded_tools.hackathon.house_move.get_transactions import GetTransactions
from coded_tools.hackathon.house_move.import_statement import ImportStatement
from coded_tools.hackathon.house_move.list_tasks import ListTasks
from coded_tools.hackathon.house_move.send_notification import SendNotification
from coded_tools.hackathon.house_move.session_tokens import SessionTokens
from coded_tools.hackathon.house_move.update_tasks import UpdateTasks
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase
from tests.coded_tools.hackathon.house_move.house_move_test_state import HouseMoveTestState


class TestSessionSecurity(HouseMoveTestCase):
    """
    Regression tests for the security review: each attack is replayed through the real session path (invoke), the way
    neuro-san calls the tools, and must fail.
    """

    HOCON: Path = Path(__file__).resolve().parents[4] / "registries/hackathon/house_move.hocon"
    SAMPLES: Path = Path(__file__).resolve().parents[4] / "coded_tools/hackathon/house_move/data/sample_statements"
    NO_FILE: AuditLog = AuditLog(None)

    @staticmethod
    def offline() -> CouncilLookup:
        """
        :return: a council lookup that answers from the cache.
        """
        return HouseMoveTestState.offline_lookup()

    @staticmethod
    def drafted_bank_session() -> str:
        """
        :return: a session token for C-10042 whose plan is fully drafted, built through the session.
        """
        return HouseMoveTestState.drafted_session()

    def test_forged_approval_in_client_state_is_ignored(self):
        """
        Finding 1: a client that sends back tasks marked "approved" gets nothing sent and no bank address change;
        the server's state is unchanged and the audit log shows no send.
        """
        token: str = self.drafted_bank_session()
        honest: list = ListTasks().invoke({}, {"session_token": token}).get("tasks")
        forged_tasks: list = copy.deepcopy(honest)
        for task in forged_tasks:
            task["status"] = "approved"
        forged: dict = {
            "session_token": token,
            "customer_id": "C-10042",
            "tasks": forged_tasks,
            "drafts": {},
            "audit_log": [],
        }

        sent = SendNotification(audit_log=self.NO_FILE).invoke({"task_ids": ["all"]}, forged)
        self.assertEqual([], sent.get("sent_to_organization") + sent.get("packs_for_customer"))
        self.assertEqual([], sent.get("direct_updates_pending"))
        bank = BankAddressApi(self.NO_FILE).invoke({}, {**forged, "tasks": forged_tasks})
        self.assertTrue(bank.startswith("Error: the customer has not approved"))

        after: list = ListTasks().invoke({}, {"session_token": token}).get("tasks")
        self.assertEqual({"awaiting_approval"}, {t.get("status") for t in after})

    def test_only_the_session_token_goes_back_to_the_client(self):
        """
        Finding 3: no case data (profile, mandates, drafts, outbox) is on the to_upstream allow-list.
        """
        hocon: str = self.HOCON.read_text()
        allowed: list = re.findall(r"to_upstream\s*\{\s*sly_data\s*=\s*\[([^\]]*)\]", hocon)
        self.assertEqual(['"session_token"'], [entry.strip() for entry in allowed])

    def test_cannot_open_another_customer(self):
        """
        Finding 3: a signed-in customer cannot open another customer's record by naming it, nor by putting its ID
        into sly_data.
        """
        token: str = SessionTokens().issue("bank:C-10042", ["C-10042"])
        sly: dict = {"session_token": token}
        refused = CustomerProfileLookup().invoke({"customer_id": "C-20001"}, sly)
        self.assertEqual("Error: no customer or case C-20001 is available to you.", refused)
        self.assertNotIn("customer_profile", sly)

        CustomerProfileLookup().invoke({"customer_id": "C-10042"}, sly)
        sneaky: dict = {"session_token": token, "customer_id": "C-20001"}
        self.assertEqual("C-10042", GetTransactions().invoke({}, sneaky).get("customer_id"))

    def test_missing_tampered_expired_or_foreign_tokens_are_refused(self):
        """
        Without a valid token signed with this server's secret, no tool runs.
        """
        good: str = SessionTokens().issue("bank:C-10042", ["C-10042"])
        tampered: str = good[:-2] + ("AA" if not good.endswith("AA") else "BB")
        expired: str = SessionTokens().issue("bank:C-10042", ["C-10042"], lifetime_seconds=-60)
        foreign: str = SessionTokens(secret=b"another-server").issue("bank:C-10042", ["C-10042"])
        forged_claims: str = SessionTokens(secret=b"x").issue("bank:C-20001", ["C-10042", "C-20001"])
        for token in (None, "", "not-a-token", tampered, expired, foreign, forged_claims):
            result = CustomerProfileLookup().invoke({"customer_id": "C-10042"}, {"session_token": token})
            self.assertTrue(str(result).startswith("Error: not signed in"), token)

    def test_profile_lookup_defaults_to_the_signed_in_customer(self):
        """
        With no customer ID, the lookup opens the signed-in bank customer's own record; a guest with no case gets
        a clear "no bank customer is signed in", and a guest with a case gets that case back.
        """
        bank: str = SessionTokens().issue(SessionTokens.bank_owner_id("C-20001"), ["C-20001"])
        profile = CustomerProfileLookup().invoke({}, {"session_token": bank})
        self.assertEqual("C-20001", profile.get("customer_id"))
        self.assertEqual(["Priya"], profile.get("first_names"))

        guest: str = SessionTokens().issue("guest:new", [])
        refused = CustomerProfileLookup().invoke({}, {"session_token": guest})
        self.assertIn("no bank customer is signed in", refused)
        self.assertIn("Do not ask for a customer ID", refused)

        args: dict = {
            "first_names": ["Ana"],
            "old_postcode": "BS6 6RB",
            "new_postcode": "NG7 2NF",
            "move_date": "2026-12-11",
        }
        case_id: str = CreateCase(council_lookup=self.offline()).invoke(args, {"session_token": guest}).get("case_id")
        self.assertEqual(case_id, CustomerProfileLookup().invoke({}, {"session_token": guest}).get("customer_id"))

    def test_a_bank_session_cannot_start_someone_elses_move(self):
        """
        A chat signed in as bank customer C-10042 cannot create a standalone case: it would be filed under that
        customer's sign-in. Nothing is created, and the customer's own record is untouched.
        """
        token: str = SessionTokens().issue(SessionTokens.bank_owner_id("C-10042"), ["C-10042"])
        args: dict = {
            "first_names": ["Varada"],
            "old_postcode": "CM2 6AP",
            "new_postcode": "HX3 0HY",
            "move_date": "2027-01-04",
        }
        result = CreateCase(council_lookup=self.offline()).invoke(args, {"session_token": token})
        self.assertIn("signed in as a bank customer", result)
        self.assertIn("I'm not a customer (guest)", result)
        self.assertIsNone(CaseStore().get_active(SessionTokens.bank_owner_id("C-10042")))

    def test_standalone_case_ids_are_unguessable_and_owned(self):
        """
        Standalone case IDs carry 128 random bits, and only the creator can open one.
        """
        owner_token: str = SessionTokens().issue("guest:alice", [])
        sly: dict = {"session_token": owner_token}
        args: dict = {
            "first_names": ["Alice"],
            "old_postcode": "BS6 6RB",
            "new_postcode": "NG7 2NF",
            "move_date": "2026-12-11",
        }
        first = CreateCase(council_lookup=self.offline()).invoke(args, sly).get("case_id")
        second = CreateCase(council_lookup=self.offline()).invoke(args, {"session_token": owner_token}).get("case_id")
        self.assertGreaterEqual(len(first), 2 + 22)
        self.assertNotEqual(first, second)

        bob: dict = {"session_token": SessionTokens().issue("guest:bob", [])}
        self.assertTrue(CustomerProfileLookup().invoke({"customer_id": first}, bob).startswith("Error: no customer"))

    def test_case_state_is_encrypted_at_rest(self):
        """
        Drafts rendered with names and addresses are stored server-side, but never in plain text.
        """
        self.drafted_bank_session()
        database: bytes = (self.folder / "cases.sqlite").read_bytes()
        for secret in (b"Wharf Approach", b"Mayfield Grove", b"Sam Morgan", b"Brightwatt"):
            self.assertNotIn(secret, database)

    def test_uploads_are_per_owner(self):
        """
        One person cannot import another person's uploaded statement by guessing its file name.
        """
        alice: str = SessionTokens().issue("guest:alice", [])
        folder: Path = self.uploads / ImportStatement.owner_folder("guest:alice")
        folder.mkdir()
        shutil.copy(self.SAMPLES / "jordan_lee_bristol.csv", folder / "jordan_lee_bristol.csv")

        bob: dict = {"session_token": SessionTokens().issue("guest:bob", [])}
        args: dict = {
            "first_names": ["Bob"],
            "old_postcode": "BS6 6RB",
            "new_postcode": "NG7 2NF",
            "move_date": "2026-12-11",
        }
        CreateCase(council_lookup=self.offline()).invoke(args, bob)
        stolen = ImportStatement().invoke({"file_name": "jordan_lee_bristol.csv"}, bob)
        self.assertEqual("Error: jordan_lee_bristol.csv has not been uploaded.", stolen)
        self.assertTrue((folder / "jordan_lee_bristol.csv").exists())
        self.assertIsNotNone(alice)

    def test_parallel_tool_calls_do_not_lose_updates(self):
        """
        neuro-san can run tools in parallel on one session; per-owner locking keeps every update.
        """
        token: str = self.drafted_bank_session()
        tasks: list = ListTasks().invoke({}, {"session_token": token}).get("tasks")

        async def update_all() -> None:
            # Nested so the parallel calls share this test's tasks; each call gets its own sly_data, as sub-agents do.
            await asyncio.gather(
                *(
                    UpdateTasks().async_invoke(
                        {
                            "updates": [
                                {
                                    "task_id": t.get("task_id"),
                                    "action": f"done {t.get('task_id')}",
                                    "channel": t.get("channel"),
                                }
                            ]
                        },
                        {"session_token": token},
                    )
                    for t in tasks
                )
            )

        asyncio.run(update_all())
        after: list = ListTasks().invoke({}, {"session_token": token}).get("tasks")
        self.assertEqual({f"done {t.get('task_id')}" for t in tasks}, {t.get("action") for t in after})
