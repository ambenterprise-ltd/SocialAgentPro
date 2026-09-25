import os
import subprocess
import json
import logging
import math
import time
from typing import Dict, Any, List, Optional, Union
from youtube_transcript_api import (
    YouTubeTranscriptApi,
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
    CouldNotRetrieveTranscript,
    YouTubeTranscriptApiException
)
from groq import Groq


LANGUAGE_TO_CODE = {
    "english": "en",
    "urdu": "ur",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "hindi": "hi",
    "arabic": "ar",
    "chinese": "zh",
    "japanese": "ja",
    "portuguese": "pt",
    "russian": "ru"
}


def resolve_language_code(language_name_or_code: Optional[str]) -> str:
    """Resolves language name or abbreviation to standard ISO 639-1 code."""
    if not language_name_or_code:
        return "en"
    clean = str(language_name_or_code).strip().lower()
    return LANGUAGE_TO_CODE.get(clean, clean[:2] if len(clean) >= 2 else "en")


class WhisperTranscriber:
    """
    Lightning-Fast Speech-to-Text Transcription Engine.
    Method 1: Native YouTube Transcript (1-2 seconds)
    Method 2: Groq API Whisper Large V3 fallback via compressed audio (<15 seconds)
    """

    def __init__(
        self,
        model_size: str = "small",
        device: str = "auto",
        compute_type: str = "default",
        cpu_threads: int = 4,
        logger: Optional[logging.Logger] = None
    ):
        self.logger = logger or logging.getLogger("Antigravity")
        # Legacy parameters kept for compatibility with pipeline.py signature
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads

    def _interpolate_words(self, text: str, start: float, end: float) -> List[Dict[str, Any]]:
        """Splits a text segment into uniformly interpolated words for ASS subtitles."""
        raw_words = [w.strip() for w in text.split(" ") if w.strip()]
        if not raw_words:
            return []
            
        duration = end - start
        word_duration = duration / len(raw_words)
        
        words_list = []
        for i, word in enumerate(raw_words):
            words_list.append({
                "word": word,
                "start": round(start + (i * word_duration), 3),
                "end": round(start + ((i + 1) * word_duration), 3),
                "probability": 1.0
            })
        return words_list

    def _fetch_youtube_native(self, video_id: str, target_language: str = "en") -> Optional[Dict[str, Any]]:
        """
        Fetches and interpolates native youtube transcript in target language.
        Strictly generates captions in the native spoken language of the video without auto-translation.
        Checks both manually created and auto-generated transcripts with dialect support.
        """
        try:
            lang_code = resolve_language_code(target_language)
            self.logger.debug(f"[FastTranscriber] Fetching transcript for '{video_id}' (lang='{target_language}')...")
            raw_transcript = None
            detected_lang = lang_code

            # Regional dialect codes for target language
            dialect_map = {
                "en": ['en', 'en-US', 'en-GB', 'en-CA', 'en-AU', 'en-IN', 'en-NZ', 'en-IE', 'en-ZA'],
                "es": ['es', 'es-ES', 'es-419', 'es-MX', 'es-US'],
                "ur": ['ur', 'ur-PK'],
                "ar": ['ar', 'ar-SA', 'ar-EG', 'ar-AE'],
                "hi": ['hi', 'hi-IN'],
                "fr": ['fr', 'fr-FR', 'fr-CA'],
                "de": ['de', 'de-DE'],
                "zh": ['zh', 'zh-Hans', 'zh-Hant', 'zh-CN', 'zh-TW'],
                "ja": ['ja', 'ja-JP'],
                "pt": ['pt', 'pt-BR', 'pt-PT'],
                "ru": ['ru', 'ru-RU']
            }
            target_codes = dialect_map.get(lang_code, [lang_code])

            try:
                ytt = YouTubeTranscriptApi()
                try:
                    t_list = ytt.list(video_id)
                except TranscriptsDisabled:
                    self.logger.debug(f"[FastTranscriber] Transcripts disabled for video '{video_id}'.")
                    return None
                except VideoUnavailable:
                    self.logger.debug(f"[FastTranscriber] Video '{video_id}' is unavailable or private.")
                    return None
                except NoTranscriptFound:
                    self.logger.debug(f"[FastTranscriber] No transcripts found on YouTube for '{video_id}'.")
                    return None
                except Exception as e_list:
                    self.logger.debug(f"[FastTranscriber] Transcript listing error for '{video_id}': {e_list}")
                    return None

                t_obj = None
                # 1. Try finding transcript matching target dialects (find_transcript checks manual first, then generated)
                try:
                    t_obj = t_list.find_transcript(target_codes)
                except NoTranscriptFound:
                    pass
                except Exception as e_find:
                    self.logger.debug(f"[FastTranscriber] find_transcript failed with: {e_find}")

                # 2. If not found, inspect all available transcripts for dialect prefix match (e.g. en-*)
                if not t_obj:
                    for t in t_list:
                        if t.language_code.lower().startswith(lang_code.lower()):
                            t_obj = t
                            break

                if not t_obj:
                    avail_langs = [t.language_code for t in t_list]
                    self.logger.debug(f"[FastTranscriber] No native transcript found in {target_codes} for '{video_id}'. (Available: {avail_langs})")
                    return None

                try:
                    raw_transcript = t_obj.fetch()
                    detected_lang = t_obj.language_code
                    is_gen = getattr(t_obj, 'is_generated', False)
                    gen_str = "auto-generated" if is_gen else "manually created"
                    self.logger.debug(f"[FastTranscriber] Found {gen_str} {target_language} transcript ({detected_lang}) for '{video_id}'.")
                except (CouldNotRetrieveTranscript, YouTubeTranscriptApiException, Exception) as e_fetch:
                    self.logger.debug(f"[FastTranscriber] Failed to fetch transcript data for '{video_id}': {e_fetch}")
                    return None

            except Exception as e_inner:
                self.logger.debug(f"[FastTranscriber] Error fetching transcript for '{video_id}': {e_inner}")
                return None

            if not raw_transcript:
                return None

            words_list = []
            full_text = []

            for i, entry in enumerate(raw_transcript):
                start = float(getattr(entry, 'start', None) if hasattr(entry, 'start') else entry.get('start', 0.0))
                dur = float(getattr(entry, 'duration', None) if hasattr(entry, 'duration') else entry.get('duration', 0.0))
                text = (getattr(entry, 'text', None) if hasattr(entry, 'text') else entry.get('text', '')).replace('\n', ' ').strip()
                if not text:
                    continue
                full_text.append(text)
                
                # Determine true spoken end time: YouTube display duration overlaps with subsequent snippets.
                # If the next snippet starts before start + dur, clamp this snippet's end to next snippet's start.
                if i + 1 < len(raw_transcript):
                    next_start = float(getattr(raw_transcript[i+1], 'start', None) if hasattr(raw_transcript[i+1], 'start') else raw_transcript[i+1].get('start', 0.0))
                    if next_start > start:
                        end = min(start + dur, next_start)
                    else:
                        end = start + dur
                else:
                    end = start + dur

                words_list.extend(self._interpolate_words(text, start, end))

            if not words_list:
                return None

            self.logger.debug(f"[FastTranscriber] Headless transcript ready ({len(words_list)} words, lang={detected_lang}).")
            return {
                "language": detected_lang,
                "language_probability": 1.0,
                "duration": words_list[-1]['end'] if words_list else 0,
                "full_text": " ".join(full_text),
                "words": words_list
            }
        except Exception as e:
            self.logger.warning(f"[FastTranscriber] Native YouTube transcript not available for '{video_id}': {e}")
            return None

    def fetch_headless_transcript(self, video_id: str, cache_json_path: Optional[str] = None, language: str = "en") -> Optional[Dict[str, Any]]:
        """
        Pure Headless Transcript Extraction: Fetches transcript directly via YouTube API with 0 audio/video downloads.
        Returns None if no transcript is available (allows skipping to the next video).
        """
        if cache_json_path and os.path.exists(cache_json_path):
            self.logger.info(f"[Transcriber] Loading cached transcript from: {cache_json_path}")
            with open(cache_json_path, "r", encoding="utf-8") as f:
                return json.load(f)

        data = self._fetch_youtube_native(video_id, target_language=language)
        if data and cache_json_path:
            os.makedirs(os.path.dirname(cache_json_path), exist_ok=True)
            with open(cache_json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

        return data

    def _compress_audio_for_groq(self, audio_path: str) -> str:
        """Compresses audio to extremely low bitrate to meet Groq 25MB limit."""
        compressed_path = audio_path.replace(".wav", "_compressed.mp3")
        if os.path.exists(compressed_path):
            return compressed_path
            
        self.logger.info(f"[FastTranscriber] Compressing audio for Groq API limit...")
        cmd = [
            "ffmpeg", "-y", "-i", audio_path,
            "-vn", "-ar", "16000", "-ac", "1", "-b:a", "24k",
            compressed_path
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return compressed_path

    def _get_audio_duration(self, audio_path: str) -> float:
        """Determines exact audio duration in seconds using ffprobe."""
        try:
            cmd = ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path]
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
            val = float(out)
            if val > 0:
                return val
        except Exception:
            pass
        try:
            cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", audio_path]
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
            data = json.loads(out)
            return float(data.get("format", {}).get("duration", 0.0))
        except Exception:
            return 0.0

    def _resolve_key_pool(self, api_key: Optional[Union[str, List[str]]]) -> List[str]:
        """Collects all valid, non-empty Groq API keys into a rotation pool."""
        keys = []
        if isinstance(api_key, list):
            for k in api_key:
                if k and isinstance(k, str) and k.strip() and k.strip() not in keys:
                    keys.append(k.strip())
        elif isinstance(api_key, str) and api_key.strip():
            keys.append(api_key.strip())

        if not keys:
            try:
                settings_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json")
                if os.path.exists(settings_path):
                    with open(settings_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    prof_name = cfg.get("active_profile", "Wealth Secrets")
                    prof = cfg.get("profiles", {}).get(prof_name, {})
                    primary = cfg.get("groq_api_key") or prof.get("groq_api_key")
                    if primary:
                        keys.append(primary)
                    for pk in cfg.get("groq_api_keys_pool", []) + prof.get("groq_api_keys_pool", []):
                        if pk and pk not in keys:
                            keys.append(pk)
                    # If still empty, check all profiles
                    if not keys:
                        for p in cfg.get("profiles", {}).values():
                            if p.get("groq_api_key") and p["groq_api_key"] not in keys:
                                keys.append(p["groq_api_key"])
                            for pk in p.get("groq_api_keys_pool", []):
                                if pk and pk not in keys:
                                    keys.append(pk)
            except Exception:
                pass
        return keys

    def _fetch_groq_whisper(
        self,
        audio_path: str,
        api_key: Optional[Union[str, List[str]]],
        language: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Uses Groq's insanely fast multilingual Whisper API with smart chunking and API key rotation.
        For audio > 600s (10 min), automatically splits into 600s chunks to prevent
        Groq 413 ASPH (Audio Seconds Per Hour) and single-request limits, rotating across the key pool.
        """
        try:
            lang_code = resolve_language_code(language)
            keys = self._resolve_key_pool(api_key)
            if not keys:
                self.logger.error("[FastTranscriber] No Groq API keys available for transcription.")
                return None

            compressed_path = self._compress_audio_for_groq(audio_path)
            duration = self._get_audio_duration(compressed_path)

            chunk_size = 600.0  # 10-minute safe chunk to never exceed Groq ASPH limit

            if duration <= chunk_size:
                # Single request for short audio
                self.logger.info(f"[FastTranscriber] Sending audio ({duration:.1f}s) to Groq Whisper (language='{lang_code}')...")
                response = None
                for attempt_idx, cur_key in enumerate(keys):
                    try:
                        client = Groq(api_key=cur_key)
                        with open(compressed_path, "rb") as file:
                            kwargs = {
                                "file": (os.path.basename(compressed_path), file.read()),
                                "model": "whisper-large-v3-turbo",
                                "response_format": "verbose_json",
                                "language": lang_code,
                                "timestamp_granularities": ["word"]
                            }
                            response = client.audio.transcriptions.create(**kwargs)
                        break
                    except Exception as ge:
                        self.logger.warning(f"[FastTranscriber] Key #{attempt_idx+1} failed ({ge}). Trying next key...")
                        if attempt_idx == len(keys) - 1:
                            raise ge

                segments = getattr(response, "segments", []) or (response.get("segments", []) if isinstance(response, dict) else [])
                groq_words = getattr(response, "words", None) or (response.get("words") if isinstance(response, dict) else None)
                words_list = []
                full_text = []

                if groq_words:
                    for w in groq_words:
                        w_dict = w if isinstance(w, dict) else w.__dict__
                        clean_w = str(w_dict.get("word", "")).strip()
                        if clean_w:
                            words_list.append({
                                "word": clean_w,
                                "start": round(float(w_dict.get("start", 0.0)), 3),
                                "end": round(float(w_dict.get("end", 0.0)), 3),
                                "probability": 1.0
                            })
                
                for segment in segments:
                    start = segment["start"] if isinstance(segment, dict) else segment.start
                    end = segment["end"] if isinstance(segment, dict) else segment.end
                    text = segment["text"] if isinstance(segment, dict) else segment.text
                    full_text.append(text)
                    if not groq_words:
                        words_list.extend(self._interpolate_words(text, start, end))

                resp_lang = getattr(response, "language", None) or (response.get("language") if isinstance(response, dict) else None)
                detected_lang = language or resp_lang or "en"
                self.logger.info(f"[FastTranscriber] Groq API transcription successful! (Language: {detected_lang}, Words: {len(words_list)})")
                return {
                    "language": detected_lang,
                    "language_probability": 1.0,
                    "duration": words_list[-1]["end"] if words_list else duration,
                    "full_text": " ".join(full_text),
                    "words": words_list
                }
            else:
                # Long audio: Smart Chunking across Key Pool
                num_chunks = int(math.ceil(duration / chunk_size))
                self.logger.info(
                    f"[FastTranscriber] Long audio detected ({duration:.1f}s / {duration/60:.1f} min). "
                    f"Applying smart chunking into {num_chunks} segments (600s each) with {len(keys)} Groq API keys in rotation..."
                )

                all_words = []
                all_text = []

                for i in range(num_chunks):
                    start_sec = i * chunk_size
                    chunk_dur = min(chunk_size, duration - start_sec)
                    chunk_file = compressed_path.replace(".mp3", f"_chunk_{i}.mp3")

                    cut_cmd = [
                        "ffmpeg", "-y", "-ss", str(start_sec), "-t", str(chunk_dur),
                        "-i", compressed_path, "-c", "copy", chunk_file
                    ]
                    subprocess.run(cut_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                    key_idx = i % len(keys)
                    success = False
                    attempts = 0

                    while not success and attempts < len(keys):
                        current_key = keys[key_idx]
                        masked_key = current_key[:8] + "..." + current_key[-4:]
                        try:
                            client = Groq(api_key=current_key)
                            with open(chunk_file, "rb") as f:
                                response = client.audio.transcriptions.create(
                                    file=(os.path.basename(chunk_file), f.read()),
                                    model="whisper-large-v3-turbo",
                                    response_format="verbose_json",
                                    language=lang_code
                                )
                            success = True
                        except Exception as ce:
                            attempts += 1
                            self.logger.warning(
                                f"[FastTranscriber] Chunk {i+1}/{num_chunks} with key #{key_idx+1} ({masked_key}) rate-limited or failed: {ce}. "
                                f"Rotating to next key..."
                            )
                            key_idx = (key_idx + 1) % len(keys)
                            time.sleep(1)

                    if os.path.exists(chunk_file):
                        try:
                            os.remove(chunk_file)
                        except Exception:
                            pass

                    if not success:
                        self.logger.error(f"[FastTranscriber] All Groq API keys failed on chunk {i+1}/{num_chunks}!")
                        return None

                    segments = getattr(response, "segments", []) or (response.get("segments", []) if isinstance(response, dict) else [])
                    for seg in segments:
                        s_start = (seg["start"] if isinstance(seg, dict) else seg.start) + start_sec
                        s_end = (seg["end"] if isinstance(seg, dict) else seg.end) + start_sec
                        s_text = seg["text"] if isinstance(seg, dict) else seg.text
                        all_text.append(s_text)
                        all_words.extend(self._interpolate_words(s_text, s_start, s_end))

                    self.logger.info(
                        f"[FastTranscriber] Completed chunk {i+1}/{num_chunks} ({start_sec:.0f}s - {start_sec+chunk_dur:.0f}s). "
                        f"Cumulative words: {len(all_words):,}"
                    )

                detected_lang = language or "en"
                self.logger.info(f"[FastTranscriber] All {num_chunks} chunks successfully transcribed! Total words: {len(all_words):,}")
                return {
                    "language": detected_lang,
                    "language_probability": 1.0,
                    "duration": all_words[-1]["end"] if all_words else duration,
                    "full_text": " ".join(all_text),
                    "words": all_words
                }

        except Exception as e:
            self.logger.error(f"[FastTranscriber] Groq API transcription failed: {e}")
            return None

    def transcribe(
        self,
        audio_path: str,
        cache_json_path: Optional[str] = None,
        url: Optional[str] = None,
        api_key: Optional[Union[str, List[str]]] = None,
        language: str = "en"
    ) -> Dict[str, Any]:
        """
        Main routing function. Tries:
        1. Native YouTube Transcript (1-2s)
        2. Groq Multilingual Whisper with smart chunking & key rotation (<30s)
        3. Local faster-whisper fallback (if Groq offline)
        """
        if cache_json_path and os.path.exists(cache_json_path):
            self.logger.info(f"[Transcriber] Loading cached transcript from: {cache_json_path}")
            with open(cache_json_path, "r", encoding="utf-8") as f:
                return json.load(f)

        result = None

        # 1. Try Native YouTube Transcript
        if url:
            import re
            match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", url)
            vid_id = match.group(1) if match else None
            if vid_id:
                result = self._fetch_youtube_native(vid_id, target_language=language)

        # 2. Try Groq Whisper API (with smart chunking & key rotation)
        if not result and api_key:
            result = self._fetch_groq_whisper(audio_path, api_key, language=language)

        # 3. Fallback to local faster-whisper if Groq fails or is offline
        if not result and os.path.exists(audio_path):
            self.logger.warning("[FastTranscriber] Groq Whisper API unavailable. Falling back to local faster-whisper engine...")
            try:
                from faster_whisper import WhisperModel
                clean_lang = resolve_language_code(language)
                threads = getattr(self, "cpu_threads", 4) or 4
                self.logger.info(f"[FastTranscriber] Initializing local faster-whisper on CPU (model='tiny', threads={threads})...")
                local_model = WhisperModel("tiny", device="cpu", compute_type="int8", cpu_threads=threads)
                total_dur = self._get_audio_duration(audio_path)
                self.logger.info(f"[FastTranscriber] Transcribing {total_dur:.1f}s audio locally on CPU...")
                segments, info = local_model.transcribe(audio_path, language=clean_lang, word_timestamps=False, beam_size=1)
                words_list = []
                full_text = []
                last_log_t = time.time()
                for s in segments:
                    full_text.append(s.text)
                    words_list.extend(self._interpolate_words(s.text, s.start, s.end))
                    now = time.time()
                    if now - last_log_t >= 8.0:
                        last_log_t = now
                        pct = (s.end / total_dur * 100.0) if total_dur > 0 else 0.0
                        self.logger.info(f"[FastTranscriber Local CPU] Transcribed: {s.end:.1f}s / {total_dur:.1f}s ({pct:.1f}%)...")
                result = {
                    "language": clean_lang,
                    "language_probability": 1.0,
                    "duration": words_list[-1]["end"] if words_list else 0.0,
                    "full_text": " ".join(full_text),
                    "words": words_list
                }
                self.logger.info(f"[FastTranscriber] Local faster-whisper transcription successful! Words: {len(words_list)}")
            except Exception as fe:
                self.logger.error(f"[FastTranscriber] Local faster-whisper fallback error: {fe}")

        if not result:
            raise RuntimeError("Both YouTube Native Transcript, Groq Whisper API, and local Whisper failed. Cannot transcribe video.")

        self.logger.info(f"[Transcriber] Transcription finished. Total words extracted: {len(result['words'])}")

        if cache_json_path:
            os.makedirs(os.path.dirname(cache_json_path), exist_ok=True)
            with open(cache_json_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

        return result

    def align_partial_clip_words(
        self,
        media_path: str,
        api_key: Optional[str] = None,
        language: str = "en"
    ) -> List[Dict[str, Any]]:
        """
        Runs word-level alignment on a short cut clip (<60s) using high-precision Groq Whisper Large-V3.
        Eliminates low-parameter local models that cause phonetic hallucinations.
        """
        if not os.path.exists(media_path):
            self.logger.error(f"[FastTranscriber] Media file not found for alignment: {media_path}")
            return []

        clean_lang = resolve_language_code(language)

        # Primary: High-Precision Groq Whisper Large-V3 API (<1s)
        if api_key:
            temp_audio = None
            try:
                self.logger.info(f"[FastTranscriber] Extracting audio for Groq Whisper Large-V3 word alignment (language='{clean_lang}')...")
                temp_audio = media_path.replace(".mp4", "_align_temp.mp3")
                cmd = [
                    "ffmpeg", "-y", "-i", media_path,
                    "-vn", "-ar", "16000", "-ac", "1", "-b:a", "64k",
                    temp_audio
                ]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

                client = Groq(api_key=api_key)
                with open(temp_audio, "rb") as f:
                    req_kwargs = {
                        "file": (os.path.basename(temp_audio), f.read()),
                        "model": "whisper-large-v3",
                        "response_format": "verbose_json",
                        "timestamp_granularities": ["word"]
                    }
                    if clean_lang:
                        req_kwargs["language"] = clean_lang

                    response = client.audio.transcriptions.create(**req_kwargs)

                groq_words = getattr(response, "words", None)
                if not groq_words and isinstance(response, dict):
                    groq_words = response.get("words", [])

                aligned_words = []
                if groq_words:
                    for w in groq_words:
                        w_dict = w if isinstance(w, dict) else w.__dict__
                        clean_w = w_dict.get("word", "").strip()
                        if clean_w:
                            aligned_words.append({
                                "word": clean_w,
                                "start": round(float(w_dict.get("start", 0.0)), 3),
                                "end": round(float(w_dict.get("end", 0.0)), 3),
                                "probability": 1.0
                            })

                if aligned_words:
                    self.logger.info(f"[FastTranscriber] Groq Whisper Large-V3 alignment successful! Extracted {len(aligned_words)} words.")
                    return aligned_words
            except Exception as ge:
                self.logger.error(f"[FastTranscriber] Groq Whisper Large-V3 alignment error: {ge}")
            finally:
                if temp_audio and os.path.exists(temp_audio):
                    try:
                        os.remove(temp_audio)
                    except Exception:
                        pass

        return []
