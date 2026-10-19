"""Server-side sessions: who is calling, which case they may open, and the case's real state."""

import json
import logging
import threading
from typing import Any

from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.house_move_data_store import HouseMoveDataStore
from coded_tools.hackathon.house_move.session_tokens import SessionTokens

logger = logging.getLogger(__name__)


class CaseSession:
    """
    Puts the server, not the client, in charge of case state.

    sly_data arrives from the client and neuro-san merges it in unchecked, so nothing in it is trusted except a
    signed session token. On open, every server-held key the client may have sent is discarded, the caller's identity
    comes from the verified token, and the case's state and profile are reloaded from the case store. On save, the
    state goes back to the store, encrypted. The client is only ever sent the token (see allow.to_upstream).

    Access rules: a bank customer record (C-...) opens only if the token lists it; a standalone case (S-...) opens
    only for the owner who created it.
    """

    TOKEN_KEY: str = "session_token"
    OWNER_KEY: str = "_owner"
    # Plan state saved per case. Personal data in drafts and the outbox is protected by encrypting the whole blob.
    STATE_KEYS: tuple[str, ...] = (
        "councils",
        "obligations",
        "tasks",
        "previous_tasks",
        "plan_dates",
        "drafts",
        "outbox",
        "audit_log",
        "processed_replies",
        "mandates",
        "approvals",
    )
    # Everything the server sets. Any of these sent by the client is discarded before a tool runs.
    SERVER_KEYS: tuple[str, ...] = STATE_KEYS + ("customer_id", "customer_profile", OWNER_KEY)

    # One lock per owner: neuro-san can run tools in parallel on the same session, and each tool reloads, changes
    # and saves the state, so tools for one owner must not interleave.
    _locks: dict[str, threading.Lock] = {}
    _locks_guard: threading.Lock = threading.Lock()

    def __init__(
        self,
        store: CaseStore | None = None,
        tokens: SessionTokens | None = None,
        data_store: HouseMoveDataStore | None = None,
    ):
        """
        :param store: the case store. Defaults to the SQLite store.
        :param tokens: verifies session tokens. Defaults to the shared secret.
        :param data_store: source of bank customer records.
        """
        self._store: CaseStore = store or CaseStore()
        self._tokens: SessionTokens = tokens or SessionTokens()
        self._data_store: HouseMoveDataStore = data_store or HouseMoveDataStore()
        self._owner: str | None = None
        self._claims: dict[str, Any] = {}
        self._lock: threading.Lock | None = None

    def verify(self, sly_data: dict[str, Any]) -> str | None:
        """
        Checks the session token and records who is calling. Touches nothing else: other tools of the same owner
        may be running on this sly_data, so state is only changed once the owner's lock is held (see load_active).
        :return: None on success, or an "Error: ..." string.
        """
        claims: dict[str, Any] | None = self._tokens.verify(sly_data.get(CaseSession.TOKEN_KEY))
        if claims is None:
            for key in CaseSession.SERVER_KEYS:
                sly_data.pop(key, None)
            return "Error: not signed in, or the session has expired. Sign in on the House Move portal first."
        self._claims = claims
        self._owner = claims.get("sub")
        return None

    def try_lock(self) -> bool:
        """
        Tries to take the owner's lock without waiting, so callers can wait asynchronously instead of tying up a
        worker thread (blocking waits starve the thread pool when many tools run in parallel).
        :return: true if the lock is now held.
        """
        lock: threading.Lock = CaseSession._lock_for(self._owner)
        if lock.acquire(blocking=False):
            self._lock = lock
            return True
        return False

    def load_active(self, sly_data: dict[str, Any], needs_case: bool) -> str | None:
        """
        With the owner's lock held: discards every server-held key the client sent and loads the owner's active case
        from the store.
        :return: None on success, or an "Error: ..." string.
        """
        for key in CaseSession.SERVER_KEYS:
            sly_data.pop(key, None)
        sly_data[CaseSession.OWNER_KEY] = self._owner
        active: str | None = self._store.get_active(self._owner)
        if active and self._may_open(active):
            self._load(sly_data, active)
        elif needs_case:
            return "Error: no case is open. Look up the customer, or start a case, first."
        return None

    def select_case(self, sly_data: dict[str, Any], case_id: str, load: bool = True) -> str | None:
        """
        Makes a case the owner's active case if they are allowed to open it.
        :param load: true to replace sly_data's state with the stored state (resuming); false to keep what is in
                     sly_data (a case that was just created).
        :return: None on success, or an "Error: ..." string. The error does not reveal whether the case exists.
        """
        if not self._may_open(case_id):
            logger.warning("Owner %s was refused access to %s", self._owner, case_id)
            return f"Error: no customer or case {case_id} is available to you."
        self._store.set_active(self._owner, case_id)
        if load:
            for key in CaseSession.STATE_KEYS:
                sly_data.pop(key, None)
            self._load(sly_data, case_id)
        else:
            sly_data["customer_id"] = case_id
        return None

    def save(self, sly_data: dict[str, Any]) -> None:
        """
        Saves the active case's state to the store, if a case is active and still the owner's.
        """
        case_id: str | None = sly_data.get("customer_id")
        if self._owner is None or not case_id or not self._may_open(case_id):
            return
        state: dict[str, Any] = {key: sly_data.get(key) for key in CaseSession.STATE_KEYS if key in sly_data}
        self._store.save_case_state(self._owner, case_id, state)

    def close(self, sly_data: dict[str, Any]) -> None:
        """
        Removes the owner marker from sly_data and releases the owner's lock.
        """
        sly_data.pop(CaseSession.OWNER_KEY, None)
        if self._lock is not None:
            self._lock.release()
            self._lock = None

    def read_only_view(self, token: str | None) -> dict[str, Any] | None:
        """
        For the portal's status page: the signed-in owner's active case, read without a lock and never saved.
        The same ownership rules apply as for the agents.
        :return: None if the token is not valid; otherwise {"owner", "case_id"} plus the case's profile and saved
                 state when a case is open (case_id is None when none is).
        """
        if self.verify({CaseSession.TOKEN_KEY: token}) is not None:
            return None
        view: dict[str, Any] = {"owner": self._owner, "case_id": None}
        active: str | None = self._store.get_active(self._owner)
        if active and self._may_open(active):
            self._load(view, active)
            view["case_id"] = active
        return view

    def _may_open(self, case_id: str) -> bool:
        """
        :return: true if the verified caller may open the case.
        """
        if CaseStore.is_case(case_id):
            return self._owner is not None and self._store.case_owner(case_id) == self._owner
        return case_id in (self._claims.get("customers") or [])

    def _load(self, sly_data: dict[str, Any], case_id: str) -> None:
        """
        Loads the case's profile (from the CRM or the encrypted vault) and its saved state into sly_data.
        """
        try:
            profile: dict[str, Any] | None = (
                self._store.get_customer(case_id)
                if CaseStore.is_case(case_id)
                else self._data_store.get_customer(case_id)
            )
        except (OSError, json.JSONDecodeError) as error:
            logger.error("Could not load the profile for %s: %s", case_id, error)
            profile = None
        sly_data["customer_id"] = case_id
        if profile is not None:
            sly_data["customer_profile"] = profile
        sly_data.update(self._store.load_case_state(self._owner, case_id))

    @classmethod
    def _lock_for(cls, owner: str) -> threading.Lock:
        """
        :return: the process-wide lock for one owner.
        """
        with cls._locks_guard:
            return cls._locks.setdefault(owner, threading.Lock())
