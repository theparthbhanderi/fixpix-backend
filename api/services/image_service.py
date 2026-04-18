import pytesseract
from PIL import Image
import logging
from api.services.factcheck_service import fact_check_pipeline

logger = logging.getLogger(__name__)

def check_image(file, mode="deep"):
    try:
        # 1. Open and convert for optimal OCR
        img = Image.open(file).convert('L') # Convert to grayscale
        
        # 2. Extract text with confidence filtering
        text = pytesseract.image_to_string(img)
        
        # 3. Clean and Normalize OCR text
        clean_text = " ".join(text.split())
        if len(clean_text) < 10:
             return {
                "verdict": "UNVERIFIED",
                "status": "UNVERIFIED",
                "confidence": 0,
                "summary": "No Clear Text Found",
                "final_explanation": "The image provided does not contain enough legible text for the intelligence engine to verify. Please upload a clearer screenshot."
            }

        # 4. Process via core fact-check engine
        result = fact_check_pipeline(clean_text[:2000], mode=mode)

        return {
            "extractedText": clean_text,
            **result
        }

    except Exception as e:
        logger.error(f"Image Check Error: {e}")
        return {
            "verdict": "UNVERIFIED", 
            "status": "UNVERIFIED", 
            "confidence": 0, 
            "summary": "Image Analysis Failed",
            "final_explanation": f"The intelligence engine failed to parse this image: {str(e)}"
        }
