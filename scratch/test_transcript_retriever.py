import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import logging
from core.transcriber import WhisperTranscriber

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestTranscriber")

def test_transcriber():
    wt = WhisperTranscriber(logger=logger)
    
    # 1. Known video with auto-generated English captions
    eng_id = "F__SF8b0fbY"
    print(f"Testing English video: {eng_id}...")
    res1 = wt._fetch_youtube_native(eng_id, target_language="en")
    assert res1 is not None, "Failed to retrieve English transcript for F__SF8b0fbY"
    assert len(res1.get("words", [])) > 100, "Transcript words too short"
    print(f"✅ Success: Fetched {len(res1['words'])} words for {eng_id}")

    # 2. Known video with only Hindi captions, requesting English
    hi_id = "UlY1YCF55Y4"
    print(f"Testing Hindi-only video for English request: {hi_id}...")
    res2 = wt._fetch_youtube_native(hi_id, target_language="en")
    assert res2 is None, f"Expected None for Hindi-only video {hi_id}, got: {res2}"
    print(f"✅ Success: Correctly returned None for Hindi-only video {hi_id}")

    # 3. Invalid video ID
    inv_id = "NON_EXISTENT_VIDEO_XYZ_123"
    print(f"Testing invalid video ID: {inv_id}...")
    res3 = wt._fetch_youtube_native(inv_id, target_language="en")
    assert res3 is None, f"Expected None for invalid video ID, got: {res3}"
    print("✅ Success: Correctly handled invalid video ID without crashing")

    print("\n🎉 ALL TRANSCRIPT RETRIEVER TESTS PASSED!")

if __name__ == "__main__":
    test_transcriber()
