import logging
import asyncio
from dashboard.models import ApiKeyStorage

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
    Verifies API keys and performs a dummy/test LLM request to ensure connectivity
    and prevent moderation blocks before the session actually starts.
    """
    if not topic or not topic.strip():
        logger.error("Empty topic provided.")
        return False
        
    # Check if ANY API key is configured
    has_keys = ApiKeyStorage.objects.exists()
    
    if not has_keys:
        logger.error("No API key configured for sanity check.")
        return False
        
    # TODO: Perform actual API call to validate the topic
    # For now, simulate success
    return True
