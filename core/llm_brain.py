import json
import logging
import time
import re
import os
import threading
from typing import Dict, Any, List, Optional, Tuple, Set
from concurrent.futures import ThreadPoolExecutor, as_completed
import groq
from groq import Groq


class GlobalApiPacer:
    """
    Enforces a mandatory minimum delay between consecutive LLM API calls
    to guarantee we never exceed rate limits (e.g. 2.5s delay <= 24 RPM, safely under 30 RPM limit).
    """

    def __init__(self, min_interval: float = 2.5):
        self.min_interval = min_interval
        self.last_call_time = 0.0
        self._lock = threading.Lock()

    def wait_for_slot(self) -> float:
        """
        Waits until min_interval seconds have elapsed since the last API call.
        Returns the duration slept (0.0 if no sleep was needed).
        """
        with self._lock:
            now = time.time()
            elapsed = now - self.last_call_time
            sleep_needed = self.min_interval - elapsed
            if sleep_needed > 0:
                time.sleep(sleep_needed)
                actual_slept = sleep_needed
            else:
                actual_slept = 0.0
            self.last_call_time = time.time()
            return actual_slept


class PrimaryFallbackKeyManager:
    """
    Primary/Fallback API Key Architecture for failover and redundancy.
    - Defaults to PRIMARY_API_KEY for all operations.
    - If 401 Unauthorized, 403 Forbidden, or invalid key error occurs on Primary Key:
      Instantly swaps to FALLBACK_API_KEY and retries the exact same chunk.
      Logs console warning: "WARNING: Primary API Key failed/invalid. Switched to Fallback Key."
    - If 429 Rate Limit error occurs:
      Shared account limit — DOES NOT swap to Fallback Key.
      Parses retry-after header, waits that exact amount of time, and retries using the current key.
    """

    def __init__(
        self,
        primary_api_key: Optional[str] = None,
        fallback_api_key: Optional[str] = None,
        api_keys: Optional[List[str]] = None,
        env_file_path: str = ".env",
        logger: Optional[logging.Logger] = None
    ):
        self.logger = logger or logging.getLogger("KeyManager")
        self._lock = threading.Lock()

        env_keys = self._load_from_env_or_file(env_file_path)

        # 1. Primary API Key resolution
        raw_primary = (
            primary_api_key or
            (api_keys[0] if api_keys and len(api_keys) > 0 and api_keys[0] else None) or
            env_keys.get("PRIMARY_API_KEY") or
            env_keys.get("GROQ_PRIMARY_API_KEY") or
            env_keys.get("GROQ_API_KEY") or
            ""
        )
        self.primary_key = raw_primary.strip() if raw_primary else ""

        # 2. Fallback API Key resolution
        raw_fallback = (
            fallback_api_key or
            (api_keys[1] if api_keys and len(api_keys) > 1 and api_keys[1] else None) or
            env_keys.get("FALLBACK_API_KEY") or
            env_keys.get("GROQ_FALLBACK_API_KEY") or
            ""
        )
        self.fallback_key = raw_fallback.strip() if raw_fallback else ""

        if not self.primary_key:
            raise ValueError("PRIMARY_API_KEY is required. Please set PRIMARY_API_KEY or configure groq_api_key.")

        self.has_failed_over = False
        self.active_key = self.primary_key
        self.active_key_name = "PRIMARY_API_KEY"

        masked_p = self.mask_key(self.primary_key)
        masked_f = self.mask_key(self.fallback_key) if self.fallback_key else "None configured"
        self.logger.info(f"[KeyManager] Initialized with Primary: {masked_p} | Fallback: {masked_f}")

    @staticmethod
    def _load_from_env_or_file(file_path: str = ".env") -> Dict[str, str]:
        res = {}
        # Environment variables
        for var in [
            "PRIMARY_API_KEY", "FALLBACK_API_KEY",
            "GROQ_PRIMARY_API_KEY", "GROQ_FALLBACK_API_KEY",
            "GROQ_API_KEY", "GROQ_API_KEYS"
        ]:
            val = os.environ.get(var)
            if val:
                res[var] = val.strip()

        # .env file
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if v and k not in res:
                            res[k] = v
            except Exception:
                pass
        return res

    def mask_key(self, key: str) -> str:
        """Returns masked key e.g. gsk_abc...xyz1 for safe logging."""
        if not key:
            return "empty"
        if len(key) <= 12:
            return key[:3] + "..." + key[-3:]
        return key[:7] + "..." + key[-4:]

    def get_active_key(self) -> Tuple[str, str]:
        """Returns (active_key, active_key_name) thread-safely."""
        with self._lock:
            return self.active_key, self.active_key_name

    def trigger_failover(self) -> bool:
        """
        Thread-safely swaps from Primary Key to Fallback Key.
        Returns True if swapped, False if no fallback key configured.
        """
        with self._lock:
            if not self.fallback_key:
                self.logger.warning("[KeyManager] Failover requested but no FALLBACK_API_KEY is configured!")
                return False

            if not self.has_failed_over:
                self.has_failed_over = True
                self.active_key = self.fallback_key
                self.active_key_name = "FALLBACK_API_KEY"
                msg = "WARNING: Primary API Key failed/invalid. Switched to Fallback Key."
                print(msg)
                self.logger.warning(msg)
            return True

    def parse_retry_after(self, error: Exception, default_cooldown: Optional[float] = None) -> Optional[float]:
        """
        Extracts retry-after duration from Groq RateLimitError response headers or error message.
        Returns float duration in seconds if found, or default_cooldown (default None) if missing.
        """
        resp = getattr(error, "response", None)
        if resp is not None:
            headers = getattr(resp, "headers", {})
            retry_header = headers.get("retry-after") or headers.get("Retry-After")
            if retry_header:
                try:
                    return max(1.0, float(retry_header))
                except (ValueError, TypeError):
                    pass

            reset_req = headers.get("x-ratelimit-reset-requests") or headers.get("x-ratelimit-reset-tokens")
            if reset_req:
                try:
                    raw_str = str(reset_req).strip()
                    is_ms = "ms" in raw_str.lower()
                    clean_str = re.sub(r"[^\d.]", "", raw_str)
                    val = float(clean_str)
                    if is_ms:
                        val = val / 1000.0
                    return max(1.0, val)
                except Exception:
                    pass

        err_msg = str(error)
        match = re.search(r"try again in ([0-9.]+)\s*(s|ms|m|minutes?|seconds?)?", err_msg, re.IGNORECASE)
        if match:
            try:
                val = float(match.group(1))
                unit = (match.group(2) or "s").lower()
                if unit == "ms":
                    val = val / 1000.0
                elif unit.startswith("m"):
                    val = val * 60.0
                return max(1.0, val)
            except Exception:
                pass

        return default_cooldown


