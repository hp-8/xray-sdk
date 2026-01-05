"""
Demo: competitor selection pipeline instrumented with X-Ray

Usage: python -m examples.competitor_selection
"""

import random
from datetime import datetime
from xray import XRay, Step, Decision


# mock catalog
PRODUCTS = [
    {
        "id": f"prod-{i}",
        "title": f"Product {i}",
        "price": random.randint(20, 200),
        "rating": round(random.uniform(2.0, 5.0), 1),
        "category": random.choice(["electronics", "accessories", "office"]),
        "relevance_score": round(random.uniform(0.3, 0.95), 2)
    }
    for i in range(5000)
]


def generate_keywords(product: dict) -> list[str]:
    words = product["title"].lower().split()
    return words + ["competitor", "alternative", product.get("category", "general")]


def search_products(keywords: list[str], limit: int = 5000) -> list[dict]:
    # in reality this would hit elasticsearch or similar
    return PRODUCTS[:limit]


def calc_relevance(candidate: dict, original: dict) -> float:
    score = candidate["relevance_score"]
    if candidate.get("category") == original.get("category"):
        score += 0.1
    price_diff = abs(candidate["price"] - original.get("price", 50)) / 100
    score -= price_diff * 0.1
    return max(0, min(1, score))


def find_competitor(product: dict, xray: XRay) -> dict | None:
    run_id = xray.start_run(
        pipeline_type="competitor_selection",
        name=f"find_competitor_{product['id']}",
        input={"product_id": product["id"], "title": product["title"], "price": product.get("price"), "category": product.get("category")},
        metadata={"source": "demo", "timestamp": datetime.now().isoformat()}
    )
    print(f"Started run: {run_id}")
    
    # step 1: keywords
    keywords = generate_keywords(product)
    xray.record_step(run_id, Step(
        name="keyword_generation",
        input={"title": product["title"], "category": product.get("category")},
        output={"keywords": keywords, "count": len(keywords)},
        reasoning=f"Generated {len(keywords)} keywords from title and category"
    ))
    print(f"  Step 1: Generated {len(keywords)} keywords")
    
    # step 2: search
    candidates = search_products(keywords, limit=5000)
    xray.record_step(run_id, Step(
        name="candidate_search",
        input={"keywords": keywords, "limit": 5000},
        output={"count": len(candidates)},
        config={"limit": 5000, "source": "mock_catalog"},
        reasoning=f"Found {len(candidates)} candidates"
    ))
    print(f"  Step 2: Found {len(candidates)} candidates")
    
    # step 3: filter
    price_cap = product.get("price", 50) * 1.5
    min_rating = 3.5
    target_cat = product.get("category")
    
    passed = []
    decisions = []
    
    for idx, c in enumerate(candidates):
        if c["price"] > price_cap:
            decisions.append(Decision(
                candidate_id=c["id"], decision_type="rejected", reason="price_exceeds_threshold",
                metadata={"price": c["price"], "threshold": price_cap, "sequence": idx}
            ))
            continue
        
        if c["rating"] < min_rating:
            decisions.append(Decision(
                candidate_id=c["id"], decision_type="rejected", reason="rating_below_minimum",
                metadata={"rating": c["rating"], "min_rating": min_rating, "sequence": idx}
            ))
            continue
        
        if target_cat and c.get("category") != target_cat:
            decisions.append(Decision(
                candidate_id=c["id"], decision_type="rejected", reason="category_mismatch",
                metadata={"candidate_category": c.get("category"), "target_category": target_cat, "sequence": idx}
            ))
            continue
        
        relevance = calc_relevance(c, product)
        decisions.append(Decision(
            candidate_id=c["id"], decision_type="accepted", reason="passed_all_filters",
            score=relevance, metadata={"sequence": idx}
        ))
        passed.append({**c, "relevance": relevance})
    
    xray.record_step(run_id, Step(
        name="filtering",
        input={"candidate_count": len(candidates)},
        output={"passed_count": len(passed)},
        config={"price_threshold": price_cap, "min_rating": min_rating, "target_category": target_cat},
        decisions=decisions,
        reasoning=f"Applied filters: price<${price_cap:.0f}, rating>={min_rating}, category={target_cat}. {len(passed)} passed."
    ))
    print(f"  Step 3: {len(passed)} candidates passed filters")
    
    if not passed:
        xray.complete_run(run_id, result={"error": "No candidates passed filters"}, status="failed")
        print("  No candidates passed!")
        return None
    
    # step 4: rank and select
    ranked = sorted(passed, key=lambda x: x["relevance"], reverse=True)
    winner = ranked[0]
    
    final_decisions = [
        Decision(
            candidate_id=c["id"],
            decision_type="accepted" if c["id"] == winner["id"] else "rejected",
            reason="selected_as_winner" if c["id"] == winner["id"] else "not_highest_ranked",
            score=c["relevance"],
            metadata={"rank": rank + 1, "sequence": rank}
        )
        for rank, c in enumerate(ranked)
    ]
    
    xray.record_step(run_id, Step(
        name="final_selection",
        input={"candidates": len(ranked)},
        output={"selected_id": winner["id"], "selected_title": winner["title"], "score": winner["relevance"]},
        decisions=final_decisions,
        reasoning=f"Selected {winner['title']} (score: {winner['relevance']:.2f})"
    ))
    print(f"  Step 4: Winner: {winner['title']} (score: {winner['relevance']:.2f})")
    
    xray.complete_run(run_id, result={
        "competitor_id": winner["id"], "competitor_title": winner["title"], "relevance_score": winner["relevance"]
    })
    print(f"Completed run: {run_id}")
    
    return winner


def main():
    print("=" * 60)
    print("X-Ray Demo: Competitor Selection Pipeline")
    print("=" * 60)
    print()
    
    xray = XRay(api_url="http://localhost:8000")
    
    product = {
        "id": "my-product-123",
        "title": "Adjustable Laptop Stand",
        "price": 45,
        "category": "office"
    }
    
    print(f"Finding competitor for: {product['title']}")
    print(f"  Price: ${product['price']}, Category: {product['category']}")
    print()
    
    winner = find_competitor(product, xray)
    
    print()
    print("-" * 60)
    if winner:
        print(f"Best competitor: {winner['title']}")
        print(f"  ID: {winner['id']}")
        print(f"  Price: ${winner['price']}")
        print(f"  Rating: {winner['rating']}")
        print(f"  Relevance: {winner['relevance']:.2f}")
    else:
        print("No competitor found!")
    
    print()
    print("View the run at: http://localhost:8000/v1/runs")
    print()
    
    xray.close()


if __name__ == "__main__":
    main()
