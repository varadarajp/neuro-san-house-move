class FakeAgentSession:  # pylint: disable=too-few-public-methods
    """
    Stands in for the Neuro SAN server in portal tests: records each request and streams scripted responses.
    """

    requests: list[dict] = []
    REPLY: str = (
        "Congratulations on the move. **Your case reference: C-10042.** See [My move](http://127.0.0.1:8090/status)."
    )

    async def streaming_chat(self, request: dict):
        """
        Records the request, then yields a progress message from the timeline agent and the final reply.
        """
        FakeAgentSession.requests.append(request)
        yield {"response": {"type": "AGENT", "origin": [{"tool": "move_coordinator"}, {"tool": "timeline_agent"}]}}
        yield {
            "response": {
                "type": "AGENT_FRAMEWORK",
                "text": FakeAgentSession.REPLY,
                "chat_context": {"turn": len(FakeAgentSession.requests)},
            }
        }
