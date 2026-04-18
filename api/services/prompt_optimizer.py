import logging
from .deepseek_service import call_deepseek

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are an advanced AI Image Prompt Optimization Engine.

Your task is to convert simple, vague user prompts into highly detailed, professional, and visually rich prompts for image generation.

STRICT RULES:
- Preserve the original subject (do NOT change core object)
- Expand with: style, lighting, mood, texture, environment, and transformation details
- Make the prompt cinematic and high-quality
- Add realism and detail (ultra-detailed, high resolution)
- Avoid generic words like "make it better"
- Avoid cartoonish output unless explicitly asked
- Keep structure clean and readable

PROMPT STRUCTURE:
1. Transformation instruction
2. Style definition
3. Visual details (face, texture, effects)
4. Lighting
5. Mood / atmosphere
6. Background adjustments
7. Quality tags
8. Negative prompts (what to avoid)

If the user input is very short (e.g., "horror cat"), intelligently infer missing details.

OUTPUT:
Return ONLY the enhanced prompt. No explanation.
"""

def optimize_image_prompt(user_input):
    """
    Optimizes a simple user prompt into a detailed image generation prompt.
    """
    if not user_input:
        return None
        
    try:
        # Prepare the full prompt for the LLM
        full_prompt = f"{SYSTEM_PROMPT}\n\nUser Prompt: {user_input}"
        
        # Call the DeepSeek service (using NVIDIA NIM for speed and intelligence)
        optimized = call_deepseek(full_prompt)
        
        if optimized:
            # Clean up unwanted markdown or artifacts if any
            optimized = optimized.replace('```', '').strip()
            return optimized
            
        return None
        
    except Exception as e:
        logger.error(f"Prompt Optimization Error: {e}")
        return None
