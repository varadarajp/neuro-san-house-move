from coded_tools.hackathon.house_move.mock_mail_gateway import MockMailGateway


class RecordingMailGateway(MockMailGateway):  # pylint: disable=too-few-public-methods
    """
    A mail gateway for tests that keeps what it was asked to deliver, so tests can check the rendered text that the
    case itself no longer stores.
    """

    def __init__(self):
        """
        Starts with nothing delivered.
        """
        self.messages: list[dict] = []

    def deliver(self, message: dict[str, str]) -> None:
        """
        Records one delivery.
        """
        self.messages.append(dict(message))
