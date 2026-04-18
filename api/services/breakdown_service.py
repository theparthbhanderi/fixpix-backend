import os
import json
import logging
from .llm_utils import call_llm

logger = logging.getLogger(__name__)

def analyze_claim(user_query):
    """
    Decomposes a raw user query into structured intelligence components.
    This acts as a pre-processor for the fact-checking pipeline.
    """
    prompt = f"""
    You are a senior intelligence analyst. Analyze the following news claim/query:

    Query: "{user_query}"

    Extract structured data for deeper research:
    1. entities: List of specific people, countries, organizations, or products.
    2. intent: What exactly is the user trying to verify? (concise string)
    3. category: The primary domain (politics, war, tech, health, finance, etc.)
    4. claim_type: Is it a 'comparison', 'statement', 'question', or 'prediction'?
    5. keywords: 5-8 highly relevant terms for search indexing.

    STRICT JSON OUTPUT:
    {{
      "entities": ["entity1", "entity2"],
      "intent": "verifying the status of X in Y",
      "category": "domain",
      "claim_type": "type",
      "keywords": ["kw1", "kw2"]
    }}
    """

    try:
        response_text = call_llm(prompt)
        # Handle potential markdown wrapping
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()
            
        analysis = json.loads(response_text)
        return analysis
    except Exception as e:
        logger.error(f"Claim analysis failed: {str(e)}")
        return {
            "entities": [],
            "intent": "analyze query",
            "category": "general",
            "claim_type": "unknown",
            "keywords": user_query.split()[:5]
        }

def generate_smart_queries(analysis):
    """
    Expands a structured analysis into high-precision search queries.
    """
    entities = " ".join(analysis.get("entities", []))
    intent = analysis.get("intent", "")
    
    queries = [
        f"{entities} {intent}",
        f"{entities} latest news updates",
        f"{entities} official statement",
        f"{' '.join(analysis.get('keywords', []))}"
    ]
    # Filter out empty or too short queries
    return [q.strip() for q in queries if len(q.strip()) > 3][:4]
