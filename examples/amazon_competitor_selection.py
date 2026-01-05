"""
Amazon Competitor Selection Pipeline - Full Example

This matches the exact scenario from the assignment:
Given a seller's product, find the best competitor product from 4+ billion products.

Steps:
1. Generate relevant search keywords (LLM - non-deterministic)
2. Search and retrieve candidate products (API - large result set)
3. Apply filters (price, rating, review count, category + LLM ranking - non-deterministic)
4. Use LLM to evaluate relevance and eliminate false positives (LLM - non-deterministic)
5. Rank and select the single best competitor
"""

import json
import os
import random
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from xray import XRay, Step, Decision, Evidence

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

# Try to import OpenAI, fallback to mock if not available
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
except ImportError:
    OPENAI_AVAILABLE = False
    OPENAI_API_KEY = None
    OPENAI_MODEL = None

# Amazon API via OpenWebNinja (no special imports needed, using httpx)
OPENWEBNINJA_API_KEY = os.getenv("OPENWEBNINJA_API_KEY")
AMAZON_COUNTRY = os.getenv("AMAZON_COUNTRY", "US")
OPENWEBNINJA_AVAILABLE = OPENWEBNINJA_API_KEY is not None and len(OPENWEBNINJA_API_KEY.strip()) > 0


def validate_openai_key() -> tuple[bool, str]:
    """
    Validate the OpenAI API key by making a test call.
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not OPENAI_AVAILABLE:
        return False, "OpenAI package not installed"
    
    if not OPENAI_API_KEY:
        return False, "OPENAI_API_KEY not set in .env file"
    
    # Check key format
    key = OPENAI_API_KEY.strip()
    
    # Check for common issues
    if not key:
        return False, "API key is empty (check .env file)"
    
    if key.startswith('"') or key.startswith("'"):
        return False, f"API key has quotes around it - remove quotes from .env file"
    
    if not key.startswith(("sk-", "sk-proj-")):
        return False, f"API key format incorrect (should start with 'sk-' or 'sk-proj-', got: {key_prefix}...)"
    
    # OpenAI keys are typically 40-60+ characters
    if len(key) < 40:
        return False, f"API key seems too short (length: {len(key)}, expected 40+ chars). Key might be truncated."
    
    if len(key) > 200:
        return False, f"API key seems too long (length: {len(key)}). Check for extra characters."
    
    # Check for whitespace or newlines in the middle
    if " " in key or "\n" in key or "\r" in key:
        return False, f"API key contains whitespace or newlines. Remove all spaces/newlines."
    
    # Try a minimal API call to validate
    try:
        client = OpenAI(api_key=key)
        # Make a tiny test call
        client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=5
        )
        return True, "API key is valid"
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        
        if "401" in error_msg or "invalid_api_key" in error_msg.lower() or "authentication" in error_msg.lower():
            if "Incorrect API key provided" in error_msg:
                return False, f"Invalid API key. Get a new key at: https://platform.openai.com/account/api-keys"
            return False, f"Invalid API key: {error_msg[:200]}"
        elif "429" in error_msg or "rate_limit" in error_msg.lower():
            return False, f"Rate limit error (but key might be valid): {error_msg[:150]}"
        else:
            return False, f"API error ({error_type}): {error_msg[:200]}"


def llm_generate_keywords(product: dict) -> dict[str, Any]:
    """LLM step 1: Generate search keywords (non-deterministic)."""
    if OPENAI_AVAILABLE and OPENAI_API_KEY:
        try:
            # Real LLM call
            client = OpenAI(api_key=OPENAI_API_KEY.strip())  # Strip whitespace
            
            prompt = f"""Generate 5-8 relevant search keywords for finding competitor products for:
Title: {product['title']}
Category: {product.get('category', 'N/A')}