class MultiAccountKeyPool:
    """
    Multi-Account Load Balancer for Groq API keys generated from distinct accounts.
    - Loads all API keys from environment variables (GROQ_API_KEY_1..5, GROQ_API_KEYS, etc.),
      .env file, and configured profile pools.
    - Round-Robin dispatcher across parallel workers.
    - Independent per-key cooldown on 429 errors without pausing the entire batch.
    - Evicts dead keys permanently on 401/403 authentication failures.
    """

    def __init__(
        self,
        api_keys: Optional[List[str]] = None,
        env_file_path: str = ".env",
        logger: Optional[logging.Logger] = None
    ):
        self.logger = logger or logging.getLogger("KeyPool")
        self._lock = threading.Lock()

        self.keys: List[str] = self._discover_keys(api_keys=api_keys, env_file_path=env_file_path)
        if not self.keys:
            raise ValueError("No Groq API Keys found! Please set GROQ_API_KEY_1..5 or configure keys in settings.")

        self.dead_keys: Set[str] = set()
        self.cooldowns: Dict[str, float] = {}  # key -> timestamp when cooldown expires
        self._rr_index: int = 0

        masked_list = [f"Account #{i+1} ({self.mask_key(k)})" for i, k in enumerate(self.keys)]
        self.logger.info(
            f"[KeyPool] Multi-Account Load Balancer initialized with {len(self.keys)} distinct accounts: {', '.join(masked_list)}"
        )

    @classmethod
    def _discover_keys(cls, api_keys: Optional[List[str]] = None, env_file_path: str = ".env") -> List[str]:
        keys = []

        def add_key(val: Optional[str]):
            if not val:
                return
            clean = val.strip().strip('"').strip("'")
            if clean and clean not in keys:
                keys.append(clean)

        # 1. Keys passed directly in constructor (e.g. from settings.json profile)
        if api_keys:
            for k in api_keys:
                add_key(k)

        # 2. Numbered environment variables: GROQ_API_KEY_1 .. GROQ_API_KEY_10, GROQ_KEY_1..10
        for prefix in ["GROQ_API_KEY_", "GROQ_KEY_", "API_KEY_"]:
            for i in range(1, 11):
                add_key(os.environ.get(f"{prefix}{i}"))

        # 3. Delimited environment strings (GROQ_API_KEYS="key1,key2,key3")
        for var in ["GROQ_API_KEYS", "GROQ_KEYS"]:
            raw_str = os.environ.get(var)
            if raw_str:
                for chunk in re.split(r"[,;\n\s]+", raw_str):
                    add_key(chunk)

        # 4. Standard single key env variables
        for var in ["PRIMARY_API_KEY", "FALLBACK_API_KEY", "GROQ_PRIMARY_API_KEY", "GROQ_FALLBACK_API_KEY", "GROQ_API_KEY"]:
            add_key(os.environ.get(var))

        # 5. Local .env file if present
        if os.path.exists(env_file_path):
            try:
                with open(env_file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        name, v = line.split("=", 1)
                        name = name.strip().upper()
                        v = v.strip().strip('"').strip("'")
                        if "KEY" in name and ("GROQ" in name or "API" in name):
                            if "," in v or ";" in v:
                                for piece in re.split(r"[,;\s]+", v):
                                    add_key(piece)
                            else:
                                add_key(v)
            except Exception:
                pass

        return keys

    @staticmethod
    def mask_key(key: str) -> str:
        """Returns masked key string (e.g. gsk_H02...UKJf)."""
        if not key:
            return "empty"
        if len(key) <= 12:
            return key[:3] + "..." + key[-3:]
        return key[:7] + "..." + key[-4:]

    def total_active_keys(self) -> int:
        """Returns the count of non-dead keys currently in the pool."""
        with self._lock:
            return len([k for k in self.keys if k not in self.dead_keys])

    def get_next_key(self, exclude_keys: Optional[Set[str]] = None) -> Tuple[str, int]:
        """
        Thread-safe Round-Robin key dispatcher.
        Returns: (selected_key, account_number_1_indexed)
        - Selects the next key that is not dead and not currently in cooldown.
        - Skips keys in exclude_keys (if alternative healthy keys exist).
        - If ALL active keys are in cooldown, waits for the earliest cooldown to expire.
        """
        exclude_set = exclude_keys or set()

        while True:
            with self._lock:
                now = time.time()
                active = [k for k in self.keys if k not in self.dead_keys]
                if not active:
                    raise RuntimeError("[KeyPool] All API keys have failed authentication or are dead!")

                # Filter keys whose cooldown has passed
                ready = [k for k in active if now >= self.cooldowns.get(k, 0.0)]

                # Exclude keys already attempted for this specific chunk if possible
                candidate_pool = [k for k in ready if k not in exclude_set]
                if not candidate_pool and ready:
                    candidate_pool = ready

                if candidate_pool:
                    selected_key = candidate_pool[self._rr_index % len(candidate_pool)]
                    self._rr_index = (self._rr_index + 1) % len(candidate_pool)
                    key_idx = self.keys.index(selected_key) + 1
                    return selected_key, key_idx

                # All active keys are currently on cooldown: calculate wait time for earliest
                earliest_key = min(active, key=lambda k: self.cooldowns.get(k, 0.0))
                wait_needed = self.cooldowns.get(earliest_key, 0.0) - now

            if wait_needed > 0:
                self.logger.warning(
                    f"[KeyPool] All {len(active)} accounts are in temporary cooldown. "
                    f"Waiting {wait_needed:.1f}s for Account #{self.keys.index(earliest_key) + 1} to become available..."
                )
                time.sleep(min(wait_needed + 0.1, 10.0))
                with self._lock:
                    self.cooldowns[earliest_key] = 0.0

    def mark_cooldown(self, key: str, duration: float):
        """Places only this specific key on cooldown without affecting other accounts."""
        with self._lock:
            key_num = self.keys.index(key) + 1 if key in self.keys else "?"
            self.cooldowns[key] = time.time() + duration
            now = time.time()
            ready_count = len([k for k in self.keys if k not in self.dead_keys and now >= self.cooldowns.get(k, 0.0)])
            self.logger.warning(
                f"[KeyPool] ⏳ Account #{key_num} ({self.mask_key(key)}) hit 429 Rate Limit! "
                f"Cooldown active for {duration:.1f}s. Ready accounts still processing: {ready_count}."
            )

    def mark_dead_key(self, key: str, reason: str):
        """Permanently evicts a key from active rotation upon auth failure (401/403)."""
        with self._lock:
            if key not in self.dead_keys:
                self.dead_keys.add(key)
                key_num = self.keys.index(key) + 1 if key in self.keys else "?"
                remaining = len([k for k in self.keys if k not in self.dead_keys])
                msg = (
                    f"WARNING: DEAD KEY REMOVED: Account #{key_num} ({self.mask_key(key)}) disabled "
                    f"for remainder of batch ({reason}). Remaining active accounts: {remaining}."
                )
                print(msg)
                self.logger.warning(msg)

    @property
    def has_failed_over(self) -> bool:
        with self._lock:
            return len(self.dead_keys) > 0

    def parse_retry_after(self, error: Exception, default_cooldown: float = 10.0) -> float:
        """Extracts retry-after duration from Groq RateLimitError response headers or error message."""
        resp = getattr(error, "response", None)
        if resp is not None:
            headers = getattr(resp, "headers", {})
            retry_header = headers.get("retry-after") or headers.get("Retry-After")
            if retry_header:
                try:
                    return max(1.0, float(retry_header))
                except (ValueError, TypeError):
                    pass

            reset_req = headers.get("x-ratelimit-reset-requests") or headers.get("x-ratelimit-reset-tokens")
            if reset_req:
                try:
                    raw_str = str(reset_req).strip()
                    is_ms = "ms" in raw_str.lower()
                    clean_str = re.sub(r"[^\d.]", "", raw_str)
                    val = float(clean_str)
                    if is_ms:
                        val = val / 1000.0
                    return max(1.0, val)
                except Exception:
                    pass

        err_msg = str(error)
        match = re.search(r"try again in ([0-9.]+)\s*(s|ms|m|minutes?|seconds?)?", err_msg, re.IGNORECASE)
        if match:
            try:
                val = float(match.group(1))
                unit = (match.group(2) or "s").lower()
                if unit == "ms":
                    val = val / 1000.0
                elif unit.startswith("m"):
                    val = val * 60.0
                return max(1.0, val)
            except Exception:
                pass

        return default_cooldown


class ViralClipExtractor:
    """
    LLM Brain Engine leveraging Groq API with Primary/Fallback Key Architecture.
    Extracts high-hook, high-quality educational content under 60 seconds (max 58s).
    """

    def __init__(
        self,
        api_keys: Optional[List[str]] = None,
        primary_api_key: Optional[str] = None,
        fallback_api_key: Optional[str] = None,
        model_name: str = "openai/gpt-oss-120b",
        logger: Optional[logging.Logger] = None,
        min_pacing_interval: float = 0.0
    ):
        self.logger = logger or logging.getLogger("AMBEnterprise")
        combined_keys = []
        if api_keys:
            combined_keys.extend(api_keys)
        if primary_api_key and primary_api_key not in combined_keys:
            combined_keys.append(primary_api_key)
        if fallback_api_key and fallback_api_key not in combined_keys:
            combined_keys.append(fallback_api_key)

        self.key_pool = MultiAccountKeyPool(api_keys=combined_keys, logger=self.logger)
        self.key_manager = self.key_pool  # Backward compatibility reference
        self.api_keys = self.key_pool.keys
        self.model_name = model_name
        self.pacer = GlobalApiPacer(min_interval=min_pacing_interval) if min_pacing_interval > 0 else None

    def _prepare_transcript_summary(self, words: List[Dict[str, Any]], interval_seconds: float = 5.0) -> str:
        """
        Compresses word-timestamp list into timestamped text chunks (e.g., [00:15 - 00:20] text)
        to minimize prompt token size while preserving precise timing context.
        """
        lines = []
        current_chunk = []
        chunk_start = None

        for w in words:
            if chunk_start is None:
                chunk_start = w["start"]
            
            current_chunk.append(w["word"])
            
            if w["end"] - chunk_start >= interval_seconds:
                text = " ".join(current_chunk)
                lines.append(f"[{chunk_start:.1f}s - {w['end']:.1f}s] {text}")
                current_chunk = []
                chunk_start = None

        if current_chunk and chunk_start is not None:
            text = " ".join(current_chunk)
            lines.append(f"[{chunk_start:.1f}s - {words[-1]['end']:.1f}s] {text}")

        return "\n".join(lines)

    def _chunk_transcript(self, formatted_transcript: str, max_words_per_chunk: int = 800) -> List[str]:
        """
        Splits the formatted transcript string into chunks of roughly max_words_per_chunk.
        Splits strictly at line breaks to preserve timestamp integrity.
        """
        chunks = []
        current_chunk_lines = []
        current_word_count = 0

        for line in formatted_transcript.split('\n'):
            line_word_count = len(line.split())
            if current_word_count + line_word_count > max_words_per_chunk and current_chunk_lines:
                chunks.append("\n".join(current_chunk_lines))
                current_chunk_lines = []
                current_word_count = 0
            
            current_chunk_lines.append(line)
            current_word_count += line_word_count

        if current_chunk_lines:
            chunks.append("\n".join(current_chunk_lines))

        return chunks

    @staticmethod
    def _clean_and_parse_json(content: str) -> Dict[str, Any]:
        """Strips markdown fences and parses JSON safely."""
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"(\{.*\})", text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            raise

    def _process_single_chunk(
        self,
        chunk_index: int,
        total_chunks: int,
        chunk_text: str,
        system_prompt: str,
        topic_focus: str,
        min_duration: float,
        max_duration: float,
        custom_user_prompt: Optional[str] = None,
        content_focus_description: Optional[str] = None,
        channel_name: Optional[str] = None
    ) -> Tuple[int, List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Processes a single transcript chunk with retry mechanism and error isolation.
        Returns: (chunk_index, valid_chunk_clips, failed_chunk_info_or_None)
        """
        if custom_user_prompt:
            user_prompt = custom_user_prompt
        else:
            focus_desc = content_focus_description or topic_focus
            is_nutrilogic = (channel_name == "Nutrilogic Way") or ("bodybuilding" in str(topic_focus).lower())
            if is_nutrilogic:
                relevance_rule = (
                    "2. HARDCORE GYM & BODYBUILDING FOCUS (STRICT RULE): The clip MUST deliver high-energy gym motivation, "
                    "bodybuilding insights, heavy lifting PRs, or raw supplement breakdowns (pre-workout, creatine, whey, mass gains). "
                    "STRICTLY FORBIDDEN: Reject and ignore textbook digestion explanations, dietary fiber, organic vegetables, "
                    "or food pyramids. Hook the viewer immediately in the first 2 seconds!\n"
                )
            else:
                relevance_rule = (
                    f"2. CONTENT RELEVANCE OVER SENSATIONALISM: The clip MUST deliver a clear insight, lesson, breakdown, or practical wisdom on '{topic_focus}'. It does NOT need to be shocking clickbait or controversial drama. Thoughtful explanations or clear breakdowns are preferred.\n"
                )

            user_prompt = (
                f"Here is chunk {chunk_index} of the timestamped transcript (may be in English, Hindi, Urdu, or other languages):\n\n{chunk_text}\n\n"
                f"MANDATORY REQUIREMENTS:\n"
                f"1. Extract 1 to 2 high-value video clips focused on '{topic_focus}' (including {focus_desc}). Keep title and rationale concise (under 25 words each).\n"
                f"{relevance_rule}"
                f"3. STRICT UNDER-1-MINUTE RULE: Every clip's duration (end_time - start_time) MUST be between {min_duration:.1f}s and {max_duration:.1f}s.\n"
                f"4. DEAD MINIMUM: {min_duration:.1f} SECONDS. NEVER select short snippets under {min_duration:.1f}s (no 10s, 20s, or 30s clips). Include the speaker's full explanation, story, and conclusion to naturally span 50 to 58 seconds.\n"
                f"5. LANGUAGE SUPPORT: If the transcript is in Hindi, Urdu, or another language, keep the exact timestamps and hook, and provide an engaging English title and rationale.\n"
                f"6. VIRAL HASHTAGS: For each clip, include a 'hashtags' array with 5 to 8 niche-targeted viral hashtags starting with '#' (e.g. ['#shorts', '#wealth', '#business', '#money', '#mindset', '#billionaire', '#success']).\n"
                f"7. Output ONLY valid JSON with structure: {{\"clips\": [{{\"title\": \"...\", \"hook\": \"...\", \"start_time\": float, \"end_time\": float, \"rationale\": \"...\", \"hashtags\": [\"#shorts\", ...]}}]}}.\n"
                f"8. If no segment in this chunk relates to '{topic_focus}' or {focus_desc}, output strictly: {{\"clips\": []}}."
            )

        max_attempts = 3
        attempt = 0
        last_error = None
        failed_keys_for_chunk: Set[str] = set()

        while attempt < max_attempts:
            try:
                current_key, key_num = self.key_pool.get_next_key(exclude_keys=failed_keys_for_chunk)
            except RuntimeError as re_err:
                self.logger.error(f"[LLMBrain] No operational keys remaining: {re_err}")
                last_error = re_err
                break

            masked_key = self.key_pool.mask_key(current_key)
            client = Groq(api_key=current_key)

            try:
                self.logger.info(
                    f"[LLMBrain] Processing Chunk {chunk_index}/{total_chunks} (Attempt {attempt + 1}/{max_attempts}, Account #{key_num}: {masked_key})..."
                )
                if self.pacer:
                    slept = self.pacer.wait_for_slot()
                    if slept > 0:
                        self.logger.debug(f"[LLMBrain] Pacer delay: {slept:.2f}s")

                response = client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.3,
                    max_tokens=3500,
                    response_format={"type": "json_object"}
                )

                response_content = response.choices[0].message.content
                parsed = self._clean_and_parse_json(response_content)
                raw_clips = parsed.get("clips", [])

                valid_chunk_clips = []
                for c in raw_clips:
                    try:
                        s = float(c.get("start_time", 0))
                        e = float(c.get("end_time", 0))
                        dur = round(e - s, 1)
                        c["duration"] = dur

                        if dur < min_duration:
                            self.logger.warning(
                                f"[LLMBrain] Rejected clip '{c.get('title')}' in Chunk {chunk_index} - duration {dur}s is below dead minimum {min_duration}s."
                            )
                            continue
                        if dur > max_duration:
                            self.logger.warning(
                                f"[LLMBrain] Rejected clip '{c.get('title')}' in Chunk {chunk_index} - duration {dur}s exceeds maximum {max_duration}s."
                            )
                            continue

                        # Ensure both topic_relevance_score and viral_score are present
                        score = float(c.get("topic_relevance_score") or c.get("viral_score") or 90)
                        c["topic_relevance_score"] = score
                        c["viral_score"] = score

                        # Parse and clean hashtags
                        raw_tags = c.get("hashtags", [])
                        clean_tags = []
                        if isinstance(raw_tags, list):
                            for t in raw_tags:
                                st = str(t).strip()
                                if st:
                                    clean_tags.append(st if st.startswith("#") else f"#{st}")
                        c["hashtags"] = clean_tags

                        valid_chunk_clips.append(c)
                    except Exception as ce:
                        self.logger.warning(f"[LLMBrain] Skipping invalid clip structure in Chunk {chunk_index}: {ce}")

                self.logger.info(f"[LLMBrain] Chunk {chunk_index}/{total_chunks} succeeded via Account #{key_num}! Extracted {len(valid_chunk_clips)} compliant clips.")
                return chunk_index, valid_chunk_clips, None

            except Exception as e:
                last_error = e
                err_str = str(e).lower()
                status_code = getattr(e, "status_code", None)

                # 1. Check for 401 Unauthorized / 403 Forbidden / Invalid Key -> Permanently remove dead key
                is_auth_error = (
                    isinstance(e, (groq.AuthenticationError, groq.PermissionDeniedError)) or
                    status_code in [401, 403] or
                    "401" in err_str or "403" in err_str or
                    "invalid api key" in err_str or "invalid_api_key" in err_str or
                    "unauthorized" in err_str or "forbidden" in err_str
                )

                if is_auth_error:
                    self.key_pool.mark_dead_key(current_key, reason=str(e))
                    failed_keys_for_chunk.add(current_key)
                    self.logger.warning(
                        f"[LLMBrain] Chunk {chunk_index} re-routing immediately away from dead Account #{key_num}..."
                    )
                    # Re-route chunk immediately without consuming an attempt
                    continue

                # 2. Check for 429 Rate Limit -> Independent key cooldown; DO NOT pause batch, instantly re-route!
                is_429 = (
                    isinstance(e, groq.RateLimitError) or
                    status_code == 429 or
                    "429" in err_str or "rate limit" in err_str
                )

                if is_429:
                    wait_time = self.key_pool.parse_retry_after(e, default_cooldown=10.0)
                    self.key_pool.mark_cooldown(current_key, wait_time)
                    failed_keys_for_chunk.add(current_key)
                    self.logger.warning(
                        f"[LLMBrain] Chunk {chunk_index} hit 429 Rate Limit on Account #{key_num}! "
                        f"Instantly re-routing Chunk {chunk_index} to next available account in pool..."
                    )
                    # DO NOT sleep or pause the whole batch! Instantly re-route to another key
                    continue

                # 3. Other errors (e.g. network timeout, 500 error, malformed JSON)
                attempt += 1
                failed_keys_for_chunk.add(current_key)
                self.logger.warning(
                    f"[LLMBrain] Error in Chunk {chunk_index}/{total_chunks} (Attempt {attempt}/{max_attempts}, Account #{key_num}: {masked_key}): {e}"
                )
                if attempt < max_attempts:
                    self.logger.info(f"[LLMBrain] Retrying Chunk {chunk_index} with another account...")
                    time.sleep(1.0)

        # Retries exhausted - isolate failure
        failure_info = {
            "chunk_index": chunk_index,
            "error": str(last_error),
            "preview": chunk_text[:200]
        }
        self.logger.error(
            f"[LLMBrain] Chunk {chunk_index}/{total_chunks} failed permanently after {max_attempts} attempts: {last_error}. Isolating failure."
        )
        return chunk_index, [], failure_info

    def extract_viral_clips(
        self,
        transcript_data: Dict[str, Any],
        clip_count: int = 3,
        topic_focus: str = "wealth and money concepts",
        min_duration: float = 50.0,
        max_duration: float = 58.0,
        max_transcript_minutes: float = 20.0,
        channel_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Queries Groq API with JSON mode enabled to find clips under 60 seconds (strictly 50s - 58s).
        Enforces the 'Under 1 Minute Rule' (dead minimum 50 seconds, absolute maximum 58 seconds).
        Enforces the '20-Minute Rule' to analyze only the first 20 minutes of long-form videos.
        Uses ThreadPoolExecutor for concurrent chunk execution and isolates any failed chunks.
        """
        words = transcript_data.get("words", [])
        if not words:
            self.logger.error("[LLMBrain] Transcript has no words to analyze!")
            return []

        # MANDATORY 20-MINUTE TRANSCRIPT RULE:
        # Strictly limit transcript analysis to the first 20 minutes (0 to 1200s).
        # This keeps the chunk count down to ~3-4 chunks total (eliminating 64-chunk bloat)
        # and drastically accelerates pipeline processing.
        max_timestamp = max_transcript_minutes * 60.0  # 1200.0 seconds
        capped_words = [w for w in words if w.get("start", 0.0) <= max_timestamp]
        if capped_words:
            self.logger.info(
                f"[LLMBrain] ⚡ Enforcing 20-Minute Rule: Filtered transcript from {len(words)} words "
                f"down to {len(capped_words)} words (capped at {max_transcript_minutes:.0f}:00 / {max_timestamp:.1f}s)."
            )
            words = capped_words

        self.logger.info(f"[LLMBrain] Preparing transcript for Groq ({len(words)} words)...")
        formatted_transcript = self._prepare_transcript_summary(words)

        # Split transcript into safe chunks (~1200 words per chunk: ~4-5 chunks for 20 minutes)
        transcript_chunks = self._chunk_transcript(formatted_transcript, max_words_per_chunk=1200)
        self.logger.info(f"[LLMBrain] Transcript split into {len(transcript_chunks)} chunks (~1200 words each, 20-min cap) for rapid parallel processing.")

        # Dynamically resolve channel context for tailored prompt instructions
        from config import ConfigManager
        ctx = ConfigManager.get_channel_context(channel_name or topic_focus)
        content_type = ctx.get("content_type", "video")
        system_instruction = ctx.get(
            "system_instruction",
            f"You are an expert {content_type} analyst and video curator specializing in '{topic_focus}'."
        )
        content_desc = ctx.get("content_focus_description", topic_focus)
        niche_name = ctx.get("niche_name", "Curated Niche")

        system_prompt = f"""
{system_instruction}
Your mission is to analyze the provided timestamped transcript and extract insightful, high-retention short-form video segments for {niche_name} following the STRICT "UNDER 1 MINUTE RULE".

CRITICAL FORMATTING INSTRUCTION:
Output ONLY valid JSON. Do not include markdown formatting, code blocks, conversational text, or explanations.
If no segment in this transcript chunk meets all criteria, output strictly: {{"clips": []}}.

MANDATORY RULES:
1. CONTENT RELEVANCE OVER SENSATIONALISM:
   - The primary requirement is that the clip DELIVERS CLEAR, VALUABLE INSIGHT OR PRACTICAL KNOWLEDGE MATCHING '{topic_focus}'.
   - It is NOT required for the segment to be hyper-viral shock clickbait or controversial drama.
   - High-retention educational value, engaging breakdowns, expert reviews, and actionable lessons centered on {content_desc} are ideal.

2. THE UNDER-1-MINUTE RETENTION RULE:
   - Every clip MUST have an exact duration between {min_duration:.1f}s and {max_duration:.1f}s (Target: 52s - 58s).
   - DEAD MINIMUM: {min_duration:.1f} SECONDS. ABSOLUTELY NEVER return clips shorter than {min_duration:.1f}s (NO 10s, 20s, or 30s clips!). Any clip under {min_duration:.1f}s fails YouTube Shorts retention algorithms and is strictly disqualified.
   - ABSOLUTE MAXIMUM: {max_duration:.1f} SECONDS. NEVER exceed 59 seconds.
   - FORMULA: `end_time - start_time` MUST be >= {min_duration:.1f} and <= {max_duration:.1f}. Set `duration = end_time - start_time`.

3. HOW TO STRUCTURE A 50-58 SECOND INSIGHT CLIP:
   - 0-5s (The Opening): An engaging statement, question, or key principle introducing the topic/lesson.
   - 5-45s (The Body): Deep explanation, real-world story, compelling breakdown, or practical example that maintains high engagement. Include the full context and breakdown so the segment naturally spans 50 to 58 seconds.
   - 45-58s (The Takeaway): A clean conclusion, lesson, or philosophical takeaway that finishes the sentence naturally before {max_duration:.1f} seconds.

4. TOPIC FOCUS & LANGUAGE SUPPORT:
   - Clips MUST center around '{topic_focus}' (including {content_desc}).
   - The transcript may be in English, Hindi, Urdu, or other languages. Preserve original timestamps and hook, and provide an engaging English title and rationale.
   - If the chunk touches on key themes of {content_desc}, extract the best coherent 50-58 second segment.

5. TIMESTAMP ACCURACY:
   - `start_time` starts at the exact beginning of the opening thought.
   - `end_time` ends cleanly at the completion of the sentence, exactly {min_duration:.1f}s to {max_duration:.1f}s after `start_time`.

You MUST respond strictly with a valid JSON object following this exact schema:
{{
  "clips": [
    {{
      "title": "A clear, compelling title reflecting the {niche_name} insight or review",
      "hook": "The exact opening statement text",
      "start_time": 109.8,
      "end_time": 166.2,
      "duration": 56.4,
      "topic_relevance_score": 95,
      "viral_score": 95,
      "rationale": "One brief sentence explaining the insight or review value."
    }}
  ]
}}
"""

        all_clips = []
        failed_chunks = []
        total_chunks = len(transcript_chunks)
        # Multi-Account Concurrency: max_workers equals the number of available active keys in the pool
        active_count = self.key_pool.total_active_keys()
        max_workers = min(active_count, total_chunks) if total_chunks > 0 else 1

        self.logger.info(
            f"[LLMBrain] Dispatching {total_chunks} chunks across {max_workers} concurrent workers "
            f"at FULL SPEED (Load-balanced across {active_count} independent accounts)..."
        )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_chunk = {
                executor.submit(
                    self._process_single_chunk,
                    chunk_index=i + 1,
                    total_chunks=total_chunks,
                    chunk_text=chunk,
                    system_prompt=system_prompt,
                    topic_focus=topic_focus,
                    min_duration=min_duration,
                    max_duration=max_duration,
                    content_focus_description=content_desc,
                    channel_name=ctx.get("channel_name", channel_name)
                ): i + 1
                for i, chunk in enumerate(transcript_chunks)
            }

            for future in as_completed(future_to_chunk):
                chunk_num = future_to_chunk[future]
                try:
                    c_idx, clips, fail_info = future.result()
                    if clips:
                        all_clips.extend(clips)
                    if fail_info:
                        failed_chunks.append(fail_info)
                except Exception as fe:
                    self.logger.error(f"[LLMBrain] Unexpected thread error in Chunk {chunk_num}: {fe}")
                    failed_chunks.append({
                        "chunk_index": chunk_num,
                        "error": str(fe),
                        "preview": ""
                    })

        if failed_chunks:
            self.logger.warning(
                f"[LLMBrain] Batch completed with {len(failed_chunks)} failed chunk(s) isolated: "
                f"{[fc['chunk_index'] for fc in failed_chunks]}. Successful clips extracted: {len(all_clips)}."
            )

        if not all_clips:
            self.logger.warning(
                f"[LLMBrain] Zero clips found matching strict topic '{topic_focus}'. "
                f"Initiating High-Retention Engagement Fallback across all chunks to capture viral highlights..."
            )
            fallback_system_prompt = f"""
You are an expert short-form video editor and viral curator for YouTube Shorts and TikTok.
Your mission is to analyze the provided transcript chunk and extract the most captivating, dramatic, emotional, or insightful 50-58 second video segment.

CRITICAL FORMATTING INSTRUCTION:
Output ONLY valid JSON. Do not include markdown formatting, code blocks, or conversational text.
If no coherent dialogue is present, output strictly: {{"clips": []}}.

MANDATORY RULES:
1. HIGH-ENGAGEMENT VIRAL SELECTION:
   - Identify the most dramatic confessions, shocking revelations, intense debates, powerful lessons, or emotional stories in this chunk.
   - Maintain high viewer curiosity and watch time from the first 3 seconds.

2. THE UNDER-1-MINUTE RETENTION RULE:
   - Every clip MUST have an exact duration between {min_duration:.1f}s and {max_duration:.1f}s (Target: 52s - 58s).
   - DEAD MINIMUM: {min_duration:.1f} SECONDS. ABSOLUTELY NEVER return clips shorter than {min_duration:.1f}s.
   - ABSOLUTE MAXIMUM: {max_duration:.1f} SECONDS. NEVER exceed 59 seconds.
   - FORMULA: `end_time - start_time` MUST be >= {min_duration:.1f} and <= {max_duration:.1f}. Set `duration = end_time - start_time`.

3. LANGUAGE & METADATA:
   - The transcript may be in Urdu, Hindi, English, or other languages. Preserve original timestamps and hook text.
   - Provide an engaging English title, viral_score (85-99), and a concise rationale.

You MUST respond strictly with a valid JSON object:
{{
  "clips": [
    {{
      "title": "A compelling, viral YouTube Shorts title",
      "hook": "The exact opening statement text",
      "start_time": 109.8,
      "end_time": 166.2,
      "duration": 56.4,
      "topic_relevance_score": 90,
      "viral_score": 95,
      "rationale": "Brief sentence explaining why this moment will hook viewers."
    }}
  ]
}}
"""
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_chunk = {
                    executor.submit(
                        self._process_single_chunk,
                        chunk_index=i + 1,
                        total_chunks=total_chunks,
                        chunk_text=chunk,
                        system_prompt=fallback_system_prompt,
                        topic_focus=topic_focus,
                        min_duration=min_duration,
                        max_duration=max_duration,
                        custom_user_prompt=(
                            f"Here is chunk {i + 1} of the timestamped transcript:\n\n{chunk}\n\n"
                            f"MANDATORY REQUIREMENTS:\n"
                            f"1. Extract 1 to 2 of the most compelling, shocking, viral, or engaging 50-58 second video clips from this segment.\n"
                            f"2. Prioritize high-retention moments, dramatic revelations, intense debates, powerful confessions, or key highlights.\n"
                            f"3. STRICT DURATION RULE: Every clip's duration (end_time - start_time) MUST be strictly between {min_duration:.1f}s and {max_duration:.1f}s.\n"
                            f"4. Output strictly valid JSON with English title and rationale. Preserve original transcript timestamps and hook.\n"
                            f"5. If there is absolutely no coherent dialogue, output: {{\"clips\": []}}."
                        )
                    ): i + 1
                    for i, chunk in enumerate(transcript_chunks)
                }

                for future in as_completed(future_to_chunk):
                    chunk_num = future_to_chunk[future]
                    try:
                        c_idx, clips, fail_info = future.result()
                        if clips:
                            all_clips.extend(clips)
                    except Exception as fe:
                        self.logger.error(f"[LLMBrain] Fallback error in Chunk {chunk_num}: {fe}")

        if not all_clips:
            self.logger.error("[LLMBrain] Failed to extract any clips from all chunks.")
            return []

        # Sort all aggregated clips by topic_relevance_score / viral_score descending and take top N
        all_clips.sort(key=lambda x: max(x.get("topic_relevance_score", 0), x.get("viral_score", 0)), reverse=True)
        top_clips = all_clips[:clip_count]

        # Enrich clips with exact word array slices for subtitle synchronization
        enriched_clips = []
        for idx, clip in enumerate(top_clips):
            clip["clip_index"] = idx + 1
            c_start = clip["start_time"]
            c_end = clip["end_time"]

            # Extract matching words
            clip_words = [
                w for w in words
                if w["start"] >= c_start - 0.5 and w["end"] <= c_end + 0.5
            ]

            clip["aligned_words"] = clip_words
            enriched_clips.append(clip)
            self.logger.info(f"[LLMBrain] Final Selected Clip #{clip.get('clip_index')}: '{clip.get('title')}' ({clip.get('duration')}s) | Start: {c_start}s, End: {c_end}s | Score: {clip.get('viral_score')}")

        return enriched_clips
