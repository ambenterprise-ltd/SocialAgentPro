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

        # --- PHASE 1 & 2: Ultra-Fast Discovery / State Filtering ---
        if manual_url and not is_channel_url:
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
            if is_channel_url:
                target_channel = manual_url
                self.logger.info(f"[Pipeline] Detected Channel URL entered in URL field: '{target_channel}'. Updating channel settings...")
                self.config_manager.set_channel_setting("target_channel_url", target_channel, chan_name)
                self.config_manager.save_config()
            else:
                target_channel = self.config_manager.get_channel_setting("target_channel_url", "", chan_name)

            if not target_channel:
                self.logger.info("[Pipeline] No Target Channel URL provided. Will use topic search keywords...")

            auto_kws = self.config_manager.get_channel_setting("auto_search_keywords", [], chan_name)
            if not auto_kws:
                ctx = self.config_manager.get_channel_context(chan_name)
                auto_kws = ctx.get("auto_search_keywords", [])
            topic_keywords = self._extract_topic_keywords(topic, auto_kws)

            candidates = []
            if target_channel:
                self.logger.info(f"🔍 [Discovery] Checking recent videos from channel: {target_channel}...")
                try:
                    candidates = ingestion.fetch_channel_recent_videos(target_channel, limit=15)
                except Exception as e:
                    self.logger.warning(f"[Pipeline] Failed to fetch recent videos from '{target_channel}': {e}")

            # Filter out processed & failed videos
            unprocessed = self.state_tracker.filter_unprocessed(candidates)

            # Rank channel candidates by title relevance to topic keywords
            if unprocessed:
                unprocessed.sort(
                    key=lambda c: self._calculate_relevance(c.get("title", ""), topic_keywords),
                    reverse=True
                )

            negative_filters = self.config_manager.get_negative_filters(chan_name)
            self.logger.debug(f"[Pipeline] Evaluating {len(unprocessed)} unprocessed candidates for topic '{topic}'...")

            # Iterate through channel candidates and validate transcript relevance
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
                    self.logger.debug(f"[Pipeline] Candidate '{cand_title}' ({cand_id}) is a Short. Skipping...")
                    self.state_tracker.mark_failed(cand_id, reason="skipped_short")
                    continue
                if cand_dur and 0 < cand_dur < 300:
                    self.logger.debug(f"[Pipeline] Candidate '{cand_title}' ({cand_id}) duration ({cand_dur}s) < 300s. Skipping...")
                    self.state_tracker.mark_failed(cand_id, reason="skipped_too_short")
                    continue

                # Regional Hindi/Urdu/Indian exclusion filter when English is required
                if lang_code == "en":
                    regional_exclusions = [
                        "hindi", "urdu", "ankur warikoo", "warikoo",
                        "raj shamani", "ranveer", "tanmay", "marwari",
                        "crorepati", "indian", "india"
                    ]
                    if any(reg in cand_title.lower() for reg in regional_exclusions):
                        self.logger.debug(
                            f"[Pipeline] Candidate '{cand_title}' ({cand_id}) matches regional South Asian filter. Skipping..."
                        )
                        self.state_tracker.mark_failed(cand_id, reason="skipped_regional_language")
                        continue

                # Negative filter exclusion check
                if negative_filters and any(neg.lower() in cand_title.lower() for neg in negative_filters):
                    self.logger.debug(
                        f"[Pipeline] Candidate '{cand_title}' ({cand_id}) matches negative filter. Skipping..."
                    )
                    self.state_tracker.mark_failed(cand_id, reason="skipped_negative_filter")
                    continue

                self.logger.debug(f"[Pipeline] Checking transcript & topic relevance for '{cand_title}' ({cand_id})...")
                cache_suffix = f"_{lang_code}" if lang_code != "en" else ""
                cache_json = os.path.join(transcripts_dir, f"{cand_id}{cache_suffix}_transcript.json")
                t_data = transcriber.fetch_headless_transcript(cand_id, cache_json_path=cache_json, language=lang_code)

                if not t_data or not t_data.get("words"):
                    self.logger.debug(f"[Pipeline] Candidate '{cand_id}' has no transcript available. Skipping...")
                    self.state_tracker.mark_failed(cand_id, reason="failed_no_transcript")
                    continue

                words = t_data.get("words", [])
                topic_mentions = self._count_transcript_topic_mentions(words, topic_keywords, max_seconds=1200.0)

                # Pre-validation: Require at least 3 topic keyword mentions in the first 20 minutes
                # (or at least 1 keyword match in the title) to prevent wasting LLM calls on off-topic videos.
                title_relevance = self._calculate_relevance(cand_title, topic_keywords)
                if title_relevance == 0 and topic_mentions < 3:
                    self.logger.debug(
                        f"[Pipeline] Candidate '{cand_title}' ({cand_id}) has low relevance to '{topic}'. Skipping..."
                    )
                    self.state_tracker.mark_failed(cand_id, reason="skipped_off_topic")
                    continue

                video_id = cand_id
                target_url = cand_url
                video_title = cand_title
                transcript_data = t_data
                self.logger.info(
                    f"📺 [Discovery] Locked onto topic video: '{video_title}' ({video_id})"
                )
                break

            # Fallback to YouTube topic discovery if channel candidates were off-topic or exhausted
            if not video_id or not transcript_data:
                ctx = self.config_manager.get_channel_context(chan_name)
                content_type = ctx.get("content_type", "podcast")
                self.logger.info(
                    f"🔍 [Discovery] Searching YouTube for '{topic}' ({content_type})..."
                )

                # Tune topic search terms with full-length intent keywords and enforce American/US English
                search_queries = []
                if topic and topic.strip():
                    topic_clean = topic.strip()
                    if lang_code == "en":
                        search_queries.append(f"{topic_clean} American {content_type} interview full episode US")
                        search_queries.append(f"{topic_clean} American podcast interview US")
                        search_queries.append(f"{topic_clean} American full episode interview US")
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
                    search_queries.extend(ctx.get("auto_search_keywords", [f"{topic} American {content_type} interview full episode US"]))

                topic_candidates = ingestion.search_youtube_topic_podcasts(
                    search_queries=search_queries,
                    limit=25,
                    negative_filters=negative_filters,
                    min_duration=300,
                    target_count=25,
                    language=lang_code
                )
                unprocessed_search = self.state_tracker.filter_unprocessed(topic_candidates)

                # Secondary search fallback if all initial candidates were already processed/failed
                if not unprocessed_search:
                    self.logger.debug(
                        f"[Pipeline] Initial candidates processed. Attempting secondary search for '{topic}'..."
                    )
                    secondary_queries = [
                        f"{topic} American podcast full episode US",
                        f"{topic} American interview full episode US",
                        f"{content_type} {topic} American full video US",
                        f"best {topic} American podcast conversation US"
                    ]
                    topic_candidates = ingestion.search_youtube_topic_podcasts(
                        search_queries=secondary_queries,
                        limit=30,
                        negative_filters=negative_filters,
                        min_duration=300,
                        target_count=30,
                        language=lang_code
                    )
                    unprocessed_search = self.state_tracker.filter_unprocessed(topic_candidates)

                if not unprocessed_search:
                    raise ValueError(
                        f"No new unprocessed videos found matching topic '{topic}'. "
                        f"All channel and search candidates have been processed or skipped."
                    )

                # Rank search candidates by title topic match
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
                        self.logger.debug(f"[Pipeline] Search candidate '{cand_title}' ({cand_id}) is a Short. Skipping...")
                        self.state_tracker.mark_failed(cand_id, reason="skipped_short")
                        continue
                    if cand_dur and 0 < cand_dur < 300:
                        self.logger.debug(f"[Pipeline] Search candidate '{cand_title}' ({cand_id}) duration ({cand_dur}s) < 300s. Skipping...")
                        self.state_tracker.mark_failed(cand_id, reason="skipped_too_short")
                        continue

                    # Regional Hindi/Urdu/Indian exclusion filter when English is required
                    if lang_code == "en":
                        regional_exclusions = [
                            "hindi", "urdu", "ankur warikoo", "warikoo",
                            "raj shamani", "ranveer", "tanmay", "marwari",
                            "crorepati", "indian", "india"
                        ]
                        if any(reg in cand_title.lower() for reg in regional_exclusions):
                            self.logger.debug(
                                f"[Pipeline] Search candidate '{cand_title}' ({cand_id}) matches regional South Asian filter. Skipping..."
                            )
                            self.state_tracker.mark_failed(cand_id, reason="skipped_regional_language")
                            continue

                    # Negative filter exclusion check
                    if negative_filters and any(neg.lower() in cand_title.lower() for neg in negative_filters):
                        self.logger.debug(
                            f"[Pipeline] Search candidate '{cand_title}' matches negative filter. Skipping..."
                        )
                        self.state_tracker.mark_failed(cand_id, reason="skipped_negative_filter")
                        continue

                    self.logger.debug(f"[Pipeline] Checking transcript for '{cand_title}' ({cand_id})...")
                    cache_suffix = f"_{lang_code}" if lang_code != "en" else ""
                    cache_json = os.path.join(transcripts_dir, f"{cand_id}{cache_suffix}_transcript.json")
                    t_data = transcriber.fetch_headless_transcript(cand_id, cache_json_path=cache_json, language=lang_code)

                    if not t_data or not t_data.get("words"):
                        self.logger.debug(
                            f"[Pipeline] Search candidate '{cand_title}' ({cand_id}) has no extractable '{lang_code}' transcript. Skipping..."
                        )
                        self.state_tracker.mark_failed(cand_id, reason="failed_no_transcript")
                        continue

                    words = t_data.get("words", [])
                    topic_mentions = self._count_transcript_topic_mentions(words, topic_keywords, max_seconds=1200.0)

                    title_relevance = self._calculate_relevance(cand_title, topic_keywords)
                    if title_relevance == 0 and topic_mentions < 3:
                        self.logger.debug(
                            f"[Pipeline] Search candidate '{cand_title}' ({cand_id}) has low relevance to '{topic}'. Skipping..."
                        )
                        self.state_tracker.mark_failed(cand_id, reason="skipped_off_topic")
                        continue

                    video_id = cand_id
                    target_url = cand_url
                    video_title = cand_title
                    transcript_data = t_data
                    self.logger.info(
                        f"📺 [Discovery] Locked onto topic video: '{video_title}' ({video_id})"
                    )
                    break

            if not video_id or not transcript_data:
                raise ValueError(
                    f"Could not find any available video with a valid transcript matching topic '{topic}'. "
                    f"Tested and exhausted candidates in search pool."
                )

        # --- PHASE 4: Groq LLM Clip Selection (50s - 58s) ---
        if stop_checker and stop_checker():
            self.logger.warning("Pipeline halted by user.")
            return []

        key_pool = self.config_manager.get_api_key_pool(chan_name)
        if not key_pool:
            raise ValueError("No Groq API Keys configured! Please add a key in Admin Settings.")

        self.logger.debug(f"[Pipeline] Querying Groq LLM Brain for '{video_id}'...")
        clip_extractor = ViralClipExtractor(api_keys=key_pool, logger=self.logger)
        planned_clips = clip_extractor.extract_viral_clips(
            transcript_data=transcript_data,
            clip_count=1,
            topic_focus=topic,
            min_duration=min_dur,
            max_duration=max_dur,
            channel_name=chan_name
        )

        if not planned_clips:
            self.logger.error("[Pipeline] Groq LLM could not find a compliant 50-58s clip in this transcript.")
            self.state_tracker.mark_failed(video_id, reason="failed_no_viral_clips")
            return []

        target_clip = planned_clips[0]
        start_sec = float(target_clip["start_time"])
        end_sec = float(target_clip["end_time"])
        duration_sec = float(target_clip["duration"])
        clip_title = target_clip.get("title", "Viral Short")

        # --- PHASE 5: Partial Video Download (Only 58 Seconds!) ---
        if stop_checker and stop_checker():
            self.logger.warning("Pipeline halted by user.")
            return []

        yt_res_str = self.config_manager.get_channel_setting("youtube_download_resolution", "1080p", chan_name)
        yt_height = int(yt_res_str.replace("p", ""))

        clip_work_dir = os.path.join(shorts_dir, video_id)
        os.makedirs(clip_work_dir, exist_ok=True)
        partial_raw_path = os.path.join(clip_work_dir, f"{video_id}_partial_raw.mp4")

        self.logger.info(f"⬇️ [Ingestion] Downloading {duration_sec:.1f}s video segment...")
        ingestion.download_partial_video(
            url=target_url,
            start_sec=start_sec,
            end_sec=end_sec,
            output_path=partial_raw_path,
            target_height=yt_height,
            progress_callback=progress_callback
        )

        # --- PHASE 5.5: Precise Word-Level Subtitle Alignment (<3s) ---
        captions_on = self.config_manager.get_channel_setting("enable_captions", True, chan_name)
        if captions_on:
            raw_words = transcript_data.get("words", []) if transcript_data else []
            # Sync transcript data to clip's start time: filter words within [start_sec, end_sec]
            # and subtract start_sec so the new clip captions start at 0.0 relative to the cut clip duration
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
                self.logger.debug(f"[Pipeline] Sliced {len(sliced_words)} words synced to [0.0s - {duration_sec:.1f}s].")
            elif os.path.exists(partial_raw_path):
                # Fallback: align partial clip directly with Groq Whisper Large-V3 API (not local CPU tiny)
                self.logger.debug(f"[Pipeline] Slicing empty; aligning with Groq Whisper Large-V3 API (language='{lang_code}')...")
                first_key = key_pool[0] if key_pool else None
                accurate_words = transcriber.align_partial_clip_words(partial_raw_path, api_key=first_key, language=lang_code)
                target_clip["aligned_words"] = accurate_words or []

        # --- PHASE 6: 9:16 Template & Subtitle Compositing ---
        if stop_checker and stop_checker():
            self.logger.warning("Pipeline halted by user.")
            self._cleanup_file_safely(partial_raw_path)
            return []

        out_w, out_h = self.config_manager.get_resolution_dimensions()
        
        # Retrieve dynamic template path from active channel context dictionary
        raw_tpl_path = self.config_manager.get_template_path(chan_name)
        resolved_tpl_path = self.config_manager.resolve_asset_path(raw_tpl_path)

        # Fallback / Safety Check: Wrap template loader with an os.path.exists() check
        final_tpl_path = None
        if resolved_tpl_path and os.path.exists(resolved_tpl_path):
            final_tpl_path = resolved_tpl_path
            self.logger.info(f"[Live Activity Log] 🎨 Loaded dynamic template overlay for '{chan_name}': {os.path.basename(final_tpl_path)}")
        else:
            default_fallback = self.config_manager.resolve_asset_path("assets/wealth secret template (2).jpg")
            if default_fallback and os.path.exists(default_fallback):
                self.logger.warning(
                    f"[Live Activity Log] ⚠️ Template Warning: Dynamic template '{raw_tpl_path}' for channel '{chan_name}' "
                    f"not found on disk. Falling back to default asset: {default_fallback}"
                )
                final_tpl_path = default_fallback
            else:
                self.logger.warning(
                    f"[Live Activity Log] ⚠️ Template Warning: Dynamic template '{raw_tpl_path}' for channel '{chan_name}' "
                    f"not found on disk. Proceeding with compositing without template overlay."
                )
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

        final_render_path = os.path.join(clip_work_dir, f"{video_id}_short.mp4")
        face_on = self.config_manager.get_channel_setting("enable_face_tracking", True, chan_name)
        caption_color = self.config_manager.get_caption_color(chan_name)
        caption_font = self.config_manager.get_caption_font(chan_name)

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

        # Instant auto-cleanup: delete raw 58s partial download
        self._cleanup_file_safely(partial_raw_path)

        target_clip["rendered_mp4_path"] = final_mp4
        target_clip["created_at"] = time.time()
        target_clip["video_id"] = video_id
        target_clip["clip_index"] = 1

        # Record to Persistent State Tracker and Config
        self.state_tracker.mark_processed(video_id, title=video_title, metadata={"short_title": clip_title, "duration": duration_sec})
        self.config_manager.add_processed_video(video_id)
        self.config_manager.record_generated_clip(target_clip, profile_name=chan_name)

        # Multi-Platform Publishing (YouTube, Facebook Reels, Instagram Reels)
        upload_successful = False
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
                platforms_str = ", ".join(platforms_list)
                self.logger.info(f"🚀 [Publisher] Successfully published to {platforms_str}!")
                upload_successful = True

                # ── Auto-Delete After Confirmed Upload ──────────────────
                if not pub_results.get("errors"):
                    self.logger.info(
                        f"🧹 [Auto-Cleanup] Upload confirmed to {platforms_str}. Removed local copy."
                    )
                    self._cleanup_file_safely(final_mp4)
                    self.config_manager.mark_clip_deleted(final_mp4)
                    final_mp4 = ""  # Clear path to signal file is gone
                else:
                    self.logger.warning(
                        f"[Auto-Cleanup] Partial platform upload errors: {pub_results['errors']}. Local file preserved."
                    )
            else:
                if pub_results.get("errors"):
                    self.logger.warning(
                        f"[Pipeline] Upload failed with errors: {pub_results['errors']}. Local file preserved at: {final_mp4}"
                    )
                else:
                    self.logger.info(f"[Pipeline] No social platforms enabled. Video saved locally: {final_mp4}")
        except Exception as e:
            self.logger.warning(
                f"[Pipeline] Multi-platform publishing note: {e}. Local file preserved at: {final_mp4}"
            )

        # ── Temp Captions Cleanup ─────────────────────────────────────────────
        # After render completes (whether uploaded or not), clean up temp PNG folder
        self._cleanup_temp_captions_dir(video_id)

        # ── Junk File Cleanup (orphaned MoviePy temp files in project root) ───
        self._cleanup_junk_temp_files()

        # ── Google Sheets Multi-Channel Logging ───────────────────────────────
        try:
            sheets_path = (
                self.config_manager.get_channel_setting("google_sheets_json_path", "", chan_name) or
                self.config_manager.get("google_sheets_json_path", "")
            ).strip()
            sheet_target = (
                self.config_manager.get_channel_setting("google_spreadsheet_id", "", chan_name) or
                self.config_manager.get("google_spreadsheet_id", "")
            ).strip()
            sheets_enabled = self.config_manager.get_channel_setting("enable_google_sheets_logging", True, chan_name)

            if sheets_enabled and sheets_path and os.path.exists(sheets_path):
                from core.sheets_logger import GoogleSheetsLogger
                last_time = float(self.config_manager.get_channel_setting("last_autopilot_run", 0, chan_name) or 0)
                next_time = float(self.config_manager.get_channel_setting("next_autopilot_run", 0, chan_name) or 0)
                sheets_logger = GoogleSheetsLogger(sheets_path, spreadsheet_id_or_name=sheet_target or "Social Agent Pro Logs", logger=self.logger)
                sheets_logger.log_video_upload_async(
                    channel_name=chan_name,
                    clip_data=target_clip,
                    upload_results=pub_results if 'pub_results' in locals() and pub_results else {},
                    last_video_time=last_time,
                    next_scheduled_time=next_time
                )
        except Exception as sheet_err:
            self.logger.warning(f"[Pipeline] Google Sheets logging notice: {sheet_err}")

        self.logger.info(f"🎉 [Pipeline] 1-Short Cycle Complete for '{chan_name}'!")
        return [target_clip]

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


