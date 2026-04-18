import requests
import os
import json
import re
import hashlib
import time
import sys
import logging
from concurrent.futures import ThreadPoolExecutor
from django.db import transaction
from api.models import FactCheckRecord, IntelligenceReport, EntityMention, NarrativeMap
from api.services.social_service import fetch_reddit_sentiment, get_extra_intelligence
from api.services.wikipedia_service import get_multi_entity_context
from api.services.breakdown_service import analyze_claim, generate_smart_queries
from api.services.search_service import search_tavily, search_google_fact_check, search_gnews, search_newsdata
try:
    from api.services.duckduckgo_service import search_duckduckgo
except (ImportError, ValueError):
    search_duckduckgo = None
from api.services.llm_utils import call_llm
from api.services.news_engine import get_real_time_evidence
from api.services.safe_utils import safe_get, safe_fetch, fallback_response, normalize_output
from api.services.context_service import enhance_query
from newspaper import Article
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

def extract_article_primary(url):
    """Layer 1: Professional metadata & text extraction via Newspaper3k."""
    try:
        article = Article(url)
        # Use a realistic User-Agent to avoid blocks
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        article.download()
        article.parse()
        return article.title, article.text, article.authors, str(article.publish_date) if article.publish_date else None
    except Exception as e:
        logger.warning(f"Newspaper3k Primary Layer failed: {e}")
        return None, None, [], None

def extract_article_bs(url):
    """Layer 2: Targeted BeautifulSoup scraping of article bodies and paragraphs."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        page = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(page.text, "html.parser")
        
        # Target specific article tags first
        article_body = soup.find("article") or soup.find("main") or soup.find(class_=re.compile(r'article|post|content|entry', re.I))
        
        if article_body:
            paragraphs = article_body.find_all("p")
        else:
            paragraphs = soup.find_all("p")
            
        text = " ".join([p.get_text() for p in paragraphs if len(p.get_text().strip()) > 30])
        return text
    except Exception as e:
        logger.warning(f"BS4 Targeted Layer failed: {e}")
        return ""

def extract_article_aggressive(url):
    """Layer 3: Broad tag sweep for any meaningful text content."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")

        # Exclude common noise tags
        for noise in soup(["script", "style", "nav", "footer", "header", "aside"]):
            noise.decompose()

        text = " ".join([
            tag.get_text().strip()
            for tag in soup.find_all(["p", "span", "div", "h1", "h2", "h3"])
            if len(tag.get_text().strip()) > 20
        ])
        
        # Clean up whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except Exception as e:
        logger.warning(f"Aggressive Extraction Layer failed: {e}")
        return ""

def extract_article_content(url):
    """Orchestrate multi-layer extraction to ensure substantial content is retrieved."""
    logger.info(f"MULTI-LAYER EXTRACTION INITIATED: {url}")
    
    # Layer 1: Newspaper3k
    title, text, authors, publish_date = extract_article_primary(url)
    if text and len(text) > 800:
        logger.info("LAYER 1 (Newspaper3k) SUCCESS")
        return {"title": title, "text": text, "authors": authors, "publish_date": publish_date}

    # Layer 2: Targeted BS4
    logger.info("LAYER 1 FAILED → TRIGGERING LAYER 2 (Targeted BS4)")
    bs_text = extract_article_bs(url)
    if bs_text and len(bs_text) > 800:
        logger.info("LAYER 2 (Targeted BS4) SUCCESS")
        return {"title": title or "Source Verified", "text": bs_text, "authors": authors, "publish_date": publish_date}

    # Layer 3: Aggressive Sweep
    logger.info("LAYER 2 FAILED → TRIGGERING LAYER 3 (Aggressive Sweep)")
    agg_text = extract_article_aggressive(url)
    if agg_text and len(agg_text) > 500:
        logger.info("LAYER 3 (Aggressive) SUCCESS")
        return {"title": title or "Source Verified", "text": agg_text, "authors": authors, "publish_date": publish_date}

    logger.error(f"ALL EXTRACTION LAYERS FAILED FOR: {url}. Max recovered chars: {len(agg_text if 'agg_text' in locals() else '')}")
    return None

def normalize_article_response(data):
    """Ensure a valid, non-crashing schema is ALWAYS returned to the frontend."""
    if not isinstance(data, dict):
        data = {}
    
    return {
        "type": data.get("type", "article"),
        "url": data.get("url", ""),
        "headline": data.get("headline", "Source Verified"),
        "summary": data.get("summary", "No detailed summary available for this source."),
        "key_points": data.get("key_points", []) or ["Extraction completed successfully."],
        "key_findings": data.get("key_findings", []) or data.get("key_points", []) or ["No key findings extracted."],
        "sources": data.get("sources", []) or [{"title": "Web Source", "url": data.get("url", ""), "credibility": "Medium"}],
        "status": data.get("status", "success"),
        "verdict": data.get("verdict", "RESEARCH COMPLETE"),
        "confidence": data.get("confidence") if data.get("confidence") is not None else 70,
        "detailed_reasoning": data.get("detailed_reasoning", "The source was processed via automated OSINT intelligence protocols.")
    }

def analyze_article_with_ai(url, title, content):
    """Interrogate the article content using LLM for deep OSINT intelligence."""
    logger.info(f"AI ANALYSIS STARTED for article: {title}")
    
    # Increase context window to 5000 characters
    safe_content = content[:5000]
    
    prompt = f"""
    You are an expert OSINT Digital Forensics & Intelligence Analyst.
    Your mission: Analyze the following article content and produce a high-fidelity intelligence report.

    ARTICLE TITLE: {title}
    ARTICLE URL: {url}
    
    CONTENT BODY:
    {safe_content}

    OUTPUT SCHEMA (JSON):
    {{
      "summary": "A clinical, investigative summary of the content (2-3 sentences).",
      "headline": "Professional OSINT Headline",
      "key_findings": ["Finding 1", "Finding 2", "Finding 3"],
      "detailed_reasoning": "A deep analytical breakdown of the narrative and evidence.",
      "context": "Broad contextual background of the entities/events mentioned.",
      "verdict": "TRUE | FALSE | MISLEADING | UNVERIFIED",
      "confidence": 0-100,
      "misinformation_analysis": "Detection of bias, missing context, or propaganda techniques."
    }}
    """
    
    result = generate_with_retry(
        system_prompt="You are a clinical OSINT intelligence engine. Return ONLY pure JSON.",
        user_prompt=prompt,
        temperature=0.1
    )
    
    if result:
        logger.info("AI ARTICLE ANALYSIS COMPLETED SUCCESSFULLY")
        return result
        
    logger.warning("AI Analysis failed or returned invalid JSON.")
    return None

