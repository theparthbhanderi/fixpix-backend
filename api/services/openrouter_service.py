import os
import json
import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# API URL Configuration
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_API_URL = os.getenv("OPENAI_BASE_URL", "https://api.groq.com/openai/v1") + "/chat/completions"

# Model Mapping (Provider Specific)
MODELS = {
    "GROQ": {
        "INTENT": "llama-3.1-8b-instant",
        "CHAT": "llama-3.3-70b-versatile",
        "REASONING": "llama-3.3-70b-versatile",
    },
    "OPENROUTER": {
        "INTENT": "mistralai/mistral-7b-instruct:free",
        "CHAT": "openchat/openchat-3.5:free",
        "REASONING": "meta-llama/llama-3-8b-instruct:free",
    }
}

class OpenRouterService:
    @staticmethod
    def call_model(model_key, messages, temperature=0.7, json_mode=True):
        """
        Generic caller with Multi-Provider (Groq -> OpenRouter) rotation fallback.
        """
        # --- 1. Try Groq (Primary / Ultra-Fast) ---
        groq_keys_str = os.getenv("OPENAI_API_KEY", "")
        groq_keys = [k.strip() for k in groq_keys_str.split(",") if k.strip()]
        
        if groq_keys:
            model = MODELS["GROQ"].get(model_key, model_key)
            result = OpenRouterService._execute_request(GROQ_API_URL, groq_keys, model, messages, temperature, json_mode)
            if result:
                return result

        # --- 2. Fallback to OpenRouter ---
        or_keys_str = os.getenv("OPENROUTER_API_KEY", "")
        or_keys = [k.strip() for k in or_keys_str.split(",") if k.strip()]
        
        if or_keys:
            model = MODELS["OPENROUTER"].get(model_key, model_key)
            return OpenRouterService._execute_request(OPENROUTER_API_URL, or_keys, model, messages, temperature, json_mode)

        logger.critical("No valid API keys found for any provider (Groq or OpenRouter)")
        return None

    @staticmethod
    def _execute_request(url, api_keys, model, messages, temperature, json_mode):
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 1024,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        for i, api_key in enumerate(api_keys):
            headers = {
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": os.getenv("APP_URL", "http://localhost:8000"),
                "X-Title": "FixPix AI Copilot",
                "Content-Type": "application/json",
            }
            try:
                logger.info(f"Trying {url} with key index {i} for model {model}")
                response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
                
                if response.status_code == 200:
                    data = response.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if not content: continue

                    if json_mode:
                        content = content.replace("```json", "").replace("```", "").strip()
                        try:
                            return json.loads(content)
                        except: continue
                    return content
                
                if response.status_code in [401, 403, 429]:
                    continue
            except Exception as e:
                logger.error(f"Provider Exception ({model}): {str(e)}")
                continue
        return None

    @staticmethod
    def chat_assistant(text, history=None):
        """
        Main entry point for the FixPix AI Assistant.
        Handles Guide, Action, and Navigation modes.
        """
        if history is None:
            history = []
            
        system_prompt = """You are FixPix AI Copilot — an ultra-intelligent assistant that controls the entire website.

You are NOT just a chatbot.
You are:
- AI image expert
- Website guide
- Action executor
- Friendly conversational assistant

----------------------------------------
🎯 PERSONALITY
----------------------------------------
- Speak naturally like ChatGPT
- Be friendly, professional, and helpful
- Mix casual + formal tone based on user
- Never sound robotic

----------------------------------------
🧠 INTELLIGENCE MODES
----------------------------------------

1. CONVERSATION MODE
- General chat
- Friendly responses
- Small talk allowed

2. WEBSITE KNOWLEDGE MODE
- Answer anything about FixPix
- Explain tools simply:
  Face Restore, Super Resolution, Magic Eraser, Remove Background, Change Background, Style Transfer, Text to Image, Edit Image, AI Tagline, Smart Frames, Batch Processing.

3. ACTION MODE
- If user gives command (e.g. "Remove background"), return ONLY JSON:
{ "type": "action", "tool": "remove_bg" }

4. NAVIGATION MODE
- If user wants page (e.g. "Go to AI tagline"), return ONLY JSON:
{ "type": "navigate", "page": "ai_tagline" }

----------------------------------------
⚡ ACTION DETECTION
----------------------------------------

Detect aliases:
- enhance, make HD, upscale → super_res
- remove bg → remove_bg
- erase, remove object → magic_eraser
- style, artistic → style_transfer
- generate, create image → text_to_image

----------------------------------------
🧾 RESPONSE RULES
----------------------------------------
- If action/navigation required: Return ONLY the JSON object.
- If normal chat/guide: Return plain text.
- Be proactive: "I can remove the background for you. Want me to do it?"
"""
        
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add history
        for msg in history[-5:]: # Keep last 5 messages
            messages.append(msg)
            
        messages.append({"role": "user", "content": text})
        
        # We try to detect if it's JSON or Text. 
        # For simplicity and reliability, we let the model decide but try to enforce JSON for actions.
        response = OpenRouterService.call_model("INTENT", messages, temperature=0.3, json_mode=False)
        
        if not response:
            return "I'm sorry, I'm having trouble connecting to my neural center. Please try again."
            
        return response

    @staticmethod
    def detect_intent(text):
        """
        Legacy support or internal intent parsing.
        """
        return OpenRouterService.chat_assistant(text)

    @staticmethod
    def optimize_prompt(user_input):
        """
        Uses Llama-3-8B for cinematic prompt expansion.
        """
        from .prompt_optimizer import SYSTEM_PROMPT
        
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Optimize this prompt: {user_input}"}
        ]
        
        return OpenRouterService.call_model("REASONING", messages, temperature=0.6, json_mode=False)
