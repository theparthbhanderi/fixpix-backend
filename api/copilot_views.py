from django.http import StreamingHttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from .services.streaming_service import CopilotStreamingService
from .services.copilot_prompts import COPILOT_V5_SYSTEM, VISION_ANALYSIS_MODIFIER
from .services.tool_registry import TOOL_SCHEMA, TOOL_MAPPING
from .services.memory_service import memory_service
import json
import logging
import base64
import re
from io import BytesIO

logger = logging.getLogger(__name__)


def _intent_actions_for_message(user_message, has_image=False):
    """Return (intent_tag, response_type_hint, actions[]) for UI guidance."""
    text = (user_message or '').strip().lower()

    if not text:
        return (
            'empty_prompt',
            'interactive_list',
            [
                {'label': 'Open Editor', 'action': 'navigate', 'target': 'editor'},
                {'label': 'See Pricing', 'action': 'navigate', 'target': 'pricing'},
            ],
        )

    greeting_re = r'^(hi|hello|hey|yo|hii|namaste|hola)\b'
    pricing_words = ['price', 'pricing', 'plan', 'subscription', 'elite', 'pro', 'free', 'upgrade', 'cost']
    feature_words = ['feature', 'tool', 'tools', 'what can you do', 'capabilities', 'help me']
    edit_words = ['remove background', 'remove bg', 'enhance', 'face restore', 'upscale', 'inpaint', 'edit image']
    nav_words = ['open editor', 'go to editor', 'open workspace', 'open pricing', 'open history']
    about_words = ['tell about you', 'about you', 'who are you', 'what are you', 'what can you help with']

    if re.search(greeting_re, text):
        if has_image:
            return (
                'greeting_with_image',
                'interactive_list',
                [
                    {'label': 'Remove Background', 'action': 'execute', 'target': 'remove background'},
                    {'label': 'Enhance Image', 'action': 'execute', 'target': 'enhance image'},
                    {'label': 'Open Editor', 'action': 'navigate', 'target': 'editor'},
                ],
            )
        return (
            'greeting',
            'interactive_list',
            [
                {'label': 'Open Editor', 'action': 'navigate', 'target': 'editor'},
                {'label': 'See Features', 'action': 'navigate', 'target': 'workspace'},
                {'label': 'Pricing Plans', 'action': 'navigate', 'target': 'pricing'},
            ],
        )

    if any(w in text for w in pricing_words):
        return (
            'pricing_query',
            'interactive_list',
            [
                {'label': 'View Pricing', 'action': 'navigate', 'target': 'pricing'},
                {'label': 'Upgrade Plan', 'action': 'upgrade', 'target': 'pricing'},
                {'label': 'Open Workspace', 'action': 'navigate', 'target': 'workspace'},
            ],
        )

    if any(w in text for w in feature_words):
        return (
            'feature_query',
            'interactive_list',
            [
                {'label': 'Remove Background', 'action': 'execute', 'target': 'remove background'},
                {'label': 'Face Restore', 'action': 'execute', 'target': 'face restore'},
                {'label': 'Super Resolution', 'action': 'execute', 'target': 'upscale image'},
            ],
        )

    if any(w in text for w in about_words):
        return (
            'about_assistant',
            'interactive_list',
            [
                {'label': 'Show Best Tools', 'action': 'execute', 'target': 'what are your best editing tools'},
                {'label': 'Open Editor', 'action': 'navigate', 'target': 'editor'},
                {'label': 'Pricing Plans', 'action': 'navigate', 'target': 'pricing'},
            ],
        )

    if any(w in text for w in nav_words):
        return (
            'navigation_query',
            'interactive_list',
            [
                {'label': 'Open Editor', 'action': 'navigate', 'target': 'editor'},
                {'label': 'Open History', 'action': 'navigate', 'target': 'history'},
                {'label': 'Open Pricing', 'action': 'navigate', 'target': 'pricing'},
            ],
        )

    if any(w in text for w in edit_words):
        actions = [
            {'label': 'Run Suggestion', 'action': 'execute', 'target': text[:80]},
            {'label': 'Open Editor', 'action': 'navigate', 'target': 'editor'},
        ]
        if not has_image:
            actions.insert(0, {'label': 'Upload & Edit in Canvas', 'action': 'navigate', 'target': 'editor'})
        return ('edit_request', 'action', actions)

    # Default: keep response text-first, but still provide useful exits.
    return (
        'general_query',
        'interactive_list',
        [
            {'label': 'Open Editor', 'action': 'navigate', 'target': 'editor'},
            {'label': 'Pricing Plans', 'action': 'navigate', 'target': 'pricing'},
        ],
    )


