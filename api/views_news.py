from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.core.cache import cache
from .services.news_service import get_news, get_fast_news, fetch_news_with_rotation
from .services.factcheck_service import batch_fact_check_news
from .services.trends_service import get_live_trends

@api_view(["GET"])
@permission_classes([AllowAny])
def news_view(request):
    return Response(get_news())

@api_view(["GET"])
@permission_classes([AllowAny])
def fast_news_view(request):
    limit = int(request.query_params.get("limit", 5))
    refresh = request.query_params.get("refresh", "false").lower() == "true"
    country = request.query_params.get("country", "global")
    response = Response(get_fast_news(limit=limit, force_refresh=refresh, country=country))
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    return response

@api_view(["POST"])
@permission_classes([AllowAny])
def batch_fact_check_view(request):
    articles = request.data.get("articles", [])
    user = request.user if request.user.is_authenticated else None
    return Response(batch_fact_check_news(articles, user=user))

@api_view(['GET'])
@permission_classes([AllowAny])
def get_trending_topics(request):
    """GET /api/news/trending/ - Returns live trending topics."""
    trends = get_live_trends()
    return Response(trends)

@api_view(["GET"])
@permission_classes([AllowAny])
def live_news_feed(request):
    category = request.query_params.get("category", "all").lower()
    country = request.query_params.get("country", "us").lower()
    try:
        page = int(request.query_params.get("page", 1))
        per_page = int(request.query_params.get("per_page", 20))
    except ValueError:
        page = 1
        per_page = 20
        
    cache_key = f"news_feed_{category}_{country}_{page}"
    cached_data = cache.get(cache_key)
    
    if cached_data:
        return Response(cached_data)
        
    # Build query
    query_parts = []
    if category != "all":
        query_parts.append(category)
    if country:
        query_parts.append(country)
    query = " ".join(query_parts) if query_parts else "latest top news"
    
    articles = fetch_news_with_rotation(query, max_results=per_page)
    
    if articles:
        response_data = {
            "status": "success",
            "total_results": len(articles),
            "page": page,
            "per_page": per_page,
            "category": category,
            "country": country,
            "articles": articles
        }
        cache.set(cache_key, response_data, 300)
    else:
        response_data = {"status": "error", "message": "News unavailable, try again"}
        
    return Response(response_data)
