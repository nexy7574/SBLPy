class StateException(Exception):
    """An exception raised when a method required a certain state, but was not met.

    For example, trying to start a server before it was initialized and ready."""
    def __init__(self, got: bool, need: bool, *, message: str = None):
        self.got = got
        self.need = need
        self.message = message or f"Expected state '{need}', but had '{got}'"

    def __str__(self):
        return self.message


class BotNotReady(StateException):
    def __init__(self):
        super().__init__(False, True, message="Bot client is not logged in yet.")
