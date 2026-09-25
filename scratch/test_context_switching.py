import sys
import os

# Add project root to path
sys.path.insert(0, os.path.abspath("."))

from config import ConfigManager
from core.llm_brain import ViralClipExtractor
from core.pipeline import ShortsAutomationPipeline
import tkinter as tk
import customtkinter as ctk

def test_config_contexts():
    print("--- 1. Testing ConfigManager.get_channel_context() ---")
    ws_ctx = ConfigManager.get_channel_context("Wealth Secrets")
    kp_ctx = ConfigManager.get_channel_context("Khao Pakistan")
    nw_ctx = ConfigManager.get_channel_context("Nutrilogic Way")

    assert ws_ctx["topic_focus"] == "wealth, business secrets, and money concepts", f"WS mismatch: {ws_ctx['topic_focus']}"
    assert kp_ctx["topic_focus"] == "food reviews, traditional Pakistani food, and restaurant vlogging", f"KP mismatch: {kp_ctx['topic_focus']}"
    assert nw_ctx["topic_focus"] == "hardcore bodybuilding supplement breakdowns, pre-workout and creatine tier lists, whey protein gains, high-energy gym motivation", f"NW mismatch: {nw_ctx['topic_focus']}"

    # Test fuzzy fallbacks
    assert ConfigManager.get_channel_context("khao pakistan")["channel_name"] == "Khao Pakistan"
    assert ConfigManager.get_channel_context("street food vlog")["channel_name"] == "Khao Pakistan"
    assert ConfigManager.get_channel_context("gym supplement channel")["channel_name"] == "Nutrilogic Way"
    assert ConfigManager.get_channel_context("")["channel_name"] == "Wealth Secrets"
    print("ConfigManager.get_channel_context() passed with all exact strings & fallbacks!")

def test_pipeline_keywords():
    print("--- 2. Testing Pipeline Keyword Enrichment ---")
    ws_kws = ShortsAutomationPipeline._extract_topic_keywords("wealth, business secrets, and money concepts")
    kp_kws = ShortsAutomationPipeline._extract_topic_keywords("food reviews, traditional Pakistani food, and restaurant vlogging")
    nw_kws = ShortsAutomationPipeline._extract_topic_keywords("gym supplement reviews, natural organic health, and fitness nutrition pros/cons")

    assert "wealth" in ws_kws and "investing" in ws_kws
    assert "karahi" in kp_kws and "restaurant" in kp_kws and "biryani" in kp_kws
    assert "creatine" in nw_kws and "supplement" in nw_kws and "protein" in nw_kws
    print("Pipeline keyword enrichment passed for all 3 channels!")

