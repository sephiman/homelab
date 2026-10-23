"""Domain errors, reported in one place by cli.main."""


class AgentError(Exception):
    pass


class ConfigError(AgentError):
    pass


class WorkspaceError(AgentError):
    pass
