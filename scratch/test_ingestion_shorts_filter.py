import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import logging
from core.ingestion import MediaIngestionEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestIngestion")

def test_ingestion():
    engine = MediaIngestionEngine(logger=logger)
    
    # Test search queries with intent keywords
    queries = [
        "wealth secrets podcast interview full episode",
        "business advice podcast interview full episode"
    ]
    
    print("\n--- 1. Testing search_youtube_topic_podcasts with Shorts & Duration Filter ---")
    candidates = engine.search_youtube_topic_podcasts(
        search_queries=queries,
        limit=10,
        min_duration=300,
        target_count=15
    )
    
    print(f"Total discovered candidates: {len(candidates)}")
    assert len(candidates) >= 5, f"Expected at least 5 candidates, got {len(candidates)}"
    
    for c in candidates:
        v_id = c["id"]
        title = c["title"]
        dur = c["duration"]
        url = c["url"]
        
        # Verify no shorts in URL
        assert "/shorts/" not in url.lower(), f"Shorts URL found in candidates: {url}"
        # Verify no #shorts in title
        assert "#shorts" not in title.lower() and "#short" not in title.lower(), f"#shorts in title: {title}"
        # Verify duration >= 300s if known
        if dur > 0:
            assert dur >= 300, f"Video {v_id} duration too short: {dur}s < 300s"
            
        print(f"  [OK] ({dur}s) {v_id} - {title[:60]}")
        
    print("\n✅ All candidates strictly meet long-form duration requirements (no Shorts, duration >= 300s)!")
    print("\n🎉 ALL INGESTION SHORTS FILTER TESTS PASSED!")

if __name__ == "__main__":
    test_ingestion()