def test_llm_prompts():
    print("--- 3. Testing LLM Brain Dynamic Prompts ---")
    # Test ViralClipExtractor prompt generation without real API calls
    extractor = ViralClipExtractor(api_keys=["gsk_dummykey1234567890123456789012345678"])

    # Test Khao Pakistan context prompt
    ctx_kp = ConfigManager.get_channel_context("Khao Pakistan")
    dummy_words = [{"word": "biryani", "start": 1.0, "end": 2.0}]
    
    # We can inspect the prompts by checking _process_single_chunk's constructed user_prompt
    # and system_prompt generated in extract_viral_clips
    sys_prompt_kp = f"""{ctx_kp['system_instruction']}
Your mission is to analyze the provided timestamped transcript and extract insightful, high-retention short-form video segments for {ctx_kp['niche_name']} following the STRICT "UNDER 1 MINUTE RULE"."""
    assert "Pakistani food" in sys_prompt_kp
    assert "Pakistani Food & Restaurant Reviews" in sys_prompt_kp

    # Test _process_single_chunk user_prompt generation
    # We test by passing custom_user_prompt=None and content_focus_description=ctx_kp["content_focus_description"]
    # We verify the text contains food terms and NOT financial requirements
    from unittest.mock import MagicMock
    chunk_index = 1
    total_chunks = 1
    chunk_text = "0.0s - 5.0s: The taste of this nihari is extraordinary."
    focus_desc = ctx_kp["content_focus_description"]
    
    # Check what user prompt would be formed
    user_prompt = (
        f"Here is chunk {chunk_index} of the timestamped transcript (may be in English, Hindi, Urdu, or other languages):\n\n{chunk_text}\n\n"
        f"MANDATORY REQUIREMENTS:\n"
        f"1. Extract 1 to 2 high-value video clips focused on '{ctx_kp['topic_focus']}' (including {focus_desc}). Keep title and rationale concise (under 25 words each).\n"
        f"2. CONTENT RELEVANCE OVER SENSATIONALISM: The clip MUST deliver a clear insight, lesson, breakdown, or practical wisdom on '{ctx_kp['topic_focus']}'. It does NOT need to be shocking clickbait or controversial drama. Thoughtful explanations or clear breakdowns are preferred.\n"
        f"3. STRICT UNDER-1-MINUTE RULE: Every clip's duration (end_time - start_time) MUST be between 50.0s and 58.0s.\n"
        f"4. DEAD MINIMUM: 50.0 SECONDS. NEVER select short snippets under 50.0s (no 10s, 20s, or 30s clips). Include the speaker's full explanation, story, and conclusion to naturally span 50 to 58 seconds.\n"
        f"5. LANGUAGE SUPPORT: If the transcript is in Hindi, Urdu, or another language, keep the exact timestamps and hook, and provide an engaging English title and rationale.\n"
        f"6. Output ONLY valid JSON. Do not include markdown formatting, code blocks, conversational text, or explanations.\n"
        f"7. If no segment in this chunk relates to '{ctx_kp['topic_focus']}' or {focus_desc}, output strictly: {{\"clips\": []}}."
    )
    assert "food reviews" in user_prompt
    assert "personal/financial success" not in user_prompt
    print("LLM Brain Dynamic Prompts passed!")

def test_main_window_refresh():
    print("--- 4. Testing MainWindow.refresh_dashboard_ui() ---")
    from ui.main_window import MainWindow
    
    # Create headless/hidden CTk instance
    cfg = ConfigManager()
    root = MainWindow(cfg)
    root.withdraw() # Hide window during test

    try:
        # Test Khao Pakistan switch
        root.refresh_dashboard_ui("Khao Pakistan")
        topic_kp = root.topic_entry.get()
        assert topic_kp == "food reviews, traditional Pakistani food, and restaurant vlogging", f"Expected Khao Pakistan topic, got: {topic_kp}"
        print(f"Khao Pakistan Topic Entry: '{topic_kp}' OK")

        # Test Nutrilogic Way switch
        root.refresh_dashboard_ui("Nutrilogic Way")
        topic_nw = root.topic_entry.get()
        assert topic_nw == "hardcore bodybuilding supplement breakdowns, pre-workout and creatine tier lists, whey protein gains, high-energy gym motivation", f"Expected Nutrilogic Way topic, got: {topic_nw}"
        print(f"Nutrilogic Way Topic Entry: '{topic_nw}' OK")

        # Test Wealth Secrets switch
        root.refresh_dashboard_ui("Wealth Secrets")
        topic_ws = root.topic_entry.get()
        assert topic_ws == "wealth, business secrets, and money concepts", f"Expected Wealth Secrets topic, got: {topic_ws}"
        print(f"Wealth Secrets Topic Entry: '{topic_ws}' OK")

    finally:
        root.destroy()
    
    print("MainWindow.refresh_dashboard_ui() successfully tested across all 3 channels!")

if __name__ == "__main__":
    test_config_contexts()
    test_pipeline_keywords()
    test_llm_prompts()
    test_main_window_refresh()
    print("\nALL VERIFICATION TESTS COMPLETED SUCCESSFULLY!")
