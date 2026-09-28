import os
import sys
import glob
import json
import time
import logging

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import shutil
import re
from typing import Dict, Any, List, Optional, Callable, Tuple, Set

from config import ConfigManager
from core.state_tracker import VideoStateTracker
from core.ingestion import MediaIngestionEngine
from core.transcriber import WhisperTranscriber
from core.llm_brain import ViralClipExtractor
from core.composer import FFmpegComposer
from core.publisher import MultiPlatformPublisher, YouTubePublisher, FacebookPublisher, InstagramPublisher


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ShortsAutomationPipeline:
    """
    Master Pipeline Orchestrator for AMB Enterprise.
    Refactored to Ultra-Fast Headless Architecture:
    1. Persistent State Tracking (processed_videos.json)
    2. Ultra-Fast Flat Channel Scraping & Topic-Targeted Discovery
    3. Headless Transcript Fetching (zero full-video downloads, zero Whisper overhead)
    4. Groq LLM Topic Selection (50s-58s retention window)
    5. Partial Video Streaming & Download via FFmpeg (only 15-25MB)
    6. 9:16 Template & Subtitle Compositing + Instant Raw Chunk Deletion
    """

    def __init__(self, config_manager: ConfigManager, logger: Optional[logging.Logger] = None):
        self.config_manager = config_manager
        self.logger = logger or logging.getLogger("AMBEnterprise")
        self.state_tracker = VideoStateTracker(
            db_path=os.path.join(BASE_DIR, "processed_videos.json"),
            logger=self.logger
        )

    @staticmethod
    def _extract_topic_keywords(topic_focus: str, auto_search_keywords: Optional[List[str]] = None) -> Set[str]:
        """
        Derives a rich set of topic keywords from topic_focus and auto_search_keywords,
        enriched with domain-specific concepts across wealth, food vlogging, and fitness nutrition.
        """
        stopwords = {
            "and", "the", "for", "with", "how", "what", "clip", "show", "podcast", "video", "youtube",
            "from", "that", "this", "all", "are", "but", "not", "you", "they", "our", "one", "out",
            "about", "get", "who", "whom", "into", "concepts", "its", "has", "have", "been", "was",
            "why", "when", "where", "can", "will", "would", "could", "should", "your", "them", "some"
        }

        topic_lower = (topic_focus or "").lower()
        base_lexicon: Set[str] = set()

        # Channel & Niche-specific enrichment lexicons
        food_lexicon = {
            "food", "recipe", "recipes", "dish", "dishes", "restaurant", "restaurants", "desi",
            "taste", "review", "street", "pakistani", "karahi", "biryani", "nihari", "bbq",
            "spicy", "flavor", "cooking", "chef", "eat", "eating", "foodie", "kebab", "roti",
            "vlog", "vlogging", "cafe", "dhaba", "haleem", "chai", "paratha", "khao"
        }
        fitness_lexicon = {
            "supplement", "supplements", "gym", "bodybuilding", "workout", "lifting", "lift",
            "creatine", "preworkout", "pre-workout", "whey", "protein", "gains", "muscle",
            "mass", "hypertrophy", "shaker", "barbell", "dumbbell", "bench", "deadlift", "squat",
            "pump", "tren", "intensity", "motivation", "energy", "tier", "transformation",
            "raw", "overload", "hardcore", "beast", "anabolic", "nutrilogic"
        }
        wealth_lexicon = {
            "wealth", "wealthy", "rich", "money", "millionaire", "millionaires", "billion", "billionaire",
            "billionaires", "business", "invest", "investing", "investment", "investments", "investor",
            "investors", "finance", "financial", "economy", "economic", "cash", "capital", "profit",
            "profitable", "profits", "revenue", "income", "startup", "startups", "entrepreneur",
            "entrepreneurs", "entrepreneurship", "company", "companies", "founder", "founders", "ceo",
            "ceos", "market", "markets", "stock", "stocks", "crypto", "bitcoin", "asset", "assets",
            "debt", "fund", "funds", "equity", "sales", "selling", "pricing", "success", "successful",
            "career", "salary", "hustle", "mindset", "discipline", "negotiation", "earning", "earnings"
        }

        if any(w in topic_lower for w in ["food", "khao", "restaurant", "vlog", "cuisine", "recipe"]):
            base_lexicon.update(food_lexicon)
        elif any(w in topic_lower for w in ["supplement", "gym", "nutri", "fitness", "health", "nutrition"]):
            base_lexicon.update(fitness_lexicon)
        else:
            base_lexicon.update(wealth_lexicon)

        if topic_focus:
            for token in re.findall(r"\b[a-zA-Z]{3,}\b", topic_focus.lower()):
                if token not in stopwords:
                    base_lexicon.add(token)

        if auto_search_keywords:
            for phrase in auto_search_keywords:
                for token in re.findall(r"\b[a-zA-Z]{3,}\b", phrase.lower()):
                    if token not in stopwords:
                        base_lexicon.add(token)

        return base_lexicon

    @staticmethod
    def _calculate_relevance(title: str, topic_keywords: Set[str]) -> int:
        """Calculates keyword match count in title using whole-word boundary matching."""
        title_tokens = set(re.findall(r"\b[a-zA-Z]{3,}\b", title.lower()))
        return len(title_tokens.intersection(topic_keywords))

    @staticmethod
    def _count_transcript_topic_mentions(words: List[Dict[str, Any]], topic_keywords: Set[str], max_seconds: float = 1200.0) -> int:
        """Counts mentions of topic keywords within the first max_seconds (e.g. 20 minutes) of transcript."""
        count = 0
        for w in words:
            if w.get("start", 0.0) > max_seconds:
                break
            text = str(w.get("word", "")).lower().strip(".,!?:;\"'()[]{}/-")
            if text in topic_keywords:
                count += 1
        return count

    def _cleanup_file_safely(self, file_path: str) -> None:
        """Safely removes a file if it exists without raising errors."""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                self.logger.info(f"[Auto-Cleanup] Successfully removed temporary file: {file_path}")
        except Exception as e:
            self.logger.warning(f"[Auto-Cleanup] Could not remove {file_path}: {e}")

    def run(
        self,
        url: Optional[str] = None,
        clip_count: Optional[int] = None,
        topic_focus: Optional[str] = None,
        channel_profile: Optional[str] = None,
        language: Optional[str] = None,
        stop_checker: Optional[Callable[[], bool]] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> List[Dict[str, Any]]:
        """
        Executes 1 short generation cycle using the Ultra-Fast Headless Pipeline.
        Supports English and native Urdu with RTL reshaping and Nastaleeq styling.
        """
        chan_name = channel_profile or self.config_manager.get_active_profile_name()
        self.config_manager.set_active_profile(chan_name)

        active_lang = language or self.config_manager.get_caption_language(chan_name)
        lang_code = self.config_manager.LANGUAGE_TO_CODE.get(str(active_lang).strip().lower(), self.config_manager.get_caption_language_code(chan_name))

        # Enforce English for Wealth Secrets profile or wealth topic to prevent Urdu/Hindi language mismatch
        if chan_name == "Wealth Secrets" or "wealth" in str(topic_focus or "").lower():
            if not language or str(language).strip().lower() in ("urdu", "ur"):
                active_lang = "English"
                lang_code = "en"

        self.logger.info(f"🎬 [Pipeline] Starting Short Generation for '{chan_name}' ({active_lang})...")

        # --- Pre-Flight System Health & Connectivity Check ---
        try:
            from core.precheck import SystemPrechecker
            prechecker = SystemPrechecker(self.config_manager, logger=None)
            diag = prechecker.run_all_checks(profile_name=chan_name)
            if not diag.get("critical_ok", True):
                crit_fails = [c["message"] for c in diag["checks"] if c["status"] == "FAIL" and c.get("critical", False)]
                err_summary = " | ".join(crit_fails)
                self.logger.error(f"[Pre-Check Block] Halting execution due to critical failure(s): {err_summary}")
                raise RuntimeError(f"System Pre-Check Failed: {err_summary}")
        except RuntimeError:
            raise
        except Exception as precheck_err:
            self.logger.debug(f"[Pre-Check Notice] Non-blocking pre-check exception: {precheck_err}")

        # Run maintenance purge on expired files from previous runs
        purged = self.config_manager.check_and_purge_expired_files()
        if purged:
            self.logger.debug(f"[Auto-Cleanup] Cleaned up {len(purged)} expired clip(s).")

        # Directories
        dirs = self.config_manager.get_channel_output_dirs(chan_name)
        chan_dir = dirs["channel_dir"]
        source_dir = dirs["source_videos"]
        transcripts_dir = dirs["transcripts"]
        shorts_dir = dirs["shorts_clips"]

        topic = topic_focus or self.config_manager.get_channel_setting("topic_focus", "wealth, business secrets, and money concepts", chan_name)
        min_dur = float(self.config_manager.get_channel_setting("min_clip_duration", 50, chan_name) or self.config_manager.get("min_clip_duration", 50))
        max_dur = float(self.config_manager.get_channel_setting("max_clip_duration", 58, chan_name) or self.config_manager.get("max_clip_duration", 58))

        # Hardware acceleration settings
        hw = self.config_manager.get_hardware_settings()
        self.logger.debug(f"[Hardware] Profile: '{self.config_manager.get('hardware_profile')}' | {hw.get('summary', '')}")

        ingestion = MediaIngestionEngine(output_dir=dirs["source_videos"], logger=self.logger)
        transcriber = WhisperTranscriber(logger=self.logger)

        video_id = ""
        target_url = ""
        video_title = ""
        transcript_data = None

        manual_url = url.strip() if url else ""

        # Detect if manual_url is a channel URL (e.g., @channel, /channel/, /c/, /videos)
        is_channel_url = bool(
            manual_url and (
                "/@" in manual_url or 
                "/channel/" in manual_url or 
                "/c/" in manual_url or 
                "/user/" in manual_url or 
                manual_url.rstrip("/").endswith("/videos")
            )
        )

        # Determine discovery mode & target channels from configuration
        discovery_mode = self.config_manager.get_discovery_mode(chan_name)  # "hybrid", "channels_only", "keywords_only"
        target_channels = self.config_manager.get_target_channels(chan_name)

        if is_channel_url:
            if manual_url not in target_channels:
                target_channels.insert(0, manual_url)
            self.logger.info(f"[Pipeline] Channel URL provided in dashboard: '{manual_url}'. Setting as primary target channel.")
            self.config_manager.set_target_channels(target_channels, chan_name)
            self.config_manager.save_config()

        # Check active video state for Smart Resume (1 Short Per Scheduled Interval)
        active_state = self.config_manager.get_channel_setting("active_video_state", {}, chan_name) or {}
        active_vid = str(active_state.get("video_id", "")).strip()
        active_completed = int(active_state.get("completed_clip_count", 0) or 0)
        configured_target_clips = int(clip_count or self.config_manager.get_channel_setting("target_clips_per_video", 5, chan_name) or 5)
        active_target = int(active_state.get("target_clip_count", 0) or configured_target_clips)
        active_target = max(1, min(8, active_target))

        is_resuming = False
        planned_clips = []
        locked_channel_idx = None
        cache_suffix = f"_{lang_code}" if lang_code != "en" else ""

        if not manual_url and active_vid and active_completed < active_target:
            cache_json = os.path.join(transcripts_dir, f"{active_vid}{cache_suffix}_transcript.json")
            planned_clips_file = os.path.join(transcripts_dir, f"{active_vid}_planned_clips.json")
            if os.path.exists(cache_json):
                try:
                    with open(cache_json, "r", encoding="utf-8") as f:
                        transcript_data = json.load(f)
                    if transcript_data and transcript_data.get("words"):
                        video_id = active_vid
                        video_title = active_state.get("video_title", active_vid)
                        target_url = active_state.get("target_url") or f"https://www.youtube.com/watch?v={video_id}"
                        locked_channel_idx = active_state.get("channel_idx")
                        if os.path.exists(planned_clips_file):
                            try:
                                with open(planned_clips_file, "r", encoding="utf-8") as pf:
                                    planned_clips = json.load(pf)
                            except Exception:
                                planned_clips = []
                        is_resuming = True
                        ch_str = f" from Channel #{locked_channel_idx + 1}" if locked_channel_idx is not None else ""
                        self.logger.info(
                            f"⚡ [Smart Resume] Active video in progress: '{video_title}' ({video_id}){ch_str}. "
                            f"Generating Short #{active_completed + 1} of {active_target} (Completed: {active_completed})..."
                        )
                except Exception as resume_err:
                    self.logger.warning(f"[Smart Resume] Could not load resume cache for '{active_vid}': {resume_err}")
                    is_resuming = False

        # --- PHASE 1 & 2: Ultra-Fast Discovery / State Filtering ---
        if is_resuming:
            pass
        elif manual_url and not is_channel_url:
            self.logger.info(f"[Pipeline] Using manually specified video URL: {manual_url}")
            match = re.search(r"(?:v=|\/|youtu\.be\/)([0-9A-Za-z_-]{11})", manual_url)
            if not match:
                raise ValueError(f"Could not extract a valid YouTube video ID from '{manual_url}'. If you meant to target a channel, enter the channel link (e.g. https://www.youtube.com/@channel/videos).")
            video_id = match.group(1)
            target_url = f"https://www.youtube.com/watch?v={video_id}"
            try:
                v_meta = ingestion.fetch_video_info(target_url)
                video_title = v_meta.get("title", video_id)
            except Exception:
                video_title = video_id
            
            # Phase 3: Fetch Headless Transcript (Multi-language supported)
            cache_suffix = f"_{lang_code}" if lang_code != "en" else ""
            cache_json = os.path.join(transcripts_dir, f"{video_id}{cache_suffix}_transcript.json")
            transcript_data = transcriber.fetch_headless_transcript(video_id, cache_json_path=cache_json, language=lang_code)

            # If headless transcript is completely unavailable on YouTube, fall back to audio extraction + Whisper
            if not transcript_data or not transcript_data.get("words"):
                self.logger.info(f"[Pipeline] No headless YouTube transcript available for '{video_id}'. Falling back to audio extraction + Groq Whisper (language='{lang_code}')...")
                key_pool = self.config_manager.get_api_key_pool(chan_name)
                try:
                    audio_info = ingestion.download_and_extract_audio(target_url, target_height=360)
                    transcript_data = transcriber.transcribe(audio_info["audio_path"], cache_json_path=cache_json, api_key=key_pool, language=lang_code)
                except Exception as we:
                    self.logger.error(f"[Pipeline] Whisper fallback failed: {we}")

            if not transcript_data or not transcript_data.get("words"):
                raise ValueError(f"Could not retrieve or generate transcript for video '{video_id}'.")
        else:
            auto_kws = self.config_manager.get_channel_setting("auto_search_keywords", [], chan_name)
            if not auto_kws:
                ctx = self.config_manager.get_channel_context(chan_name)
                auto_kws = ctx.get("auto_search_keywords", [])
            topic_keywords = self._extract_topic_keywords(topic, auto_kws)
            negative_filters = self.config_manager.get_negative_filters(chan_name)
            ctx = self.config_manager.get_channel_context(chan_name)
            content_type = ctx.get("content_type", "podcast")

            channel_candidate_pool = []
            locked_channel_idx: Optional[int] = None

            # =========================================================================
            # TIER 1: TARGETED CHANNELS DISCOVERY (ROUND-ROBIN ROTATION)
            # =========================================================================
            if discovery_mode in ("channels_only", "hybrid") and target_channels:
                # Rotate through channels starting from last_target_channel_index
                last_ch_idx = int(self.config_manager.get_channel_setting("last_target_channel_index", 0, chan_name) or 0)
                if last_ch_idx >= len(target_channels):
                    last_ch_idx = 0

                ordered_channels = [
                    ((last_ch_idx + i) % len(target_channels), target_channels[(last_ch_idx + i) % len(target_channels)])
                    for i in range(len(target_channels))
                ]

                self.logger.info(
                    f"🎯 [Discovery - Tier 1] Checking {len(target_channels)} target channel(s) for '{chan_name}' "
                    f"(Starting with Channel #{last_ch_idx + 1}, Mode: {discovery_mode})..."
                )

                for ch_pos, (ch_idx, target_channel) in enumerate(ordered_channels):
                    if stop_checker and stop_checker():
                        self.logger.warning("Pipeline halted by user.")
                        return []

                    self.logger.info(f"🔍 [Tier 1] [{ch_pos + 1}/{len(ordered_channels)}] Checking Channel #{ch_idx + 1}: {target_channel}...")
                    try:
                        cands = ingestion.fetch_channel_recent_videos(target_channel, limit=25)
                    except Exception as e:
                        self.logger.warning(f"[Tier 1] Could not fetch videos from '{target_channel}': {e}")
                        continue

                    if not cands:
                        continue

                    channel_candidate_pool.extend(cands)
                    unprocessed = self.state_tracker.filter_unprocessed(cands)

                    if not unprocessed:
                        self.logger.debug(f"[Tier 1] All recent videos from '{target_channel}' already seen. Checking retryable candidates...")
                        unprocessed = self.state_tracker.filter_unprocessed(cands, allow_retry_failed=True)

                    if not unprocessed:
                        continue

                    # Rank candidates by topic keyword relevance
                    unprocessed.sort(
                        key=lambda c: self._calculate_relevance(c.get("title", ""), topic_keywords),
                        reverse=True
                    )

                    for cand in unprocessed:
                        if stop_checker and stop_checker():
                            self.logger.warning("Pipeline halted by user.")
                            return []

                        cand_id = cand["id"]
                        cand_title = cand.get("title", cand_id)
                        cand_url = cand.get("url", f"https://www.youtube.com/watch?v={cand_id}")
                        cand_dur = cand.get("duration", 0) or 0

                        # Shorts & duration check (require >= 300s)
                        if "/shorts/" in cand_url.lower() or "#shorts" in cand_title.lower() or "#short" in cand_title.lower():
                            self.state_tracker.mark_failed(cand_id, reason="skipped_short")
                            continue
                        if cand_dur and 0 < cand_dur < 300:
                            self.state_tracker.mark_failed(cand_id, reason="skipped_too_short")
                            continue

                        # Regional South Asian filter when English is required
                        if lang_code == "en":
                            regional_exclusions = [
                                "hindi", "urdu", "ankur warikoo", "warikoo",
                                "raj shamani", "ranveer", "tanmay", "marwari",
                                "crorepati", "indian", "india"
                            ]
                            if any(reg in cand_title.lower() for reg in regional_exclusions):
                                self.state_tracker.mark_failed(cand_id, reason="skipped_regional_language")
                                continue

                        # Negative filter exclusion check
                        if negative_filters and any(neg.lower() in cand_title.lower() for neg in negative_filters):
                            self.state_tracker.mark_failed(cand_id, reason="skipped_negative_filter")
                            continue

                        self.logger.debug(f"[Tier 1] Checking transcript for '{cand_title}' ({cand_id})...")
                        cache_suffix = f"_{lang_code}" if lang_code != "en" else ""
                        cache_json = os.path.join(transcripts_dir, f"{cand_id}{cache_suffix}_transcript.json")
                        t_data = transcriber.fetch_headless_transcript(cand_id, cache_json_path=cache_json, language=lang_code)

                        if not t_data or not t_data.get("words"):
                            self.state_tracker.mark_failed(cand_id, reason="failed_no_transcript")
                            continue

                        words = t_data.get("words", [])
                        topic_mentions = self._count_transcript_topic_mentions(words, topic_keywords, max_seconds=1200.0)
                        title_relevance = self._calculate_relevance(cand_title, topic_keywords)

                        # Relevance threshold: at least 1 keyword match in title OR 3 mentions in transcript
                        if title_relevance == 0 and topic_mentions < 3:
                            self.logger.info(
                                f"⏭️ [Tier 1] Skipping off-topic video: '{cand_title}' "
                                f"(Relevance: Title={title_relevance}, Transcript={topic_mentions}/3). Checking next..."
                            )
                            self.state_tracker.mark_failed(cand_id, reason="skipped_off_topic")
                            continue

                        video_id = cand_id
                        target_url = cand_url
                        video_title = cand_title
                        transcript_data = t_data
                        locked_channel_idx = ch_idx
                        self.logger.info(f"📺 [Tier 1] Locked onto target channel video: '{video_title}' ({video_id}) from Channel #{ch_idx + 1}")
                        break

                    if video_id and transcript_data:
                        break

            # Handle channels_only exhaustion / fallback
            if discovery_mode == "channels_only" and (not video_id or not transcript_data):
                if channel_candidate_pool:
                    self.logger.info("⚡ [Tier 1] Trying Whisper audio fallback for top target channel candidate...")
                    top_cand = channel_candidate_pool[0]
                    cand_id = top_cand.get("id")
                    cand_url = top_cand.get("url") or f"https://www.youtube.com/watch?v={cand_id}"
                    cand_title = top_cand.get("title", cand_id)
                    cache_suffix = f"_{lang_code}" if lang_code != "en" else ""
                    cache_json = os.path.join(transcripts_dir, f"{cand_id}{cache_suffix}_transcript.json")
                    key_pool = self.config_manager.get_api_key_pool(chan_name)
                    try:
                        audio_info = ingestion.download_and_extract_audio(cand_url, target_height=360)
                        t_data = transcriber.transcribe(audio_info["audio_path"], cache_json_path=cache_json, api_key=key_pool, language=lang_code)
                        if t_data and t_data.get("words"):
                            video_id = cand_id
                            target_url = cand_url
                            video_title = cand_title
                            transcript_data = t_data
                            self.logger.info(f"📺 [Tier 1] Audio Whisper transcription succeeded! Locked onto: '{video_title}' ({video_id})")
                    except Exception as we:
                        self.logger.warning(f"[Tier 1] Whisper fallback failed: {we}")

                if not video_id or not transcript_data:
                    raise ValueError(
                        f"All videos from configured target channel(s) ({len(target_channels)}) have been processed or lack transcripts. "
                        f"Discovery Mode is set to 'Targeted Channels Only'. "
                        f"To search across YouTube, switch Discovery Mode to 'Targeted Channels + Keyword Fallback' in Admin Settings."
                    )

            # =========================================================================
            # TIER 2: TOPIC KEYWORD RESEARCH DISCOVERY
            # =========================================================================
            if not video_id or not transcript_data:
                self.logger.info(f"🔍 [Discovery - Tier 2] Searching YouTube for topic '{topic}' ({content_type})...")

                search_queries = []
                if topic and topic.strip():
                    topic_clean = topic.strip()
                    if lang_code == "en":
                        search_queries.append(f"{topic_clean} American {content_type} interview full episode US")
                        search_queries.append(f"{topic_clean} American podcast interview US")
                    else:
                        search_queries.append(f"{topic_clean} {content_type} interview full episode")
                        search_queries.append(f"{topic_clean} full episode interview")

                if auto_kws:
                    for kw in auto_kws:
                        kw_clean = str(kw).strip()
                        if not kw_clean:
                            continue
                        if lang_code == "en" and "american" not in kw_clean.lower() and "us" not in kw_clean.lower():
                            kw_clean = f"{kw_clean} American US"
                        if all(term not in kw_clean.lower() for term in ["full episode", "interview", "podcast"]):
                            search_queries.append(f"{kw_clean} interview full episode")
                        else:
                            search_queries.append(kw_clean)
                else:
                    search_queries.append(f"{topic} American {content_type} interview full episode US")

                topic_candidates = ingestion.search_youtube_topic_podcasts(
                    search_queries=search_queries,
                    limit=25,
                    negative_filters=negative_filters,
                    min_duration=300,
                    target_count=25,
                    language=lang_code
                )
                unprocessed_search = self.state_tracker.filter_unprocessed(topic_candidates)

                # Secondary search query expansion if initial batch is already processed
                if not unprocessed_search:
                    self.logger.info(f"🔍 [Discovery - Tier 2] Expanding search queries for '{topic}'...")
                    secondary_queries = [
                        f"{topic} American podcast full episode US" if lang_code == "en" else f"{topic} podcast full episode",
                        f"{topic} American interview full episode US" if lang_code == "en" else f"{topic} interview full episode",
                        f"best {topic} conversation full episode"
                    ]
                    topic_candidates = ingestion.search_youtube_topic_podcasts(
                        search_queries=secondary_queries,
                        limit=35,
                        negative_filters=negative_filters,
                        min_duration=300,
                        target_count=35,
                        language=lang_code
                    )
                    unprocessed_search = self.state_tracker.filter_unprocessed(topic_candidates)

                # Allow retrying candidates that previously failed or were skipped
                if not unprocessed_search and topic_candidates:
                    self.logger.info("⚡ [Discovery - Tier 2] Checking retryable search candidates...")
                    unprocessed_search = self.state_tracker.filter_unprocessed(topic_candidates, allow_retry_failed=True)

                if unprocessed_search:
                    unprocessed_search.sort(
                        key=lambda c: self._calculate_relevance(c.get("title", ""), topic_keywords),
                        reverse=True
                    )

                    for cand in unprocessed_search:
                        if stop_checker and stop_checker():
                            self.logger.warning("Pipeline halted by user.")
                            return []

                        cand_id = cand["id"]
                        cand_title = cand.get("title", cand_id)
                        cand_url = cand.get("url", f"https://www.youtube.com/watch?v={cand_id}")
                        cand_dur = cand.get("duration", 0) or 0

                        # Shorts & duration check (require >= 300s)
                        if "/shorts/" in cand_url.lower() or "#shorts" in cand_title.lower() or "#short" in cand_title.lower():
                            self.state_tracker.mark_failed(cand_id, reason="skipped_short")
                            continue
                        if cand_dur and 0 < cand_dur < 300:
                            self.state_tracker.mark_failed(cand_id, reason="skipped_too_short")
                            continue

                        # Regional South Asian filter when English is required
                        if lang_code == "en":
                            regional_exclusions = [
                                "hindi", "urdu", "ankur warikoo", "warikoo",
                                "raj shamani", "ranveer", "tanmay", "marwari",
                                "crorepati", "indian", "india"
                            ]
                            if any(reg in cand_title.lower() for reg in regional_exclusions):
                                self.state_tracker.mark_failed(cand_id, reason="skipped_regional_language")
                                continue

                        # Negative filter exclusion check
                        if negative_filters and any(neg.lower() in cand_title.lower() for neg in negative_filters):
                            self.state_tracker.mark_failed(cand_id, reason="skipped_negative_filter")
                            continue

                        self.logger.debug(f"[Tier 2] Checking transcript for '{cand_title}' ({cand_id})...")
                        cache_suffix = f"_{lang_code}" if lang_code != "en" else ""
                        cache_json = os.path.join(transcripts_dir, f"{cand_id}{cache_suffix}_transcript.json")
                        t_data = transcriber.fetch_headless_transcript(cand_id, cache_json_path=cache_json, language=lang_code)

                        if not t_data or not t_data.get("words"):
                            self.state_tracker.mark_failed(cand_id, reason="failed_no_transcript")
                            continue

                        words = t_data.get("words", [])
                        topic_mentions = self._count_transcript_topic_mentions(words, topic_keywords, max_seconds=1200.0)
                        title_relevance = self._calculate_relevance(cand_title, topic_keywords)

                        if title_relevance == 0 and topic_mentions < 3:
                            self.logger.info(
                                f"⏭️ [Tier 2] Skipping off-topic video: '{cand_title}' "
                                f"(Relevance: Title={title_relevance}, Transcript={topic_mentions}/3). Checking next..."
                            )
                            self.state_tracker.mark_failed(cand_id, reason="skipped_off_topic")
                            continue

                        video_id = cand_id
                        target_url = cand_url
                        video_title = cand_title
                        transcript_data = t_data
                        self.logger.info(f"📺 [Tier 2] Locked onto topic video: '{video_title}' ({video_id})")
                        break

                # Robust Groq Whisper Fallback if headless transcripts failed across candidate pool
                if not video_id or not transcript_data:
                    fallback_pool = topic_candidates if 'topic_candidates' in locals() and topic_candidates else channel_candidate_pool
                    if fallback_pool:
                        top_cand = fallback_pool[0]
                        cand_id = top_cand.get("id")
                        cand_url = top_cand.get("url") or f"https://www.youtube.com/watch?v={cand_id}"
                        cand_title = top_cand.get("title", cand_id)

                        self.logger.info(
                            f"⚡ [Tier 2] Headless transcripts unavailable on network. "
                            f"Downloading lightweight audio stream for Groq Whisper: '{cand_title}' ({cand_id})..."
                        )
                        cache_suffix = f"_{lang_code}" if lang_code != "en" else ""
                        cache_json = os.path.join(transcripts_dir, f"{cand_id}{cache_suffix}_transcript.json")
                        key_pool = self.config_manager.get_api_key_pool(chan_name)
                        try:
                            audio_info = ingestion.download_and_extract_audio(cand_url, target_height=360)
                            t_data = transcriber.transcribe(
                                audio_info["audio_path"],
                                cache_json_path=cache_json,
                                api_key=key_pool,
                                language=lang_code
                            )
                            if t_data and t_data.get("words"):
                                video_id = cand_id
                                target_url = cand_url
                                video_title = cand_title
                                transcript_data = t_data
                                self.logger.info(f"📺 [Tier 2] Audio Whisper transcription succeeded! Locked onto: '{video_title}' ({video_id})")
                        except Exception as we:
                            self.logger.error(f"[Tier 2] Audio Whisper fallback failed: {we}")

            if not video_id or not transcript_data:
                raise ValueError(
                    f"Could not find any available video with a valid transcript matching topic '{topic}'. "
                    f"Tested and exhausted candidates across configured discovery sources."
                )

        # --- PHASE 4: Groq LLM Clip Selection (50s - 58s) ---
        if stop_checker and stop_checker():
            self.logger.warning("Pipeline halted by user.")
            return []

        key_pool = self.config_manager.get_api_key_pool(chan_name)
        if not key_pool:
            raise ValueError("No Groq API Keys configured! Please add a key in Admin Settings.")

        planned_clips_file = os.path.join(transcripts_dir, f"{video_id}_planned_clips.json")

        if not planned_clips or len(planned_clips) <= active_completed:
            target_clip_count = int(clip_count or self.config_manager.get_channel_setting("target_clips_per_video", 5, chan_name) or 5)
            target_clip_count = max(1, min(8, target_clip_count))

            self.logger.info(f"🧠 [Brain] Querying Groq AI to extract top {target_clip_count} viral clips for '{video_id}'...")
            clip_extractor = ViralClipExtractor(api_keys=key_pool, logger=self.logger)
            planned_clips = clip_extractor.extract_viral_clips(
                transcript_data=transcript_data,
                clip_count=target_clip_count,
                topic_focus=topic,
                min_duration=min_dur,
                max_duration=max_dur,
                channel_name=chan_name
            )

            if not planned_clips:
                self.logger.error("[Pipeline] Groq LLM could not find compliant 50-58s clips in this transcript.")
                self.state_tracker.mark_failed(video_id, reason="failed_no_viral_clips")
                return []

            try:
                with open(planned_clips_file, "w", encoding="utf-8") as pf:
                    json.dump(planned_clips, pf, indent=2)
            except Exception as pe:
                self.logger.debug(f"[Pipeline] Could not cache planned clips: {pe}")

            active_target = len(planned_clips)
            active_completed = 0
            self.config_manager.set_channel_setting("active_video_state", {
                "video_id": video_id,
                "video_title": video_title,
                "target_url": target_url,
                "channel_idx": locked_channel_idx,
                "target_clip_count": active_target,
                "completed_clip_count": 0,
                "status": "in_progress"
            }, chan_name)
            self.config_manager.save_config()
        else:
            active_target = len(planned_clips)

        clip_idx = active_completed
        if clip_idx >= len(planned_clips):
            self.logger.warning(f"[Pipeline] Clip index {clip_idx} exceeds planned clips count {len(planned_clips)}. Marking video complete.")
            self.state_tracker.mark_processed(video_id, title=video_title, metadata={"clips_generated": len(planned_clips)})
            self.config_manager.add_processed_video(video_id, profile_name=chan_name)
            self.config_manager.clear_active_video_state(chan_name)
            return []

        target_clip = planned_clips[clip_idx]
        self.logger.info(f"🎬 [Pipeline] Generating Short #{clip_idx + 1} of {active_target} from '{video_title}'...")

        yt_res_str = self.config_manager.get_channel_setting("youtube_download_resolution", "1080p", chan_name)
        yt_height = int(yt_res_str.replace("p", ""))

        safe_chan = re.sub(r'[^\w\s-]', '', chan_name).strip().replace(' ', '_')
        final_render_path = os.path.join(chan_dir, f"{safe_chan}_Short_{clip_idx + 1}_{video_id}.mp4")

        # Enforce 1 recent short policy in channel folder
        for old_mp4 in glob.glob(os.path.join(chan_dir, "*.mp4")):
            if os.path.abspath(old_mp4) != os.path.abspath(final_render_path):
                if self.config_manager.is_clip_uploaded(old_mp4, chan_name) or (time.time() - os.path.getmtime(old_mp4) > 1800):
                    self._cleanup_file_safely(old_mp4)
                    self.config_manager.mark_clip_deleted(old_mp4)

        out_w, out_h = self.config_manager.get_resolution_dimensions()
        raw_tpl_path = self.config_manager.get_template_path(chan_name)
        resolved_tpl_path = self.config_manager.resolve_asset_path(raw_tpl_path)

        final_tpl_path = None
        if resolved_tpl_path and os.path.exists(resolved_tpl_path):
            final_tpl_path = resolved_tpl_path
            self.logger.info(f"[Live Activity Log] 🎨 Loaded dynamic template overlay for '{chan_name}': {os.path.basename(final_tpl_path)}")
        else:
            default_fallback = self.config_manager.resolve_asset_path("assets/wealth secret template (2).jpg")
            if default_fallback and os.path.exists(default_fallback):
                final_tpl_path = default_fallback
            else:
                final_tpl_path = None

        composer = FFmpegComposer(
            output_width=out_w,
            output_height=out_h,
            template_path=final_tpl_path,
            ffmpeg_threads=hw["ffmpeg_threads"],
            ffmpeg_preset=hw["ffmpeg_preset"],
            ffmpeg_encoder_args=hw.get("ffmpeg_encoder_args"),
            logger=self.logger
        )

        face_on = self.config_manager.get_channel_setting("enable_face_tracking", True, chan_name)
        caption_color = self.config_manager.get_caption_color(chan_name)
        caption_font = self.config_manager.get_caption_font(chan_name)
        captions_on = self.config_manager.get_channel_setting("enable_captions", True, chan_name)

        sheets_path = (
            self.config_manager.get_channel_setting("google_sheets_json_path", "", chan_name) or
            self.config_manager.get("google_sheets_json_path", "")
        ).strip()
        sheet_target = (
            self.config_manager.get_channel_setting("google_spreadsheet_id", "", chan_name) or
            self.config_manager.get("google_spreadsheet_id", "")
        ).strip()
        sheets_enabled = self.config_manager.get_channel_setting("enable_google_sheets_logging", True, chan_name)

        completed_clips = []

        if stop_checker and stop_checker():
            self.logger.warning("Pipeline halted by user.")
            return []

        start_sec = float(target_clip["start_time"])
        end_sec = float(target_clip["end_time"])
        duration_sec = float(target_clip["duration"])
        clip_title = target_clip.get("title", f"Short #{clip_idx + 1}")

        self.logger.info(
            f"✂️ [Short {clip_idx + 1}/{active_target}] '{clip_title}' "
            f"({duration_sec:.1f}s: {start_sec:.1f}s - {end_sec:.1f}s)..."
        )

        partial_raw_path = os.path.join(source_dir, f"temp_{video_id}_clip_{clip_idx + 1}_raw.mp4")

        try:
            ingestion.download_partial_video(
                url=target_url,
                start_sec=start_sec,
                end_sec=end_sec,
                output_path=partial_raw_path,
                target_height=yt_height,
                progress_callback=progress_callback
            )

            if captions_on:
                raw_words = transcript_data.get("words", []) if transcript_data else []
                sliced_words = []
                for w in raw_words:
                    w_start = float(w.get("start", 0))
                    w_end = float(w.get("end", 0))
                    if w_end > start_sec and w_start < end_sec:
                        offset_w = dict(w)
                        offset_w["start"] = max(0.0, round(w_start - start_sec, 3))
                        offset_w["end"] = min(round(duration_sec, 3), max(offset_w["start"] + 0.05, round(w_end - start_sec, 3)))
                        sliced_words.append(offset_w)

                if sliced_words:
                    target_clip["aligned_words"] = sliced_words
                elif os.path.exists(partial_raw_path):
                    first_key = key_pool[0] if key_pool else None
                    accurate_words = transcriber.align_partial_clip_words(partial_raw_path, api_key=first_key, language=lang_code)
                    target_clip["aligned_words"] = accurate_words or []

            final_mp4 = composer.render_short_clip(
                source_video_path=partial_raw_path,
                clip_data=target_clip,
                output_mp4_path=final_render_path,
                enable_captions=captions_on,
                enable_face_tracking=face_on,
                is_pre_cut=True,
                language=active_lang,
                caption_color=caption_color,
                caption_font=caption_font
            )

            self._cleanup_file_safely(partial_raw_path)
            for tmp_chunk in glob.glob(os.path.join(source_dir, f"temp_{video_id}_*")):
                self._cleanup_file_safely(tmp_chunk)

            target_clip["rendered_mp4_path"] = final_mp4
            target_clip["created_at"] = time.time()
            target_clip["video_id"] = video_id
            target_clip["clip_index"] = clip_idx + 1

            self.config_manager.record_generated_clip(target_clip, profile_name=chan_name)

            # Publishing
            pub_results = {}
            try:
                multi_pub = MultiPlatformPublisher(self.config_manager, chan_name, logger=self.logger)
                pub_results = multi_pub.publish_all(
                    video_path=final_mp4,
                    title=clip_title,
                    hook=target_clip.get("hook", ""),
                    rationale=target_clip.get("rationale", ""),
                    channel_name=chan_name,
                    hashtags=target_clip.get("hashtags"),
                    progress_callback=progress_callback
                )
                success_count = pub_results.get("success_count", 0)
                if success_count > 0:
                    self.config_manager.mark_clip_uploaded(final_mp4, pub_results, profile_name=chan_name)
                    platforms_list = [p.capitalize() for p in pub_results.get("platforms", [])]
                    self.logger.info(f"🚀 [Publisher] Clip #{clip_idx + 1} published to {', '.join(platforms_list)}!")

                    # Check if uploaded on ALL turned-on platforms
                    upload_yt = self.config_manager.get_channel_setting("upload_to_youtube", True, chan_name)
                    upload_fb = self.config_manager.get_channel_setting("upload_to_facebook", True, chan_name)
                    upload_ig = self.config_manager.get_channel_setting("upload_to_instagram", True, chan_name)

                    enabled_platforms = []
                    if upload_yt: enabled_platforms.append("youtube")
                    if upload_fb: enabled_platforms.append("facebook")
                    if upload_ig: enabled_platforms.append("instagram")

                    successful_platforms = pub_results.get("platforms", [])
                    all_enabled_uploaded = bool(enabled_platforms and all(p in successful_platforms for p in enabled_platforms))

                    if all_enabled_uploaded:
                        self.config_manager.schedule_file_deletion(final_mp4, delay_seconds=1800, clip_id=f"{video_id}_{clip_idx + 1}")
                        self.logger.info(
                            f"⏳ [Auto-Cleanup] Successfully published to all turned-on platforms ({', '.join(platforms_list)})! "
                            f"'{os.path.basename(final_mp4)}' will remain in '{chan_name}' for 30 minutes, then auto-delete."
                        )
                    else:
                        pending_platforms = [p.capitalize() for p in enabled_platforms if p not in successful_platforms]
                        self.logger.info(
                            f"📁 [Auto-Cleanup] Kept '{os.path.basename(final_mp4)}' in '{chan_name}' folder (upload pending on: {', '.join(pending_platforms)})."
                        )
            except Exception as pub_err:
                self.logger.warning(f"[Pipeline] Publishing note for clip #{clip_idx + 1}: {pub_err}")

            # Google Sheets logging
            try:
                if sheets_enabled and sheets_path and os.path.exists(sheets_path):
                    from core.sheets_logger import GoogleSheetsLogger
                    sheets_logger = GoogleSheetsLogger(sheets_path, spreadsheet_id_or_name=sheet_target or "Social Agent Pro Logs", logger=self.logger)
                    sheets_logger.log_video_upload_async(
                        channel_name=chan_name,
                        clip_data=target_clip,
                        upload_results=pub_results or {},
                        last_video_time=float(self.config_manager.get_channel_setting("last_autopilot_run", 0, chan_name) or 0),
                        next_scheduled_time=float(self.config_manager.get_channel_setting("next_autopilot_run", 0, chan_name) or 0)
                    )
            except Exception as sh_err:
                self.logger.debug(f"[Pipeline] Google sheets logging notice: {sh_err}")

            completed_clips.append(target_clip)
            new_completed = clip_idx + 1
            self.logger.info(f"✅ [Short {new_completed}/{active_target}] Successfully created & processed: '{clip_title}'")

            # Cleanup temp captions & junk files
            self._cleanup_temp_captions_dir(video_id)
            self._cleanup_junk_temp_files()

            if new_completed >= active_target:
                self.state_tracker.mark_processed(video_id, title=video_title, metadata={"clips_generated": new_completed})
                self.config_manager.add_processed_video(video_id, profile_name=chan_name)
                self.config_manager.clear_active_video_state(chan_name)

                if os.path.exists(planned_clips_file):
                    try:
                        os.remove(planned_clips_file)
                    except Exception:
                        pass

                # Advance Round-Robin rotation index to the NEXT channel
                if locked_channel_idx is not None and target_channels:
                    next_ch_idx = (locked_channel_idx + 1) % len(target_channels)
                    self.config_manager.set_channel_setting("last_target_channel_index", next_ch_idx, chan_name)
                    self.config_manager.save_config()
                    next_chan_url = target_channels[next_ch_idx]
                    self.logger.info(
                        f"🔄 [Channel Rotation] All {active_target} shorts completed from Channel #{locked_channel_idx + 1}! "
                        f"Advancing to Channel #{next_ch_idx + 1}/{len(target_channels)}: '{next_chan_url}' for next scheduled run."
                    )
                else:
                    self.logger.info(f"🎉 [Pipeline] All {active_target} shorts completed for '{video_title}'!")
            else:
                self.config_manager.set_channel_setting("active_video_state", {
                    "video_id": video_id,
                    "video_title": video_title,
                    "target_url": target_url,
                    "channel_idx": locked_channel_idx,
                    "target_clip_count": active_target,
                    "completed_clip_count": new_completed,
                    "status": "in_progress"
                }, chan_name)
                self.config_manager.save_config()
                self.logger.info(
                    f"⏳ [Smart Resume] Video '{video_title}' progress: {new_completed}/{active_target} shorts completed. "
                    f"Next short (Clip #{new_completed + 1}) will generate on next Autopilot interval."
                )

        except Exception as clip_err:
            self.logger.error(f"[Pipeline] Failed processing clip #{clip_idx + 1}: {clip_err}")
            self._cleanup_file_safely(partial_raw_path)

        self.logger.info(f"🎉 [Pipeline] Complete: Successfully generated {len(completed_clips)} Shorts for '{chan_name}'!")
        return completed_clips

    def _cleanup_temp_captions_dir(self, video_id: str = "") -> None:
        """Deletes the temp_captions output directory after PNGs have been composited."""
        temp_dirs_to_check = [
            os.path.join(BASE_DIR, "output", "temp_captions"),
        ]
        for d in temp_dirs_to_check:
            if os.path.isdir(d):
                try:
                    import shutil
                    shutil.rmtree(d, ignore_errors=True)
                    self.logger.info("[Auto-Cleanup] Removed temp_captions directory: %s", d)
                except Exception as e:
                    self.logger.warning("[Auto-Cleanup] Could not remove temp_captions dir %s: %s", d, e)

    def _cleanup_junk_temp_files(self) -> None:
        """
        Removes orphaned MoviePy/FFmpeg temporary files left behind in the project root
        (e.g., *TEMP_MPY_wvf_snd.mp4, *.tmp files).
        """
        patterns = [
            os.path.join(BASE_DIR, "*TEMP_MPY_wvf_snd.mp4"),
            os.path.join(BASE_DIR, "*.tmp"),
            os.path.join(BASE_DIR, "scratch_nvenc_test.mp4"),
        ]
        for pattern in patterns:
            for junk_file in glob.glob(pattern):
                self._cleanup_file_safely(junk_file)


