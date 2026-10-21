"""Resume codes: how a guest gets back to their own move after signing out."""

import hashlib
import hmac
import logging
import secrets

from coded_tools.hackathon.house_move.case_database import CaseDatabase

logger = logging.getLogger(__name__)


class ResumeCodes(CaseDatabase):
    """
    A guest's sign-in is a random identity that only their session carries, and their case belongs to it. To come
    back after signing out they give their case reference and the resume code they were shown when the case
    started; redeeming both returns the case's owner, so the portal can sign them in as that owner again.

    Only a salted, slow hash of each code is kept (resume_codes table), one code per case. A new code replaces the
    old one. After MAX_FAILURES wrong codes the case's code stops working until the owner, signed in, makes a new one.
    The code never goes to the LLM or the chat: only the portal issues and checks it.
    """

    # 8 characters from 32 easily-read symbols (no 0/O or 1/I): 40 bits, shown as XXXX-XXXX.
    ALPHABET: str = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    LENGTH: int = 8
    MAX_FAILURES: int = 5
    HASH_ROUNDS: int = 200_000

    def issue(self, case_id: str) -> str:
        """
        Makes a new resume code for a case, replacing any earlier one and clearing its failed attempts.
        :return: the code, for showing to the customer once. Only its hash is stored.
        """
        code: str = "".join(secrets.choice(ResumeCodes.ALPHABET) for _ in range(ResumeCodes.LENGTH))
        salt: bytes = secrets.token_bytes(16)
        with self._transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO resume_codes VALUES (?, ?, ?, 0, ?)",
                (case_id, salt, self._hash(code, salt), self._now()),
            )
        logger.info("Issued a resume code for case %s", case_id)
        return f"{code[:4]}-{code[4:]}"

    def redeem(self, case_id: str, code: str) -> str | None:
        """
        Checks a case reference and resume code as typed (case, spaces and dashes in the code do not matter).
        :return: the owner of the case if the code is right, it has not been locked by wrong attempts, and the case
                 still exists; otherwise None, without saying which check failed.
        """
        typed: str = "".join(character for character in (code or "").upper() if character.isalnum())
        with self._reading() as connection:
            row: tuple | None = connection.execute(
                "SELECT r.salt, r.code_hash, r.failures, c.owner_id FROM resume_codes r "
                "JOIN cases c ON c.case_id = r.case_id WHERE r.case_id = ?",
                ((case_id or "").strip(),),
            ).fetchone()
        if row is None:
            logger.warning("Resume refused: no code for that case reference")
            return None
        salt, code_hash, failures, owner = row
        if failures >= ResumeCodes.MAX_FAILURES:
            logger.warning("Resume refused for case %s: locked after %d wrong codes", case_id, failures)
            return None
        right: bool = hmac.compare_digest(code_hash, self._hash(typed, salt))
        with self._transaction() as connection:
            connection.execute(
                "UPDATE resume_codes SET failures = ? WHERE case_id = ?", (0 if right else failures + 1, case_id)
            )
        if not right:
            logger.warning("Resume refused for case %s: wrong code", case_id)
            return None
        logger.info("Resumed case %s", case_id)
        return owner

    def has_code(self, case_id: str) -> bool:
        """
        :return: true if the case has a resume code (working or locked).
        """
        with self._reading() as connection:
            row: tuple | None = connection.execute(
                "SELECT 1 FROM resume_codes WHERE case_id = ?", (case_id,)
            ).fetchone()
        return row is not None

    @staticmethod
    def _hash(code: str, salt: bytes) -> bytes:
        """
        :return: the PBKDF2-SHA256 of a code: slow on purpose, since a code is short enough to try many offline.
        """
        return hashlib.pbkdf2_hmac("sha256", code.encode("ascii", errors="ignore"), salt, ResumeCodes.HASH_ROUNDS)
