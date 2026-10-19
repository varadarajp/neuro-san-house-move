"""Base class for every tool that reads or changes a case."""

import asyncio
from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from coded_tools.hackathon.house_move.case_session import CaseSession


class CaseTool(CodedTool):
    """
    Wraps a tool's business logic in a server-side session (CaseSession): verify the caller, discard any state the
    client sent, load the real state, run, save, release.

    Subclasses implement run() (synchronous logic) or arun() (asynchronous logic). invoke() and async_invoke() are
    what neuro-san calls; unit tests of the business logic may call run() directly.
    Hooks: before_run() and after_run() let a tool choose or create the case (profile lookup, case creation).
    """

    NEEDS_CASE: bool = True  # False for tools that can run before a case is open
    LOCK_POLL_SECONDS: float = 0.005

    def run(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        """
        The tool's business logic, without the session. Synchronous tools override this.
        """
        return asyncio.run(self.arun(args, sly_data))

    async def arun(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        """
        The tool's business logic, without the session. Asynchronous tools override this.
        """
        return await asyncio.to_thread(self.run, args, sly_data)

    # Default hooks do nothing; subclasses that choose or create a case override them.
    # pylint: disable=unused-argument
    def before_run(self, args: dict[str, Any], sly_data: dict[str, Any], session: CaseSession) -> str | None:
        """
        Called after the session opens. Return an "Error: ..." string to stop.
        """
        return None

    def after_run(self, result: Any, sly_data: dict[str, Any], session: CaseSession) -> None:
        """
        Called after run, before the state is saved.
        """

    # pylint: enable=unused-argument

    def invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        """
        Synchronous entry point: the session-wrapped tool.
        """
        return asyncio.run(self.async_invoke(args, sly_data))

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        """
        neuro-san's entry point. Waiting for the owner's lock is asynchronous, and the session's database work runs
        on a worker thread, so neither the event loop nor the thread pool is ever blocked.
        """
        session: CaseSession = CaseSession()
        try:
            error: str | None = session.verify(sly_data)
            if error is None:
                while not session.try_lock():
                    await asyncio.sleep(CaseTool.LOCK_POLL_SECONDS)
                error = await asyncio.to_thread(session.load_active, sly_data, self.NEEDS_CASE)
            if error is None:
                error = await asyncio.to_thread(self.before_run, args, sly_data, session)
            if error is not None:
                return error
            result: Any = await self.arun(args, sly_data)
            await asyncio.to_thread(self.after_run, result, sly_data, session)
            await asyncio.to_thread(session.save, sly_data)
            return result
        finally:
            session.close(sly_data)
