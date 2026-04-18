import logging
import os
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
import requests
from django.conf import settings
from django.utils import timezone
from api.models import FactCheckRecord
from api.utils.ai_client import ai_client

logger = logging.getLogger(__name__)

# --- Credibility Database ---
SOURCE_SCORES = {
    "Reuters": 0.98, "Associated Press": 0.98, "AP News": 0.98,
    "BBC": 0.95, "BBC News": 0.95, "NPR": 0.92, "The New York Times": 0.90,
    "The Washington Post": 0.90, "The Wall Street Journal": 0.88,
    "The Guardian": 0.88, "Financial Times": 0.88, "CNN": 0.80,
    "Snopes": 0.92, "PolitiFact": 0.92, "FactCheck.org": 0.92,
    "The Hindu": 0.88, "The Indian Express": 0.85, "NDTV": 0.80
}

def get_source_credibility(source_name: str, url: str = None) -> float:
    if not source_name:
        return 0.5
    clean_name = source_name.strip()
    if clean_name in SOURCE_SCORES:
        return SOURCE_SCORES[clean_name]
    for key, score in SOURCE_SCORES.items():
        if key.lower() in clean_name.lower() or clean_name.lower() in key.lower():
            return score
    if url:
        if ".gov" in url: return 0.95
        if ".edu" in url: return 0.85
        if ".org" in url: return 0.60
    return 0.5

# --- Search Logic ---
def search_newsapi(query: str, limit: int = 5) -> list:
    api_key = os.environ.get("NEWSAPI_KEY") or os.environ.get("NEWS_API_KEY")
    if not api_key: return []
    url = "https://newsapi.org/v2/everything"
    params = {"q": query, "language": "en", "sortBy": "relevancy", "pageSize": limit, "apiKey": api_key}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return [{"title": a.get("title", ""), "url": a.get("url", ""), "source": a.get("source", {}).get("name", "Unknown"), "description": a.get("description", "")} for a in data.get("articles", [])]
    except: return []

def search_google_news(query: str, limit: int = 5) -> list:
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-US&gl=US&ceid=US:en"
    try:
        resp = requests.get(url, timeout=10)
        root = ET.fromstring(resp.content)
        results = []
        for item in root.findall(".//item")[:limit]:
            source_elem = item.find("source")
            results.append({
                "title": item.findtext("title", ""),
                "url": item.findtext("link", ""),
                "source": source_elem.text if source_elem is not None else "Google News",
                "description": item.findtext("description", "")
            })
        return results
    except: return []

# --- Core Pipeline ---
def run_fact_check_pipeline(claim: str, user=None) -> dict:
    logger.info(f"🚀 Starting RAG pipeline for: {claim[:50]}...")
    
    # 1. Normalization
    norm_claim = re.sub(r'[^\w\s.,!?-]', '', claim)
    norm_claim = re.sub(r'\s+', ' ', norm_claim).strip()
    
    # 2. Search & Evidence Collection
    news_results = search_newsapi(norm_claim, 5)
    gn_results = search_google_news(norm_claim, 5)
    all_results = news_results + gn_results
    
    unique_results = []
    seen_urls = set()
    for item in all_results:
        if item['url'] not in seen_urls:
            item['credibility'] = get_source_credibility(item['source'], item['url'])
            unique_results.append(item)
            seen_urls.add(item['url'])
    
    unique_results.sort(key=lambda x: x['credibility'], reverse=True)
    evidence = unique_results[:5] # Top 5 credible sources
    
    # 3. LLM Analysis (RAG)
    evidence_str = "\n\n".join([f"Source: {e['source']}\nTitle: {e['title']}\nContent: {e['description']}" for e in evidence])
    
    prompt = f"""
ACT AS AN EXPERT FACT-CHECKER.
Analyze the claim below using the provided evidence articles.

CLAIM: "{norm_claim}"

EVIDENCE:
{evidence_str if evidence else "No direct evidence found in news search."}

TASK:
1. Determine the verdict: REAL, FAKE, or UNVERIFIED.
2. Provide a concise explanation.
3. Assign a confidence score (0-100%).

RESPONSE FORMAT (JSON ONLY):
{{
  "verdict": "REAL/FAKE/UNVERIFIED",
  "explanation": "...",
  "confidence": 85
}}
"""
    try:
        response_text = ai_client(prompt)
        cleaned_text = response_text.replace("```json", "").replace("```", "").strip()
        data = json.loads(cleaned_text)
        
        result = {
            "status": data.get("verdict", "UNVERIFIED"),
            "confidence": f"{data.get('confidence', 0)}%",
            "explanation": data.get("explanation", "No explanation provided."),
            "evidence": evidence
        }
    except Exception as e:
        logger.error(f"Pipeline Error: {e}")
        result = {"status": "UNVERIFIED", "confidence": "0%", "explanation": "Error in AI analysis.", "evidence": []}

    # 4. Persistence
    try:
        FactCheckRecord.objects.create(
            user=user if user and user.is_authenticated else None,
            original_claim=claim,
            claim=norm_claim,
            verdict=result["status"],
            confidence=float(result["confidence"].replace("%", "")),
            explanation=result["explanation"],
            evidence=evidence
        )
    except Exception as e:
        logger.error(f"Failed to save record: {e}")

    return result
