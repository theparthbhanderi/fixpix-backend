import requests
import logging
from urllib.parse import quote

logger = logging.getLogger(__name__)

def search_wikipedia(entity, limit=1):
    """
    Fetch a summary for a given entity from Wikipedia.
    Returns a list of results with title, url, and summary.
    """
    if not entity:
        return []

    try:
        # Step 1: Search for the most relevant page
        search_url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={quote(entity)}&limit={limit}&namespace=0&format=json"
        search_res = requests.get(search_url, timeout=5)
        search_data = search_res.json()

        if not search_data or len(search_data) < 4 or not search_data[1]:
            return []

        results = []
        titles = search_data[1]
        urls = search_data[3]

        for i in range(len(titles)):
            title = titles[i]
            url = urls[i]
            
            # Step 2: Extract the summary (extracts)
            extract_url = f"https://en.wikipedia.org/w/api.php?action=query&prop=extracts&exintro&explaintext&titles={quote(title)}&format=json&redirects=1"
            extract_res = requests.get(extract_url, timeout=5)
            extract_data = extract_res.json()
            
            pages = extract_data.get("query", {}).get("pages", {})
            for page_id in pages:
                page = pages[page_id]
                summary = page.get("extract", "")
                if summary:
                    results.append({
                        "title": f"Wikipedia: {title}",
                        "source": "Wikipedia",
                        "url": url,
                        "text": summary[:1500], # Limit summary length
                        "type": "knowledge_base",
                        "credibility": 100 # Wikipedia is high trust for general knowledge
                    })
        
        return results

    except Exception as e:
        logger.error(f"Wikipedia Search Failed for '{entity}': {e}")
        return []

def get_multi_entity_context(entities):
    """
    Fetch background context for multiple entities (e.g., ['India', 'Israel']).
    """
    if not entities:
        return []
        
    all_context = []
    # Limit to top 3 entities to avoid excessive latency
    for entity in entities[:3]:
        results = search_wikipedia(entity)
        if results:
            all_context.extend(results)
            
    return all_context
