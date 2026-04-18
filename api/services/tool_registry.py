# tool_registry.py

TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "remove_background",
            "description": "Removes the background from the current image, isolating the subject.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "enhance_image",
            "description": "Automatically improves lighting, contrast, and color balance of the photo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "strength": {"type": "number", "description": "Intensity of enhancement (0.0 to 1.0)", "default": 0.8}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "super_resolution",
            "description": "Upscales the image to higher resolution (4K) while keeping it sharp.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scale": {"type": "integer", "enum": [2, 4], "default": 2}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "face_restore",
            "description": "Fixes blurry, low-quality, or old faces in an image using neural restoration.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fidelity": {"type": "number", "description": "How much to stick to original face (0.0=more creative, 1.0=exact match)", "default": 0.5}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generative_inpaint",
            "description": "Removes specific objects or repairs areas based on a provided mask.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Description of what to fill in the area (optional)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "replace_background",
            "description": "Replaces the background with a blurred version or a new scene.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bg_type": {"type": "string", "enum": ["blur", "color", "custom"], "default": "blur"},
                    "blur_strength": {"type": "integer", "default": 25}
                }
            }
        }
    }
]

# Mapping between Chat names and Pipeline Engine Tool Keys
TOOL_MAPPING = {
    "remove_background": {"id": "remove_bg", "category": "Editing"},
    "enhance_image": {"id": "enhance", "category": "Enhancement"},
    "super_resolution": {"id": "upscale", "category": "Enhancement"},
    "face_restore": {"id": "face_restore", "category": "Enhancement"},
    "generative_inpaint": {"id": "inpaint", "category": "Editing"},
    "replace_background": {"id": "replace_bg", "category": "Creative AI"}
}
