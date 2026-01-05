"""
Amazon competitor selection pipeline with X-Ray.

Demonstrates the full assignment scenario:
- Given a seller's product, find the best competitor from 4B+ products
- Uses real Amazon/OpenAI APIs when available, falls back to mock

Usage: python -m examples.amazon_competitor_selection [optional product name]
"""

import json
import os
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from xray import XRay, Step, Decision, Evidence

# load .env
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

# openai setup
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
except ImportError:
    OPENAI_AVAILABLE = False
    OPENAI_API_KEY = None
    OPENAI_MODEL = None

# amazon api setup (openwebninja)
OPENWEBNINJA_API_KEY = os.getenv("OPENWEBNINJA_API_KEY")
AMAZON_COUNTRY = os.getenv("AMAZON_COUNTRY", "US")
OPENWEBNINJA_AVAILABLE = bool(OPENWEBNINJA_API_KEY and OPENWEBNINJA_API_KEY.strip())


def validate_openai_key() -> tuple[bool, str]:
    if not OPENAI_AVAILABLE:
        return False, "OpenAI package not installed"
    if not OPENAI_API_KEY:
        return False, "OPENAI_API_KEY not set"
    
    key = OPENAI_API_KEY.strip()
    if not key.startswith(("sk-", "sk-proj-")):
        return False, "Invalid key format"
    if len(key) < 40:
        return False, "Key too short"
    
    try:
        client = OpenAI(api_key=key)
        client.chat.completions.create(model=OPENAI_MODEL, messages=[{"role": "user", "content": "Hi"}], max_tokens=5)
        return True, "Valid"
    except Exception as e:
        return False, str(e)[:100]


def llm_generate_keywords(product: dict) -> dict[str, Any]:
    """Generate search keywords using LLM (or mock fallback)."""
    if OPENAI_AVAILABLE and OPENAI_API_KEY:
        try:
            client = OpenAI(api_key=OPENAI_API_KEY.strip())
            prompt = f"""Generate 5-8 search keywords for finding competitor products for:
Title: {product['title']}
Category: {product.get('category', 'N/A')}

Return only a comma-separated list."""
            
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "system", "content": "You generate product search keywords."}, {"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=100
            )
            
            keywords_text = resp.choices[0].message.content.strip()
            keywords = [k.strip() for k in keywords_text.split(",")]
            
            return {
                "keywords": keywords,
                "model": OPENAI_MODEL,
                "tokens_used": resp.usage.total_tokens if resp.usage else 0,
                "reasoning": "LLM generated keywords from product title and category",
                "raw_response": keywords_text
            }
        except Exception as e:
            print(f"   LLM error: {str(e)[:80]}... (using mock)")
    
    # mock fallback
    title = product["title"].lower()
    keywords = title.split() + ["competitor", "alternative", product.get("category", ""), "similar"]
    return {
        "keywords": keywords,
        "model": "mock",
        "tokens_used": 0,
        "reasoning": "Mock keyword generation"
    }


def get_random_product_from_api() -> dict | None:
    """Fetch a random product from Amazon API."""
    if not OPENWEBNINJA_AVAILABLE:
        return None
    
    try:
        import httpx
        
        searches = ["laptop stand", "wireless headphones", "water bottle", "yoga mat", "gaming keyboard", "phone case"]
        url = "https://api.openwebninja.com/realtime-amazon-data/search"
        headers = {"x-api-key": OPENWEBNINJA_API_KEY.strip()}
        params = {"query": random.choice(searches), "country": AMAZON_COUNTRY, "page": "1"}
        
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=headers, params=params)
            if resp.status_code != 200:
                return None
            data = resp.json()
        
        items = _extract_items(data)
        if not items:
            return None
        
        item = random.choice(items[:20])
        return _parse_product(item)
    except Exception as e:
        print(f"   API error: {str(e)[:80]}...")
        return None


