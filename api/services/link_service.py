import logging
import hashlib
from api.services.factcheck_service import fact_check_pipeline, extract_article_content, generate_article_response, fallback_article_response

logger = logging.getLogger(__name__)

def check_link(url, mode="deep"):
    """
    Unified entry point for link analysis.
    Delegates to the master fact_check_pipeline which handles URL detection,
    triple-layer extraction, and AI intelligence synthesis.
    """
    logger.info(f"LINK SERVICE: Processing {url} (mode: {mode})")
    
    # Standardize on the master pipeline for all logic
    return fact_check_pipeline(url, mode=mode)

# Keep the following for backward compatibility if other services import them
def extract_article_content_legacy(url):
    return extract_article_content(url)