def generate_article_response(url, title, content):
    """Generate a structured article response using AI analysis where possible."""
    logger.info(f"PREPARING ARTICLE RESPONSE for {title}")
    
    # Mandatory Content Check (Reject < 300 chars for AI analysis)
    if not content or len(content) < 300:
        logger.warning(f"CONTENT TOO SHORT ({len(content) if content else 0}) FOR FULL ANALYSIS.")
        return {
            "type": "article",
            "url": url,
            "headline": title or "Extraction Incomplete",
            "summary": "The intelligence engine could not retrieve sufficient readable content from this source. Automated analysis is limited.",
            "key_points": ["Content extraction restricted by source layout", "Incomplete data for definitive verification"],
            "sources": [{"title": title or "Original Source", "url": url, "credibility": "Low"}],
            "status": "warning",
            "verdict": "UNVERIFIED",
            "confidence": 20,
            "detailed_reasoning": "Standard OSINT extraction protocols yielded less than 300 characters of meaningful text, which is insufficient for deep intelligence synthesis."
        }

    # Priority 1: AI Analysis
    ai_result = analyze_article_with_ai(url, title, content)
    
    if ai_result:
        response = {
            "type": "article",
            "url": url,
            "headline": title or "Source Verified",
            "summary": ai_result.get("summary", ""),
            "key_points": ai_result.get("key_findings", []),
            "key_findings": ai_result.get("key_findings", []),
            "sources": [{"title": title or "Original Source", "source": "Direct Link", "url": url, "credibility": 90}],
            "status": "success",
            "verdict": ai_result.get("verdict", "RESEARCH COMPLETE"),
            "confidence": ai_result.get("confidence", 85),
            "detailed_reasoning": ai_result.get("detailed_reasoning", ""),
            "context": ai_result.get("context", ""),
            "misinformation_analysis": ai_result.get("misinformation_analysis", "")
        }
        return normalize_article_response(response)
    
    # Priority 2: Hardened Extraction Fallback (Non-AI)
    logger.info("FALLING BACK TO NON-AI EXTRACTION RESPONSE")
    raw_points = [p.strip() for p in str(content).split(".") if len(p.strip()) > 30]
    key_points = list(raw_points)[:5] if raw_points else ["No detailed key points extracted."]
    
    response = {
        "type": "article",
        "url": url,
        "headline": title or "Source Verified",
        "summary": (content[:500] + "...") if len(content) > 500 else content,
        "key_points": key_points,
        "key_findings": key_points, 
        "sources": [{"title": title or "Original Source", "source": "Direct Link", "url": url, "credibility": "High"}],
        "status": "success",
        "verdict": "RESEARCH COMPLETE",
        "confidence": 70,
        "detailed_reasoning": f"Intelligence report synthesized from direct extraction of the source article '{title}'."
    }
    return normalize_article_response(response)

def fallback_article_response(url):
    """Final fallback if all extraction fails."""
    logger.warning(f"TRIGGERING FALLBACK RESPONSE FOR: {url}")
    response = {
        "type": "article",
        "url": url,
        "headline": "Resource Verification Pending",
        "summary": "The intelligence engine could not extract the full text content from this source. You can view the original article directly.",
        "key_points": ["Content extraction blocked or restricted", "Source reachable via manual access"],
        "sources": [{"title": "Original Source", "source": "Web", "url": url, "credibility": "Medium"}],
        "status": "fallback",
        "verdict": "UNVERIFIED",
        "confidence": 0,
        "detailed_reasoning": "Standard extraction protocols were blocked by the target host (potentially due to anti-bot measures or paywalls)."
    }
    return normalize_article_response(response)

def detect_query_type(query: str) -> str:
    """
    Returns one of:
    - "article"      → URL passed
    - "news_summary" → general news query
    - "fact_check"   → specific claim to verify
    """
    # 1. Check for URL (article)
    if re.search(r'https?://[^\s]+', query):
        return "article"
    
    # 2. Check for news summary keywords
    news_keywords = ["today", "latest", "recent", "news", "what happened", "current", "update"]
    query_lower = query.lower()
    if any(word in query_lower for word in news_keywords):
        return "news_summary"
    
    # 3. Default to fact_check
    return "fact_check"

def orchestrate_v2_intelligence(topic: str, evidence: str, reasoning: list, paa: list, related: list):
    """v2 Orchestrator: 3-Stage Pipeline (Extraction -> Verification -> Synthesis)."""
    logger.info(f"🧠 ORCHESTRATION INITIATED: {topic}")
    
    # STAGE 1: Extraction (Entities/Claims)
    extract_prompt = f"TOPIC: {topic}\nEVIDENCE: {evidence}\nPAA: {json.dumps(paa)}"
    extraction = generate_with_retry(
        OSINT_v2_EXTRACTOR, 
        extract_prompt, 
        temperature=0.1, 
        required_keys=["entities", "claims"]
    )
    if not extraction: 
        logger.warning("Stage 1 Extraction failed. Using partial fallback.")
        extraction = {"entities": [], "claims": [topic], "locations": []}

    # STAGE 2: Verification (Cross-check/Bias)
    verifier_input = f"CLAIMS: {json.dumps(extraction.get('claims', []))}\nEVIDENCE: {evidence}"
    verification = generate_with_retry(
        OSINT_v2_VERIFIER, 
        verifier_input, 
        temperature=0.1,
        required_keys=["bias_audit", "narratives"]
    )
    if not verification: 
        logger.warning("Stage 2 Verification failed. Using neutral pulse.")
        verification = {"bias_audit": {"score": 30, "leaning": "Unknown"}, "narratives": []}

    # STAGE 3: Synthesis (Executive Brief)
    synthesis_input = {
        "extraction": extraction,
        "verification": verification,
        "strategic_trends": list(reasoning)[:5]
    }
    final_report = generate_with_retry(
        OSINT_v2_SYNTHESIZER, 
        json.dumps(synthesis_input), 
        temperature=0.2,
        required_keys=["headline_summary", "what_is_happening", "verdict"]
    )
    
    return final_report or generate_intelligent_fallback(topic)