def catalog_search(keywords: list[str], limit: int = 5000) -> list[dict]:
    """Search Amazon catalog (or mock fallback)."""
    if OPENWEBNINJA_AVAILABLE:
        try:
            import httpx
            
            url = "https://api.openwebninja.com/realtime-amazon-data/search"
            headers = {"x-api-key": OPENWEBNINJA_API_KEY.strip()}
            params = {"query": " ".join(keywords[:5]), "country": AMAZON_COUNTRY, "page": "1"}
            
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url, headers=headers, params=params)
                if resp.status_code != 200:
                    raise Exception(f"API returned {resp.status_code}")
                data = resp.json()
            
            items = _extract_items(data)
            products = [_parse_product(item) for item in items[:min(len(items), limit, 50)]]
            
            # pad with mock if needed
            while len(products) < limit:
                products.append(_mock_product(len(products)))
            
            return products[:limit]
        except Exception as e:
            print(f"   API error: {str(e)[:80]}... (using mock)")
    
    # mock fallback
    return [_mock_product(i) for i in range(limit)]


def _extract_items(data: dict) -> list:
    """Extract product list from API response."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ["data", "products", "results", "search_results", "items"]:
            val = data.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):
                items = _extract_items(val)
                if items:
                    return items
    return []


def _parse_product(item: dict) -> dict:
    """Parse API product item into our format."""
    asin = item.get("asin") or item.get("product_id") or item.get("id") or ""
    title = item.get("title") or item.get("product_title") or item.get("name") or "Unknown"
    
    price = None
    price_data = item.get("price") or item.get("current_price")
    if isinstance(price_data, (int, float)):
        price = float(price_data)
    elif isinstance(price_data, str):
        match = re.search(r'[\d.]+', price_data.replace(',', ''))
        if match:
            price = float(match.group())
    
    rating = None
    rating_data = item.get("rating") or item.get("stars")
    if isinstance(rating_data, (int, float)):
        rating = float(rating_data)
    elif isinstance(rating_data, str):
        try:
            rating = float(rating_data.split()[0])
        except:
            pass
    
    review_count = None
    review_data = item.get("reviews_count") or item.get("review_count") or item.get("total_reviews")
    if isinstance(review_data, (int, float)):
        review_count = int(review_data)
    elif isinstance(review_data, str):
        match = re.search(r'[\d,]+', review_data)
        if match:
            review_count = int(match.group().replace(',', ''))
    
    category = (item.get("category") or item.get("product_category") or "general").lower()
    
    return {
        "id": asin or f"B0{random.randint(10000000, 99999999)}",
        "asin": asin,
        "title": title,
        "price": price or random.uniform(10, 200),
        "rating": rating or round(random.uniform(2.0, 5.0), 1),
        "review_count": review_count or random.randint(10, 50000),
        "category": category,
        "source": "openwebninja"
    }


def _mock_product(i: int) -> dict:
    return {
        "id": f"B0{i:08d}",
        "title": f"Product {i}",
        "price": random.uniform(10, 200),
        "rating": round(random.uniform(2.0, 5.0), 1),
        "review_count": random.randint(10, 50000),
        "category": random.choice(["electronics", "office", "accessories", "home"]),
        "asin": f"B0{i:08d}",
        "source": "mock"
    }


def llm_rank_candidates(candidates: list[dict], product: dict) -> list[dict]:
    """Rank candidates by relevance using LLM (or mock)."""
    if OPENAI_AVAILABLE and OPENAI_API_KEY and len(candidates) <= 50:
        try:
            client = OpenAI(api_key=OPENAI_API_KEY.strip())
            
            candidates_text = "\n".join([
                f"{i+1}. {c['title']} - ${c['price']:.2f}, {c['rating']}/5"
                for i, c in enumerate(candidates[:50])
            ])
            
            prompt = f"""Product: {product['title']} (${product.get('price', 0):.2f})

Rank these by relevance (1-10):
{candidates_text}

