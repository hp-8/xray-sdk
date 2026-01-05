"""
Example: Competitor Selection Pipeline with X-Ray SDK

This example demonstrates how to use the X-Ray SDK to debug a
competitor selection pipeline for e-commerce products.

The pipeline:
1. Generate search keywords from product title
2. Search for candidate competitor products
3. Filter candidates by price, rating, and category
4. Rank remaining candidates by relevance
5. Select the best competitor match

Run with:
    python -m examples.competitor_selection
"""

import random
from datetime import datetime

from xray import XRay, Step, Decision


# Simulated product catalog
MOCK_PRODUCTS = [
    {"id": f"prod-{i}", "title": f"Product {i}", "price": random.randint(20, 200), 
     "rating": round(random.uniform(2.0, 5.0), 1), "category": random.choice(["electronics", "accessories", "office"]),
     "relevance_score": round(random.uniform(0.3, 0.95), 2)}
    for i in range(5000)
]


def generate_keywords(product: dict) -> list[str]:
    """Simulate keyword generation (would be LLM in production)."""
    title_words = product["title"].lower().split()
    return title_words + ["competitor", "alternative", product.get("category", "general")]


def search_products(keywords: list[str], limit: int = 5000) -> list[dict]:
    """Simulate product search (would be catalog API in production)."""
    # Return mock products as "search results"
    return MOCK_PRODUCTS[:limit]


def calculate_relevance(candidate: dict, original: dict) -> float:
    """Calculate relevance score between candidate and original product."""
    score = candidate["relevance_score"]
    
    # Boost if same category
    if candidate.get("category") == original.get("category"):
        score += 0.1
    
    # Penalize if price is very different
    price_diff = abs(candidate["price"] - original.get("price", 50)) / 100
    score -= price_diff * 0.1
    
    return min(max(score, 0), 1)  # Clamp to [0, 1]