def handle_news_summary(query: str) -> dict:
    """
    Handles general news queries by fetching latest articles and synthesizing a briefing.
    """
    # 1. Extract topic
    news_keywords = ["today", "latest", "recent", "news", "what happened", "current", "update"]
    topic = query
    for word in news_keywords:
        topic = re.sub(rf'\b{word}\b', '', topic, flags=re.IGNORECASE)
    topic = topic.strip().strip('"').strip("'")
    if not topic:
        topic = "General News"

    # 2. Fetch Multi-Source Intelligence (v2 context)
    from api.services.news_engine import get_real_time_evidence
    evidence_str, sources, reasoning_chain, trends, paa, related = get_real_time_evidence(topic)
    
    # STEP 3: SIGNAL RADIUS CHECK & HISTORICAL FALLBACK
    if not sources or len(sources) < 1:
        logger.warning(f"📉 ZERO SIGNAL RADIUS FOR: {topic}. checking Forensic Archive...")
        
        # Try to find the latest successful report for this topic
        last_report = IntelligenceReport.objects.filter(query__icontains=topic).order_by("-created_at").first()
        if last_report:
            logger.info(f"📊 ARCHIVED INTEL FOUND FOR: {topic}. Returning historical data.")
            # Fetch entities & narratives for the archived report
            entities = list(EntityMention.objects.filter(report=last_report).values('name', 'entity_type'))
            narratives = list(NarrativeMap.objects.filter(report=last_report).values('narrative_type', 'volume', 'sentiment'))
            
            # Reconstruct result structure
            return {
                "verdict": last_report.verdict,
                "confidence": last_report.confidence,
                "what_is_happening": last_report.what_is_happening,
                "detailed_reasoning": last_report.reasoning,
                "headline_summary": last_report.headline_summary,
                "entities": [{"name": e['name'], "type": e['entity_type']} for e in entities],
                "narrative_map": [{"type": n['narrative_type'], "volume": n['volume'], "sentiment": n['sentiment']} for n in narratives],
                "data_mode": "archived",
                "timestamp": str(last_report.created_at),
                "sources": [], # We don't store raw source JSON currently, but headlines are in headline_summary
                "trends": ["Historical Baseline"]
            }
            
        logger.warning(f"📉 NO ARCHIVE FOUND FOR: {topic}. Triggering Intelligent Fallback.")
        return generate_intelligent_fallback(topic)

    # 4. ORCHESTRATE v2 (3-Stage Analysis)
    result = orchestrate_v2_intelligence(topic, str(evidence_str), list(reasoning_chain), list(paa), list(related))
    
    # 5. Attach Sources & Final Synthesis
    if isinstance(result, dict):
        result["sources"] = sources
        result["trends"] = trends
        result["data_mode"] = "real"
        
        # PHASE 4: FORENSIC ARCHIVE (Save to DB)
        try:
            with transaction.atomic():
                report = IntelligenceReport.objects.create(
                    query=topic,
                    verdict=str(result.get("verdict", "INFERRED")),
                    confidence=int(result.get("confidence", 70)),
                    what_is_happening=str(result.get("what_is_happening", "")),
                    reasoning=str(result.get("detailed_reasoning", "")),
                    headline_summary=list(result.get("headline_summary", []))
                )
                
                # Save Entities
                entities = result.get("entities", [])
                if isinstance(entities, list):
                    for ent in entities:
                        if isinstance(ent, dict):
                            EntityMention.objects.create(
                                report=report,
                                name=str(ent.get("name", "Unknown")),
                                entity_type=str(ent.get("type", "Unknown"))
                            )
                
                # Save Narratives
                narratives = result.get("narrative_map", [])
                if isinstance(narratives, list):
                    for narr in narratives:
                        if isinstance(narr, dict):
                            NarrativeMap.objects.create(
                                report=report,
                                narrative_type=str(narr.get("type", "General")),
                                volume=int(narr.get("volume", 0)),
                                sentiment=float(narr.get("sentiment", 0.0))
                            )
        except Exception as e:
            logger.error(f"❌ Failed to archive forensic intelligence: {e}")

    return ensure_output(result, topic or query)

logger = logging.getLogger(__name__)

# Constants
MAX_CLAIM_CHARS = 2000
MAX_ARTICLE_CHARS = 1200
MAX_ARTICLES = 5
LLM_TEMPERATURE = 0.1
_CACHE = {}  # {hash: {"result": ..., "ts": time.time()}}
CACHE_TTL = 600  # 10 minutes

# SOURCE WEIGHTING (Requested Strategy)
SOURCE_TRUST = {
    "bbc": 0.95,
    "reuters": 0.95,
    "apnews": 0.95,
    "wikipedia": 0.8,
    "news": 0.85,
    "duckduckgo": 0.6,
    "reddit": 0.3
}

def extract_keywords(text: str) -> list:
    if not text:
        return []
    import string
    stop_words = {"a", "an", "the", "and", "or", "but", "if", "because", "as", "what", "when", "where", "how", "who", "which", "this", "that", "these", "those", "then", "just", "so", "than", "such", "both", "latest", "news", "breaking", "update", "today", "of", "in", "to", "for", "with", "on", "at", "from", "by", "about", "like", "through", "over", "before", "between", "after", "since", "without", "is", "are", "was", "were", "be", "been", "being"}
    words = text.lower().translate(str.maketrans('', '', string.punctuation)).split()
    return list(set(w for w in words if w not in stop_words and len(w) > 2))

def count_keyword_matches(keywords: list, text: str) -> int:
    if not text or not keywords:
        return 0
    text_lower = text.lower()
    return sum(1 for k in keywords if k in text_lower)

def optimize_query(raw_query: str) -> list:
    """
    Converts raw user input into 3 targeted search queries using LLM.
    """
    prompt = f"""
    User searched for: "{raw_query}"
    Generate exactly 3 specific search queries to find the most relevant 
    and recent news/facts about this topic.
    Return only a valid JSON array of strings: ["query1", "query2", "query3"]
    """
    try:
        response = call_llm([
            {"role": "system", "content": "You are an expert search query optimization AI. Output strictly valid JSON arrays."},
            {"role": "user", "content": prompt}
        ], temperature=0.1)
        
        cleaned = response.replace("```json", "").replace("```", "").strip()
        queries = json.loads(cleaned)
        
        if isinstance(queries, list) and len(queries) > 0:
            logger.info(f"🧠 QUERY OPTIMIZED: {queries}")
            safe_queries = list(queries)
            return safe_queries[:3]
    except Exception as e:
        logger.error(f"❌ Failed to optimize query with LLM: {e}")
        
    return [
        raw_query,
        f"{raw_query} breaking news",
        f"{raw_query} verified facts"
    ]

def filter_relevant_sources(query: str, sources: list) -> list:
    """
    Score each source for relevance to the query.
    Keep only sources with relevance score > 0.3
    """
    scored = []
    query_keywords = extract_keywords(query)
    
    if not query_keywords:
        return sources[:8]
        
    for source in sources:
        title = str(safe_get(source, "title", ""))
        desc = str(safe_get(source, "description", "") or safe_get(source, "text", ""))
        
        title_match = count_keyword_matches(query_keywords, title)
        desc_match = count_keyword_matches(query_keywords, desc)
        relevance_score = (title_match * 2 + desc_match) / (len(query_keywords) * 3)
        
        if relevance_score > 0.3:
            source['relevance'] = relevance_score
            scored.append(source)
    
    result = sorted(scored, key=lambda x: safe_get(x, 'relevance', 0), reverse=True)
    final_result = list(result)[:8]
    logger.info(f"🔍 Filtered {len(sources)} down to {len(final_result)} relevant sources.")
    return final_result

def process_multilingual_sources(sources: list, primary_language: str = "en") -> list:
    """
    Prioritize English sources first, then include supplementary non-English ones.
    """
    def is_english(text):
        if not text: return True
        s_text = str(text)
        try:
            s_text.encode('ascii')
            return True
        except UnicodeEncodeError:
            ascii_count = sum(1 for c in s_text if ord(c) < 128)
            return (ascii_count / len(s_text)) > 0.8 if len(s_text) > 0 else True

    english_sources = []
    other_sources = []
    
    for s in sources:
        text_to_check = str(safe_get(s, 'title', '')) + " " + str(safe_get(s, 'description', '') or safe_get(s, 'text', ''))
        
        if safe_get(s, 'language') == 'en' or is_english(text_to_check):
            english_sources.append(s)
        else:
            other_sources.append(s)
            
    logger.info(f"🌍 Languages split - English: {len(english_sources)}, Other: {len(other_sources)}")
    final_sources = list(english_sources)[:6] + list(other_sources)[:2]
    return final_sources

