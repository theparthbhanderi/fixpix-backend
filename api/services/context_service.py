import logging
import json
import re
from functools import lru_cache
from .llm_utils import call_llm

logger = logging.getLogger(__name__)

# Rule-based expansion for common high-intent terms
FAST_RULES = {
    "war": "latest global war news geopolitical conflicts 2026",
    "election": "latest election results political updates 2026",
    "economy": "latest global economic news inflation trends 2026",
    "climate": "climate change latest scientific reports 2026",
    "tech": "latest technology breakthroughs and AI news 2026",
    "market": "stock market latest trends and financial analysis 2026",
}

@lru_cache(maxsize=128)
def enhance_query(query, user_country=None):
    """
    Enhances a vague user query with missing context (time, location, domain).
    Returns a tuple: (enhanced_query, context_tags)
    """
    if not query or len(query.strip()) < 2:
        return query, []

    query_lower = query.lower().strip()
    
    # 1. Fast Rule-based Layer
    for key, expansion in FAST_RULES.items():
        if key in query_lower and len(query_lower.split()) < 3:
            tags = ["🕒 Latest", "🌍 Global", "📰 News"]
            return expansion, tags

    # 2. AI Layer for complex or ultra-vague queries
    prompt = f"""
    You are a search intelligence engine. Enhance this user query for professional OSINT research:
    
    Input: "{query}"
    
    RULES:
    - Add missing context: Current Year (2026), Location (if missing), and Domain (News/Politics/etc).
    - If the query is already specific, keep it mostly as is but add "latest 2026".
    - If it's nonsense, return "ERROR: UNKNOWN".
    - Return ONLY the improved query string.
    
    Output Format:
    Improved Query: [Your expansion]
    Tags: [Comma separated short tags like Global, Recent, Politics]
    """

    try:
        messages = [{"role": "user", "content": prompt}]
        response = call_llm(messages)
        
        if not response or "ERROR: UNKNOWN" in str(response):
            return query, ["❓ Unclear"]
            
        # Parse AI response
        resp_str = str(response)
        lines = [line.strip() for line in resp_str.split('\n') if line.strip()]
        enhanced = query
        tags = ["🕒 Recent"]
        
        for line in lines:
            if line.startswith("Improved Query:"):
                enhanced = line.replace("Improved Query:", "").strip()
            elif line.startswith("Tags:"):
                tag_str = line.replace("Tags:", "").strip()
                tags = [f" {t.strip()}" for t in tag_str.split(",")]

        # 3. Location Detection (Advanced)
        if user_country and user_country.upper() == "IN" and "india" not in enhanced.lower():
            enhanced = f"{enhanced} India perspective"
            if " 🇮🇳 India" not in tags:
                tags.append(" 🇮🇳 India")

        return enhanced, tags[:4]
        
    except Exception as e:
        logger.error(f"Query enhancement failed: {e}")
        return query, ["🕒 Latest"]

def get_context_tags(enhanced_query):
    """Fallback tag extractor if AI parsing fails"""
    tags = ["🕒 Latest"]
    if "2026" in enhanced_query: tags.append("📅 2026")
    if "global" in enhanced_query.lower(): tags.append("🌍 Global")
    if "india" in enhanced_query.lower(): tags.append("🇮🇳 India")
    return tags[:3]
