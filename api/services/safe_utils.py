import logging

logger = logging.getLogger(__name__)

def safe_get(obj, key, default=None):
    """Safely get a key from a dictionary that might be None or not a dict."""
    try:
        if obj and isinstance(obj, dict):
            return obj.get(key, default)
        return default
    except Exception:
        return default

def validate_response(res):
    """Ensure the parsed API response is a safe dictionary."""
    if res is None:
        return {"status": "error", "data": []}
    if not isinstance(res, dict):
        return {"status": "error", "data": []}
    return res

def safe_fetch(source_name, source_fn, query, *args, **kwargs):
    """Safely fetch data from an external API, returning [] on error."""
    try:
        result = source_fn(query, *args, **kwargs)
        if isinstance(result, list):
            return result
        elif isinstance(result, dict) and "data" in result:
             return result.get("data", [])
        return result if result else []
    except Exception as e:
        logger.error(f"ERROR in safe_fetch from {source_name}: {e}")
        return []

def fallback_response(query, queries_used=None):
    """Return a detailed fallback response when search results are empty or system fails."""
    return {
        "verdict": "UNVERIFIED",
        "confidence": 20,
        "summary": "No conclusive evidence found in current multi-source OSINT scan.",
        "key_findings": ["No credible confirming or denying evidence found across integrated intelligence nodes."],
        "detailed_reasoning": "The topic was searched across 6 major data providers including BBC, Wikipedia, and Reddit. No direct matches or reliable context was established within the current observation window.",
        "real_world_context": "Insufficient data footprint to build a reality baseline for this specific claim.",
        "misinformation_analysis": "Zero source signal. No viral rumor patterns or official debunking detected.",
        "sources_analysis": [],
        "final_explanation": "Currently, we cannot confirm or deny this claim due to a lack of public data or news coverage. It may be a localized rumor or an upcoming event yet to be indexed.",
        "search_queries_used": queries_used or [query],
        "status": "partial_success"
    }

def normalize_output(data, query, queries_used=None):
    """Refine and standardize the final JSON output for the OSINT Analyst persona."""
    if not data or not isinstance(data, dict):
        return fallback_response(query, queries_used)
    
    return {
        "verdict": safe_get(data, "verdict", "UNVERIFIED"),
        "confidence": safe_get(data, "confidence", 20),
        "summary": safe_get(data, "summary", "OSINT analysis completed."),
        "key_findings": safe_get(data, "key_findings", ["Analysis results synthesized."]),
        "detailed_reasoning": safe_get(data, "detailed_reasoning", "Process complete."),
        "real_world_context": safe_get(data, "real_world_context", "Contextual truth synthesized from available signals."),
        "misinformation_analysis": safe_get(data, "misinformation_analysis", "No anomalies detected."),
        "sources_analysis": safe_get(data, "sources_analysis", []),
        "final_explanation": safe_get(data, "final_explanation", "Investigation concluded with available evidence."),
        "search_queries_used": safe_get(data, "search_queries_used", queries_used or [query]),
        "status": safe_get(data, "status", "success")
    }