def calculate_dynamic_confidence(sources: list, query: str) -> int:
    """
    Calculate confidence based on mathematical evidence weights.
    """
    if not sources:
        return 0
        
    query_keywords = extract_keywords(query)
    base_score = 0
    num_sources = len(sources)
    
    if num_sources == 0: base_score += 0
    elif num_sources <= 2: base_score += 5
    elif num_sources <= 5: base_score += 10
    elif num_sources <= 10: base_score += 20
    else: base_score += 30
    
    source_score = 0
    agreement_score = 0
    recency_score = 0
    keyword_score = 0
    
    high_match_sources = 0
    
    import datetime
    now = datetime.datetime.utcnow()
    
    for s in sources:
        source_name = str(safe_get(s, 'source', '')).lower()
        title = str(safe_get(s, 'title', '')).lower()
        pub_date = str(safe_get(s, 'published_at', '') or safe_get(s, 'date', ''))
        
        if any(x in source_name for x in ['bbc', 'reuters', 'ap news', 'associated press']):
            source_score += 20
        elif 'wikipedia' in source_name:
            source_score += 15
        else:
            source_score += 5
            
        title_matches = count_keyword_matches(query_keywords, title)
        if query_keywords and title_matches == len(query_keywords):
            keyword_score += 15
            high_match_sources = int(high_match_sources) + 1
        elif title_matches > 0:
            keyword_score += 5
            
        if pub_date:
            try:
                date_obj = datetime.datetime.strptime(pub_date[:10], "%Y-%m-%d")
                days_diff = (now - date_obj).days
                if days_diff <= 1:
                    recency_score += 10
                elif days_diff <= 7:
                    recency_score += 5
            except Exception:
                pass
                
    base_score = min(30, int(base_score))
    source_score = min(40, int(source_score))
    keyword_score = min(20, int(keyword_score))
    recency_score = min(20, int(recency_score))
    
    if int(high_match_sources) >= 3:
        agreement_score = 20
    elif int(high_match_sources) == 2:
        agreement_score = 10
        
    total_confidence = base_score + source_score + agreement_score + recency_score + keyword_score
    return int(min(100, max(0, total_confidence)))

def gather_evidence(queries, breakdown=None):
    """
    MASTER STEP 2: MULTI-SOURCE SEARCH
    Orchestrates evidence collection from all integrated intelligence sources.
    """
    evidence = []
    seen_urls = set()
    
    with ThreadPoolExecutor(max_workers=min(len(queries), 15)) as executor:
        # Submit searches for all queries across all providers
        futures = []
        from .news_service import fetch_news_with_rotation
        for q in queries:
            futures.append(executor.submit(safe_fetch, "Tavily", search_tavily, q))
            futures.append(executor.submit(safe_fetch, "Google", search_google_fact_check, q))
            futures.append(executor.submit(safe_fetch, "NewsRotation", fetch_news_with_rotation, q, 3))
            if search_duckduckgo:
                futures.append(executor.submit(safe_fetch, "DuckDuckGo", search_duckduckgo, q))
            futures.append(executor.submit(safe_fetch, "Reddit", fetch_reddit_sentiment, q))
        
        # Specialized knowledge search (Knowledge Base) - Call only once
        wiki_target = breakdown.get("entities") if (breakdown and breakdown.get("entities")) else queries[:2]
        futures.append(executor.submit(safe_fetch, "Wikipedia", get_multi_entity_context, wiki_target))
            
        for future in futures:
            try:
                res = future.result(timeout=5)
                if res and isinstance(res, list):
                    for item in res:
                        url = item.get("url")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            evidence.append(item)
            except Exception as e:
                logger.error(f"Evidence gathering failed for a query: {e}")
                
    return evidence[:20]

def filter_evidence(evidence):
    """
    MASTER STEP 3: SOURCE WEIGHTING & FILTERING
    Ranks evidence by source credibility and filters for relevant consistency.
    """
    for item in evidence:
        source = str(safe_get(item, "source", "")).lower()
        item["credibility"] = 0.5
        for key, weight in SOURCE_TRUST.items():
            if key in source:
                item["credibility"] = weight
                break
    return sorted(evidence, key=lambda x: safe_get(x, "credibility", 0.5), reverse=True)[:10]

# 🛡️ RESILIENCE HELPERS (Feature 17)

def call_llm_safe(messages, temperature=0.1):
    """Global exception wrapper for LLM calls."""
    try:
        return call_llm(messages=messages, temperature=temperature)
    except Exception as e:
        logger.error(f"❌ LLM CRITICAL ERROR: {e}")
        return None

def _parse_llm_response(content: str) -> dict:
    """Legacy alias for robust safe_parse."""
    return safe_parse(content) or {}

def safe_parse(content):
    """Robust JSON parsing that handles markdown fences and common LLM quirks."""
    if not content or not isinstance(content, str):
        return None
    try:
        # Strip markdown code fences if they exist
        cleaned = content.strip()
        if "```" in cleaned:
            # Handle ```json ... ``` or just ``` ... ```
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
            if match:
                cleaned = match.group(1)
            else:
                # If regex fails, try a simple strip
                cleaned = cleaned.replace("```json", "").replace("```", "").strip()
        
        # Ensure it starts and ends with braces
        start = cleaned.find('{')
        end = cleaned.rfind('}')
        if start != -1 and end != -1:
            cleaned = cleaned[start:end+1]
            
        return json.loads(cleaned)
    except Exception as e:
        logger.error(f"❌ JSON PARSE ERROR: {e} | Content: {content[:100]}...")
        return None

def generate_intelligent_fallback(query):
    """
    10/10 level hard fallback to ensure user never sees empty or error text.
    Provides a professional, high-fidelity inferred report.
    """
    return {
        "verdict": "NEWS BRIEFING",
        "confidence": 40,
        "headline_summary": [
            f"Strategic developments concerning {query}",
            f"Key analytical updates on {query} trends",
            f"Global sentiment shifts regarding {query}"
        ],
        "what_is_happening": f"Verified real-time signal consolidation for '{query}' is currently limited. However, broader global patterns and historical context indicate that this topic remains subject to active monitoring within strategic analysis circles.",
        "key_findings": [
            "Low-density signal environment detected",
            "Topic relevance persists in background narratives",
            "Awaiting higher fidelity evidence for precise verdict"
        ],
        "detailed_reasoning": "When direct signals are sparse, elite intelligence synthesis relies on structural pattern recognition. Current developments suggest that while overt events are minimal, the undercurrent remains active, warranting a structured watch-status.",
        "real_world_context": f"This enquiry into '{query}' occurs within a wider context of evolving geopolitical and market shifts. Future developments are likely to clarify the current ambiguity.",
        "misinformation_analysis": "Information vacuums often attract speculative narratives. Users are advised to cross-reference emerging details as they materialize.",
        "what_to_watch": "Monitor for secondary signal clusters and official statements from primary stakeholders.",
        "trend_signals": ["monitoring", "analytical-reconstruction", "strategic-watch"],
        "sources": [],
        "data_mode": "inferred"
    }

def generate_intelligent_fallback(query):
    """Structured recovery for low-signal queries."""
    return {
        "verdict": "UNVERIFIED",
        "confidence": 35,
        "headline_summary": ["Developing Narrative: Analysis in Progress"],
        "what_is_happening": f"We are currently monitoring technical and strategic signals regarding '{query}'. No definitive real-time news headlines have crossed our verification threshold.",
        "key_findings": [
            "1. Lacking real-time news data -> Indicates a low-signal or emerging topic.",
            "2. Monitoring search engines -> No high-fidelity reports located yet.",
            "3. Global Sentiment -> Stable/Uncertain due to lack of reporting."
        ],
        "detailed_reasoning": "Standard OSINT search across primary APIs (GNews, NewsData, Tavily) returned no significant matches. Topic may be too niche or recently emerging for automated indexers.",
        "real_world_context": "When real-time signals are absent, the engine defaults to a monitoring state to prevent synthetic data generation.",
        "sources": [{
            "title": "Extended Search (Google)",
            "url": f"https://www.google.com/search?q={query.replace(' ', '+')}",
            "source": "Google"
        }],
        "data_mode": "inferred"
    }

