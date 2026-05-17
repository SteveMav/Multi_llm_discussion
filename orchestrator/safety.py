import asyncio
import logging

logger = logging.getLogger(__name__)

# Dictionary to hold abort events mapped by session_id
ABORT_EVENTS: dict[int, asyncio.Event] = {}

def get_abort_event(session_id: int) -> asyncio.Event:
    if session_id not in ABORT_EVENTS:
        ABORT_EVENTS[session_id] = asyncio.Event()
    return ABORT_EVENTS[session_id]

def set_abort_event(session_id: int) -> None:
    if session_id not in ABORT_EVENTS:
        ABORT_EVENTS[session_id] = asyncio.Event()
    ABORT_EVENTS[session_id].set()

def clear_abort_event(session_id: int) -> None:
    if session_id in ABORT_EVENTS:
        ABORT_EVENTS.pop(session_id, None)

def run_sanity_check(topic: str) -> bool:
    """
    Perform a sanity check on the provided debate topic.
    The MVP keeps this intentionally permissive: API connectivity is validated
    during the live stream and gracefully falls back to local simulation.
    """
    if not topic or not topic.strip():
        logger.error("Empty topic provided.")
        return False
    if len(topic.strip()) < 8:
        logger.error("Topic too short for a useful debate.")
        return False
    return True
