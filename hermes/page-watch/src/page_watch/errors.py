class PageWatchError(Exception):
    """Expected failure: the CLI prints the message and exits 1, without a traceback."""


class InvalidInputError(PageWatchError):
    pass


class FetchError(PageWatchError):
    pass


class JudgeError(PageWatchError):
    pass