def _shortcut_response_for_intent(intent_tag, user_message, has_image=False):
    """High-quality deterministic replies for common intents.

    This avoids repetitive generic model outputs for simple questions.
    """
    if intent_tag == 'greeting_with_image':
        return (
            "Great, I can see your image context. "
            "Best next step: remove background for clean subject focus, then enhance for detail. "
            "Choose a quick action below."
        )

    if intent_tag == 'greeting':
        return (
            "Welcome to FixPix Assistant PRO. "
            "I can edit photos, guide workflows, and navigate the app. "
            "Pick what you want to do next from the quick buttons."
        )

    if intent_tag == 'about_assistant':
        return (
            "I am your FixPix AI copilot for restoration and creative editing. "
            "I can run tools like remove background, face restore, upscale, and inpaint, "
            "plus guide you to editor, history, and pricing instantly."
        )

    if intent_tag == 'pricing_query':
        return (
            "Pricing depends on usage intensity and advanced features. "
            "If you edit often, Pro gives the best value; Elite is best for high-volume workflows. "
            "Use the buttons to compare and upgrade."
        )

    if intent_tag == 'feature_query':
        if has_image:
            return (
                "For your current image, top results usually come from this order: "
                "Remove Background, then Enhance, then Face Restore if needed. "
                "Run any option below."
            )
        return (
            "Top FixPix tools for quality output: Remove Background, Face Restore, and Super Resolution. "
            "Start with one button below and I will guide the next step."
        )

    if intent_tag == 'navigation_query':
        return "I can route you instantly. Choose where you want to go from the quick navigation buttons."

    return None

def extract_base64_bytes(data_url):
    """Helper to extract bytes from a base64 data URL."""
    try:
        if 'base64,' in data_url:
            header, encoded = data_url.split('base64,')
            return base64.b64decode(encoded)
        return None
    except Exception:
        return None

def image_to_data_url(image_bytes, format='png'):
    """Helper to convert image bytes back to a base64 data URL."""
    try:
        encoded = base64.b64encode(image_bytes).decode('utf-8')
        return f"data:image/{format};base64,{encoded}"
    except Exception:
        return None

