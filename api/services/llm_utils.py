import os
import json
import logging
import requests

logger = logging.getLogger(__name__)

def call_llm(messages, temperature=0.2, timeout=40):
    """
    Unified LLM caller with multi-model Groq fallback chain and OpenRouter redundancy.
    Focuses on free/resilient models to avoid rate-limits and credit issues.
    """
    if not isinstance(messages, list):
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        else:
            return None

    # 1. NVIDIA NIM - High intelligence, free trial/tier often available
    nv_keys_str = os.environ.get("NVIDIA_API_KEYS", "")
    nv_keys = [k.strip() for k in nv_keys_str.split(",") if k.strip()]
    
    NV_MODELS = [
        "meta/llama-3.3-70b-instruct",
        "meta/llama-3.1-70b-instruct",
        "nvidia/llama-3.1-nemotron-70b-instruct",
    ]

    if nv_keys:
        for nv_key in nv_keys:
            if not nv_key or "nvapi-" not in nv_key: continue
            for model in NV_MODELS:
                try:
                    res = requests.post(
                        "https://integrate.api.nvidia.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {nv_key}"},
                        json={
                            "model": model,
                            "messages": messages,
                            "temperature": temperature,
                            "max_tokens": 1024
                        },
                        timeout=timeout
                    )
                    if res.status_code == 200:
                        logger.info(f"✅ NVIDIA NIM success: {model}")
                        return res.json()["choices"][0]["message"]["content"]
                    elif res.status_code == 429:
                        logger.warning(f"⚠️ NVIDIA Rate-Limit: {model} with key {nv_key[:10]}...")
                        break # Try next key for this model provider
                    else:
                        logger.warning(f"❌ NVIDIA {model} Status {res.status_code}")
                except Exception as e:
                    logger.error(f"❌ NVIDIA {model} Exception: {str(e)}")
                    continue

    # 2. Groq - Best for speed and 0-cost if within rate limits
    GROQ_MODELS = [
        "llama-3.1-8b-instant",      # Fast, high rate-limit
        "llama-3.3-70b-versatile",   # Intelligent, but lower rate-limit
        "mixtral-8x7b-32768",        # Resilient fallback
    ]
    
    groq_key = os.environ.get("OPENAI_API_KEY") # This is likely your Groq Key based on .env
    groq_base = os.environ.get("OPENAI_BASE_URL", "https://api.groq.com/openai/v1")
    
    if groq_key:
        for model in GROQ_MODELS:
            try:
                res = requests.post(
                    f"{groq_base}/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}"},
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": temperature
                    },
                    timeout=timeout
                )
                if res.status_code == 200:
                    logger.info(f"✅ Groq success: {model}")
                    return res.json()["choices"][0]["message"]["content"]
                elif res.status_code == 429:
                    logger.warning(f"⚠️ Groq Rate-Limit: {model}")
                    continue
                else:
                    logger.warning(f"❌ Groq {model} Status {res.status_code}")
            except Exception as e:
                logger.error(f"❌ Groq {model} Exception: {str(e)}")
                continue
    
    # 3. OpenRouter - Primary fallback focusing on FREE models
    api_key = os.environ.get("OPENROUTER_API_KEY")
    # List of FREE models on OpenRouter (check current status on openrouter.ai/models?free=true)
    OR_FREE_MODELS = [
        "google/gemma-2-9b-it:free",
        "meta-llama/llama-3.1-8b-instruct:free",
        "mistralai/mistral-7b-instruct:free",
        "meta-llama/llama-3.3-70b-instruct" # Non-free fallback
    ]
    
    if api_key and "your_" not in api_key:
        for model in OR_FREE_MODELS:
            try:
                res = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": temperature
                    },
                    timeout=timeout
                )
                if res.status_code == 200:
                    logger.info(f"✅ OpenRouter success: {model}")
                    return res.json()["choices"][0]["message"]["content"]
                elif res.status_code == 402:
                    logger.error("❌ OpenRouter: Insufficient Credits. Skipping non-free models.")
                    break # Skip other non-free models if one 402s
                else:
                    logger.warning(f"❌ OpenRouter {model} failed ({res.status_code})")
            except Exception as e:
                logger.error(f"❌ OpenRouter {model} Exception: {str(e)}")
    
    logger.error("🚨 CRITICAL: ALL AI MODELS FAILED.")
    return None
