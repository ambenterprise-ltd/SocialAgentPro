import os
import random
import logging
from typing import Dict, Any, List, Callable, Optional
import yt_dlp


class MediaIngestionEngine:
    """
    Ingestion Engine for AMB Enterprise.
    Handles searching & downloading target YouTube videos in selected resolution presets (1080p down to 240p),
    extracting 16kHz WAV audio, anti-403 YouTube request spoofing, and Smart Resume file caching.
    """

    def __init__(self, output_dir: str = "output", logger: Optional[logging.Logger] = None):
        self.output_dir = output_dir
        self.logger = logger or logging.getLogger("AMBEnterprise")
        os.makedirs(self.output_dir, exist_ok=True)

    @staticmethod
    def get_anti_403_headers() -> Dict[str, Any]:
        """Returns browser headers and player client options to bypass YouTube HTTP 403 Forbidden blocks."""
        opts = {
            "nocheckcertificate": True,
            "geo_bypass": True,
            "geo_bypass_country": "US",
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            "js_runtimes": {
                "node": {},
                "deno": {},
                "quickjs": {},
                "bun": {}
            },
            "retries": 10,
            "fragment_retries": 10
        }
        
        if os.path.exists("cookies.txt"):
            opts["cookiefile"] = "cookies.txt"
            
        return opts

    def fetch_video_info(self, url: str) -> Dict[str, Any]:
        """Fetches metadata for target video without downloading payload."""
        self.logger.info(f"[Ingestion] Fetching metadata for URL: {url}")
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            **self.get_anti_403_headers()
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "id": info.get("id"),
                "title": info.get("title"),
                "duration": info.get("duration"),
                "uploader": info.get("uploader"),
                "thumbnail": info.get("thumbnail"),
                "webpage_url": info.get("webpage_url", url)
            }

    def fetch_channel_recent_videos(
        self,
        channel_url: str,
        limit: int = 25,
        min_duration: int = 300
    ) -> List[Dict[str, Any]]:
        """
        Ultra-Fast Flat Scraping: Fetches only the metadata of the `limit` most recent videos from a channel.
        Excludes YouTube Shorts and short videos shorter than min_duration (default 300s / 5 min).
        """
        if not channel_url:
            raise ValueError("No Target Channel URL provided! Please set one in Admin Settings.")

        base_url = channel_url.split('?')[0].rstrip('/')
        if not base_url.endswith('/videos'):
            base_url += '/videos'

        self.logger.debug(f"[Ingestion] Flat scraping {limit} recent videos from: {base_url} (min_duration={min_duration}s)...")

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "playlist_items": f"1-{limit}",
            **self.get_anti_403_headers()
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                search_results = ydl.extract_info(base_url, download=False)
                entries = search_results.get("entries", [])
        except Exception as e:
            self.logger.error(f"[Ingestion] Failed to fetch channel videos: {e}")
            raise

        videos = []
        for entry in entries:
            v_id = entry.get("id")
            if not v_id:
                continue

            title = entry.get("title", "Unknown Title")
            url = f"https://www.youtube.com/watch?v={v_id}"
            webpage_url = entry.get("webpage_url", url)
            dur = entry.get("duration", 0) or 0

            # Filter out YouTube Shorts by URL and hashtag
            if "/shorts/" in webpage_url.lower() or "/shorts/" in url.lower():
                self.logger.debug(f"[Ingestion] Excluding channel video '{title}' ({v_id}): YouTube Shorts URL.")
                continue
            if "#shorts" in title.lower() or "#short" in title.lower():
                self.logger.debug(f"[Ingestion] Excluding channel video '{title}' ({v_id}): #shorts hashtag.")
                continue

            # Filter out videos shorter than min_duration (if duration is known)
            if dur and 0 < dur < min_duration:
                self.logger.debug(f"[Ingestion] Excluding channel video '{title}' ({v_id}): duration {dur}s < {min_duration}s.")
                continue

            videos.append({
                "id": v_id,
                "title": title,
                "url": url,
                "duration": dur,
                "view_count": entry.get("view_count", 0) or 0
            })

        self.logger.debug(f"[Ingestion] Extracted {len(videos)} valid long-form video candidates.")
        return videos

    def search_youtube_topic_podcasts(
        self,
        search_queries: List[str],
        limit: int = 25,
        negative_filters: Optional[List[str]] = None,
        min_duration: int = 300,
        target_count: int = 25,
        language: str = "en"
    ) -> List[Dict[str, Any]]:
        """
        Searches YouTube directly for topic-focused podcasts/interviews using auto_search_keywords.
        Filters out YouTube Shorts and short videos (< min_duration, default 300s).
        Enforces English queries and filters out regional Hindi/Urdu candidates when language is English.
        Applies negative keyword filters to exclude off-topic candidate videos.
        Accumulates candidates across multiple search queries up to target_count.
        Returns a list of candidate video dictionaries.
        """
        if not search_queries:
            search_queries = ["wealth secrets English podcast interview full episode", "business advice English podcast interview"]

        queries_to_try = [q for q in search_queries if q and str(q).strip()]
        if not queries_to_try:
            queries_to_try = ["wealth secrets English podcast interview full episode"]

        # If English mode is active, enforce English in queries to avoid regional bias
        if language == "en":
            queries_to_try = [
                q if "english" in q.lower() else f"{q} English"
                for q in queries_to_try
            ]

        all_candidates: List[Dict[str, Any]] = []
        seen_ids = set()
        regional_exclusions = [
            "hindi", "urdu", "ankur warikoo", "warikoo",
            "raj shamani", "ranveer", "tanmay", "marwari",
            "crorepati", "indian", "india"
        ]

        for query in queries_to_try:
            if len(all_candidates) >= target_count:
                break

            self.logger.debug(f"[Ingestion] Topic search for: '{query}' (limit={limit}, min_dur={min_duration}s)...")
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,
                "geo_bypass_country": "US",
                **self.get_anti_403_headers()
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    search_results = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
                    entries = search_results.get("entries", [])

                for entry in entries:
                    if not entry:
                        continue
                    v_id = entry.get("id")
                    if not v_id or v_id in seen_ids:
                        continue

                    title = entry.get("title", "Unknown Title")
                    url = f"https://www.youtube.com/watch?v={v_id}"
                    webpage_url = entry.get("webpage_url", url)
                    dur = entry.get("duration", 0) or 0
                    title_lower = title.lower()

                    # Filter out YouTube Shorts by URL and hashtag
                    if "/shorts/" in webpage_url.lower() or "/shorts/" in url.lower():
                        self.logger.debug(f"[Ingestion] Excluding candidate '{title}' ({v_id}): YouTube Shorts URL.")
                        continue
                    if "#shorts" in title.lower() or "#short" in title.lower():
                        self.logger.debug(f"[Ingestion] Excluding candidate '{title}' ({v_id}): #shorts hashtag.")
                        continue

                    # Filter out short videos under min_duration (5 minutes)
                    if dur and 0 < dur < min_duration:
                        self.logger.debug(f"[Ingestion] Excluding candidate '{title}' ({v_id}): duration {dur}s < {min_duration}s.")
                        continue

                    # Filter out regional Hindi/Urdu videos when English is requested
                    if language == "en" and any(reg in title_lower for reg in regional_exclusions):
                        self.logger.debug(f"[Ingestion] Excluding regional candidate '{title}' ({v_id}).")
                        continue

                    # Apply negative filters / exclusions
                    if negative_filters and title:
                        if any(neg.lower() in title_lower for neg in negative_filters):
                            self.logger.debug(f"[Ingestion] Excluding candidate '{title}' matching negative filter.")
                            continue

                    seen_ids.add(v_id)
                    all_candidates.append({
                        "id": v_id,
                        "title": title,
                        "url": url,
                        "duration": dur,
                        "view_count": entry.get("view_count", 0) or 0
                    })

                    if len(all_candidates) >= target_count:
                        break

            except Exception as e:
                self.logger.warning(f"[Ingestion] Topic search query '{query}' failed: {e}. Trying next query...")

        self.logger.debug(f"[Ingestion] Total discovered candidates across queries: {len(all_candidates)} (Target: {target_count}).")
        return all_candidates

    def auto_fetch_channel_video(
        self,
        channel_url: str,
        processed_history: List[str],
        fetch_strategy: str = "Viral (Most Viewed)",
        topic_keywords: Optional[List[str]] = None,
        negative_filters: Optional[List[str]] = None,
        min_duration: int = 300
    ) -> Dict[str, Any]:
        """
        Ultra-Fast Channel Discovery: Fetches top 25 recent videos, filters out processed videos,
        excludes negative filter terms, excludes Shorts (< min_duration), and returns the best candidate.
        """
        candidates = self.fetch_channel_recent_videos(channel_url, limit=25, min_duration=min_duration)
        
        valid_entries = [c for c in candidates if c["id"] not in processed_history]

        if negative_filters:
            valid_entries = [
                c for c in valid_entries
                if not any(neg.lower() in c.get("title", "").lower() for neg in negative_filters)
            ]

        if not valid_entries:
            raise ValueError(f"No new unique videos found in the 25 most recent videos of {channel_url}. All have been processed or skipped.")

        # If topic keywords are provided, prioritize videos whose title matches topic keywords
        if topic_keywords:
            lowered_kws = [k.lower() for k in topic_keywords if len(k) > 2]
            def _calc_topic_score(cand):
                title_lower = cand.get("title", "").lower()
                return sum(1 for kw in lowered_kws if kw in title_lower)
            valid_entries.sort(key=_calc_topic_score, reverse=True)

        if fetch_strategy == "Random":
            import random
            candidate = random.choice(valid_entries)
        else:
            # Sort by topic score first (if applicable), then view count
            candidate = valid_entries[0]

        self.logger.info(f"[Ingestion] Selected unique video '{candidate['title']}' via strategy '{fetch_strategy}': {candidate['url']}")
        return candidate

    def download_partial_video(
        self,
        url: str,
        start_sec: float,
        end_sec: float,
        output_path: str,
        target_height: int = 1080,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> str:
        """
        Partial Video Download: Downloads ONLY the specific start_sec to end_sec chunk (~15-25MB)
        directly from YouTube via FFmpeg stream slicing without downloading the full video.
        """
        duration = end_sec - start_sec
        self.logger.info(f"[Ingestion] Partial download: Streaming [{start_sec:.1f}s - {end_sec:.1f}s] ({duration:.1f}s) directly from YouTube...")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        def _partial_progress_hook(d):
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                if total > 0:
                    pct = (downloaded / total) * 100
                    speed = d.get("_speed_str", "N/A")
                    eta = d.get("_eta_str", "N/A")
                    if progress_callback:
                        progress_callback(pct, f"Downloading 58s clip ({target_height}p): {pct:.1f}% ({speed})")

        format_spec = (
            f"bestvideo[height<={target_height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={target_height}]+bestaudio/"
            f"best[height<={target_height}]/best"
        )

        import yt_dlp.utils
        ydl_opts = {
            "format": format_spec,
            "outtmpl": output_path,
            "download_ranges": yt_dlp.utils.download_range_func(None, [(start_sec, end_sec)]),
            "overwrites": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [_partial_progress_hook],
            **self.get_anti_403_headers()
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as e:
            self.logger.warning(f"[Ingestion] Primary partial download format spec failed ({e}). Retrying with best format...")
            fallback_opts = dict(ydl_opts)
            fallback_opts["format"] = "best"
            with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                ydl.download([url])

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError(f"Partial video download failed to produce valid file at: {output_path}")

        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        self.logger.info(f"[Ingestion] Partial 58s download complete: {output_path} ({file_size_mb:.2f} MB)")
        return output_path

    def download_and_extract_audio(
        self,
        url: str,
        target_height: int = 1080,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> Dict[str, str]:
        """
        Downloads video stream in selected target resolution (1080p, 720p, 480p, 360p, 240p)
        and extracts 16kHz mono WAV audio file.
        SMART RESUME: Automatically skips downloading/extraction if files already exist on disk.
        """
        info = self.fetch_video_info(url)
        video_id = info["id"]

        work_dir = os.path.join(self.output_dir, video_id)
        os.makedirs(work_dir, exist_ok=True)

        video_filename = f"{video_id}_source_{target_height}p.mp4"
        audio_filename = f"{video_id}_audio_16k.wav"

        video_path = os.path.join(work_dir, video_filename)
        audio_path = os.path.join(work_dir, audio_filename)

        # --- SMART RESUME: CHECK VIDEO payload ---
        if os.path.exists(video_path) and os.path.getsize(video_path) > 10000:
            self.logger.info(f"[Smart Resume] Source video already downloaded ({video_path}). Skipping video download.")
            if progress_callback:
                progress_callback(100.0, "Video download cached.")
        else:
            def _yt_progress_hook(d):
                if d.get("status") == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    downloaded = d.get("downloaded_bytes", 0)
                    if total > 0:
                        percentage = (downloaded / total) * 100
                        speed = d.get("_speed_str", "N/A")
                        eta = d.get("_eta_str", "N/A")
                        msg = f"Downloading ({target_height}p): {percentage:.1f}% ({speed}, ETA {eta})"
                        if progress_callback:
                            progress_callback(percentage, msg)

            self.logger.info(f"[Ingestion] Downloading video at target resolution ({target_height}p) for: '{info['title']}'...")

            format_spec = (
                f"bestvideo[height<={target_height}][ext=mp4]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={target_height}]+bestaudio/"
                f"best[height<={target_height}]/best"
            )

            ydl_video_opts = {
                "format": format_spec,
                "outtmpl": video_path,
                "overwrites": True,
                "quiet": True,
                "no_warnings": True,
                "progress_hooks": [_yt_progress_hook],
                **self.get_anti_403_headers()
            }

            try:
                with yt_dlp.YoutubeDL(ydl_video_opts) as ydl:
                    ydl.download([url])
            except Exception as e:
                self.logger.warning(f"[Ingestion] Primary format spec download failed ({e}). Retrying with best available format...")
                fallback_opts = dict(ydl_video_opts)
                fallback_opts["format"] = "best"
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    ydl.download([url])

            self.logger.info(f"[Ingestion] Video downloaded to: {video_path}")

        # --- SMART RESUME: CHECK AUDIO payload ---
        if os.path.exists(audio_path) and os.path.getsize(audio_path) > 10000:
            self.logger.info(f"[Smart Resume] 16kHz WAV audio already extracted ({audio_path}). Skipping audio extraction.")
        else:
            self.logger.info(f"[Ingestion] Extracting 16kHz mono WAV audio stream for Whisper...")
            ydl_audio_opts = {
                "format": "bestaudio/best",
                "outtmpl": os.path.join(work_dir, f"{video_id}_temp_audio.%(ext)s"),
                "overwrites": True,
                "quiet": True,
                "no_warnings": True,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "wav",
                    "preferredquality": "192",
                }],
                "postprocessor_args": [
                    "-ar", "16000",  # 16kHz sample rate optimal for Whisper
                    "-ac", "1",      # Mono channel
                ],
                **self.get_anti_403_headers()
            }

            try:
                with yt_dlp.YoutubeDL(ydl_audio_opts) as ydl:
                    ydl.download([url])
            except Exception as e:
                self.logger.warning(f"[Ingestion] Audio extract failed ({e}). Retrying audio download...")
                with yt_dlp.YoutubeDL(ydl_audio_opts) as ydl:
                    ydl.download([url])

            temp_wav = os.path.join(work_dir, f"{video_id}_temp_audio.wav")
            if os.path.exists(temp_wav):
                if os.path.exists(audio_path):
                    os.remove(audio_path)
                os.rename(temp_wav, audio_path)

            self.logger.info(f"[Ingestion] Audio extraction complete: {audio_path}")

        return {
            "video_id": video_id,
            "title": info["title"],
            "duration": info["duration"],
            "video_path": video_path,
            "audio_path": audio_path,
            "work_dir": work_dir
        }
