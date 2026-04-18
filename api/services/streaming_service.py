import os
import json
import requests
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

class CopilotStreamingService:
    def __init__(self):
        self.groq_keys = [k.strip() for k in os.environ.get("OPENAI_API_KEY", "").split(",") if k.strip()]
        self.groq_base = os.environ.get("OPENAI_BASE_URL", "https://api.groq.com/openai/v1")
        self.nv_keys = [k.strip() for k in os.environ.get("NVIDIA_API_KEYS", "").split(",") if k.strip()]
        # Intentionally do not trust OPENAI_MODEL here: legacy env values may point
        # to decommissioned models and break chat for all users.
        self.default_text_model = os.environ.get("COPILOT_TEXT_MODEL", "llama-3.3-70b-versatile")
        self.default_vision_model = os.environ.get("COPILOT_VISION_MODEL", "llama-3.2-11b-vision-preview")
        self.safe_fallback_model = os.environ.get("COPILOT_SAFE_FALLBACK_MODEL", "llama-3.3-70b-versatile")

    def _has_image_content(self, messages):
        for message in messages:
            content = message.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "image_url":
                        return True
        return False

    def _build_payload(self, messages, tools=None):
        model = self.default_vision_model if self._has_image_content(messages) else self.default_text_model
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "temperature": 0.2,
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        return payload

    @staticmethod
    def _coerce_messages_to_string(messages):
        """Convert multimodal OpenAI-style message content lists into plain strings.

        Some provider/model combinations only accept string content.
        """
        coerced = []
        for message in messages:
            content = message.get("content")
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                    elif item.get("type") == "image_url":
                        text_parts.append("[Image attached by user]")

                coerced_message = dict(message)
                coerced_message["content"] = "\n".join([p for p in text_parts if p]).strip() or "[User message with image]"
                coerced.append(coerced_message)
            else:
                coerced.append(message)
        return coerced

    @staticmethod
    def _looks_like_bad_model_response(response):
        if response.status_code != 400:
            return False
        try:
            text = (response.text or "").lower()
        except Exception:
            return False
        return (
            "model_decommissioned" in text
            or "decommissioned" in text
            or "model not found" in text
            or "invalid model" in text
        )

    @staticmethod
    def _needs_string_content_response(response):
        if response.status_code != 400:
            return False
        try:
            text = (response.text or "").lower()
        except Exception:
            return False
        return (
            "content must be a string" in text
            or "messages[1].content" in text
            or "messages[0].content" in text
        )

    def generate_chat_stream(self, messages, tools=None, user_id="anonymous"):
        """
        Yields tokens/chunks for SEO-compatible StreamingHttpResponse.
        """
        key = self.groq_keys[0] if self.groq_keys else None
        if not key:
            yield f"data: {json.dumps({'type': 'chunk', 'content': 'Error: Missing AI API configurations in Django.'})}\n\n"
            return

        try:
            active_messages = messages
            response = requests.post(
                f"{self.groq_base}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=self._build_payload(active_messages, tools=tools),
                stream=True,
                timeout=30
            )

            # Some providers/models reject tool-calling payloads with 400.
            # Retry once without tools to keep chat usable.
            if response.status_code == 400 and tools:
                response = requests.post(
                    f"{self.groq_base}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json=self._build_payload(active_messages, tools=None),
                    stream=True,
                    timeout=30
                )

            # If provider requires string-only content, flatten multimodal message payload and retry.
            if self._needs_string_content_response(response):
                active_messages = self._coerce_messages_to_string(active_messages)
                response = requests.post(
                    f"{self.groq_base}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json=self._build_payload(active_messages, tools=tools),
                    stream=True,
                    timeout=30
                )

                if response.status_code == 400 and tools:
                    response = requests.post(
                        f"{self.groq_base}/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json=self._build_payload(active_messages, tools=None),
                        stream=True,
                        timeout=30
                    )

            # Recover from stale/decommissioned model names by forcing a safe fallback.
            if self._looks_like_bad_model_response(response):
                fallback_payload = self._build_payload(active_messages, tools=tools)
                fallback_payload["model"] = self.safe_fallback_model
                response = requests.post(
                    f"{self.groq_base}/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json=fallback_payload,
                    stream=True,
                    timeout=30
                )

                if response.status_code == 400 and tools:
                    fallback_payload.pop("tools", None)
                    fallback_payload.pop("tool_choice", None)
                    response = requests.post(
                        f"{self.groq_base}/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json=fallback_payload,
                        stream=True,
                        timeout=30
                    )

            if response.status_code != 200:
                error_hint = ""
                try:
                    raw = response.text[:300]
                    if raw:
                        error_hint = f" - {raw}"
                except Exception:
                    pass
                yield f"data: {json.dumps({'type': 'chunk', 'content': f'Neural error: {response.status_code}{error_hint}'})}\n\n"
                return

            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith('data: '):
                        data_str = decoded_line[6:].strip()
                        if data_str == '[DONE]':
                            break
                        
                        try:
                            data = json.loads(data_str)
                            chunk = data['choices'][0]['delta']
                            
                            # Standard text chunk
                            if 'content' in chunk and chunk['content']:
                                yield f"data: {json.dumps({'type': 'chunk', 'content': chunk['content']})}\n\n"
                            
                            # Function calling (tool calls)
                            if 'tool_calls' in chunk:
                                yield f"data: {json.dumps({'type': 'tool_call', 'delta': chunk['tool_calls']})}\n\n"
                                
                        except (json.JSONDecodeError, KeyError):
                            continue

        except Exception as e:
            logger.error(f"Streaming Engine Error: {str(e)}")
            yield f"data: {json.dumps({'type': 'chunk', 'content': ' Neural connection interrupted. Attempting reconnection...'})}\n\n"

    def format_sse(self, type, content=None, data=None):
        """Helper to format SSE data string."""
        payload = {"type": type}
        if content: payload["content"] = content
        if data: payload.update(data)
        return f"data: {json.dumps(payload)}\n\n"
