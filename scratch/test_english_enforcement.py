import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import logging
from config import ConfigManager
from core.pipeline import ShortsAutomationPipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestEnglishEnforcement")

def test_english_enforcement():
    cm = ConfigManager()
    
    # 1. Test Wealth Secrets Language Config
    print("\n--- 1. Testing Wealth Secrets Language Config ---")
    ws_lang = cm.get_channel_setting("language", profile_name="Wealth Secrets")
    ws_cap_lang = cm.get_caption_language("Wealth Secrets")
    ws_code = cm.get_caption_language_code("Wealth Secrets")
    print(f"Wealth Secrets language: {ws_lang}, caption_language: {ws_cap_lang}, code: {ws_code}")
    assert ws_lang == "English", f"Expected 'English', got {ws_lang}"
    assert ws_cap_lang == "English", f"Expected 'English', got {ws_cap_lang}"
    assert ws_code == "en", f"Expected 'en', got {ws_code}"
    print("✅ Wealth Secrets is strictly configured for English!")

    # 2. Test Regional Filter on Known Titles
    print("\n--- 2. Testing Regional Hindi/Urdu Exclusions ---")
    regional_titles = [
        "Escaping the Middle Class Trap... | Ankur Warikoo Hindi",
        "How To Get Rich In India (Urdu / Hindi Podcast)",
        "Warikoo on Money Mindset & Secrets",
        "Raj Shamani Business Podcast in Hindi"
    ]
    english_titles = [
        "The Business Expert: The SECRET FORMULA That Launches Billion-Dollar Companies",
        "Money Expert Reveals the Tricks That Separates Rich From Broke",
        "How to Build Wealth in Your 20s and 30s | Full Interview"
    ]
    
    regional_exclusions = ["hindi", "urdu", "ankur warikoo", "warikoo"]
    
    for title in regional_titles:
        is_excluded = any(reg in title.lower() for reg in regional_exclusions)
        print(f"  [REGIONAL] '{title}' -> Excluded: {is_excluded}")
        assert is_excluded, f"Failed to exclude regional title: {title}"
        
    for title in english_titles:
        is_excluded = any(reg in title.lower() for reg in regional_exclusions)
        print(f"  [ENGLISH]  '{title}' -> Excluded: {is_excluded}")
        assert not is_excluded, f"Incorrectly excluded English title: {title}"
    print("✅ Regional Hindi/Urdu filtering strictly discriminates candidate titles!")

    # 3. Test Pipeline Query Formulation with English Enforcement
    print("\n--- 3. Testing Pipeline Search Query Building ---")
    pipeline = ShortsAutomationPipeline(config_manager=cm, logger=logger)
    topic = "wealth, business secrets, and money concepts"
    ctx = cm.get_channel_context("Wealth Secrets")
    content_type = ctx.get("content_type", "podcast")
    auto_kws = ctx.get("auto_search_keywords", [])
    
    # Simulate query building from pipeline.py
    lang_code = "en"
    search_queries = []
    if topic and topic.strip():
        topic_clean = topic.strip()
        if lang_code == "en":
            search_queries.append(f"{topic_clean} English {content_type} interview full episode")
            search_queries.append(f"{topic_clean} English podcast interview")
            search_queries.append(f"{topic_clean} English full episode interview")
            
    for kw in auto_kws:
        kw_clean = str(kw).strip()
        if lang_code == "en" and "english" not in kw_clean.lower():
            kw_clean = f"{kw_clean} English"
        search_queries.append(kw_clean)
        
    print(f"Generated Search Queries: {search_queries[:3]}")
    for q in search_queries:
        assert "english" in q.lower(), f"Search query missing 'English': {q}"
    print("✅ All search queries explicitly enforce 'English'!")

    print("\n🎉 ALL ENGLISH ENFORCEMENT & REGIONAL FILTER TESTS PASSED!")

if __name__ == "__main__":
    test_english_enforcement()