@api_view(['POST'])
@permission_classes([AllowAny])
def ai_chat_stream_view(request):
    """
    Direct Neural Stream (V5) endpoint for real-time Copilot interaction.
    Handles memory, vision, and function-calling logic.
    """
    data = request.data or {}
    raw_message = data.get('message', '')
    user_message = raw_message if isinstance(raw_message, str) else str(raw_message or '')
    image_data = data.get('imageData')
    context = data.get('context') if isinstance(data.get('context'), dict) else {}
    user_profile = context.get('userProfile') if isinstance(context.get('userProfile'), dict) else {}
    user_id = user_profile.get('id', 'anonymous')
    # Persist last uploaded image for recovery in execute-pipeline calls.
    if image_data and isinstance(image_data, str):
        memory_service.record_action(
            user_id=user_id,
            tool='chat_image_context',
            params={'source': 'ai_chat_stream'},
            image_url=image_data,
        )
    
    # ─── Neural Context Assembly ────────────────────────────
    # 1. Fetch persistent user memory
    memory = memory_service.get_user_memory(user_id)
    memory_block = f"\n\n[USER_MEMORY]\nLast Tool: {memory.get('last_tool', 'none')}\nLast Params: {json.dumps(memory.get('last_params', {}))}\n"
    
    messages = [
        {"role": "system", "content": COPILOT_V5_SYSTEM + memory_block}
    ]
    
    if image_data:
        messages[0]["content"] += f"\n\n{VISION_ANALYSIS_MODIFIER}"
        # Keep payload provider-compatible: some models reject non-string message content.
        # We still signal that an image is attached so the assistant can drive edit actions.
        image_hint = "[User has attached an image for editing context.]"
        safe_user_text = (user_message or "Analyze this image.").strip()
        messages.append({"role": "user", "content": f"{safe_user_text}\n\n{image_hint}"})
    else:
        messages.append({"role": "user", "content": user_message})

    # ─── Streaming Engine Configuration ──────────────────────
    engine = CopilotStreamingService()
    
    has_image_context = bool(image_data)
    intent_tag, response_hint, intent_actions = _intent_actions_for_message(user_message, has_image=has_image_context)

    def event_stream():
        try:
            # 1. Initial Meta
            yield engine.format_sse('meta', data={'status': 'thinking', 'intent': 'analyzing'})

            shortcut_text = _shortcut_response_for_intent(
                intent_tag=intent_tag,
                user_message=user_message,
                has_image=has_image_context,
            )

            # For common Q&A intents, prefer deterministic high-quality answers.
            if shortcut_text and intent_tag in {
                'greeting',
                'greeting_with_image',
                'about_assistant',
                'pricing_query',
                'feature_query',
                'navigation_query',
            }:
                yield engine.format_sse('chunk', content=shortcut_text)

                if intent_actions:
                    yield engine.format_sse('ui_actions', data={'actions': intent_actions})

                yield engine.format_sse('meta', data={
                    'pipeline': [],
                    'auto_execute': False,
                    'response_type': response_hint or 'interactive_list',
                    'intent': intent_tag,
                    'status': 'completed'
                })
                yield "data: [DONE]\n\n"
                return
            
            # 2. Call AI with tool support
            collected_tool_calls = []
            
            for chunk_raw in engine.generate_chat_stream(messages, tools=TOOL_SCHEMA):
                # The chunk_raw is already a formatted SSE string, but we need to parse it
                # to collect tool calls for the final pipeline meta.
                if isinstance(chunk_raw, str) and 'tool_call' in chunk_raw:
                    try:
                        data_json = json.loads(chunk_raw.replace('data: ', '').strip())
                        if data_json.get('delta'):
                            collected_tool_calls.append(data_json['delta'][0])
                    except Exception:
                        pass

                yield chunk_raw
                
            # 3. 🚀 Pipeline Assembly & Auto-Execution
            pipeline = []
            auto_run = False
            
            # Group and merge tool call arguments
            merged_calls = {}
            for tc in collected_tool_calls:
                idx = tc.get('index', 0)
                if idx not in merged_calls:
                    merged_calls[idx] = {"name": tc.get('function', {}).get('name'), "args": ""}
                if tc.get('function', {}).get('arguments'):
                    merged_calls[idx]["args"] += tc['function']['arguments']
            
            for idx in sorted(merged_calls.keys()):
                call = merged_calls[idx]
                tool_name = call["name"]
                if tool_name in TOOL_MAPPING:
                    try:
                        params = json.loads(call["args"]) if call["args"] else {}
                    except Exception:
                        params = {}
                    
                    pipeline.append({
                        "tool": TOOL_MAPPING[tool_name]["id"],
                        "params": params
                    })
                    
                    # Auto-run high value tools
                    if TOOL_MAPPING[tool_name]["id"] in ['remove_bg', 'enhance', 'face_restore']:
                        auto_run = True

            # 4. Detect Response Type for Frontend switching
            lowered_message = user_message.lower()
            response_type = 'text'
            if pipeline:
                response_type = 'action'
            elif any(x in lowered_message for x in ['feature', 'what can you', 'help', 'list']):
                response_type = 'interactive_list'
            elif any(x in lowered_message for x in ['how to', 'explain', 'guide']):
                response_type = 'guide'

            # Keep answer mode aligned to user question when model returns plain text.
            if response_type == 'text' and response_hint:
                response_type = response_hint

            if intent_actions:
                yield engine.format_sse('ui_actions', data={'actions': intent_actions})

            yield engine.format_sse('meta', data={
                'pipeline': pipeline,
                'auto_execute': auto_run,
                'response_type': response_type,
                'intent': intent_tag,
                'status': 'completed'
            })
            
            yield "data: [DONE]\n\n"
        except Exception as stream_error:
            logger.error(f"[Copilot Stream] Fatal stream error: {stream_error}")
            yield engine.format_sse('chunk', content='Neural stream recovered from an internal error. Please retry your request.')
            yield "data: [DONE]\n\n"

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'  # For Nginx
    return response

