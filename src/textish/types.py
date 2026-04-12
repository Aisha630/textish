from enum import Enum, auto


class ProcessState(Enum):
    """Lifecycle states for the Textual app subprocess managed by AppSession."""

    PENDING = auto()   # created, subprocess not yet launched
    RUNNING = auto()   # handshake succeeded, forwarding packets
    STOPPING = auto()  # quit signal sent, waiting for the process to exit
    STOPPED = auto()   # process has exited, session is done
