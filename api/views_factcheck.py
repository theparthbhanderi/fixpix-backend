import logging
from subscriptions.utils import check_and_log_usage
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from .models import FactCheckRecord, SharedResult, IntelligenceReport, EntityMention, NarrativeMap
from .services.factcheck_service import fact_check_pipeline, detect_query_type, handle_news_summary
from .services.link_service import check_link
from .services.image_service import check_image

@api_view(["POST"])
@permission_classes([AllowAny])
def fact_check_view(request):
    text = request.data.get("text", "")
    mode = request.data.get("mode", "deep")
    user_country = request.data.get("country", None)
    
    # Check Usage Limit
    usage = check_and_log_usage(request.user, "fact_checker")
    if not usage["allowed"]:
        return Response(usage, status=429)

    # NEW: Query Type Detection & Routing
    query_type = detect_query_type(text)
    
    if query_type == "news_summary":
        return Response(handle_news_summary(text))
    
    # Default fact-check pipeline
    return Response(fact_check_pipeline(text, user=request.user if request.user.is_authenticated else None, mode=mode, user_country=user_country))

@api_view(["POST"])
@permission_classes([AllowAny])
def fact_check_link(request):
    url = request.data.get("url", "")
    mode = request.data.get("mode", "deep")
    return Response(check_link(url, mode=mode))

@api_view(["POST"])
@permission_classes([AllowAny])
def fact_check_image(request):
    file = request.FILES.get("image")
    mode = request.data.get("mode", "deep")
    if not file:
        return Response({"error": "No image provided"}, status=400)
    
    # Check Usage Limit
    usage = check_and_log_usage(request.user, "image_fact_checker")
    if not usage["allowed"]:
        return Response(usage, status=429)

    return Response(check_image(file, mode=mode))
    
@api_view(["POST"])
@permission_classes([AllowAny])
def fact_check_screenshot(request):
    """
    Placeholder for screenshot-to-intelligence verification.
    """
    return Response({"status": "UNVERIFIED", "summary": "Screenshot analysis coming soon.", "confidence": 0})

from .services.suggestion_service import generate_suggestions

@api_view(["GET"])
@permission_classes([AllowAny])
def suggestions_view(request):
    query = request.GET.get("q", "")
    return Response(generate_suggestions(query, user_id=request.user.id if request.user.is_authenticated else None))

@api_view(["GET"])
@permission_classes([AllowAny])
def get_history(request):
    """
    Unified history endpoint for FactCheckRecords and IntelligenceReports.
    """
    # 1. Fetch Legacy Records
    records = FactCheckRecord.objects.all().order_by("-created_at")[:15]
    history = []
    for r in records:
        history.append({
            "id": f"fact_{r.id}",
            "type": "fact_check",
            "claim": r.claim,
            "verdict": r.verdict,
            "confidence": r.confidence,
            "timestamp": r.created_at,
            "explanation": r.explanation
        })
    
    # 2. Fetch v2 Intelligence Reports
    intel_reports = IntelligenceReport.objects.all().order_by("-created_at")[:15]
    for ir in intel_reports:
        history.append({
            "id": f"intel_{ir.id}",
            "type": "intelligence",
            "claim": ir.query,
            "verdict": ir.verdict,
            "confidence": ir.confidence,
            "timestamp": ir.created_at,
            "explanation": ir.what_is_happening
        })
        
    # Sort unified history
    history.sort(key=lambda x: x["timestamp"], reverse=True)
    return Response(history[:20])


@api_view(["POST"])
@permission_classes([AllowAny])
def create_share_item(request):
    """
    Creates a shareable snapshot of a fact-check result.
    """
    query = request.data.get("query", "")
    data = request.data.get("result", {})
    
    if not query or not data:
        return Response({"error": "Missing query or result data"}, status=400)
        
    share_item = SharedResult.objects.create(query=query, data=data)
    
    return Response({
        "share_id": str(share_item.id),
        "share_url": f"/share/{share_item.id}"
    })


@api_view(["GET"])
@permission_classes([AllowAny])
def get_share_item(request, share_id):
    """
    Retrieves a shared fact-check snapshot.
    """
    try:
        share = SharedResult.objects.get(id=share_id)
        response_data = {
            "query": share.query,
            "result": share.data,
            "created_at": share.created_at.isoformat()
        }
        return Response(response_data)
    except (SharedResult.DoesNotExist, ValueError):
        return Response({"error": "Share link invalid or expired"}, status=404)
