class PriceHuntError(Exception):
    """Expected failure: the CLI prints the message and exits 1, without a traceback."""


class AliExpressError(PriceHuntError):
    pass


class InvalidInputError(PriceHuntError):
    pass
