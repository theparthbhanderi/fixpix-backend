import os
import logging
import json
from openai import OpenAI
from ..utils.rotator import APIRotator

logger = logging.getLogger(__name__)

# Initialize rotator with Groq keys from environment
# User will add keys to GROQ_API_KEYS (comma-separated)
groq_keys_str = os.getenv("GROQ_API_KEYS", os.getenv("OPENAI_API_KEY", ""))
groq_keys = [k.strip() for k in groq_keys_str.split(",") if k.strip()]
rotator = APIRotator(groq_keys)

# Style-specific instructions
STYLE_INSTRUCTIONS = {
    'professional': 'Tone: professional, authoritative, clean. Think Apple, Nike, Tesla.',
    'funny': 'Tone: witty, humorous, playful. Use puns or clever wordplay.',
    'emotional': 'Tone: heartfelt, emotional, touching. Evoke warmth and nostalgia.',
    'luxury': 'Tone: premium, exclusive, sophisticated. High-end fashion vibes.',
    'instagram': 'Tone: trendy, viral, social-media-friendly. Use relevant emojis.',
    'product_ad': 'Tone: persuasive, benefit-driven, action-oriented.',
    'romantic': 'Tone: romantic, dreamy, poetic. Use metaphors of love and beauty.',
}

def _get_groq_client():
    """Get a Groq client by rotating keys."""
    api_key = rotator.get_next()
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.groq.com/openai/v1")
    
    if not api_key:
        return None
    try:
        return OpenAI(base_url=base_url, api_key=api_key)
    except Exception as e:
        logger.error(f"Failed to initialize OpenAI client: {e}")
        return None


def generate_tagline_from_image(image_base64, style='professional'):
    """
    Generate tagline from actual image using Groq Vision model with key rotation.
    """
    all_keys = rotator.get_all()
    if not all_keys:
        logger.error("No GROQ_API_KEYS found")
        return None

    style_instruction = STYLE_INSTRUCTIONS.get(style, STYLE_INSTRUCTIONS['professional'])
    
    # Ensure proper data URL format for Groq
    if image_base64 and not image_base64.startswith('data:'):
        image_base64 = f"data:image/jpeg;base64,{image_base64}"

    prompt = f"""Look at this image carefully. Based on what you SEE in the image, generate:

1. **tagline**: A short catchy tagline (MAX 10 words) that captures the essence
2. **caption**: An Instagram-style caption (1-2 sentences with 1-2 emojis)  
3. **headline**: An ad-style headline (MAX 12 words), powerful and memorable
4. **occasion**: Select EXACTLY ONE: "birthday", "wedding", "anniversary", "festival", or "general"

{style_instruction}

Return ONLY valid JSON, no explanation:
{{"tagline": "...", "caption": "...", "headline": "...", "occasion": "..."}}"""

    # Try each key if rate limited
    for _ in range(len(all_keys)):
        client = _get_groq_client()
        if not client:
            continue
            
        try:
            response = client.chat.completions.create(
                model="llama-3.2-11b-vision-preview",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": image_base64}
                        }
                    ]
                }],
                temperature=0.7,
                max_tokens=300,
            )

            if response.choices and response.choices[0].message:
                content = response.choices[0].message.content
                logger.info("✅ Groq Vision tagline generated")
                return _parse_tagline_response(content)

        except Exception as e:
            logger.warning(f"Groq Vision API Key failed or rate-limited: {e}")
            continue
    
    return None


def generate_tagline(description, style='professional'):
    """
    Fallback: Generate tagline from text description using fast Groq text model with rotation.
    """
    all_keys = rotator.get_all()
    if not all_keys:
        return _fallback_tagline(description)
    
    style_instruction = STYLE_INSTRUCTIONS.get(style, STYLE_INSTRUCTIONS['professional'])

    prompt = f"""Based on this image description, generate creative copy:

IMAGE: {description}

{style_instruction}

Generate:
1. tagline: Short catchy tagline (MAX 10 words)
2. caption: Instagram-style caption (1-2 sentences, 1-2 emojis)
3. headline: Ad-style headline (MAX 12 words)

Return ONLY valid JSON:
{{"tagline": "...", "caption": "...", "headline": "..."}}"""

    for _ in range(len(all_keys)):
        client = _get_groq_client()
        if not client:
            continue
            
        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=300,
            )

            if response.choices and response.choices[0].message:
                content = response.choices[0].message.content
                logger.info("✅ Groq text tagline generated")
                return _parse_tagline_response(content)
        except Exception as e:
            logger.warning(f"Groq text API Key failed: {e}")
            continue

    return _fallback_tagline(description)


def _parse_tagline_response(content):
    """Parse LLM response into tagline dict."""
    if not content:
        return None
        
    clean = content.replace('```json', '').replace('```', '').strip()
    
    try:
        data = json.loads(clean)
        if all(k in data for k in ('tagline', 'caption', 'headline')):
            return data
    except json.JSONDecodeError:
        start = clean.find('{')
        end = clean.rfind('}')
        if start != -1 and end != -1:
            try:
                data = json.loads(clean[start:end+1])
                if all(k in data for k in ('tagline', 'caption', 'headline')):
                    return data
            except json.JSONDecodeError:
                pass
    return None


def _fallback_tagline(description):
    """Fallback when AI fails."""
    words = description.split()[:5] if description else ["the", "image"]
    subject = ' '.join(words).strip('.,!?')
    return {
        'tagline': f'Discover the beauty of {subject}',
        'caption': f'Every moment tells a story ✨ #{subject.replace(" ", "")}',
        'headline': f'Experience {subject} Like Never Before'
    }
