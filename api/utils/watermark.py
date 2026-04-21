import cv2
import numpy as np

def apply_watermark(image, text="FixPix AI", reinforce=False):
    """
    Applies a subtle watermark to the bottom-right of the image.
    Works with both BGR and BGRA images.
    """
    if image is None:
        return None

    h, w = image.shape[:2]
    
    # Font settings
    font = cv2.FONT_HERSHEY_DUPLEX
    # Scale font size based on image width (approx 2% of width)
    font_scale = max(0.5, w / 1000.0 * 1.5)
    thickness = max(1, int(font_scale * 2))
    
    # Get text size
    text_size, _ = cv2.getTextSize(text, font, font_scale, thickness)
    text_w, text_h = text_size
    
    # Position: bottom right with margin
    margin = int(min(w, h) * 0.03)
    pos_x = w - text_w - margin
    pos_y = h - margin
    
    # Create overlay for blending
    overlay = image.copy()
    
    # Draw drop shadow (darker, offset)
    shadow_offset = max(1, int(font_scale * 2))
    cv2.putText(overlay, text, (pos_x + shadow_offset, pos_y + shadow_offset), 
                font, font_scale, (0, 0, 0), thickness, cv2.LINE_AA)
    
    # Draw main text (white)
    cv2.putText(overlay, text, (pos_x, pos_y), 
                font, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)
    
    if reinforce:
        center_text = "FixPix"
        center_scale = max(0.4, font_scale * 0.65)
        center_thickness = max(1, int(center_scale * 1.4))
        center_size, _ = cv2.getTextSize(center_text, font, center_scale, center_thickness)
        center_x = max(margin, int((w - center_size[0]) * 0.5))
        center_y = max(text_h + margin, int(h * 0.52))
        cv2.putText(
            overlay,
            center_text,
            (center_x, center_y),
            font,
            center_scale,
            (245, 245, 245),
            center_thickness,
            cv2.LINE_AA,
        )

    # Blend overlay with original for transparency
    alpha = 0.30 if reinforce else 0.35
    output = cv2.addWeighted(overlay, alpha, image, 1 - alpha, 0)
    
    return output