Return JSON: {{"1": 8.5, "2": 7.2, ...}}"""
            
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "system", "content": "Return only valid JSON."}, {"role": "user", "content": prompt}],
                temperature=0.7,
                response_format={"type": "json_object"}
            )
            
            scores = json.loads(resp.choices[0].message.content)
            
            ranked = []
            for i, c in enumerate(candidates[:50]):
                score = scores.get(str(i+1), 5.0) / 10.0
                ranked.append({**c, "llm_relevance_score": score})
            
            for c in candidates[50:]:
                score = random.uniform(0.3, 0.95)
                if c.get("category") == product.get("category"):
                    score += 0.1
                ranked.append({**c, "llm_relevance_score": min(score, 1.0)})
            
            return sorted(ranked, key=lambda x: x["llm_relevance_score"], reverse=True)
        except Exception as e:
            print(f"   LLM ranking error: {str(e)[:80]}... (using mock)")
    
    # mock
    ranked = []
    for c in candidates:
        score = random.uniform(0.3, 0.95)
        if c.get("category") == product.get("category"):
            score += 0.1
        ranked.append({**c, "llm_relevance_score": min(score, 1.0)})
    return sorted(ranked, key=lambda x: x["llm_relevance_score"], reverse=True)


def llm_evaluate_relevance(candidate: dict, product: dict) -> dict[str, Any]:
    """Evaluate if candidate is relevant using LLM (or mock)."""
    if OPENAI_AVAILABLE and OPENAI_API_KEY:
        try:
            client = OpenAI(api_key=OPENAI_API_KEY.strip())
            
            prompt = f"""Is this a relevant competitor?

Seller: {product['title']} - ${product.get('price', 0):.2f}
Candidate: {candidate['title']} - ${candidate.get('price', 0):.2f}