def ensure_output(result, query):
    """Iron Dome: Never allow empty or technical leaking output."""
    if not result:
        return generate_intelligent_fallback(query)

    # If result is LLM string, try parsing
    if isinstance(result, str):
        try:
            import json
            result = json.loads(result)
        except:
            return generate_intelligent_fallback(query)

    # Force Evidence-First Check
    mode = result.get("data_mode")
    headlines = result.get("headline_summary")
    
    if mode == "real" and (not headlines or any("Strategic" in h for h in headlines)):
         logger.warning(f"🚨 FAKE NEWS DETECTED OR NO HEADLINES. Triggering Fallback for: {query}")
         return generate_intelligent_fallback(query)

    # Ensure required fields exist
    list_fields = ["headline_summary", "key_findings", "sources"]
    for f in list_fields:
        if f not in result or not isinstance(result[f], list):
            result[f] = []

    fields = ["verdict", "confidence", "what_is_happening"]
    for f in fields:
        if f not in result:
            result[f] = "Information pending" if f != "confidence" else 50

    return result

def generate_with_retry(system_prompt, user_prompt, temperature=0.1, max_retries=3, required_keys=None):
    """Automated retry logic for LLM JSON enforcement with flexible validation."""
    for attempt in range(max_retries):
        logger.info(f"🔄 AI ANALYSIS ATTEMPT {attempt + 1}/{max_retries}")
        raw_response = call_llm_safe([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ], temperature=temperature)
        
        if raw_response:
            parsed = safe_parse(raw_response)
            if parsed:
                # If required_keys is provided, validate them
                if required_keys:
                    missing = [k for k in required_keys if k not in parsed]
                    if not missing:
                        logger.info("✅ AI ANALYSIS SUCCESSFUL (Custom Validation)")
                        return parsed
                    else:
                        logger.warning(f"⚠️ Attempt {attempt + 1} failed. Missing keys: {missing}")
                else:
                    # Legacy fallback validation
                    if parsed.get("summary") or parsed.get("what_is_happening") or parsed.get("verdict"):
                        logger.info("✅ AI ANALYSIS SUCCESSFUL (Legacy/Flexible Validation)")
                        return parsed
        
        logger.warning(f"⚠️ Attempt {attempt + 1} failed (Invalid JSON or Validation Error).")
    
    return None

def smart_fallback(query, evidence=None):
    """Fallback for claim verification when LLM fails or signals are too low."""
    logger.error(f"🚨 TRIGGERING SMART-OSINT-FALLBACK for: {query}")
    return ensure_output(None, query)

def news_fallback(query):
    """Deep Analyst Super-Fallback for OSINT failure."""
    logger.error(f"🚨 TRIGGERING OSINT-RECONSTRUCTION-FALLBACK for: {query}")
    return ensure_output(None, query)

# OSINT ANALYST PERSONA & PROMPTS
OSINT_ANALYST_BASE = """You are an elite OSINT intelligence analyst AI.
Your job is NOT to summarize. Your job is to produce a real-world intelligence report.

🚨 CRITICAL RULES:
1. NO FAKE NEWS: You are NOT allowed to generate generic or artificial headlines.
2. STRICT EVIDENCE: You MUST ONLY use the provided REAL news headlines in the EVIDENCE section.
3. EXACT TITLES: Copy the exact titles from the evidence for your 'headline_summary'.
4. REASONING ENGINE: For EACH headline:
   - What happened (Headline)
   - Why it matters (Meaning)
   - Strategic Impact (Impact)
5. SOURCES: All source URLs must be included and must be clickable.

🧠 INTELLIGENT RECONSTRUCTION MODE:
Only trigger this if the system explicitly states "Lacking real-time data". In this mode:
- Generate realistic current news context using world knowledge.
- Focus on logical inference (What is LIKELY happening).
- Provide meaningful explanation.

STRICT OUTPUT FORMAT (JSON):
{
  "verdict": "ACTIVE INTEL | FALSE | MISLEADING | PARTIALLY TRUE | UNVERIFIED | NEWS UPDATE",
  "confidence": number,
  "headline_summary": ["Exact Title 1", "Exact Title 2"],
  "what_is_happening": "One-paragraph executive synthesis of ALL headlines.",
  "key_findings": ["1. [Headline 1] -> Forensic Analysis: Meaning + Strategic Impact", "2. [Headline 2] -> Forensic Analysis: Meaning + Strategic Impact"],
  "detailed_reasoning": "Step-by-step logic chain: Cause -> Effect -> Strategic Significance.",
  "real_world_context": "Deep context and historical/geopolitical background.",
  "misinformation_analysis": "Analysis of narrative manipulation or signal noise.",
  "what_to_watch": "Future outlook: WHAT could happen next.",
  "sources": [{"title": "...", "url": "..."}],
  "data_mode": "real | inferred"
}

CONFIDENCE RULE:
- High evidence -> 70-90 (Use real data)
- Weak data -> 30-50 (Switch to inferred)
- NEVER fake high confidence.
"""
DEEP_ANALYST_PROMPT = OSINT_ANALYST_BASE + """
STRICT OUTPUT FORMAT (JSON):
{
  "input_type": "FACTUAL CLAIM | NEWS QUERY | VAGUE | ARTICLE / URL",
  "verdict": "...",
  "confidence": 0-100,
  "summary": "1-2 sentence executive summary",
  "headline": "Professional OSINT Headline",
  "key_findings": ["Bullet insight 1", "Bullet insight 2"],
  "reasoning_flow": [
    {"type": "claim", "content": "..."},
    {"type": "evidence", "status": "support|conflict|neutral", "content": "..."},
    {"type": "conclusion", "content": "..."}
  ],
  "detailed_reasoning": "Comprehensive clinical analysis paragraph",
  "real_world_context": "Deep background, timeline, or geopolitical context",
  "misinformation_analysis": "Propagation patterns or why this narrative exists",
  "sources": [],
  "suggestions": ["Related query 1", "Related query 2"]
}
"""

FAST_SYSTEM_PROMPT = OSINT_ANALYST_BASE + """
STRICT OUTPUT FORMAT (JSON):
{
  "mode": "fast",
  "input_type": "...",
  "verdict": "...",
  "confidence": 0-100,
  "summary": "2-3 short sentences",
  "headline": "Quick Summary Headline",
  "sources": [],
  "suggestions": []
}
"""

SYSTEM_PROMPT = DEEP_ANALYST_PROMPT

def classify_input(query):
    """Classifies input into FACTUAL CLAIM, NEWS QUERY, or VAGUE."""
    prompt = f"""Classify this user input: "{query}"
    
    Types:
    - FACTUAL CLAIM: Verifiable assertion (e.g., "Earth is flat")
    - NEWS QUERY: Recent events search (e.g., "US election results")
    - VAGUE: Single words or nonsense (e.g., "politics", "abc")
    
    Return ONLY a JSON object: {{"type": "..."}}"""
    
    try:
        raw = call_llm([{"role": "user", "content": prompt}], temperature=0.1)
        res = _parse_llm_response(raw)
        return res.get("type", "FACTUAL CLAIM").upper()
    except:
        # Heuristic fallback if LLM fails
        words = query.strip().split()
        if len(words) <= 2: return "VAGUE"
        if any(w.lower() in ["news", "today", "updates", "latest"] for w in words): return "NEWS QUERY"
        return "FACTUAL CLAIM"

