from enum import Enum, auto


class ProcessState(Enum):
    """Represents the state of the Textual app process."""

    PENDING = auto()
    RUNNING = auto()
    STOPPING = auto()
    STOPPED = auto()
