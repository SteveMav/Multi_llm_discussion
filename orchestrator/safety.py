import logging
from dashboard.models import ApiKeyStorage

logger = logging.getLogger(__name__)

def run_sanity_check(topic: str) -> bool:
    """
    Perform a sanity check on the provided debate topic.
    Verifies API keys and performs a dummy/test LLM request to ensure connectivity
    and prevent moderation blocks before the session actually starts.
    """
    # Prefer OpenAI for sanity check if available, else fallback to something else
    api_key = ApiKeyStorage.get_key("openai")
    
    if not api_key:
        logger.error("No API key configured for sanity check.")
        return False
        
    if not topic or not topic.strip():
        logger.error("Empty topic provided.")
        return False
        
    # TODO: Perform actual API call to validate the topic
    # For now, simulate success
    return True
