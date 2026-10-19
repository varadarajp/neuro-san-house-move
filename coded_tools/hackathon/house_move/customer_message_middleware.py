"""Records what the customer actually typed, where the LLM cannot write."""

import asyncio
import logging
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import override

from langchain.agents.middleware.types import AgentMiddleware
from langchain.agents.middleware.types import ContextT
from langchain.agents.middleware.types import ModelRequest
from langchain.agents.middleware.types import ModelResponse
from langchain.agents.middleware.types import ResponseT
from langchain_core.messages import BaseMessage
from langchain_core.messages import HumanMessage

from coded_tools.hackathon.house_move.case_session import CaseSession
from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.session_tokens import SessionTokens

logger = logging.getLogger(__name__)


class CustomerMessageMiddleware(AgentMiddleware):
    """
    Middleware for the front man (move_coordinator). Before each model call it takes the latest human message, which is
    the customer's own chat input, and stores it in the case store under the signed-in owner.

    Confirmation codes are checked against this stored message (see ConfirmWithCode), never against words the LLM
    passes to a tool, so an LLM, or text injected into it, cannot approve, import or delete on the customer's behalf.
    Only the front man is wrapped: in sub-agents a human message is an inquiry written by another agent.
    """

    def __init__(
        self,
        sly_data: dict[str, Any] | None = None,
        store: CaseStore | None = None,
        tokens: SessionTokens | None = None,
    ):
        """
        :param sly_data: the request's sly_data, injected by neuro-san when the HOCON lists "sly_data": null.
        :param store: where messages are stored. Defaults to the SQLite case store.
        :param tokens: verifies the session token. Defaults to the shared secret.
        """
        super().__init__()
        self._sly_data: dict[str, Any] = sly_data if sly_data is not None else {}
        self._store: CaseStore | None = store
        self._tokens: SessionTokens | None = tokens

    @override
    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        """
        Records the customer's message, then lets the model call run unchanged.
        """
        self.record(request.messages)
        return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        """
        Async version: the database write runs on a worker thread.
        """
        await asyncio.to_thread(self.record, request.messages)
        return await handler(request)

    def record(self, messages: list[BaseMessage]) -> bool:
        """
        Stores the latest human message for the signed-in owner.
        :return: true if a message was stored.
        """
        claims: dict[str, Any] | None = (self._tokens or SessionTokens()).verify(
            self._sly_data.get(CaseSession.TOKEN_KEY)
        )
        text: str | None = self._latest_human_text(messages)
        if claims is None or text is None:
            return False
        (self._store or CaseStore()).record_customer_message(claims.get("sub"), text)
        return True

    @staticmethod
    def _latest_human_text(messages: list[BaseMessage]) -> str | None:
        """
        :return: the text of the last human message, or None.
        """
        for message in reversed(messages or []):
            if isinstance(message, HumanMessage):
                content: Any = message.content
                if isinstance(content, str):
                    return content
                return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
        return None