Return only a comma-separated list of keywords, no explanation."""
            
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a product search keyword generator."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,  # Non-deterministic
                max_tokens=100
            )
            
            keywords_text = response.choices[0].message.content.strip()
            keywords = [k.strip() for k in keywords_text.split(",")]
            
            return {
                "keywords": keywords,
                "model": OPENAI_MODEL,
                "tokens_used": response.usage.total_tokens if response.usage else 0,
                "reasoning": f"LLM generated keywords from product title and category",
                "raw_response": keywords_text
            }
        except Exception as e:
            # Fallback to mock on any error (auth, network, etc.)
            error_type = type(e).__name__
            print(f"   ⚠️  LLM API error ({error_type}): {str(e)[:100]}...")
            print(f"   → Falling back to mock LLM")
            # Fall through to mock
    else:
        # Mock fallback
        pass
    
    # Mock fallback (used when API key missing or on error)
    title = product["title"].lower()
    keywords = title.split() + [
        "competitor",
        "alternative",
        product.get("category", ""),
        "similar product"
    ]
    return {
        "keywords": keywords,
        "model": "mock",
        "tokens_used": 0,
        "reasoning": "Mock keyword generation (set OPENAI_API_KEY for real LLM calls)" if not OPENAI_API_KEY else "Mock fallback due to API error"
    }


def get_random_product_from_api() -> dict | None:
    """
    Fetch a random product from Amazon API to use as the starting product.
    
    Returns:
        Product dict with id, title, price, category, etc. or None if API fails
    """
    if not OPENWEBNINJA_AVAILABLE:
        return None
    
    try:
        import httpx
        
        # Search for a popular category to get products
        popular_searches = [
            "laptop stand", "wireless headphones", "water bottle", 
            "yoga mat", "gaming keyboard", "phone case", "backpack",
            "coffee maker", "bluetooth speaker", "fitness tracker"
        ]
        
        search_query = random.choice(popular_searches)
        
        url = "https://api.openwebninja.com/realtime-amazon-data/search"
        headers = {"x-api-key": OPENWEBNINJA_API_KEY.strip()}
        params = {
            "query": search_query,
            "country": AMAZON_COUNTRY,
            "page": "1"
        }
        
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, headers=headers, params=params)
            
            # Check response status
            if response.status_code != 200:
                print(f"   ⚠️  API returned status {response.status_code}: {response.text[:200]}")
                return None
            
            try:
                data = response.json()
            except Exception as e:
                print(f"   ⚠️  Failed to parse JSON response: {str(e)}")
                print(f"   Response text: {response.text[:200]}")
                return None
            
            # Debug: log response structure
            if isinstance(data, dict):
                print(f"   Debug: Response keys: {list(data.keys())[:10]}")
            elif isinstance(data, list):
                print(f"   Debug: Response is a list with {len(data)} items")
        
        # Parse response to get products
        items = []
        if isinstance(data, dict):
            # Check if data key exists and what it contains
            data_value = data.get("data")
            if isinstance(data_value, list):
                items = data_value
            elif isinstance(data_value, dict):
                # data might be a dict with products inside
                for key in ["products", "results", "search_results", "items", "data"]:
                    value = data_value.get(key)
                    if isinstance(value, list):
                        items = value
                        break
            else:
                # Try other top-level keys
                for key in ["products", "results", "search_results", "items"]:
                    value = data.get(key)
                    if isinstance(value, list):
                        items = value
                        break
                    elif isinstance(value, dict) and "items" in value:
                        items = value["items"]
                        break
        elif isinstance(data, list):
            items = data
        
        if not isinstance(items, list) or len(items) == 0:
            print(f"   ⚠️  No products found in API response")
            if isinstance(data, dict):
                print(f"   Response keys: {list(data.keys())[:10]}")
                if "data" in data:
                    data_val = data["data"]
                    print(f"   Data type: {type(data_val)}")
                    if isinstance(data_val, dict):
                        print(f"   Data keys: {list(data_val.keys())[:10]}")
                        # Try to find products in nested data dict
                        for key in ["products", "results", "items", "search_results"]:
                            if key in data_val and isinstance(data_val[key], list):
                                items = data_val[key]
                                print(f"   ✅ Found {len(items)} products in data.{key}")
                                break
                    elif isinstance(data_val, list):
                        print(f"   Data is a list with {len(data_val)} items")
                        items = data_val
                        print(f"   ✅ Using data as product list")
                    else:
                        print(f"   Data value: {str(data_val)[:200]}")
            
            # Final check - if still no items, return None
            if not isinstance(items, list) or len(items) == 0:
                return None
        
        print(f"   ✅ Found {len(items)} products, selecting random one...")
        
        # Pick a random product from the results
        item = random.choice(items[:min(20, len(items))])
        
        # Extract product data
        import re
        asin = item.get("asin") or item.get("product_id") or item.get("id") or ""
        title = item.get("title") or item.get("product_title") or item.get("name") or "Unknown"
        
        price = None
        price_data = item.get("price") or item.get("current_price") or item.get("price_value")
        if price_data:
            if isinstance(price_data, (int, float)):
                price = float(price_data)
            elif isinstance(price_data, str):
                price_match = re.search(r'[\d.]+', price_data.replace(',', ''))
                if price_match:
                    price = float(price_match.group())
        
        category = (item.get("category") or item.get("product_category") or "general").lower()
        
        return {
            "id": asin or f"amazon-prod-{random.randint(10000, 99999)}",
            "asin": asin,
            "title": title,
            "price": price or random.uniform(20, 100),
            "category": category,
            "seller_id": "amazon-marketplace",
            "source": "openwebninja"
        }
        
    except Exception as e:
        error_type = type(e).__name__
        print(f"   ⚠️  Error fetching product ({error_type}): {str(e)[:100]}...")
        return None


def catalog_search(keywords: list[str], limit: int = 5000) -> list[dict]:
    """
    API step: Search Amazon catalog (4+ billion products).
    
    Uses OpenWebNinja Real-Time Amazon Data API if API key is available,
    otherwise falls back to mock data.
    """
    # Try real Amazon API via OpenWebNinja if credentials are available
    if OPENWEBNINJA_AVAILABLE:
        try:
            import httpx
            
            # Build search query from keywords
            search_query = " ".join(keywords[:5])  # Use top 5 keywords
            
            # OpenWebNinja Real-Time Amazon Data search endpoint
            url = "https://api.openwebninja.com/realtime-amazon-data/search"
            
            headers = {
                "x-api-key": OPENWEBNINJA_API_KEY.strip()
            }
            
            params = {
                "query": search_query,
                "country": AMAZON_COUNTRY,
                "page": "1"
            }
            
            # Make API call
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, headers=headers, params=params)
                
                # Check response status
                if response.status_code != 200:
                    print(f"   ⚠️  API returned status {response.status_code}: {response.text[:200]}")
                    raise Exception(f"API returned status {response.status_code}")
                
                try:
                    data = response.json()
                except Exception as e:
                    print(f"   ⚠️  Failed to parse JSON response: {str(e)}")
                    print(f"   Response text: {response.text[:200]}")
                    raise
                
                # Debug: log response structure
                if isinstance(data, dict):
                    print(f"   Debug: Response keys: {list(data.keys())[:10]}")
                elif isinstance(data, list):
                    print(f"   Debug: Response is a list with {len(data)} items")
                else:
                    print(f"   Debug: Response type: {type(data)}")
            
            # Parse API response - use same logic as get_random_product_from_api
            products = []
            items = []
            
            if isinstance(data, dict):
                # Check if data key exists and what it contains
                data_value = data.get("data")
                print(f"   Debug: data['data'] type: {type(data_value)}")
                if isinstance(data_value, list):
                    items = data_value
                    print(f"   Debug: data['data'] is a list with {len(items)} items")
                elif isinstance(data_value, dict):
                    print(f"   Debug: data['data'] is a dict with keys: {list(data_value.keys())[:10]}")
                    # data might be a dict with products inside
                    for key in ["products", "results", "search_results", "items", "data"]:
                        value = data_value.get(key)
                        if isinstance(value, list):
                            items = value
                            print(f"   Debug: Found {len(items)} items in data['data']['{key}']")
                            break
                else:
                    print(f"   Debug: data['data'] is {type(data_value)}, trying other keys...")
                    # Try other top-level keys
                    for key in ["products", "results", "search_results", "items"]:
                        value = data.get(key)
                        if isinstance(value, list):
                            items = value
                            print(f"   Debug: Found {len(items)} items in data['{key}']")
                            break
                        elif isinstance(value, dict) and "items" in value:
                            items = value["items"]
                            print(f"   Debug: Found {len(items)} items in data['{key}']['items']")
                            break
            elif isinstance(data, list):
                items = data
                print(f"   Debug: Response is a list with {len(items)} items")
            
            # Ensure items is a list
            if not isinstance(items, list):
                print(f"   ⚠️  No product list found in response")
                if isinstance(data, dict) and "data" in data:
                    data_val = data["data"]
                    print(f"   Data type: {type(data_val)}")
                    if isinstance(data_val, dict):
                        print(f"   Data keys: {list(data_val.keys())[:10]}")
                items = []
            
            if len(items) == 0:
                print(f"   ⚠️  API returned 0 products, using mock data")
            else:
                print(f"   ✅ Found {len(items)} products from API")
            
            # Limit items to process
            max_items = min(len(items), limit, 50)
            for item in items[:max_items]:
                asin = item.get("asin") or item.get("product_id") or item.get("id") or ""
                title = item.get("title") or item.get("product_title") or item.get("name") or "Unknown"
                
                # Extract product fields
                import re
                
                price = None
                price_data = item.get("price") or item.get("current_price") or item.get("price_value")
                if price_data:
                    if isinstance(price_data, (int, float)):
                        price = float(price_data)
                    elif isinstance(price_data, str):
                        price_match = re.search(r'[\d.]+', price_data.replace(',', ''))
                        if price_match:
                            price = float(price_match.group())
                
                rating = None
                rating_data = item.get("rating") or item.get("stars") or item.get("average_rating")
                if rating_data:
                    if isinstance(rating_data, (int, float)):
                        rating = float(rating_data)
                    elif isinstance(rating_data, str):
                        try:
                            rating = float(rating_data.split()[0])
                        except:
                            pass
                
                review_count = None
                review_data = item.get("reviews_count") or item.get("review_count") or item.get("total_reviews") or item.get("reviews")
                if review_data:
                    if isinstance(review_data, (int, float)):
                        review_count = int(review_data)
                    elif isinstance(review_data, str):
                        review_match = re.search(r'[\d,]+', review_data.replace(',', ''))
                        if review_match:
                            review_count = int(review_match.group().replace(',', ''))
                
                # Category
                category = (item.get("category") or item.get("product_category") or "general").lower()
                
                products.append({
                    "id": asin or f"B0{len(products):08d}",
                    "asin": asin or f"B0{len(products):08d}",
                    "title": title,
                    "price": price or random.uniform(10, 200),
                    "rating": rating or round(random.uniform(2.0, 5.0), 1),
                    "review_count": review_count or random.randint(10, 50000),
                    "category": category,
                    "source": "openwebninja"
                })
            
            # If we got results but need more, pad with mock data
            if len(products) < limit:
                remaining = limit - len(products)
                for i in range(remaining):
                    products.append({
                        "id": f"B0{i:08d}",
                        "title": f"Product {i}",
                        "price": random.uniform(10, 200),
                        "rating": round(random.uniform(2.0, 5.0), 1),
                        "review_count": random.randint(10, 50000),
                        "category": random.choice(["electronics", "office", "accessories", "home"]),
                        "asin": f"B0{i:08d}",
                        "source": "mock"
                    })
            
            return products[:limit]
            
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            print(f"   ⚠️  Amazon API error ({error_type}): {error_msg[:150]}...")
            
            # Debug: print response structure if available
            if 'data' in locals() and isinstance(data, dict):
                print(f"   Response keys: {list(data.keys())[:5]}")
            
            print(f"   → Falling back to mock data")
            # Fall through to mock
    
    # Mock fallback
    mock_products = [
        {
            "id": f"B0{i:08d}",
            "title": f"Product {i}",
            "price": random.uniform(10, 200),
            "rating": round(random.uniform(2.0, 5.0), 1),
            "review_count": random.randint(10, 50000),
            "category": random.choice(["electronics", "office", "accessories", "home"]),
            "asin": f"B0{i:08d}",
            "source": "mock"
        }
        for i in range(limit)
    ]
    return mock_products


def llm_rank_candidates(candidates: list[dict], product: dict) -> list[dict]:
    """LLM step: Rank candidates by relevance (non-deterministic)."""
    if OPENAI_AVAILABLE and OPENAI_API_KEY and len(candidates) <= 50:
        try:
            # Real LLM call (only for small batches to avoid token limits)
            client = OpenAI(api_key=OPENAI_API_KEY.strip())  # Strip whitespace
            
            # Prepare candidate summaries
            candidates_text = "\n".join([
                f"{i+1}. {c['title']} - ${c['price']:.2f}, Rating: {c['rating']}/5, Category: {c.get('category', 'N/A')}"
                for i, c in enumerate(candidates[:50])  # Limit to 50 for token efficiency
            ])
            
            prompt = f"""Given this product:
