import asyncio

from aiohttp import ClientError

from apps.house_move_upload.chat_relay import ChatRelay
from tests.apps.house_move_upload.fake_agent_session import FakeAgentSession
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase


class UnreachableSession:  # pylint: disable=too-few-public-methods
    """
    An agent session whose server is down.
    """

    async def streaming_chat(self, request: dict):
        """
        Fails before yielding anything.
        """
        raise ClientError("connection refused")
        yield request  # pylint: disable=unreachable


class TestChatRelay(HouseMoveTestCase):
    """
    Unit tests for the Chat tab's relay to the agent network.
    """

    def setUp(self):
        """
        A relay talking to the fake agent session.
        """
        super().setUp()
        FakeAgentSession.requests = []
        self.relay: ChatRelay = ChatRelay(session_factory=FakeAgentSession)

    def send(self, relay: ChatRelay, owner: str, token: str, text: str) -> list:
        """
        :return: every event the relay yields for one message.
        """

        async def collect() -> list:
            # Nested: gathers the async generator's events for this call.
            return [event async for event in relay.send(owner, token, text)]

        return asyncio.run(collect())

    def test_attaches_the_session_and_keeps_the_conversation(self):
        """
        The server-side session token goes in sly_data; progress is named in plain words; the chat context returned
        by one turn is sent with the next; the visible history holds both sides.
        """
        events = self.send(self.relay, "guest:a", "token-a", "  hello  ")
        self.assertEqual(
            [
                {"type": "progress", "text": "Building your dated plan"},
                {"type": "reply", "text": FakeAgentSession.REPLY},
            ],
            events,
        )
        first: dict = FakeAgentSession.requests[0]
        self.assertEqual({"session_token": "token-a"}, first.get("sly_data"))
        self.assertEqual("hello", first.get("user_message").get("text"))
        self.assertNotIn("chat_context", first)

        self.send(self.relay, "guest:a", "token-a", "next")
        self.assertEqual({"turn": 1}, FakeAgentSession.requests[1].get("chat_context"))
        self.assertEqual(
            ["you", "assistant", "you", "assistant"], [m.get("role") for m in self.relay.history("guest:a")]
        )
        self.assertEqual([], self.relay.history("guest:b"))
        self.assertFalse(self.relay.is_busy("guest:a"))

        self.relay.forget("guest:a")
        self.assertEqual([], self.relay.history("guest:a"))

    def test_server_down_is_a_friendly_error(self):
        """
        If the agent server cannot be reached, the customer gets a short message and the relay is free again.
        """
        relay: ChatRelay = ChatRelay(session_factory=UnreachableSession)
        events = self.send(relay, "guest:a", "t", "hello")
        self.assertEqual("error", events[-1].get("type"))
        self.assertFalse(relay.is_busy("guest:a"))
