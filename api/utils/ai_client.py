import os
import json
import requests
import logging

logger = logging.getLogger(__name__)

def ai_client(prompt: str) -> str:
    """
    Sends a prompt to OpenRouter API (or fallback) and returns the text response.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("OPENROUTER_API_KEY is not set. Returning fallback response.")
        return json.dumps({"status": "UNVERIFIED", "confidence": "0%"})

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # Use gpt-4o-mini as requested for fast/cheap fact checking
    payload = {
        "model": "openai/gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"AI Client Error: {e}")
        return json.dumps({"status": "UNVERIFIED", "confidence": "0%"})