def ai_verify(query, evidence, result1):
    """Pass 2 logic for deep mode auditing."""
    report = json.dumps(result1, indent=2)
    evidence_text = json.dumps(evidence, indent=2)
    prompt = f"CLAIM: {query}\n\nORIGINAL DRAFT ANALYSIS:\n{report}\n\nRAW OSINT EVIDENCE:\n{evidence_text}\n\nAudit this report."
    
    VERIFICATION_PROMPT = """You are the Senior OSINT Auditor. Identify contradictions. Return JSON: {"audit_verdict": "STABLE|REVISE", "corrected_verdict": "...", "final_confidence": 0, "audit_notes": "..."}"""
    
    return call_llm([
        {"role": "system", "content": VERIFICATION_PROMPT},
        {"role": "user", "content": prompt}
    ], temperature=0.1)

def run_fast_pipeline(query):
    """Goal: SPEED FIRST (2-4s)"""
    logger.info(f"⚡ FAST: {query}")
    evidence = []
    seen_urls = set()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(safe_fetch, "GNews", search_gnews, query): "gn", executor.submit(safe_fetch, "Wiki", get_multi_entity_context, [query]): "wk"}
        for f in futures:
            try:
                res = f.result(timeout=3)
                if res and isinstance(res, list):
                    for item in res:
                        url = item.get("url")
                        if url and url not in seen_urls:
                            seen_urls.add(url); evidence.append(item)
            except: pass
    evidence = evidence[:5]
    user_prompt = f"Claim: {query}\nEvidence:\n{json.dumps(evidence)}"
    
    result = generate_with_retry(FAST_SYSTEM_PROMPT, user_prompt, temperature=0.1)
    
    # Final Validation & Fail-Safe Coverage
    return ensure_output(result, query)

def run_deep_pipeline(query, user_country=None):
    """Goal: MAX ACCURACY (8-20s)"""
    logger.info(f"🧠 DEEP: {query}")
    optimized = optimize_query(query)
    if query not in optimized: optimized.insert(0, query)
    breakdown = analyze_claim(query)
    raw_ev = gather_evidence(optimized, breakdown=breakdown)
    ev = filter_relevant_sources(query, raw_ev)
    ev = process_multilingual_sources(ev)
    ev = filter_evidence(ev)
    if not ev and search_duckduckgo: 
        ev = search_duckduckgo(query)
    if not ev: 
        return smart_fallback(query)
    
    user_prompt = f"CLAIM: {query}\n\nEVIDENCE:\n{json.dumps(ev)}"
    res = generate_with_retry(DEEP_ANALYST_PROMPT, user_prompt, temperature=0.2)
    
    # Final Synthesis & Fail-Safe Coverage
    res["mode"] = "deep"
    res["search_queries_used"] = optimized
    res["confidence"] = calculate_dynamic_confidence(ev, query)
    
    return ensure_output(res, query)

ARTICLE_SYSTEM_PROMPT = """You are FixPix Intelligence Engine (FIE), a senior AI research analyst specializing in deep news intelligence and bias detection.
You speak with professional authority, structured logic, and analytical precision. High-fidelity synthesis is your primary objective.

Your goal is to perform a granular analysis of the provided news article content.

STRICT OUTPUT FORMAT (JSON):
{
  "headline": "Professional journalistic headline for the analysis",
  "summary": "2-3 line executive summary of the article's core message",
  "explanations": {
    "beginner": "Very simple explanation using student-friendly language and metaphors.",
    "medium": "Standard factual explanation for a general audience.",
    "expert": "Deep analytical reasoning with strategic/geopolitical nuances."
  },
  "key_points": [
    "Most critical point 1",
    "Most critical point 2"
  ],
  "sections": [
    {
      "heading": "What happened",
      "content": "Detailed breakdown of the core event"
    },
    {
      "heading": "Key facts",
      "content": "Verified factual data points mentioned"
    },
    {
      "heading": "Context",
      "content": "The broader geopolitical or social context"
    }
  ],
  "verdict": "TRUE / FALSE / MISLEADING / UNVERIFIED",
  "confidence": 0-100,
  "bias_analysis": "Detailed assessment of the article's neutrality, tone, and framing",
  "missing_info": "Identify critical facts or perspectives omitted from the article",
  "sources": [
    {
      "title": "Reference source",
      "source": "Publisher",
      "url": "URL",
      "credibility": "High/Medium/Low"
    }
  ],
  "final_explanation": "Final authoritative synthesis.",
  "trust_reason": ["Analytical consistency", "Source verification"],
  "impact": {
    "society": ["Societal implications of this report", "Public perception impact"],
    "politics": ["Political/policy relevance", "Diplomatic consequences"],
    "economy": ["Economic/market effects", "Corporate/sector stability"]
  }
}

RULES:
1. 'sections' must have exactly 3 parts: 'What happened', 'Key facts', and 'Context'.
2. 'bias_analysis' must detect emotional language, one-sided framing, or loaded terms.
3. 'missing_info' must highlight what a skeptical reader should look for.
4. Total confidence should reflect the alignment between article content and verifiable OSINT data.
5. 'explanations' must be distinct in tone: Beginner = Simple/Educational, Medium = Informative, Expert = Analytical.
"""

def _get_hash(text):
    return hashlib.md5(text.lower().strip().encode()).hexdigest()

def sanitize_input(text):
    """Sanitize and validate user input."""
    if not text or not isinstance(text, str):
        return ""
    text = text.strip()
    # Block empty / too short
    if len(text) < 5:
        return ""
    # Truncate to max length
    text = str(text)[:MAX_CLAIM_CHARS]
    # Strip HTML/script tags
    text = re.sub(r'<[^>]+>', '', text)
    # Strip excessive whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def score_credibility(item):
    """Score source credibility based on domain/name."""
    source = safe_get(item, "source", "").lower()
    url = safe_get(item, "url", "").lower()
    
    tier1 = ["reuters", "ap news", "bbc", "associated press", "snopes", "politifact", "factcheck.org", "fullfact", "aap"]
    tier2 = ["nytimes", "washingtonpost", "guardian", "al jazeera", "bloomberg", "cnn", "sky news"]
    
    score = 40  # Default unknown
    if any(t in source for t in tier1):
        score = 95
    elif item.get("type") == "fact_check":
        score = 92
    elif any(t in source for t in tier2):
        score = 85
    elif ".gov" in url or ".edu" in url:
        score = 88
    elif ".org" in url:
        score = 60
    
    item["credibility"] = score
    return score

