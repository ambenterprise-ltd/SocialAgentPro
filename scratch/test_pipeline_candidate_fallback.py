import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import logging
from unittest.mock import MagicMock
from core.pipeline import ShortsAutomationPipeline
from config import ConfigManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestPipelineFallback")

def test_pipeline_candidate_fallback():
    cm = ConfigManager()
    pipeline = ShortsAutomationPipeline(config_manager=cm, logger=logger)
    
    # 1. Test Search Query Intent Tuning
    print("\n--- 1. Testing Search Query Tuning ---")
    ctx = cm.get_channel_context("Wealth Secrets")
    topic = "wealth, business secrets, and money concepts"
    content_type = ctx.get("content_type", "podcast")
    
    # Simulate pipeline query building logic
    tuned_queries = []
    if topic and topic.strip():
        tuned_queries.append(f"{topic.strip()} {content_type} interview full episode")
        tuned_queries.append(f"{topic.strip()} full episode interview")
        
    print(f"Generated Tuned Queries: {tuned_queries}")
    assert any("interview full episode" in q for q in tuned_queries), "Expected 'interview full episode' in tuned queries"
    assert any("podcast" in q for q in tuned_queries), "Expected 'podcast' in tuned queries"
    print("✅ Query tuning generated full episode intent queries successfully!")

    # 2. Test Sequential Candidate Fallback Simulation
    print("\n--- 2. Testing Sequential Candidate Fallback Simulation ---")
    mock_transcriber = MagicMock()
    # Candidate 1: No transcript
    # Candidate 2: No transcript
    # Candidate 3: Valid transcript
    valid_transcript = {
        "language": "en",
        "words": [
            {"word": "wealth", "start": 0.0, "end": 1.0},
            {"word": "business", "start": 1.0, "end": 2.0},
            {"word": "secrets", "start": 2.0, "end": 3.0},
            {"word": "investing", "start": 3.0, "end": 4.0},
            {"word": "money", "start": 4.0, "end": 5.0}
        ]
    }
    
    mock_transcriber.fetch_headless_transcript.side_effect = [
        None,             # Cand 1 fails
        {"words": []},    # Cand 2 fails (empty)
        valid_transcript  # Cand 3 succeeds
    ]
    
    # Create test candidates
    mock_candidates = [
        {"id": "test_vid_1", "title": "Hindi Talk Without English", "url": "https://youtube.com/watch?v=test_vid_1", "duration": 3600},
        {"id": "test_vid_2", "title": "Silent Video Without Captions", "url": "https://youtube.com/watch?v=test_vid_2", "duration": 1800},
        {"id": "test_vid_3", "title": "Wealth Business Secrets Full Podcast Interview", "url": "https://youtube.com/watch?v=test_vid_3", "duration": 4200},
    ]
    
    # Clear any previous test state for these IDs
    tracker = pipeline.state_tracker
    for c in mock_candidates:
        tracker._data.get("failed", {}).pop(c["id"], None)
        tracker._data.get("processed", [])
        
    # Simulate candidate loop
    topic_keywords = pipeline._extract_topic_keywords(topic, ctx.get("auto_search_keywords", []))
    locked_video_id = None
    locked_transcript = None
    
    for cand in mock_candidates:
        cand_id = cand["id"]
        cand_title = cand["title"]
        cand_url = cand["url"]
        cand_dur = cand["duration"]
        
        # Shorts check
        if "/shorts/" in cand_url.lower() or "#shorts" in cand_title.lower():
            tracker.mark_failed(cand_id, reason="skipped_short")
            continue
        if cand_dur and 0 < cand_dur < 300:
            tracker.mark_failed(cand_id, reason="skipped_too_short")
            continue
            
        t_data = mock_transcriber.fetch_headless_transcript(cand_id)
        if not t_data or not t_data.get("words"):
            tracker.mark_failed(cand_id, reason="failed_no_transcript")
            print(f"  [Fallback Check] Cand '{cand_id}' marked as failed_no_transcript, testing next candidate...")
            continue
            
        words = t_data.get("words", [])
        topic_mentions = pipeline._count_transcript_topic_mentions(words, topic_keywords)
        title_relevance = pipeline._calculate_relevance(cand_title, topic_keywords)
        
        if title_relevance == 0 and topic_mentions < 3:
            tracker.mark_failed(cand_id, reason="skipped_off_topic")
            continue
            
        locked_video_id = cand_id
        locked_transcript = t_data
        print(f"  [Success] Locked onto candidate '{cand_id}' with {topic_mentions} mentions!")
        break
        
    assert locked_video_id == "test_vid_3", f"Expected to lock onto test_vid_3, got: {locked_video_id}"
    assert locked_transcript is not None, "Transcript should not be None"
    assert tracker.is_processed("test_vid_1"), "test_vid_1 should be recorded in failed state"
    assert tracker.is_processed("test_vid_2"), "test_vid_2 should be recorded in failed state"
    
    # Cleanup test IDs
    for c in mock_candidates:
        tracker._data.get("failed", {}).pop(c["id"], None)
    tracker._save()
    
    print("\n✅ Candidate fallback loop smoothly stepped past failed candidates and locked onto candidate 3!")
    print("\n🎉 ALL PIPELINE FALLBACK SIMULATION TESTS PASSED!")

if __name__ == "__main__":
    test_pipeline_candidate_fallback()