@api_view(['POST'])
@permission_classes([AllowAny])
def execute_pipeline_view(request):
    """
    Executes an orchestrated AI pipeline.
    Improved reliability: Handles missing image_url via memory context.
    """
    data = request.data
    pipeline = data.get('pipeline', [])
    image_url = data.get('image_url')
    user_id = data.get('context', {}).get('userProfile', {}).get('id')
    if not user_id and getattr(request, 'user', None) and request.user.is_authenticated:
        user_id = request.user.id
    user_id = user_id or 'anonymous'

    # 🚀 Context Fallback (Phase 4)
    if not image_url:
        memory = memory_service.get_user_memory(user_id)
        image_url = memory.get('last_image')
        if image_url:
            logger.info(f"[RECOVERY] Using last_image context for user {user_id}")

    # Validation (Phase 2)
    if not pipeline:
        return Response({'type': 'error', 'message': 'No steps defined in the pipeline.'}, status=status.HTTP_400_BAD_REQUEST)
    
    if not image_url:
        return Response({
            'type': 'guide',
            'message': 'I need an image to work on! Please upload one or select a previous result 👇'
        }, status=status.HTTP_400_BAD_REQUEST)

    current_image_bytes = extract_base64_bytes(image_url)
    if not current_image_bytes:
        return Response({'type': 'error', 'message': 'Invalid image format. Please re-upload.'}, status=status.HTTP_400_BAD_REQUEST)

    steps_applied = []
    steps_failed = []

    logger.info(f"[FIXPIX RECOVERY] 🚀 Starting Pipeline | User: {user_id} | Steps: {len(pipeline)}")

    try:
        from .ai_engine import AIEngine
        from .services.stability_service import StabilityService
        from subscriptions.utils import get_user_plan
        stability = StabilityService()
        
        for i, step in enumerate(pipeline):
            tool = step.get("tool")
            params = step.get("params", {})
            step_num = i + 1
            
            logger.info(f"[Pipeline] Step {step_num}: {tool} | Params: {params}")

            try:
                result = None

                # ─── Background Tools ────────────────────────────
                if tool == "remove_bg":
                    # Match editor/remove-bg behavior (AIEngine smart routing via same backend stack)
                    result_img = AIEngine.remove_background(current_image_bytes, return_path=False)
                    if result_img is not None:
                        import cv2
                        success, buffer = cv2.imencode('.png', result_img)
                        if success: result = buffer.tobytes()

                elif tool == "replace_bg":
                    bg_type = params.get('bg_type', 'blur')
                    result_img = AIEngine.replace_background(
                        current_image_bytes,
                        bg_type=bg_type,
                        blur_strength=params.get('blur_strength', 25),
                        return_path=False
                    )
                    if result_img is not None:
                        import cv2
                        success, buffer = cv2.imencode('.png', result_img)
                        if success: result = buffer.tobytes()

                # ─── Enhancement Tools ───────────────────────────
                elif tool == "enhance":
                    # Align with editor's "auto enhance" intent first; upscale is a separate tool.
                    result_img = AIEngine.auto_enhance(current_image_bytes, return_path=False)
                    if result_img is not None:
                        import cv2
                        success, buffer = cv2.imencode('.png', result_img)
                        if success: result = buffer.tobytes()

                elif tool == "upscale":
                    scale = params.get('scale', 2)
                    # Match editor's preferred endpoint path: conservative upscale first.
                    result = stability.conservative_upscale(current_image_bytes, params.get('prompt', 'high quality upscale'))
                    if not result:
                        result_img = AIEngine.upscale_image(current_image_bytes, scale=scale, return_path=False)
                        if result_img is not None:
                            import cv2
                            success, buffer = cv2.imencode('.png', result_img)
                            if success: result = buffer.tobytes()

                # ─── Face Tools ──────────────────────────────────
                elif tool == "face_restore":
                    # Match editor face-restore behavior (plan-aware processing + fidelity)
                    plan_name = get_user_plan(request.user) if getattr(request, 'user', None) and request.user.is_authenticated else 'free'
                    result_img = AIEngine.restore_faces(
                        current_image_bytes,
                        return_path=False,
                        fidelity=params.get('fidelity', 0.5),
                        preserve_skin_tone=params.get('preserve_skin_tone', False),
                        plan_name=plan_name,
                    )
                    if result_img is not None:
                        import cv2
                        success, buffer = cv2.imencode('.png', result_img)
                        if success: result = buffer.tobytes()

                elif tool == "face_detail":
                    result_img = AIEngine.enhance_face_details(
                        current_image_bytes,
                        eye_enhance=params.get('eye_enhance', True),
                        skin_smooth=params.get('skin_smooth', True),
                        sharpen_strength=params.get('sharpen_strength', 1.2),
                        return_path=False
                    )
                    if result_img is not None:
                        import cv2
                        success, buffer = cv2.imencode('.png', result_img)
                        if success: result = buffer.tobytes()

                # ─── Color Tools ─────────────────────────────────
                elif tool == "colorize":
                    result_img = AIEngine.colorize_image(current_image_bytes, return_path=False)
                    if result_img is not None:
                        import cv2
                        success, buffer = cv2.imencode('.png', result_img)
                        if success: result = buffer.tobytes()

                elif tool == "white_balance":
                    result_img = AIEngine.correct_white_balance(current_image_bytes, return_path=False)
                    if result_img is not None:
                        import cv2
                        success, buffer = cv2.imencode('.png', result_img)
                        if success: result = buffer.tobytes()

                elif tool in ("sharpen", "brightness", "contrast", "saturation"):
                    brightness = params.get('brightness', 1.0)
                    contrast_val = params.get('contrast', 1.0)
                    sat_val = params.get('saturation', 1.0)
                    result_img = AIEngine.adjust_image(
                        current_image_bytes,
                        brightness=brightness,
                        contrast=contrast_val,
                        saturation=sat_val,
                        return_path=False
                    )
                    if result_img is not None:
                        import cv2
                        success, buffer = cv2.imencode('.png', result_img)
                        if success: result = buffer.tobytes()

                # ─── Repair Tools ────────────────────────────────
                elif tool == "denoise":
                    strength = params.get('strength', 50)
                    result_img = AIEngine.denoise_advanced(current_image_bytes, strength=strength, return_path=False)
                    if result_img is not None:
                        import cv2
                        success, buffer = cv2.imencode('.png', result_img)
                        if success: result = buffer.tobytes()

                elif tool == "remove_scratches":
                    strength = params.get('strength', 50)
                    result_img = AIEngine.remove_scratches(current_image_bytes, strength=strength, return_path=False)
                    if result_img is not None:
                        import cv2
                        success, buffer = cv2.imencode('.png', result_img)
                        if success: result = buffer.tobytes()

                # ─── Inpainting ──────────────────────────────────
                elif tool == "inpaint":
                    mask_data = params.get('mask')
                    if mask_data:
                        mask_bytes = extract_base64_bytes(mask_data)
                        if mask_bytes:
                            import cv2
                            import numpy as np

                            nparr_img = np.frombuffer(current_image_bytes, np.uint8)
                            img = cv2.imdecode(nparr_img, cv2.IMREAD_COLOR)

                            nparr_mask = np.frombuffer(mask_bytes, np.uint8)
                            mask = cv2.imdecode(nparr_mask, cv2.IMREAD_UNCHANGED)

                            if img is not None and mask is not None:
                                result_img = AIEngine.inpaint_object(
                                    img,
                                    mask,
                                    return_path=False,
                                    prompt=params.get('prompt', 'remove object and fill naturally')
                                )
                                if result_img is not None:
                                    success, buffer = cv2.imencode('.png', result_img)
                                    if success:
                                        result = buffer.tobytes()
                    else:
                        logger.warning(f"[Pipeline] Step {step_num}: Inpaint skipped — no mask provided")

                else:
                    logger.warning(f"[Pipeline] Step {step_num}: Unknown tool: {tool}")

                # Update current image if step succeeded
                if result is not None:
                    current_image_bytes = result
                    steps_applied.append(tool)
                    logger.info(f"[Pipeline] Step {step_num} ✅ - Checked")
                else:
                    steps_failed.append({'tool': tool, 'error': 'Tool returned None'})
                    logger.warning(f"[Pipeline] Step {step_num} ❌ - Returned None")

            except Exception as step_error:
                steps_failed.append({'tool': tool, 'error': str(step_error)})
                logger.error(f"[Pipeline] Step {step_num} ❌ - Failed: {step_error}")

        # Build final result
        final_data_url = image_to_data_url(current_image_bytes)
        logger.info(f"[Pipeline] 🏁 Completed. Applied: {len(steps_applied)}/{len(pipeline)}")

        # Persist latest successful image context for recovery in future actions.
        if final_data_url and steps_applied:
            memory_service.record_action(
                user_id=user_id,
                tool=steps_applied[-1],
                params={'pipeline': pipeline, 'steps_applied': steps_applied},
                image_url=final_data_url,
            )

        return Response({
            'status': 'success' if steps_applied else 'failed',
            'type': 'success' if steps_applied else 'error',
            'image': final_data_url,
            'steps_applied': steps_applied,
            'steps_failed': steps_failed,
            'total_steps': len(pipeline),
            'message': f'Pipeline complete: {len(steps_applied)}/{len(pipeline)} successful.' if steps_applied else 'Processing failed. Try again.'
        })

    except Exception as e:
        logger.error(f"[Pipeline] Critical error: {e}")
        return Response(
            {
                'type': 'error', 
                'message': 'Processing failed. Try again.',
                'steps_applied': steps_applied, 
                'steps_failed': steps_failed
            },
            status=status.HTTP_200_OK # Return 200 so the UI can handle the error object gracefully
        )