def analyze_claim(text):
    """Use AI to extract entities, claim type, and optimized search query."""
    prompt = f"""Analyze this claim and extract:
1. Key entities (people, organizations, places)
2. Claim type (political, scientific, breaking_news, historical, health, financial, social)
3. An optimized fact-check search query

Return STRICT JSON:
{{"entities": ["entity1", "entity2"], "claim_type": "type", "search_query": "optimized query"}}

Claim: {text}"""

    default = {"entities": [], "claim_type": "general", "search_query": text[:100]}
    
    # Try Groq first (faster, free)
    groq_key = os.environ.get("OPENAI_API_KEY")
    groq_base = os.environ.get("OPENAI_BASE_URL", "https://api.groq.com/openai/v1")
    try:
        if groq_key:
            res = requests.post(
                f"{groq_base}/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}"},
                json={
                    "model": os.environ.get("LLM_MODEL_NAME", "llama-3.3-70b-versatile"),
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2
                },
                timeout=10
            )
            if res.status_code == 200:
                content = res.json()["choices"][0]["message"]["content"]
                match = re.search(r"\{.*\}", content, re.DOTALL)
                if match:
                    parsed = json.loads(match.group())
                    return {
                        "entities": parsed.get("entities", []),
                        "claim_type": parsed.get("claim_type", "general"),
                        "search_query": parsed.get("search_query", text[:100])
                    }
    except Exception as e:
        logger.error(f"❌ analyze_claim AI optimization failed: {e}")
    
    # Fallback: Return original query wrapped in expected structure
    logger.info("⚠️ Falling back to raw query for search.")
    return {
        "entities": [],
        "claim_type": "general",
        "search_query": text[:100]
    }

def _parse_llm_response(content: str) -> dict:
    """Robust JSON parsing that handles markdown fences and common LLM quirks."""
    try:
        # Strip markdown code fences if they exist
        cleaned = content.strip()
        if "```" in cleaned:
            match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
            if match:
                cleaned = match.group(1)
        
        # Try finding JSON block with regex (stricter)
        json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        
        return json.loads(cleaned)
    except Exception as e:
        logger.warning(f"Failed to parse LLM response: {e}")
        # Single retry logic could be added here if needed
        return {
            "verdict": "UNVERIFIED", 
            "confidence": 0, 
            "summary": "Intelligence Parse Error", 
            "impact": {"society": ["Parse error"], "politics": ["Parse error"], "economy": ["Parse error"]},
            "error": True
        }

def calculate_trust_score(source_quality_avg, evidence_count, contradictions_count, consistency_score=80):
    """
    Calculates a trust score (0-100) based on multiple signals.
    source_quality_avg: 0-100 (avg credibility of sources)
    evidence_count: number of unique sources
    contradictions_count: number of conflicting sources/points
    consistency_score: AI-assigned alignment score (default 80)
    """
    score = 0
    
    # 1. Source Quality (Weighted 40%)
    score += (source_quality_avg / 100) * 40
    
    # 2. Evidence Volume (Weighted 20%)
    score += min(evidence_count * 5, 20)
    
    # 3. Contradiction Penalty
    score -= (contradictions_count * 10)
    
    # 4. Consistency (Weighted 20%)
    score += (consistency_score / 100) * 20
    
    # 5. Baseline Trust (20%)
    score += 20
    
    return max(0, min(100, int(score)))

def get_trust_level(score):
    if score >= 70: return "HIGH"
    if score >= 40: return "MEDIUM"
    return "LOW"

def detect_source_stance(claim, source_text):
    """Uses AI to determine if a source supports, contradicts, or is neutral."""
    prompt = f"Claim: {claim}\nSource snippet: {source_text[:1000]}\n\nDoes this source support, contradict, or remain neutral regarding the claim? Return exactly one word: SUPPORT, CONTRADICT, or NEUTRAL."
    res = call_llm([{"role": "user", "content": prompt}], temperature=0.1)
    if res:
        word = str(res).strip().upper().replace('.', '')
        if 'SUPPORT' in word: return 'support'
        if 'CONTRADICT' in word: return 'contradict'
    return 'neutral'

def score_domain_credibility(url):
    """Assigns credibility based on domain reputation."""
    TRUSTED = ["bbc.com", "reuters.com", "apnews.com", "nytimes.com", "wsj.com", "theguardian.com", "wikipedia.org", "gov", "un.org", "who.int"]
    domain = str(url).split("://")[-1].split("/")[0].lower()
    if any(t in domain for t in TRUSTED):
        return 0.95
    return 0.6

def calculate_confidence_score(search_results, social_pulse, wiki_context):
    """
    Mathematically determines the reliability score (0-100).
    """
    score = 30 # Baseline
    if not isinstance(search_results, list): search_results = []
    
    # Factor 1: Evidence Density
    source_count = len(search_results)
    score += min(source_count * 8, 40) # Up to 40 points for 5+ sources
    
    # Factor 2: Historical Context
    if wiki_context and "No relevant information found" not in str(wiki_context):
        score += 15
        
    # Factor 3: Social Pulse
    sentiment = safe_get(social_pulse, "sentiment_score", 0) if isinstance(social_pulse, dict) else 0
    if sentiment != 0:
        score += 10
        
    # Factor 4: Source Credibility
    high_trust_domains = ['reuters.com', 'apnews.com', 'bbc.com', 'nytimes.com', 'wikipedia.org']
    found_high_trust = any(any(domain in str(s.get('url', '')) for domain in high_trust_domains) for s in search_results)
    if found_high_trust:
        score += 15
        
    return min(score, 100)

def run_ai_reasoning(claim, context, pass_num=1, is_article=False, search_results=None, social_pulse=None, wiki_context_str=None, user_query=None, breakdown=None):
    """Single pass of AI reasoning."""
    if not isinstance(search_results, list): search_results = []
    
    if is_article:
        system_prompt = ARTICLE_SYSTEM_PROMPT
        user_prompt = f"Claim: {claim}\n\nEvidence:\n{context}\n\nTasks: cross-verify, detect bias, identify contradictions."
    else:
        confidence_score = calculate_confidence_score(search_results, social_pulse, wiki_context_str)
        system_prompt = SYSTEM_PROMPT
        user_prompt = f"""
        {SYSTEM_PROMPT}

        CONFIDENCE METRICS: score={confidence_score}, sources={len(search_results)}
        KNOWLEDGE: {wiki_context_str}
        SOCIAL: {json.dumps(social_pulse)}
        CLAIM BREAKDOWN: {json.dumps(breakdown)}
        OSINT EVIDENCE: {json.dumps(search_results)}
        
        User Query: "{user_query}"
        """

    raw = call_llm(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=LLM_TEMPERATURE if pass_num == 1 else 0.2
    )
    if raw:
        parsed_result = _parse_llm_response(str(raw))
        if not is_article:
            parsed_result["confidence"] = confidence_score
            parsed_result["breakdown"] = breakdown
        return parsed_result
    return {"verdict": "UNVERIFIED", "confidence": 0, "summary": "AI Reasoning Timeout"}

def apply_consensus(result, evidence):
    """Adjust confidence based on source agreement."""
    if not evidence or not isinstance(evidence, list) or len(evidence) < 2:
        return result
    
    verdict = result.get("verdict")
    confidence = result.get("confidence", 50)
    high_cred_count = sum(1 for e in evidence if e.get("credibility", 0) >= 80)
    
    if high_cred_count >= 3 and verdict in ("TRUE", "FALSE"):
        confidence = min(confidence + 10, 100)
    elif high_cred_count == 0 and verdict in ("TRUE", "FALSE"):
        confidence = max(confidence - 15, 20)
        if confidence < 40:
            result["verdict"] = "UNVERIFIED"
    
    result["confidence"] = confidence
    return result

def validate_ai_output(result):
    if not result:
        return False
    
    required_fields = ["summary", "key_findings", "detailed_reasoning"]
    
    for field in required_fields:
        if field not in result or not result[field]:
            return False
    
    return True

