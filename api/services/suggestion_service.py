import json
import logging
import re
from .llm_utils import call_llm
from .trends_service import get_live_trends

logger = logging.getLogger(__name__)

def generate_suggestions(query, user_id=None):
    """
    Generate 5 smart search suggestions using AI + Trending context.
    - query: User's current partial input
    - user_id: (Optional) To integrate history later
    """
    if not query or len(query.strip()) < 2:
        # Return only trending if query is too short
        trends = get_live_trends()
        return [t["topic"] for t in trends[:5]]

    # 1. AI Generation for context-aware suggestions & corrections
    prompt = f"""
    Given the user input: "{query}"

    Generate 5 HIGHLY RELEVANT and REALISTIC search suggestions for a news/fact-checking platform.
    
    Include:
    - Autocomplete (what they are likely typing)
    - Spell correction (if input looks misspelled)
    - Related trending topics or entities
    - Expanded investigative queries (e.g., if user types "india", suggest "India Israel defense deals")

    RULES:
    - Return ONLY a JSON array of strings.
    - No numbering, no extra text.
    - Keep suggestions concise (3-8 words).
    - If the input is ambiguous, provide a variety of likely intents.

    Suggestions:
    """

    ai_suggestions = []
    try:
        raw_response = call_llm([{"role": "user", "content": prompt}], temperature=0.3)
        if raw_response:
            # Robust JSON extraction
            match = re.search(r"\[.*\]", raw_response, re.DOTALL)
            if match:
                ai_suggestions = json.loads(match.group())
            else:
                # Fallback: line split if JSON fails
                ai_suggestions = [line.strip().strip('"') for line in raw_response.split('\n') if line.strip() and len(line) > 3][:5]
    except Exception as e:
        logger.error(f"AI Suggestions failed: {e}")

    # 2. Merge with Trending Topics (if they match the query intent or as general filler)
    try:
        trends = get_live_trends()
        trending_topics = [t["topic"] for t in trends]
        
        # Add top trends that aren't already in AI suggestions
        for topic in trending_topics:
            if len(ai_suggestions) >= 8: break
            if topic not in ai_suggestions:
                # Simple relevance check: if query is in topic, add it higher
                if query.lower() in topic.lower():
                    ai_suggestions.insert(0, topic)
                else:
                    ai_suggestions.append(topic)
    except:
        pass

    # Unique and Limit
    final = []
    seen = set()
    for s in ai_suggestions:
        s_clean = s.strip()
        if s_clean.lower() not in seen:
            final.append(s_clean)
            seen.add(s_clean.lower())
        if len(final) >= 6: break

    return final
