"""
🧠 Smart Prompt Engine — Master Prompt Builder for AI Image Processing

Automatically enhances prompts for each feature type with:
- Feature-specific prompt templates
- Quality boost words
- Negative prompts
- User input integration

FORMULA: [Feature Prompt] + [User Input] + [Quality Boost] + [Negative Prompt]
"""

# ─────────────────────────────────────────────────────
# QUALITY CONSTANTS
# ─────────────────────────────────────────────────────

QUALITY_BOOST = "high quality, sharp, realistic, no artifacts, professional"

NEGATIVE_PROMPT = (
    "blurry, low quality, distorted, deformed, bad anatomy, artifacts, "
    "oversaturated, unrealistic, noise, jpeg artifacts, watermark, "
    "text, logo, banner, extra fingers, mutated hands, poorly drawn"
)

# ─────────────────────────────────────────────────────
# FEATURE PROMPT TEMPLATES
# ─────────────────────────────────────────────────────

FEATURE_PROMPTS = {
    # 🎨 Colorization
    "colorize": "realistic colorized, natural skin tones, high fidelity",

    # ✨ Upscale / Enhance
    "upscale": (
        "Enhance image quality to ultra high resolution, sharp details, "
        "remove blur, improve textures, realistic lighting, 4K quality, "
        "no artifacts, natural look"
    ),

    # 👤 Face Enhancement
    "face_restore": (
        "Enhance facial details, restore skin texture, sharp eyes, "
        "natural skin tone, remove blur and noise, keep identity unchanged, "
        "high realism"
    ),

    # 🧹 Background Remove
    "remove_bg": (
        "Remove background cleanly, preserve subject edges, "
        "high precision cutout, no artifacts, transparent background"
    ),

    # 🧽 Object Remove / Inpaint
    "inpaint": (
        "Remove unwanted objects seamlessly, fill area naturally, "
        "match surrounding textures, no visible artifacts, realistic reconstruction"
    ),

    # 🧠 Image-to-Image (Generic)
    "img2img": (
        "Transform the image with highly detailed output, cinematic lighting, "
        "realistic textures, maintain original composition and subject identity, "
        "sharp focus, 4K quality"
    ),

    # 🎭 Style Transfer Presets
    "horror": (
        "dark horror atmosphere, eerie, glowing eyes, cinematic lighting, "
        "highly detailed, realistic textures, sharp focus, 4K quality"
    ),
    "anime": (
        "anime style illustration, studio quality, vibrant colors, "
        "clean lines, detailed shading, professional anime art"
    ),
    "cinematic": (
        "cinematic lighting, film look, dramatic shadows, "
        "color grading, anamorphic lens feel, professional cinematography"
    ),
    "portrait": (
        "professional portrait photograph, studio lighting, "
        "sharp focus, bokeh background, skin detail, high-end photography"
    ),
    "realistic": (
        "photorealistic, natural lighting, high detail, "
        "accurate colors, sharp focus, professional photography"
    ),

    # 🎭 Text-to-Image (Generic)
    "text2img": (
        "highly detailed, masterpiece quality, professional, "
        "sharp focus, vibrant colors, perfect composition"
    ),
}


# ─────────────────────────────────────────────────────
# SMART PROMPT BUILDER
# ─────────────────────────────────────────────────────

def build_prompt(feature_type, user_input="", style=None, include_extras=True):
    """
    Build an optimized prompt for any AI image feature.
    """
    # Get the feature-specific prompt template
    feature_prompt = FEATURE_PROMPTS.get(feature_type, FEATURE_PROMPTS.get("text2img", ""))

    # For img2img/text2img with a specific style, merge the style prompt
    if style and style in FEATURE_PROMPTS:
        style_prompt = FEATURE_PROMPTS[style]
        feature_prompt = f"{feature_prompt}, {style_prompt}"

    # Build final prompt: Feature + User Input + Quality
    parts = [feature_prompt]

    if user_input and user_input.strip():
        parts.append(user_input.strip())

    if include_extras:
        parts.append(QUALITY_BOOST)

    final_prompt = ", ".join(parts)

    return {
        "prompt": final_prompt,
        "negative_prompt": NEGATIVE_PROMPT if include_extras else "",
    }


def build_colorize_prompt(user_hint=""):
    """Shortcut for colorization prompts. Disables extra quality words to preserve identity."""
    return build_prompt("colorize", user_hint, include_extras=False)


def build_upscale_prompt(user_hint=""):
    """Shortcut for upscale/enhance prompts."""
    return build_prompt("upscale", user_hint)


def build_face_prompt(user_hint=""):
    """Shortcut for face restoration prompts."""
    return build_prompt("face_restore", user_hint)


def build_remove_bg_prompt():
    """Shortcut for background removal."""
    return build_prompt("remove_bg")


def build_inpaint_prompt(user_hint=""):
    """Shortcut for object removal / inpainting."""
    return build_prompt("inpaint", user_hint)


def build_img2img_prompt(user_input, style=None):
    """Shortcut for image-to-image transformation."""
    return build_prompt("img2img", user_input, style)


def build_text2img_prompt(user_input, style=None):
    """Shortcut for text-to-image generation."""
    return build_prompt("text2img", user_input, style)