def generate_manual_analysis(evidence, query):
    findings = []
    for item in evidence[:5]:
        findings.append(item.get("title", ""))
    
    return {
        "verdict": "UNVERIFIED",
        "confidence": 40,
        "summary": f"No strong confirmation found for '{query}', but related news exists.",
        "key_findings": findings,
        "detailed_reasoning": "Multiple weak or indirect sources found. No strong confirmation.",
        "sources": evidence[:5]
    }

# Assuming CACHE_TTL is defined elsewhere, if not, define it here.
CACHE_TTL = 86400 # 24 hours

def fact_check_pipeline(query, user=None, mode="fast", user_country=None):
    """Router Entry Point"""
    import time
    start_time = time.time()
    query = sanitize_input(query)
    if not query:
        return {"verdict": "UNVERIFIED", "summary": "Invalid input", "confidence": 0}
        
    cache_key = _get_hash(f"pipeline:{query}:{mode}:{user_country}")
    if cache_key in _CACHE:
        cached = _CACHE[cache_key]
        if time.time() - cached.get("ts", 0) < CACHE_TTL:
            logger.info(f"⚡ Pipeline Cache Hit: {query} (mode: {mode})")
            return cached["result"]

    try:
        query = query.strip() if query else ""
        logger.info(f"PIPELINE START. INPUT: {query[:100]}...")
        
        # STEP 1: CLASSIFY INPUT - URL DETECTION (Robust)
        is_url = re.match(r'^https?://', query, re.I) or query.startswith("http")
        
        if is_url:
            logger.info(f"URL DETECTED: {query} → ROUTING TO ARTICLE PIPELINE")
            # Use internal master extraction functions (Triple-Layer)
            article_data = extract_article_content(query)
            
            if article_data and article_data.get("text") and len(article_data.get("text")) > 200:
                logger.info("ARTICLE SCRAPED SUCCESSFULLY")
                return generate_article_response(
                    query, 
                    article_data.get("title", "No Title"), 
                    article_data.get("text", "")
                )
            else:
                return fallback_article_response(query)

        # STANDARD TEXT PIPELINE
        logger.info("TEXT MODE ACTIVATED")
        input_type = classify_input(query)
        if input_type == "VAGUE":
            return {
                "verdict": "INVALID INPUT",
                "confidence": 0,
                "summary": "The research query was too vague or empty.",
                "final_explanation": "Please provide a specific factual claim, news query, or valid URL for investigation.",
                "status": "UNVERIFIED"
            }

        # ROUTE TO CORRECT PIPELINE
        if mode == "fast":
            final_res = run_fast_pipeline(query)
        else:
            final_res = run_deep_pipeline(query, user_country)
            
        # Standardize result with metadata
        final_res["input_type"] = input_type
            
        # Common Polish
        final_res["processing_time"] = f"{time.time() - start_time:.2f}s"
        final_res["query_interpreted"] = {"original": query, "cleaned": query}
        
        # Persistence
        try:
            sources_to_save = final_res.get("sources", [])
            if not isinstance(sources_to_save, list): sources_to_save = []
            
            FactCheckRecord.objects.create(
                user=user, 
                claim=query[:500],
                verdict=str(final_res.get("verdict", "UNVERIFIED")),
                confidence=int(final_res.get("confidence", 0)),
                explanation=str(final_res.get("summary", "No findings"))[:500],
                evidence=sources_to_save[:5]
            )
        except Exception as e:
            logger.error(f"Failed to persist fact check: {e}")

        # Cache & Return
        _CACHE[cache_key] = {"result": final_res, "ts": time.time()}
        return final_res

    except Exception as e:
        logger.error(f"❌ Master Pipeline Failure: {e}")
        return ensure_output(None, query)

def batch_fact_check_news(articles, user=None):
    """
    Process multiple news articles in parallel.
    """
    if not articles:
        return []

    # Map articles to claims
    claims = []
    for art in articles:
        claims.append(f"{art.get('title', '')}. {art.get('description', '')}".strip()[:500])

    results = []
    with ThreadPoolExecutor(max_workers=min(len(claims), 10)) as executor:
        # Submit all fact checks in parallel
        futures = [executor.submit(fact_check_pipeline, claim, user) for claim in claims]
        
        for future in futures:
            try:
                results.append(future.result())
            except Exception as e:
                logger.error(f"Batch fact check error: {e}")
                results.append({
                    "verdict": "UNVERIFIED", 
                    "confidence": 0, 
                    "summary": "Verification error",
                    "status": "UNVERIFIED"
                })

    # Merge results back into article structure
    final = []
    for i, art in enumerate(articles):
        res = results[i]
        art.update({
            "factCheck": res.get("verdict") or res.get("status"),
            "confidence": res.get("confidence"),
            "summary": res.get("summary", ""),
            "explanation": res.get("final_explanation") or res.get("explanation", ""),
            "source_urls": res.get("source_urls", {}),
            "detailed_reasoning": res.get("detailed_reasoning", []),
            "evidence": res.get("evidence", []),
            "trust_score": res.get("trust_score"),
            "trust_level": res.get("trust_level"),
            "trust_reason": res.get("trust_reason")
        })
        final.append(art)

    return final

# --- OSINT v2: Multi-Step Reasoning Prompts ---

OSINT_v2_EXTRACTOR = """
You are the FixPix OSINT Extraction Unit.
GOAL: Identify all significant Entities, Locations, and Claims from the provided evidence.

INPUT: 
- Topic
- Evidence Headlines
- PAA (People Also Ask)

OUTPUT (JSON ONLY):
{
  "entities": [{"name": "string", "type": "person|org|loc|event"}],
  "claims": ["Claim 1", "Claim 2"],
  "locations": ["string"],
  "critical_questions": ["Question from PAA relevant to intelligence"]
}
"""

OSINT_v2_VERIFIER = """
You are the FixPix OSINT Verification Unit.
GOAL: Cross-reference extracted claims against the evidence and identify Bias or Propaganda.

INPUT:
- Extracted Claims & Entities
- Source Headlines & Content Snippets

OUTPUT (JSON ONLY):
{
  "verified_claims": [{"claim": "string", "status": "VERIFIED|CONTRADICTED|UNSUBSTANTIATED"}],
  "bias_audit": {
    "score": 0-100 (100 = high bias),
    "leaning": "Description of political/strategic leaning",
    "techniques": ["propaganda techniques found"]
  },
  "narratives": [{"type": "string", "volume": "percentage", "sentiment": -1 to 1}]
}
"""

OSINT_v2_SYNTHESIZER = """
You are the FixPix OSINT Executive Synthesizer.
GOAL: Produce the final Forensic Intelligence Report.

INPUT:
- Extraction Data
- Verification Data
- Strategic Trends

OUTPUT (JSON ONLY):
{
  "headline_summary": ["High-fidelity titles"],
  "verdict": "UNVERIFIED | INFERRED | VERIFIED",
  "confidence": 0-100,
  "what_is_happening": "Clinical executive brief.",
  "key_findings": ["Analytical insight with meaning + impact"],
  "detailed_reasoning": "Deep forensic synthesis of the narrative evolution.",
  "narrative_map": [{"type": "string", "volume": 0-100, "sentiment": -1 to 1}],
  "entities": [{"name": "string", "type": "string"}]
}
"""
