from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.resume_codes import ResumeCodes
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase


class TestResumeCodes(HouseMoveTestCase):
    """
    Unit tests for ResumeCodes: a guest's way back to their own case after signing out (issue #10).
    """

    OWNER: str = "guest:resume-test"

    def new_case(self) -> str:
        """
        :return: a standalone case owned by OWNER.
        """
        return CaseStore().create_case({"first_names": ["Jamie"]}, {}, self.OWNER)

    def test_right_code_returns_the_owner(self):
        """
        The reference and code give back the case's owner; the code is matched without regard to case, spaces or
        dashes, and only a hash of it is stored.
        """
        case_id: str = self.new_case()
        code: str = ResumeCodes().issue(case_id)
        self.assertRegex(code, r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
        self.assertEqual(self.OWNER, ResumeCodes().redeem(case_id, code))
        self.assertEqual(self.OWNER, ResumeCodes().redeem(f" {case_id} ", code.lower().replace("-", " ")))
        self.assertNotIn(code.replace("-", "").encode(), (self.folder / "cases.sqlite").read_bytes())

    def test_wrong_codes_and_unknown_cases_are_refused(self):
        """
        A wrong code, another case's code, an unknown reference or a case without a code all give nothing.
        """
        case_id: str = self.new_case()
        other: str = self.new_case()
        ResumeCodes().issue(case_id)
        other_code: str = ResumeCodes().issue(other)
        self.assertIsNone(ResumeCodes().redeem(case_id, "AAAA-AAAA"))
        self.assertIsNone(ResumeCodes().redeem(case_id, other_code))
        self.assertIsNone(ResumeCodes().redeem("S-unknown", other_code))
        self.assertIsNone(ResumeCodes().redeem(self.new_case(), ""))

    def test_too_many_wrong_codes_lock_until_a_new_code(self):
        """
        After MAX_FAILURES wrong codes even the right one is refused; a new code, made while signed in, works and the
        old one does not.
        """
        case_id: str = self.new_case()
        code: str = ResumeCodes().issue(case_id)
        for _ in range(ResumeCodes.MAX_FAILURES):
            self.assertIsNone(ResumeCodes().redeem(case_id, "AAAA-AAAA"))
        self.assertIsNone(ResumeCodes().redeem(case_id, code))
        fresh: str = ResumeCodes().issue(case_id)
        self.assertIsNone(ResumeCodes().redeem(case_id, code))
        self.assertEqual(self.OWNER, ResumeCodes().redeem(case_id, fresh))

    def test_deleting_the_case_deletes_its_code(self):
        """
        Erasing a case erases its resume code too.
        """
        case_id: str = self.new_case()
        code: str = ResumeCodes().issue(case_id)
        CaseStore().delete_case(case_id)
        self.assertFalse(ResumeCodes().has_code(case_id))
        self.assertIsNone(ResumeCodes().redeem(case_id, code))