Return JSON: {{"is_relevant": true/false, "confidence": 0.0-1.0, "reasoning": "..."}}"""
            
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "system", "content": "Return only valid JSON."}, {"role": "user", "content": prompt}],
                temperature=0.7,
                response_format={"type": "json_object"}
            )
            
            result = json.loads(resp.choices[0].message.content)
            
            return {
                "is_relevant": result.get("is_relevant", False),
                "confidence": float(result.get("confidence", 0.5)),
                "reasoning": result.get("reasoning", ""),
                "model": OPENAI_MODEL,
                "tokens_used": resp.usage.total_tokens if resp.usage else 0,
                "raw_response": resp.choices[0].message.content
            }
        except Exception:
            pass  # fall through to mock
    
    # mock
    score = candidate.get("llm_relevance_score", 0.5)
    is_relevant = score > 0.6
    return {
        "is_relevant": is_relevant,
        "confidence": score,
        "reasoning": f"Mock evaluation - score {score:.2f}",
        "model": "mock",
        "tokens_used": 0
    }


def find_amazon_competitor(product: dict, xray: XRay) -> dict | None:
    """Main pipeline: find best competitor for a product."""
    
    # show config
    print("API Config:")
    if OPENWEBNINJA_AVAILABLE:
        print(f"   Amazon API: enabled")
    else:
        print("   Amazon API: mock (set OPENWEBNINJA_API_KEY for real)")
    
    if OPENAI_AVAILABLE and OPENAI_API_KEY:
        valid, msg = validate_openai_key()
        print(f"   LLM ({OPENAI_MODEL}): {'valid' if valid else 'invalid - ' + msg}")
    else:
        print("   LLM: mock (set OPENAI_API_KEY for real)")
    print()
    
    # start run
    run_id = xray.start_run(
        pipeline_type="amazon_competitor_selection",
        name=f"find_competitor_{product['id']}",
        input={"product_id": product["id"], "title": product["title"], "price": product.get("price"), "category": product.get("category")},
        metadata={"source": "amazon_api", "timestamp": datetime.now().isoformat()}
    )
    
    print(f"Finding competitor for: {product['title']}")
    print(f"   Run ID: {run_id}\n")
    
    # step 1: keywords
    print("Step 1: Generating keywords...")
    llm_result = llm_generate_keywords(product)
    keywords = llm_result["keywords"]
    
    xray.record_step(run_id, Step(
        name="keyword_generation",
        input={"title": product["title"], "category": product.get("category")},
        output={"keywords": keywords, "count": len(keywords)},
        config={"model": llm_result["model"]},
        evidence=[Evidence(evidence_type="llm_output", data=llm_result)],
        reasoning=llm_result["reasoning"]
    ))
    print(f"   Generated {len(keywords)} keywords\n")
    
    # step 2: search
    print("Step 2: Searching catalog...")
    candidates = catalog_search(keywords, limit=5000)
    real_count = sum(1 for c in candidates if c.get("source") == "openwebninja")
    
    xray.record_step(run_id, Step(
        name="candidate_search",
        input={"keywords": keywords, "limit": 5000},
        output={"candidates_found": len(candidates), "real_api_results": real_count},
        config={"api": "openwebninja" if real_count > 0 else "mock"},
        reasoning=f"Found {len(candidates)} candidates" + (f" ({real_count} from real API)" if real_count else "")
    ))
    print(f"   Found {len(candidates)} candidates\n")
    
    # step 3: filter + rank
    print("Step 3: Filtering and ranking...")
    
    price_min = product.get("price", 0) * 0.5
    price_max = product.get("price", 100) * 1.5
    min_rating = 3.5
    min_reviews = 100
    target_category = product.get("category")
    
    decisions = []
    filtered = []
    
    for idx, c in enumerate(candidates):
        if c["price"] < price_min or c["price"] > price_max:
            decisions.append(Decision(candidate_id=c["id"], decision_type="rejected", reason="price_out_of_range", metadata={"price": c["price"], "sequence": idx}))
            continue
        if c["rating"] < min_rating:
            decisions.append(Decision(candidate_id=c["id"], decision_type="rejected", reason="rating_below_threshold", metadata={"rating": c["rating"], "sequence": idx}))
            continue
        if c["review_count"] < min_reviews:
            decisions.append(Decision(candidate_id=c["id"], decision_type="rejected", reason="insufficient_reviews", metadata={"review_count": c["review_count"], "sequence": idx}))
            continue
        if target_category and c.get("category") != target_category:
            decisions.append(Decision(candidate_id=c["id"], decision_type="rejected", reason="category_mismatch", metadata={"category": c.get("category"), "sequence": idx}))
            continue
        filtered.append(c)
    
    ranked = llm_rank_candidates(filtered, product)
    
    for rank, c in enumerate(ranked):
        decisions.append(Decision(candidate_id=c["id"], decision_type="accepted", reason="passed_filters", score=c.get("llm_relevance_score"), metadata={"rank": rank + 1, "sequence": len(candidates) - len(ranked) + rank}))
    
    xray.record_step(run_id, Step(
        name="filtering_and_ranking",
        input={"candidate_count": len(candidates)},
        output={"filtered_count": len(filtered), "ranked_count": len(ranked)},
        config={"price_range": [price_min, price_max], "min_rating": min_rating, "min_reviews": min_reviews, "target_category": target_category},
        decisions=decisions,
        reasoning=f"Applied filters. {len(filtered)} passed, ranked by LLM."
    ))
    print(f"   {len(filtered)} passed filters\n")
    
    # step 4: llm evaluation
    print("Step 4: LLM evaluation...")
    
    max_eval = min(20, len(ranked))
    top = ranked[:max_eval]
    
    eval_decisions = []
    relevant = []
    eval_results = []
    
    for idx, c in enumerate(top):
        evaluation = llm_evaluate_relevance(c, product)
        eval_results.append(evaluation)
        
        if evaluation["is_relevant"]:
            eval_decisions.append(Decision(candidate_id=c["id"], decision_type="accepted", reason="llm_confirmed", score=evaluation["confidence"], metadata={"sequence": idx}))
            relevant.append({**c, "evaluation": evaluation})
        else:
            eval_decisions.append(Decision(candidate_id=c["id"], decision_type="rejected", reason="llm_false_positive", score=evaluation["confidence"], metadata={"reasoning": evaluation["reasoning"], "sequence": idx}))
    
    xray.record_step(run_id, Step(
        name="llm_relevance_evaluation",
        input={"candidates_to_evaluate": len(top)},
        output={"relevant_count": len(relevant), "false_positives": len(top) - len(relevant)},
        config={"model": eval_results[0]["model"] if eval_results else "unknown"},
        decisions=eval_decisions,
        evidence=[Evidence(evidence_type="llm_batch_evaluation", data={"total": len(top), "relevant": len(relevant), "sample_evaluations": eval_results[:5]})],
        reasoning=f"LLM evaluated {len(top)} candidates. {len(relevant)} confirmed relevant."
    ))
    print(f"   {len(relevant)} confirmed relevant\n")
    
    if not relevant:
        xray.complete_run(run_id, result={"error": "No relevant competitors found"}, status="failed")
        print("   No relevant competitors found!")
        return None
    
    # step 5: final selection
    print("Step 5: Final selection...")
    
    final_ranked = sorted(relevant, key=lambda x: x.get("llm_relevance_score", 0) * x.get("evaluation", {}).get("confidence", 0.5), reverse=True)
    winner = final_ranked[0]
    
    final_decisions = [
        Decision(
            candidate_id=c["id"],
            decision_type="accepted" if c["id"] == winner["id"] else "rejected",
            reason="selected_as_winner" if c["id"] == winner["id"] else "not_highest_ranked",
            score=c.get("llm_relevance_score", 0) * c.get("evaluation", {}).get("confidence", 0.5),
            metadata={"rank": rank + 1, "sequence": rank}
        )
        for rank, c in enumerate(final_ranked)
    ]
    
    xray.record_step(run_id, Step(
        name="final_selection",
        input={"candidates": len(final_ranked)},
        output={"selected_id": winner["id"], "selected_title": winner["title"]},
        decisions=final_decisions,
        reasoning=f"Selected {winner['title']} as best competitor match."
    ))
    print(f"   Winner: {winner['title']}\n")
    
    # complete
    xray.complete_run(run_id, result={
        "competitor_id": winner["id"],
        "competitor_asin": winner.get("asin"),
        "competitor_title": winner["title"],
        "final_score": winner.get("llm_relevance_score", 0) * winner.get("evaluation", {}).get("confidence", 0.5)
    })
    
    print(f"Run completed: {run_id}")
    print(f"   View: http://localhost:8000/visualize/runs/{run_id}")
    
    return winner


def main():
    import sys
    
    print("=" * 60)
    print("Amazon Competitor Selection Pipeline")
    print("=" * 60)
    print()
    
    xray = XRay(api_url="http://localhost:8000")
    
    if len(sys.argv) > 1:
        # custom product from cli
        seller_product = {
            "id": f"seller-{random.randint(1000, 9999)}",
            "title": " ".join(sys.argv[1:]),
            "price": random.uniform(20, 100),
            "category": "general",
        }
    else:
        # try to get real product from api
        print("Fetching product from Amazon API...")
        seller_product = get_random_product_from_api()
        
        if not seller_product:
            for _ in range(2):
                seller_product = get_random_product_from_api()
                if seller_product:
                    break
        
        if not seller_product:
            print("Failed to fetch product. Check OPENWEBNINJA_API_KEY.")
            return
    
    print(f"Product: {seller_product['title']}")
    print(f"   Price: ${seller_product['price']:.2f}, Category: {seller_product.get('category', 'N/A')}")
    print()
    
    winner = find_amazon_competitor(seller_product, xray)
    
    if winner:
        print("\n" + "-" * 60)
        print(f"Best Competitor: {winner['title']}")
        print(f"   ASIN: {winner.get('asin', winner['id'])}")
        print(f"   Price: ${winner['price']:.2f}")
        print(f"   Rating: {winner['rating']}/5.0")
        print()
    
    xray.close()


if __name__ == "__main__":
    main()
