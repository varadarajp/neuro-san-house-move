"""Stand-in for the email and post service that would deliver a sent notification."""

import logging

logger = logging.getLogger(__name__)


class MockMailGateway:  # pylint: disable=too-few-public-methods  # one delivery call
    """
    Receives each rendered notification at the moment it is sent, in place of a real email or print-and-post service.
    The rendered text (with the customer's name, addresses and references filled in) exists only for this call: the
    case keeps the draft with its {{placeholders}} and a fingerprint of what was sent, never the filled-in text.
    This mock logs only the reference, never the content.
    """

    def deliver(self, message: dict[str, str]) -> None:
        """
        :param message: "ref" (recorded on the task), "task_id", "delivery" (to the organization, or a pack to the
                        customer), and the rendered "subject" and "body".
        """
        size: int = len(message.get("subject", "")) + len(message.get("body", ""))
        logger.info(
            "Mock-delivered %s for %s (%s, %d characters)",
            message.get("ref"),
            message.get("task_id"),
            message.get("delivery"),
            size,
        )
