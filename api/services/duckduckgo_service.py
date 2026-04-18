import requests
from bs4 import BeautifulSoup
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

def search_duckduckgo(query: str) -> List[Dict]:
    """
    Search DuckDuckGo (HTML version) for free search results using GET.
    """
    if not query:
        return []

    try:
        url = "https://html.duckduckgo.com/html/"
        params = {"q": query}
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://duckduckgo.com/"
        }

        # Use GET instead of POST as it's more stable for DDG HTML
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        if response.status_code != 200:
            logger.warning(f"DuckDuckGo search failed with status {response.status_code}")
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        results = []

        # Find all result blocks
        for result in soup.select(".result"):
            title_node = result.select_one(".result__title")
            link_node = result.select_one(".result__a")
            snippet_node = result.select_one(".result__snippet")

            if title_node and link_node:
                raw_url = link_node.get("href")
                # Handle DDG proxy links if necessary, but organic links are usually in 'href'
                results.append({
                    "title": title_node.get_text(strip=True),
                    "url": raw_url,
                    "text": snippet_node.get_text(strip=True) if snippet_node else "",
                    "source": "DuckDuckGo",
                    "type": "search_engine",
                    "credibility": 0.6
                })

        logger.info(f"✅ DuckDuckGo returned {len(results)} results for: {query}")
        return results[:12]

    except Exception as e:
        logger.error(f"❌ DuckDuckGo search error: {str(e)}")
        return []