def find_competitor(product: dict, xray: XRay) -> dict | None:
    """
    Find the best competitor product using X-Ray for debugging.
    
    This demonstrates:
    - Recording pipeline runs
    - Capturing decision events at each step
    - Recording reasoning for debugging
    """
    
    # Start the X-Ray run
    run_id = xray.start_run(
        pipeline_type="competitor_selection",
        name=f"find_competitor_{product['id']}",
        input={
            "product_id": product["id"],
            "title": product["title"],
            "price": product.get("price"),
            "category": product.get("category")
        },
        metadata={"source": "demo", "timestamp": datetime.now().isoformat()}
    )
    
    print(f"Started run: {run_id}")
    
    # Step 1: Keyword Generation
    keywords = generate_keywords(product)
    
    xray.record_step(run_id, Step(
        name="keyword_generation",
        input={"title": product["title"], "category": product.get("category")},
        output={"keywords": keywords, "count": len(keywords)},
        reasoning=f"Generated {len(keywords)} keywords from product title and category"
    ))
    print(f"  Step 1: Generated {len(keywords)} keywords")
    
    # Step 2: Candidate Search
    candidates = search_products(keywords, limit=5000)
    
    xray.record_step(run_id, Step(
        name="candidate_search",
        input={"keywords": keywords, "limit": 5000},
        output={"count": len(candidates)},
        config={"limit": 5000, "source": "mock_catalog"},
        reasoning=f"Retrieved {len(candidates)} candidate products from catalog"
    ))
    print(f"  Step 2: Found {len(candidates)} candidates")
    
    # Step 3: Filtering - This is where we capture detailed decisions
    price_threshold = product.get("price", 50) * 1.5  # 50% above original
    min_rating = 3.5
    target_category = product.get("category")
    
    passed = []
    decisions = []
    
    for idx, c in enumerate(candidates):
        # Check price
        if c["price"] > price_threshold:
            decisions.append(Decision(
                candidate_id=c["id"],
                decision_type="rejected",
                reason="price_exceeds_threshold",
                score=None,
                metadata={
                    "price": c["price"],
                    "threshold": price_threshold,
                    "sequence": idx
                }
            ))
            continue
        
        # Check rating
        if c["rating"] < min_rating:
            decisions.append(Decision(
                candidate_id=c["id"],
                decision_type="rejected",
                reason="rating_below_minimum",
                score=None,
                metadata={
                    "rating": c["rating"],
                    "min_rating": min_rating,
                    "sequence": idx
                }
            ))
            continue
        
        # Check category
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
        
        # Passed all filters
        relevance = calculate_relevance(c, product)
        decisions.append(Decision(
            candidate_id=c["id"],
            decision_type="accepted",
            reason="passed_all_filters",
            score=relevance,
            metadata={"sequence": idx}
        ))
        passed.append({**c, "relevance": relevance})
    
    xray.record_step(run_id, Step(
        name="filtering",
        input={"candidate_count": len(candidates)},
        output={"passed_count": len(passed)},
        config={
            "price_threshold": price_threshold,
            "min_rating": min_rating,
            "target_category": target_category
        },
        decisions=decisions,  # SDK will sample if too many
        reasoning=f"Applied price cap (${price_threshold:.0f}), minimum rating ({min_rating}), and category filter ({target_category}). {len(passed)} candidates passed."
    ))
    print(f"  Step 3: {len(passed)} candidates passed filters")
    
    if not passed:
        # No candidates passed - complete with failure
        xray.complete_run(run_id, result={"error": "No candidates passed filters"}, status="failed")
        print("  No candidates passed filters!")
        return None
    
    # Step 4: Ranking and Selection
    # Sort by relevance score
    ranked = sorted(passed, key=lambda x: x["relevance"], reverse=True)
    winner = ranked[0]
    
    # Record final selection decisions
    final_decisions = []
    for rank, c in enumerate(ranked):
        final_decisions.append(Decision(
            candidate_id=c["id"],
            decision_type="accepted" if c["id"] == winner["id"] else "rejected",
            reason="selected_as_winner" if c["id"] == winner["id"] else "not_highest_ranked",
            score=c["relevance"],
            metadata={"rank": rank + 1, "sequence": rank}
        ))
    
    xray.record_step(run_id, Step(
        name="final_selection",
        input={"candidates": len(ranked)},
        output={
            "selected_id": winner["id"],
            "selected_title": winner["title"],
            "score": winner["relevance"]
        },
        decisions=final_decisions,
        reasoning=f"Selected {winner['title']} (score: {winner['relevance']:.2f}) as best competitor match"
    ))
    print(f"  Step 4: Selected winner: {winner['title']} (score: {winner['relevance']:.2f})")
    
    # Complete the run
    xray.complete_run(run_id, result={
        "competitor_id": winner["id"],
        "competitor_title": winner["title"],
        "relevance_score": winner["relevance"]
    })
    print(f"Completed run: {run_id}")
    
    return winner


def main():
    """Run the demo."""
    print("=" * 60)
    print("X-Ray SDK Demo: Competitor Selection Pipeline")
    print("=" * 60)
    print()
    
    # Initialize X-Ray client
    xray = XRay(api_url="http://localhost:8000")
    
    # Create a sample product to find competitors for
    product = {
        "id": "my-product-123",
        "title": "Adjustable Laptop Stand",
        "price": 45,
        "category": "office"
    }
    
    print(f"Finding competitor for: {product['title']}")
    print(f"  Price: ${product['price']}")
    print(f"  Category: {product['category']}")
    print()
    
    # Run the pipeline
    winner = find_competitor(product, xray)
    
    print()
    print("-" * 60)
    if winner:
        print(f"Best competitor found: {winner['title']}")
        print(f"  ID: {winner['id']}")
        print(f"  Price: ${winner['price']}")
        print(f"  Rating: {winner['rating']}")
        print(f"  Relevance: {winner['relevance']:.2f}")
    else:
        print("No competitor found!")
    
    print()
    print("To view the run data, check the X-Ray API:")
    print("  GET http://localhost:8000/v1/runs")
    print()
    
    xray.close()


if __name__ == "__main__":
    main()