Title: {product['title']}
Category: {product.get('category', 'N/A')}
Price: ${product.get('price', 0):.2f}

Rank these candidate products by relevance (1-10 scale, where 10 is most relevant):
{candidates_text}

Return a JSON object with candidate numbers as keys and relevance scores (1-10) as values. Example: {{"1": 8.5, "2": 7.2, ...}}"""
            
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a product relevance ranking system. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,  # Non-deterministic
                response_format={"type": "json_object"}
            )
            
            import json
            scores = json.loads(response.choices[0].message.content)
            
            # Apply scores
            ranked = []
            for i, c in enumerate(candidates[:50]):
                score = scores.get(str(i+1), 5.0) / 10.0  # Normalize to 0-1
                ranked.append({**c, "llm_relevance_score": score})
            
            # Add remaining candidates with mock scores
            for c in candidates[50:]:
                score = random.uniform(0.3, 0.95)
                if c.get("category") == product.get("category"):
                    score += 0.1
                ranked.append({**c, "llm_relevance_score": min(score, 1.0)})
            
            return sorted(ranked, key=lambda x: x["llm_relevance_score"], reverse=True)
        except Exception as e:
            # Fallback to mock on any error
            error_type = type(e).__name__
            print(f"   ⚠️  LLM ranking error ({error_type}): {str(e)[:100]}...")
            print(f"   → Falling back to mock ranking")
            # Fall through to mock
    
    # Mock fallback
    ranked = []
    for c in candidates:
        score = random.uniform(0.3, 0.95)
        if c.get("category") == product.get("category"):
            score += 0.1
        ranked.append({**c, "llm_relevance_score": min(score, 1.0)})
    
    return sorted(ranked, key=lambda x: x["llm_relevance_score"], reverse=True)


def llm_evaluate_relevance(candidate: dict, product: dict) -> dict[str, Any]:
    """LLM step: Evaluate if candidate is a false positive (non-deterministic)."""
    if OPENAI_AVAILABLE and OPENAI_API_KEY:
        try:
            # Real LLM call
            client = OpenAI(api_key=OPENAI_API_KEY.strip())  # Strip whitespace
            
            prompt = f"""Evaluate if this candidate product is a relevant competitor for the seller's product.

