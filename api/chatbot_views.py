from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
import logging
import json
from .services.deepseek_service import call_deepseek
from .services.prompt_optimizer import optimize_image_prompt

logger = logging.getLogger(__name__)

INTENTS = ['home', 'news', 'editor', 'enhance', 'remove_bg', 'colorize', 'style']

@api_view(['POST'])
@permission_classes([AllowAny])
def detect_intent(request):
    """
    Unified AI Assistant endpoint using OpenRouter.
    POST /api/chatbot/detect-intent/
    """
    text = request.data.get('text', '').strip()
    history = request.data.get('history', [])
    
    if not text:
        return Response({'error': 'No text provided'}, status=status.HTTP_400_BAD_REQUEST)
        
    try:
        from .services.openrouter_service import OpenRouterService
        response = OpenRouterService.chat_assistant(text, history)
        
        # Check if response is JSON (Action/Navigate)
        try:
            # Try to parse if it's a string that looks like JSON
            if isinstance(response, str) and response.strip().startswith('{'):
                clean_json = response.replace('```json', '').replace('```', '').strip()
                data = json.loads(clean_json)
                return Response(data)
            
            # If it's already a dict
            if isinstance(response, dict):
                return Response(response)
                
            # Otherwise it's plain text
            return Response({
                'type': 'chat',
                'message': response
            })
                
        except json.JSONDecodeError:
            return Response({
                'type': 'chat',
                'message': response
            })
                
    except Exception as e:
        logger.error(f"AI Assistant Error: {e}")
        return Response({
            'type': 'chat', 
            'message': "I'm experiencing a neural hiccup. Let's try that again!"
        })

@api_view(['POST'])
@permission_classes([AllowAny])
def optimize_prompt_view(request):
    """
    Optimize an image prompt using AI.
    POST /api/chatbot/optimize-prompt/
    """
    prompt = request.data.get('prompt', '').strip()
    
    if not prompt:
        return Response({'error': 'No prompt provided'}, status=status.HTTP_400_BAD_REQUEST)
        
    try:
        optimized = optimize_image_prompt(prompt)
        
        if optimized:
            return Response({
                'prompt': prompt,
                'optimized_prompt': optimized
            })
        else:
            return Response({'error': 'Failed to optimize prompt'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
    except Exception as e:
        logger.error(f"Optimize Prompt View Error: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([AllowAny])
def generate_tagline_view(request):
    """
    Generate tagline, caption, and headline from an image (vision) or description.
    POST /api/chatbot/generate-tagline/
    Body: { "image": str (base64, optional), "description": str (optional), "style": str }
    """
    image_base64 = request.data.get('image', '').strip()
    description = request.data.get('description', '').strip()
    style = request.data.get('style', 'professional').strip().lower()
    
    if not image_base64 and not description:
        return Response(
            {'error': 'No image or description provided'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        from .services.tagline_service import generate_tagline, generate_tagline_from_image
        
        result = None
        # Try vision first if image is provided
        if image_base64:
            result = generate_tagline_from_image(image_base64, style)
            
        # Fallback to text optimization if vision fails or only description provided
        if not result and description:
            result = generate_tagline(description, style)
        
        if result:
            return Response({
                'tagline': result.get('tagline', ''),
                'caption': result.get('caption', ''),
                'headline': result.get('headline', ''),
                'style': style,
                'method': 'vision' if image_base64 and result.get('method') != 'fallback' else 'text'
            })
        else:
            return Response(
                {'error': 'Failed to generate tagline'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
            
    except Exception as e:
        logger.error(f"Tagline generation view error: {e}")
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
