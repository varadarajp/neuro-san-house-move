"""Relays the portal's Chat tab to the House Move agent network on the Neuro SAN server."""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from datetime import UTC
from datetime import datetime
from typing import Any

from aiohttp import ClientError
from neuro_san.session.async_http_service_agent_session import AsyncHttpServiceAgentSession

logger = logging.getLogger(__name__)


class ChatRelay:
    """
    Sends a signed-in person's chat messages to the house_move network and streams back progress and the reply.

    The session token is attached here, on the server, from the portal's own sign-in cookie: the customer never
    copies it, and it cannot be another customer's. Each owner's conversation (the network's chat_context and the
    visible messages) is held in this process's memory only, one message at a time, and is forgotten on sign-out or
    "New chat". Personal details still never belong in the chat; the agents say so and point to the Details tab.
    """

    AGENT: str = "hackathon/house_move"
    HOST_ENV: str = "HOUSE_MOVE_AGENT_HOST"
    PORT_ENV: str = "HOUSE_MOVE_AGENT_PORT"
    MAX_TEXT: int = 2000
    # Agent names in the network -> what the customer sees while that agent works.
    PROGRESS: dict[str, str] = {
        "intake_agent": "Setting up your case",
        "profile_agent": "Looking up your details",
        "discovery_agent": "Finding who you pay",
        "obligations_agent": "Working out who needs telling",
        "timeline_agent": "Building your dated plan",
        "local_services_agent": "Finding local GPs, schools and water",
        "government_agent": "Preparing government and council letters",
        "home_utilities_agent": "Preparing home and utility letters",
        "financial_agent": "Preparing bank and insurance letters",
        "health_family_agent": "Preparing health and family letters",
        "comms_agent": "Writing your letters",
        "tracker_agent": "Handling approvals and sending",
        "reply_agent": "Reading replies from providers",
    }

    def __init__(self, session_factory: Any = None):
        """
        :param session_factory: makes the agent session; tests pass a fake. Defaults to the HTTP session for the
                                server at HOUSE_MOVE_AGENT_HOST:HOUSE_MOVE_AGENT_PORT (localhost:8080).
        """
        self._session_factory: Any = session_factory or self._http_session
        self._conversations: dict[str, dict[str, Any]] = {}
        self._busy: set[str] = set()

    def history(self, owner: str) -> list[dict[str, str]]:
        """
        :return: the owner's visible messages, oldest first: {"role" (you or assistant), "text", "at"}.
        """
        return list((self._conversations.get(owner) or {}).get("messages") or [])

    def forget(self, owner: str) -> None:
        """
        Ends the owner's conversation (sign-out, or "New chat").
        """
        self._conversations.pop(owner, None)

    def is_busy(self, owner: str) -> bool:
        """
        :return: true while a message from this owner is being answered.
        """
        return owner in self._busy

    async def send(self, owner: str, token: str, text: str) -> AsyncIterator[dict[str, str]]:
        """
        Sends one message and yields events as they arrive: {"type": "progress", "text"} while agents work, then
        {"type": "reply", "text"}, or {"type": "error", "text"} if the agent server cannot be reached.
        Callers check is_busy first: one message per owner at a time.
        """
        text = text.strip()[: ChatRelay.MAX_TEXT]
        conversation: dict[str, Any] = self._conversations.setdefault(owner, {"chat_context": None, "messages": []})
        conversation.get("messages").append(self._message("you", text))
        request: dict[str, Any] = {
            "user_message": {"type": "HUMAN", "text": text},
            "sly_data": {"session_token": token},
            # Without MAXIMAL the server sends only the final reply; with it, each agent's messages arrive as they
            # happen, which is what the progress labels are made from. Their text is never shown.
            "chat_filter": {"chat_filter_type": "MAXIMAL"},
        }
        if conversation.get("chat_context"):
            request["chat_context"] = conversation.get("chat_context")

        self._busy.add(owner)
        reply: str | None = None
        last_progress: str | None = None
        try:
            async for message in self._session_factory().streaming_chat(request):
                response: dict[str, Any] = message.get("response") or {}
                if response.get("chat_context"):
                    conversation["chat_context"] = response.get("chat_context")
                    reply = response.get("text") or reply
                    continue
                progress: str | None = self._progress(response)
                if progress and progress != last_progress:
                    last_progress = progress
                    yield {"type": "progress", "text": progress}
        except (ClientError, OSError, ValueError, asyncio.TimeoutError) as error:
            logger.error("The agent server could not be reached: %s", error)
            yield {"type": "error", "text": "The assistant is not available right now. Please try again shortly."}
            return
        finally:
            self._busy.discard(owner)

        reply = reply or "Sorry, I did not get an answer. Please try again."
        conversation.get("messages").append(self._message("assistant", reply))
        yield {"type": "reply", "text": reply}

    @staticmethod
    def _progress(response: dict[str, Any]) -> str | None:
        """
        :return: what the customer sees for an intermediate message: the deepest known agent at work, or None.
        """
        for step in reversed(response.get("origin") or []):
            label: str | None = ChatRelay.PROGRESS.get((step or {}).get("tool"))
            if label:
                return label
        return None

    @staticmethod
    def _message(role: str, text: str) -> dict[str, str]:
        """
        :return: one visible message with its time.
        """
        return {"role": role, "text": text, "at": datetime.now(UTC).isoformat(timespec="seconds")}

    @staticmethod
    def _http_session() -> AsyncHttpServiceAgentSession:
        """
        :return: a session to the Neuro SAN server's house_move network, with no overall time limit: a turn that
                 drafts every letter takes several minutes.
        """
        return AsyncHttpServiceAgentSession(
            host=os.environ.get(ChatRelay.HOST_ENV) or "localhost",
            port=os.environ.get(ChatRelay.PORT_ENV) or "8080",
            agent_name=ChatRelay.AGENT,
        )