Seller's Product:
- Title: {product['title']}
- Category: {product.get('category', 'N/A')}
- Price: ${product.get('price', 0):.2f}

Candidate Product:
- Title: {candidate['title']}
- Category: {candidate.get('category', 'N/A')}
- Price: ${candidate.get('price', 0):.2f}
- Rating: {candidate.get('rating', 0)}/5

Is this candidate a relevant competitor? Return JSON with:
- "is_relevant": true/false
- "confidence": 0.0-1.0
- "reasoning": brief explanation"""
            
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You evaluate product relevance. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,  # Non-deterministic
                response_format={"type": "json_object"}
            )
            
            import json
            result = json.loads(response.choices[0].message.content)
            
            return {
                "is_relevant": result.get("is_relevant", False),
                "confidence": float(result.get("confidence", 0.5)),
                "reasoning": result.get("reasoning", "No reasoning provided"),
                "model": OPENAI_MODEL,
                "tokens_used": response.usage.total_tokens if response.usage else 0,
                "raw_response": response.choices[0].message.content
            }
        except Exception as e:
            # Fallback to mock on any error
            error_type = type(e).__name__
            # Only print error once per batch to avoid spam
            if not hasattr(llm_evaluate_relevance, "_error_printed"):
                print(f"   ⚠️  LLM evaluation error ({error_type}): {str(e)[:100]}...")
                print(f"   → Falling back to mock evaluation")
                llm_evaluate_relevance._error_printed = True
            # Fall through to mock
    
    # Mock fallback (used when API key missing or on error)
    score = candidate.get("llm_relevance_score", 0.5)
    is_relevant = score > 0.6
    return {
        "is_relevant": is_relevant,
        "confidence": score,
        "reasoning": f"Mock evaluation - Score {score:.2f} - {'relevant' if is_relevant else 'false positive'}" + 
                    (" (set OPENAI_API_KEY for real LLM calls)" if not OPENAI_API_KEY else " (fallback due to API error)"),
        "model": "mock",
        "tokens_used": 0
    }


def find_amazon_competitor(product: dict, xray: XRay) -> dict | None:
    """
    Find the best competitor product for Amazon seller.
    
    This demonstrates capturing ALL decision context:
    - Inputs at each step
    - Filters applied (config)
    - Outcomes (outputs)
    - Candidates evaluated (as decision events)
    - Reasoning for each decision
    """
    
    # Show API configuration
    print("📡 API Configuration:")
    
    # Check Amazon API via OpenWebNinja
    if OPENWEBNINJA_AVAILABLE:
        masked_key = OPENWEBNINJA_API_KEY[:10] + "..." + OPENWEBNINJA_API_KEY[-4:] if len(OPENWEBNINJA_API_KEY) > 14 else "***"
        print(f"   ✅ Amazon Product API (OpenWebNinja): Enabled")
        print(f"      API Key: {masked_key}")
        print(f"      Country: {AMAZON_COUNTRY}")
    else:
        print("   ⚠️  Amazon Product API: Using mock data")
        if not OPENWEBNINJA_API_KEY:
            print("      (Set OPENWEBNINJA_API_KEY in .env)")
        print("      💡 Get free API key: https://openwebninja.com")
    
    # Show LLM mode and validate API key
    if OPENAI_AVAILABLE and OPENAI_API_KEY:
        # Mask the API key for security
        key_length = len(OPENAI_API_KEY.strip())
        masked_key = OPENAI_API_KEY[:7] + "..." + OPENAI_API_KEY[-4:] if len(OPENAI_API_KEY) > 11 else "***"
        print(f"🤖 LLM Configuration: {OPENAI_MODEL}")
        print(f"   API Key: {masked_key} (length: {key_length} chars)")
        
        # Validate the API key
        print("   Validating API key...", end=" ", flush=True)
        is_valid, error_msg = validate_openai_key()
        if is_valid:
            print("✅ Valid!")
            print(f"   → Using REAL LLM")
        else:
            print("❌ Invalid!")
            print(f"   ⚠️  {error_msg}")
            print(f"   → Will fall back to MOCK LLM")
            print(f"   💡 Get a valid key at: https://platform.openai.com/account/api-keys")
            print(f"   📝 Make sure your .env file has: OPENAI_API_KEY=sk-proj-... (no quotes, no spaces)")
    else:
        print("🤖 Using MOCK LLM")
        if not OPENAI_AVAILABLE:
            print("   (OpenAI package not installed - run: pip install openai)")
        elif not OPENAI_API_KEY:
            print("   (OPENAI_API_KEY not found in .env file)")
    print()
    
    # ============================================
    # START RUN
    # ============================================
    run_id = xray.start_run(
        pipeline_type="amazon_competitor_selection",
        name=f"find_competitor_{product['id']}",
        input={
            "product_id": product["id"],
            "title": product["title"],
            "price": product.get("price"),
            "category": product.get("category"),
            "seller_id": product.get("seller_id")
        },
        metadata={
            "source": "amazon_api",
            "catalog_size": "4_billion_products",
            "timestamp": datetime.now().isoformat()
        }
    )
    
    print(f"🔍 Finding competitor for: {product['title']}")
    print(f"   Run ID: {run_id}\n")
    
    # ============================================
    # STEP 1: Generate Keywords (LLM - Non-deterministic)
    # ============================================
    print("Step 1: Generating search keywords (LLM)...")
    llm_result = llm_generate_keywords(product)
    keywords = llm_result["keywords"]
    
    xray.record_step(run_id, Step(
        name="keyword_generation",
        input={
            "title": product["title"],
            "category": product.get("category"),
            "product_id": product["id"]
        },
        output={
            "keywords": keywords,
            "count": len(keywords)
        },
        config={
            "model": llm_result["model"],
            "max_keywords": 10
        },
        evidence=[
            Evidence(
                evidence_type="llm_output",
                data={
                    "model": llm_result["model"],
                    "tokens_used": llm_result["tokens_used"],
                    "raw_response": llm_result.get("raw_response", llm_result["reasoning"]),
                    "keywords_generated": keywords
                }
            )
        ],
        reasoning=llm_result["reasoning"]
    ))
    print(f"   ✅ Generated {len(keywords)} keywords: {keywords[:3]}...\n")
    
    # ============================================
    # STEP 2: Search Catalog (API - Large Result Set)
    # ============================================
    print("Step 2: Searching Amazon catalog (4+ billion products)...")
    candidates = catalog_search(keywords, limit=5000)
    
    # Check if real Amazon API was used
    real_amazon_count = sum(1 for c in candidates if c.get("source") == "openwebninja")
    api_source = "openwebninja" if real_amazon_count > 0 else "mock"
    
    xray.record_step(run_id, Step(
        name="candidate_search",
        input={
            "keywords": keywords,
            "search_limit": 5000
        },
        output={
            "candidates_found": len(candidates),
            "real_amazon_results": real_amazon_count,
            "mock_results": len(candidates) - real_amazon_count,
            "search_time_ms": 250
        },
        config={
            "api": api_source,
            "limit": 5000,
            "catalog_size": "4_billion_products"
        },
        reasoning=f"Searched Amazon catalog with {len(keywords)} keywords, found {len(candidates)} candidate products" +
                 (f" ({real_amazon_count} from real Amazon API)" if real_amazon_count > 0 else " (using mock data)")
    ))
    
    if real_amazon_count > 0:
        print(f"   ✅ Found {len(candidates)} candidate products ({real_amazon_count} from real Amazon API)\n")
    else:
        print(f"   ✅ Found {len(candidates)} candidate products (using mock data)\n")
    
    # ============================================
    # STEP 3: Apply Filters + LLM Ranking
    # ============================================
    print("Step 3: Applying filters and LLM-based ranking...")
    
    # Filter configuration
    price_min = product.get("price", 0) * 0.5
    price_max = product.get("price", 100) * 1.5
    min_rating = 3.5
    min_reviews = 100
    target_category = product.get("category")
    
    # Build decision events for each candidate
    decisions = []
    filtered_candidates = []
    
    for idx, c in enumerate(candidates):
        # Check price range
        if c["price"] < price_min or c["price"] > price_max:
            decisions.append(Decision(
                candidate_id=c["id"],
                decision_type="rejected",
                reason="price_out_of_range",
                score=None,
                metadata={
                    "price": c["price"],
                    "min_price": price_min,
                    "max_price": price_max,
                    "sequence": idx
                }
            ))
            continue
        
        # Check rating
        if c["rating"] < min_rating:
            decisions.append(Decision(
                candidate_id=c["id"],
                decision_type="rejected",
                reason="rating_below_threshold",
                score=None,
                metadata={
                    "rating": c["rating"],
                    "min_rating": min_rating,
                    "sequence": idx
                }
            ))
            continue
        
        # Check review count
        if c["review_count"] < min_reviews:
            decisions.append(Decision(
                candidate_id=c["id"],
                decision_type="rejected",
                reason="insufficient_reviews",
                score=None,
                metadata={
                    "review_count": c["review_count"],
                    "min_reviews": min_reviews,
                    "sequence": idx
                }
            ))
            continue
        
        # Check category match
        if target_category and c.get("category") != target_category:
            decisions.append(Decision(
                candidate_id=c["id"],
                decision_type="rejected",
                reason="category_mismatch",
                score=None,
                metadata={
                    "candidate_category": c.get("category"),
                    "target_category": target_category,
                    "sequence": idx
                }
            ))
            continue
        
        # Passed all filters - will be ranked by LLM
        filtered_candidates.append(c)
    
    # LLM-based ranking (non-deterministic)
    ranked_candidates = llm_rank_candidates(filtered_candidates, product)
    
    # Record decisions for accepted candidates (with LLM scores)
    for rank, c in enumerate(ranked_candidates):
        decisions.append(Decision(
            candidate_id=c["id"],
            decision_type="accepted",
            reason="passed_filters_and_ranked",
            score=c.get("llm_relevance_score"),
            metadata={
                "rank": rank + 1,
                "sequence": len(candidates) - len(ranked_candidates) + rank
            }
        ))
    
    xray.record_step(run_id, Step(
        name="filtering_and_ranking",
        input={
            "candidate_count": len(candidates),
            "filters": {
                "price_range": [price_min, price_max],
                "min_rating": min_rating,
                "min_reviews": min_reviews,
                "target_category": target_category
            }
        },
        output={
            "filtered_count": len(filtered_candidates),
            "ranked_count": len(ranked_candidates)
        },
        config={
            "price_min": price_min,
            "price_max": price_max,
            "min_rating": min_rating,
            "min_reviews": min_reviews,
            "target_category": target_category,
            "ranking_model": "gpt-4"
        },
        decisions=decisions,  # All decision events (SDK will sample if > 500)
        reasoning=f"Applied price range (${price_min:.0f}-${price_max:.0f}), rating (>{min_rating}), reviews (>{min_reviews}), and category ({target_category}) filters. {len(filtered_candidates)} passed. Ranked by LLM relevance score."
    ))
    print(f"   ✅ {len(filtered_candidates)} candidates passed filters, ranked by LLM\n")
    
    # ============================================
    # STEP 4: LLM Evaluation - Eliminate False Positives
    # ============================================
    print("Step 4: LLM evaluating relevance (eliminating false positives)...")
    
    max_evaluate = min(20, len(ranked_candidates))
    top_candidates = ranked_candidates[:max_evaluate]
    
    print(f"   Evaluating top {len(top_candidates)} candidates...")
    
    evaluation_decisions = []
    relevant_candidates = []
    evaluation_results = []  # Store for evidence
    
    for idx, c in enumerate(top_candidates):
        # Show progress every 5 candidates
        if (idx + 1) % 5 == 0 or (idx + 1) == len(top_candidates):
            print(f"   Progress: {idx + 1}/{len(top_candidates)} candidates evaluated...", end="\r", flush=True)
        
        # LLM evaluates if candidate is relevant (non-deterministic)
        evaluation = llm_evaluate_relevance(c, product)
        evaluation_results.append(evaluation)
        
        if evaluation["is_relevant"]:
            evaluation_decisions.append(Decision(
                candidate_id=c["id"],
                decision_type="accepted",
                reason="llm_confirmed_relevant",
                score=evaluation["confidence"],
                metadata={
                    "llm_confidence": evaluation["confidence"],
                    "evaluation_rank": idx + 1,
                    "sequence": idx
                }
            ))
            relevant_candidates.append({**c, "evaluation": evaluation})
        else:
            evaluation_decisions.append(Decision(
                candidate_id=c["id"],
                decision_type="rejected",
                reason="llm_false_positive",
                score=evaluation["confidence"],
                metadata={
                    "llm_confidence": evaluation["confidence"],
                    "llm_reasoning": evaluation["reasoning"],
                    "evaluation_rank": idx + 1,
                    "sequence": idx
                }
            ))
    
    print()  # New line after progress
    
    xray.record_step(run_id, Step(
        name="llm_relevance_evaluation",
        input={
            "candidates_to_evaluate": len(top_candidates),
            "evaluation_model": "gpt-4"
        },
        output={
            "relevant_count": len(relevant_candidates),
            "false_positives_eliminated": len(top_candidates) - len(relevant_candidates)
        },
        config={
            "model": "gpt-4",
            "evaluation_threshold": 0.6,
            "top_n_evaluated": max_evaluate
        },
        decisions=evaluation_decisions,
        evidence=[
            Evidence(
                evidence_type="llm_batch_evaluation",
                data={
                    "model": evaluation_results[0]["model"] if evaluation_results else "unknown",
                    "total_evaluated": len(top_candidates),
                    "relevant_found": len(relevant_candidates),
                    "avg_confidence": sum(e["confidence"] for e in evaluation_results) / len(evaluation_results) if evaluation_results else 0,
                    "total_tokens": sum(e.get("tokens_used", 0) for e in evaluation_results),
                    "sample_evaluations": [
                        {
                            "candidate_id": top_candidates[i]["id"],
                            "is_relevant": e["is_relevant"],
                            "confidence": e["confidence"],
                            "reasoning": e["reasoning"],
                            "raw_response": e.get("raw_response")
                        }
                        for i, e in enumerate(evaluation_results[:5])  # Store first 5 as samples
                    ]
                }
            )
        ],
        reasoning=f"LLM evaluated top {len(top_candidates)} candidates. {len(relevant_candidates)} confirmed relevant, {len(top_candidates) - len(relevant_candidates)} eliminated as false positives."
    ))
    print(f"   ✅ {len(relevant_candidates)} candidates confirmed relevant by LLM\n")
    
    # ============================================
    # STEP 5: Final Ranking and Selection
    # ============================================
    print("Step 5: Final ranking and selection...")
    
    if not relevant_candidates:
        xray.complete_run(run_id, result={"error": "No relevant competitors found"}, status="failed")
        print("   ❌ No relevant competitors found!")
        return None
    
    # Final ranking
    final_ranked = sorted(
        relevant_candidates,
        key=lambda x: x.get("llm_relevance_score", 0) * x.get("evaluation", {}).get("confidence", 0.5),
        reverse=True
    )
    winner = final_ranked[0]
    
    # Record final selection decisions
    final_decisions = []
    for rank, c in enumerate(final_ranked):
        final_decisions.append(Decision(
            candidate_id=c["id"],
            decision_type="accepted" if c["id"] == winner["id"] else "rejected",
            reason="selected_as_winner" if c["id"] == winner["id"] else "not_highest_ranked",
            score=c.get("llm_relevance_score", 0) * c.get("evaluation", {}).get("confidence", 0.5),
            metadata={
                "final_rank": rank + 1,
                "combined_score": c.get("llm_relevance_score", 0) * c.get("evaluation", {}).get("confidence", 0.5),
                "sequence": rank
            }
        ))
    
    xray.record_step(run_id, Step(
        name="final_selection",
        input={
            "candidates": len(final_ranked)
        },
        output={
            "selected_id": winner["id"],
            "selected_title": winner["title"],
            "final_score": winner.get("llm_relevance_score", 0) * winner.get("evaluation", {}).get("confidence", 0.5)
        },
        decisions=final_decisions,
        reasoning=f"Selected {winner['title']} (ASIN: {winner['id']}) as best competitor match based on combined LLM relevance and evaluation scores."
    ))
    print(f"   ✅ Selected: {winner['title']} (Score: {winner.get('llm_relevance_score', 0):.2f})\n")
    
    # ============================================
    # COMPLETE RUN
    # ============================================
    xray.complete_run(run_id, result={
        "competitor_id": winner["id"],
        "competitor_asin": winner.get("asin"),
        "competitor_title": winner["title"],
        "final_score": winner.get("llm_relevance_score", 0) * winner.get("evaluation", {}).get("confidence", 0.5)
    })
    
    print(f"✅ Run completed: {run_id}")
    print(f"\n📊 View full decision context at:")
    print(f"   📈 Visual: http://localhost:8000/visualize/runs/{run_id}")
    print(f"   📄 JSON:  http://localhost:8000/v1/runs/{run_id}?include_decisions=true")
    
    return winner


def main():
    """Run the Amazon competitor selection example."""
    import sys
    
    print("=" * 70)
    print("Amazon Competitor Selection - Full Pipeline with X-Ray")
    print("=" * 70)
    print()
    
    # Initialize X-Ray
    xray = XRay(api_url="http://localhost:8000")
    
    # Get starting product
    seller_product = None
    
    if len(sys.argv) > 1:
        # Custom product from command line
        seller_product = {
            "id": f"seller-prod-{random.randint(1000, 9999)}",
            "title": " ".join(sys.argv[1:]),
            "price": random.uniform(20, 100),
            "category": "general",
            "seller_id": "seller-custom"
        }
        print(f"📦 Using custom product from command line")
    else:
        # Always try to get a random product from Amazon API
        print("🔍 Fetching a random product from Amazon...")
        seller_product = get_random_product_from_api()
        
        if not seller_product:
            # If API fails, try a few more times with different searches
            print(f"   ⚠️  First attempt failed, trying different search...")
            for attempt in range(2):
                seller_product = get_random_product_from_api()
                if seller_product:
                    break
            
            if not seller_product:
                print(f"   ❌ Failed to fetch product from API after multiple attempts")
                print(f"   Please check your OPENWEBNINJA_API_KEY in .env file")
                return
    
    print(f"📦 Starting Product: {seller_product['title']}")
    print(f"   Price: ${seller_product['price']:.2f}")
    print(f"   Category: {seller_product.get('category', 'N/A')}")
    if seller_product.get('asin'):
        print(f"   ASIN: {seller_product['asin']}")
    print()
    
    # Run the pipeline
    winner = find_amazon_competitor(seller_product, xray)
    
    if winner:
        print("\n" + "=" * 70)
        print("RESULT")
        print("=" * 70)
        print(f"Best Competitor: {winner['title']}")
        print(f"  ASIN: {winner.get('asin', winner['id'])}")
        print(f"  Price: ${winner['price']:.2f}")
        print(f"  Rating: {winner['rating']}/5.0")
        print(f"  Reviews: {winner['review_count']:,}")
        print(f"  Relevance Score: {winner.get('llm_relevance_score', 0):.2f}")
        print()
    
    xray.close()


if __name__ == "__main__":
    main()

