from openai import OpenAI
import os
import logging
from ..utils.rotator import APIRotator

logger = logging.getLogger(__name__)

# Initialize rotator with keys from environment
nvidia_keys_str = os.getenv("NVIDIA_API_KEYS", "")
keys = nvidia_keys_str.split(",") if nvidia_keys_str else []
rotator = APIRotator(keys)

def call_deepseek(prompt):
    """
    Calls NVIDIA DeepSeek model with automatic failover across multiple API keys.
    """
    all_keys = rotator.get_all()
    if not all_keys:
        logger.error("No NVIDIA_API_KEYS found in environment")
        return None

    # Try each key once
    for _ in range(len(all_keys)):
        api_key = rotator.get_next()
        try:
            client = OpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=api_key
            )

            response = client.chat.completions.create(
                model="deepseek-ai/deepseek-v3.2",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1000,
                extra_body={"chat_template_kwargs": {"thinking": True}}
            )

            if response.choices and response.choices[0].message:
                return response.choices[0].message.content

        except Exception as e:
            logger.warning(f"NVIDIA API Key rate-limited or failed: {e}")
            continue

    # --- Groq Fallback ---
    logger.info("NVIDIA failed, attempting Groq fallback...")
    groq_key = os.getenv("OPENAI_API_KEY") # Shared key for Groq in this project
    groq_url = os.getenv("OPENAI_BASE_URL", "https://api.groq.com/openai/v1")
    
    if groq_key and "groq" in groq_url.lower():
        try:
            client = OpenAI(base_url=groq_url, api_key=groq_key)
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1000
            )
            if response.choices and response.choices[0].message:
                logger.info("✅ Groq fallback successful")
                return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Groq fallback also failed: {e}")

    logger.error("All AI providers (NVIDIA & Groq) failed")
    return None
