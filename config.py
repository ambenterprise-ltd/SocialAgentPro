import os
import re
import time
import json
import hashlib
import threading
import logging
from typing import Dict, Any, List, Tuple, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "settings.json")
URDU_FONT_PATH = os.path.join(BASE_DIR, "urdu fonts", "JameelNooriNastaleeq.ttf")


class ConfigManager:
    """
    Thread-safe Configuration Manager for AMB Enterprise.
    Handles loading, saving, active work state persistence for Smart Resume,
    automatic cleanup options, GPU auto-acceleration across all profiles,
    and post-upload 1-hour deletion tracking with duplicate-prevention history.
    """

    CHANNEL_SPECIFIC_KEYS = {
        "groq_api_key",
        "groq_api_keys_pool",
        "youtube_oauth_json_path",
        "template_path",
        "resolution_preset",
        "resolution_width",
        "resolution_height",
        "topic_focus",
        "language",
        "caption_language",
        "caption_color",
        "caption_font",
        "auto_search_keywords",
        "auto_pilot",
        "autopilot_interval_hours",
        "last_autopilot_run",
        "target_clips_per_video",
        "active_video_state",
        "last_generated_clip",
        "generated_clips_history",
        "processed_video_ids",
    }

    SUPPORTED_CAPTION_LANGUAGES: List[str] = [
        "English", "Urdu", "Spanish", "French", "German",
        "Hindi", "Arabic", "Chinese", "Japanese", "Portuguese", "Russian"
    ]

    SUPPORTED_CAPTION_FONTS: List[str] = [
        "Arial Black",
        "Jameel Noori Nastaleeq",
        "Montserrat",
        "Roboto",
        "Impact",
        "Arial",
        "Calibri",
        "Segoe UI"
    ]

    LANGUAGE_TO_CODE: Dict[str, str] = {
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

    CODE_TO_LANGUAGE: Dict[str, str] = {
        "en": "English",
        "ur": "Urdu",
        "es": "Spanish",
        "fr": "French",
        "de": "German",
        "hi": "Hindi",
        "ar": "Arabic",
        "zh": "Chinese",
        "ja": "Japanese",
        "pt": "Portuguese",
        "ru": "Russian"
    }

    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH):
        self.config_path = config_path
        self._config: Dict[str, Any] = {}
        self.logger = logging.getLogger("AMBEnterprise")
        self._copy_uploaded_template()
        self.load_config()
        self.normalize_directories()
        self._init_deletion_timers()

    def _copy_uploaded_template(self):
        """
        Copies any user-uploaded template into assets directory.
        Only attempted on Windows where the Antigravity brain directory exists.
        Silently skipped on EC2/Linux or if the source file is not present.
        """
        uploaded_src = os.path.join(
            os.path.expanduser("~"), ".gemini", "antigravity", "brain",
            "876d8493-7ad2-4726-9d80-0ce3b3005959", ".user_uploaded", "media_1787799510003.png"
        )
        if not os.path.exists(uploaded_src):
            return  # Not on Windows dev machine or file not present — skip silently
        assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
        target_tpl = os.path.join(assets_dir, "wealth_secrets_template.png")
        try:
            import shutil
            os.makedirs(assets_dir, exist_ok=True)
            if not os.path.exists(target_tpl):
                shutil.copyfile(uploaded_src, target_tpl)
            shutil.copyfile(uploaded_src, os.path.join(assets_dir, "template.png"))
        except Exception as e:
            self.logger.debug("Template copy note: %s", e)


    @staticmethod
    def hash_password(password: str) -> str:
        """Utility to generate SHA-256 hash for admin passwords."""
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    def get_default_channel_profile(self, name: str = "Wealth Secrets") -> Dict[str, Any]:
        """Returns standard configuration dictionary for a single channel profile."""
        tpl_path = os.path.join("assets", "wealth_secrets_template.png")
        if not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), tpl_path)):
            tpl_path = os.path.join("assets", "template.png")

        return {
            "channel_name": name,
            "groq_api_key": "",
            "groq_api_keys_pool": [],
            "youtube_oauth_json_path": "",
            "template_path": tpl_path,
            "resolution_preset": "1080x1920 (9:16 Vertical Shorts)",
            "resolution_width": 1080,
            "resolution_height": 1920,
            "topic_focus": "wealth, business secrets, and money concepts",
            "language": "English",
            "caption_language": "English",
            "caption_color": "Yellow",
            "caption_font": "Arial Black",
            "auto_search_keywords": [
                "wealth secrets podcast",
                "business advice podcast interview",
                "how to build wealth podcast",
                "money mindset podcast clip"
            ],
            "auto_pilot": False,
            "autopilot_interval_hours": 2,
            "last_autopilot_run": 0,
            "target_clips_per_video": 3,
            "active_video_state": {
                "video_id": "",
                "target_clip_count": 3,
                "completed_clip_count": 0,
                "status": "idle"
            },
            "last_generated_clip": {},
            "generated_clips_history": [],
            "processed_video_ids": []
        }

    def get_default_config(self) -> Dict[str, Any]:
        """Returns default top-level application settings with multi-channel profile storage."""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        base_out = os.path.join(base_dir, "output")
        default_profile = self.get_default_channel_profile("Wealth Secrets")

        return {
            "active_profile": "Wealth Secrets",
            "profiles": {
                "Wealth Secrets": default_profile
            },
            "hardware_profile": "⚡ Auto-Detect & Maximum Performance (GPU Priority)",
            "reuse_cached_video_first": True,
            "auto_cleanup_temp_files": True,
            "face_focus_crop": True,
            "admin_password_hash": self.hash_password("admin123"),
            "output_directory": base_out,
            "source_videos_directory": os.path.join(base_out, "source_videos"),
            "transcripts_directory": os.path.join(base_out, "transcripts"),
            "shorts_clips_directory": os.path.join(base_out, "shorts_clips"),
            "min_clip_duration": 50,
            "max_clip_duration": 58,
            "pending_deletions": []
        }

    def normalize_directories(self) -> None:
        """Ensures directories exist and are accessible, correcting stale paths from other drives."""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        default_out = os.path.join(base_dir, "output")

        current_out = self._config.get("output_directory", default_out)
        drive = os.path.splitdrive(current_out)[0]
        if drive and not os.path.exists(drive + "\\"):
            current_out = default_out
            self._config["output_directory"] = default_out
            self._config["source_videos_directory"] = os.path.join(default_out, "source_videos")
            self._config["transcripts_directory"] = os.path.join(default_out, "transcripts")
            self._config["shorts_clips_directory"] = os.path.join(default_out, "shorts_clips")
            self.save_config()

        for k in ["output_directory", "source_videos_directory", "transcripts_directory", "shorts_clips_directory"]:
            p = self._config.get(k)
            if p:
                try:
                    os.makedirs(p, exist_ok=True)
                except Exception:
                    pass

    def load_config(self) -> Dict[str, Any]:
        """Loads configuration from JSON file and automatically migrates single-profile setups."""
        if not os.path.exists(self.config_path):
            self._config = self.get_default_config()
            self.save_config()
        else:
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    defaults = self.get_default_config()
                    defaults.update(loaded)
                    self._config = defaults

                # Multi-profile migration check
                profiles = self._config.get("profiles", {})
                if not isinstance(profiles, dict) or not profiles:
                    # Migrate existing flat config into 'Wealth Secrets' profile
                    ws_profile = self.get_default_channel_profile("Wealth Secrets")
                    for k in self.CHANNEL_SPECIFIC_KEYS:
                        if k in self._config:
                            ws_profile[k] = self._config[k]
                    self._config["profiles"] = {"Wealth Secrets": ws_profile}
                    self._config["active_profile"] = "Wealth Secrets"
                    self.save_config()
                elif "active_profile" not in self._config or self._config["active_profile"] not in self._config["profiles"]:
                    self._config["active_profile"] = list(self._config["profiles"].keys())[0]

            except Exception as e:
                print(f"[ConfigManager] Error loading config ({e}), reverting to defaults.")
                self._config = self.get_default_config()
        return self._config

    def save_config(self) -> bool:
        """Saves current settings to disk."""
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=4)
            return True
        except Exception as e:
            print(f"[ConfigManager] Error saving config: {e}")
            return False

    # --- MULTI-CHANNEL PROFILES MANAGEMENT ---
    def get_profiles_list(self) -> List[str]:
        """Returns list of all available channel profile names."""
        profiles = self._config.get("profiles", {})
        return list(profiles.keys()) if profiles else ["Wealth Secrets"]

    def get_active_profile_name(self) -> str:
        """Returns the currently active channel profile name."""
        active = self._config.get("active_profile", "Wealth Secrets")
        profiles = self.get_profiles_list()
        if active not in profiles and profiles:
            active = profiles[0]
            self._config["active_profile"] = active
        return active

    def set_active_profile(self, name: str) -> bool:
        """Switches the active channel profile."""
        if name in self.get_profiles_list():
            self._config["active_profile"] = name
            self.save_config()
            return True
        return False

    def create_profile(self, name: str, copy_from: Optional[str] = None) -> bool:
        """Creates a new channel profile (+ button)."""
        clean_name = name.strip()
        if not clean_name:
            return False

        profiles = self._config.setdefault("profiles", {})
        if clean_name in profiles:
            return False  # already exists

        base_profile = None
        source_name = copy_from or self.get_active_profile_name()
        if source_name in profiles:
            base_profile = profiles[source_name]

        new_profile = self.get_default_channel_profile(clean_name)
        if base_profile:
            for k, v in base_profile.items():
                # Copy settings but start with fresh video locks and history
                if k not in ["channel_name", "active_video_state", "generated_clips_history", "processed_video_ids", "last_generated_clip"]:
                    new_profile[k] = v

        profiles[clean_name] = new_profile
        self._config["active_profile"] = clean_name
        self.save_config()
        return True

    def delete_profile(self, name: str) -> bool:
        """Deletes a channel profile (- button). Cannot delete the last remaining profile."""
        profiles = self._config.get("profiles", {})
        if len(profiles) <= 1 or name not in profiles:
            return False

        del profiles[name]
        if self._config.get("active_profile") == name:
            self._config["active_profile"] = list(profiles.keys())[0]
        self.save_config()
        return True

    def get_channel_setting(self, key: str, default: Any = None, profile_name: Optional[str] = None) -> Any:
        """Retrieves setting value for a specific channel profile."""
        prof_name = profile_name or self.get_active_profile_name()
        prof = self._config.get("profiles", {}).get(prof_name, {})
        return prof.get(key, self._config.get(key, default))

    def set_channel_setting(self, key: str, value: Any, profile_name: Optional[str] = None) -> None:
        """Updates setting value for a specific channel profile and saves."""
        prof_name = profile_name or self.get_active_profile_name()
        profiles = self._config.setdefault("profiles", {})
        if prof_name not in profiles:
            profiles[prof_name] = self.get_default_channel_profile(prof_name)
        profiles[prof_name][key] = value
        self.save_config()

    def get_caption_language(self, profile_name: Optional[str] = None) -> str:
        """Returns configured caption language for profile from SUPPORTED_CAPTION_LANGUAGES."""
        prof = profile_name or self.get_active_profile_name()
        val = self.get_channel_setting("caption_language", None, prof)
        if not val:
            val = self.get_channel_setting("language", "English", prof)
        val_clean = str(val).strip().lower()
        if val_clean in self.LANGUAGE_TO_CODE:
            code = self.LANGUAGE_TO_CODE[val_clean]
            return self.CODE_TO_LANGUAGE[code]
        if val_clean in self.CODE_TO_LANGUAGE:
            return self.CODE_TO_LANGUAGE[val_clean]
        return "English"

    def get_caption_language_code(self, profile_name: Optional[str] = None) -> str:
        """Returns standard ISO 639-1 code (e.g. 'en', 'ur', 'es', 'ar') for caption language."""
        lang = self.get_caption_language(profile_name)
        return self.LANGUAGE_TO_CODE.get(lang.lower(), "en")

    def set_caption_language(self, language: str, profile_name: Optional[str] = None) -> None:
        """Sets caption language for profile."""
        prof = profile_name or self.get_active_profile_name()
        val_clean = str(language).strip().lower()
        if val_clean in self.LANGUAGE_TO_CODE:
            code = self.LANGUAGE_TO_CODE[val_clean]
            clean_title = self.CODE_TO_LANGUAGE[code]
        elif val_clean in self.CODE_TO_LANGUAGE:
            clean_title = self.CODE_TO_LANGUAGE[val_clean]
        else:
            clean_title = "English"

        self.set_channel_setting("caption_language", clean_title, prof)
        self.set_channel_setting("language", clean_title, prof)

    def get_language(self, profile_name: Optional[str] = None) -> str:
        """Returns the configured language for profile."""
        return self.get_caption_language(profile_name)

    def set_language(self, language: str, profile_name: Optional[str] = None) -> None:
        """Sets language for profile."""
        self.set_caption_language(language, profile_name)

    def get_caption_color(self, profile_name: Optional[str] = None) -> str:
        """Returns configured caption color ('White', 'Yellow', 'Neon Green') for profile."""
        prof = profile_name or self.get_active_profile_name()
        val = str(self.get_channel_setting("caption_color", "Yellow", prof)).strip()
        val_lower = val.lower()
        if "white" in val_lower:
            return "White"
        elif "green" in val_lower:
            return "Neon Green"
        else:
            return "Yellow"

    def set_caption_color(self, color: str, profile_name: Optional[str] = None) -> None:
        """Sets caption color ('White', 'Yellow', 'Neon Green') for profile."""
        prof = profile_name or self.get_active_profile_name()
        c = str(color).strip().lower()
        if "white" in c:
            clean = "White"
        elif "green" in c:
            clean = "Neon Green"
        else:
            clean = "Yellow"
        self.set_channel_setting("caption_color", clean, prof)

    def get_caption_font(self, profile_name: Optional[str] = None) -> str:
        """Returns configured caption font for profile. Defaults to Jameel Noori Nastaleeq for Urdu, Arial Black for other languages."""
        prof = profile_name or self.get_active_profile_name()
        val = self.get_channel_setting("caption_font", None, prof)
        if not val:
            lang = self.get_caption_language(prof)
            if str(lang).strip().lower() in ("ur", "urdu"):
                return "Jameel Noori Nastaleeq"
            return "Arial Black"
        return str(val).strip()

    def set_caption_font(self, font_name_or_path: str, profile_name: Optional[str] = None) -> None:
        """Sets caption font for profile."""
        prof = profile_name or self.get_active_profile_name()
        clean = str(font_name_or_path).strip()
        self.set_channel_setting("caption_font", clean, prof)

    def get_supported_caption_fonts(self) -> List[str]:
        """Returns list of default supported caption fonts."""
        return list(self.SUPPORTED_CAPTION_FONTS)

    def get_channel_output_dirs(self, profile_name: Optional[str] = None) -> Dict[str, str]:
        """Returns isolated directory paths for the specified channel profile."""
        name = profile_name or self.get_active_profile_name()
        safe_name = re.sub(r'[^\w\s-]', '', name).strip() or "Default"
        base_out = self._config.get("output_directory", os.path.join(os.path.dirname(os.path.abspath(__file__)), "output"))
        chan_dir = os.path.join(base_out, safe_name)
        dirs = {
            "channel_dir": chan_dir,
            "source_videos": os.path.join(chan_dir, "source_videos"),
            "transcripts": os.path.join(chan_dir, "transcripts"),
            "shorts_clips": os.path.join(chan_dir, "shorts_clips")
        }
        for p in dirs.values():
            try:
                os.makedirs(p, exist_ok=True)
            except Exception:
                pass
        return dirs

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve setting value, routing channel-specific keys to active profile."""
        if key in self.CHANNEL_SPECIFIC_KEYS:
            return self.get_channel_setting(key, default=default)
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Update setting value, routing channel-specific keys to active profile."""
        if key in self.CHANNEL_SPECIFIC_KEYS:
            self.set_channel_setting(key, value)
        else:
            self._config[key] = value
            self.save_config()

    def get_resolution_dimensions(self, preset_str: Optional[str] = None) -> Tuple[int, int]:
        """
        Extracts (width, height) integers from preset string like '1080x1920 (9:16 Vertical Shorts)'
        or stored configuration.
        """
        preset = preset_str or self.get("resolution_preset", "1080x1920 (9:16 Vertical Shorts)")
        match = re.search(r"(\d+)\s*[xX]\s*(\d+)", str(preset))
        if match:
            w = int(match.group(1))
            h = int(match.group(2))
            return w, h
        return self.get("resolution_width", 1080), self.get("resolution_height", 1920)

    def get_hardware_settings(self) -> Dict[str, Any]:
        """
        Auto-detects maximum system hardware (CPU cores, RAM, GPU Name, VRAM,
        CUDA Compute Type, and FFmpeg Video Encoders) dynamically.
        Prioritizes GPU by default if detected.
        """
        from core.hardware import HardwareDetector
        profile = self._config.get("hardware_profile", "⚡ Auto-Detect & Maximum Performance (GPU Priority)")
        
        # Get dynamic configuration based on active profile string
        return HardwareDetector.get_optimal_settings(profile)


    def update_active_video_state(self, video_id: str, target_count: int, completed_count: int, status: str = "in_progress", profile_name: Optional[str] = None):
        """Updates persistent state for active video to enforce Smart Resume locks."""
        if not profile_name:
            profile_name = self.get("active_profile")

        state = {
            "video_id": video_id,
            "target_clip_count": target_count,
            "completed_clip_count": completed_count,
            "status": status
        }
        self.set_channel_setting("active_video_state", state, profile_name)
        self.save_config()

    def reset_active_video_state(self, profile_name: Optional[str] = None):
        """Forces the pipeline to start a fresh discovery by clearing active state."""
        if not profile_name:
            profile_name = self.get("active_profile")
            
        import random
        target_count = random.randint(3, 5)
            
        state = {
            "video_id": "",
            "target_clip_count": target_count,
            "completed_clip_count": 0,
            "status": "idle"
        }
        self.set_channel_setting("active_video_state", state, profile_name)
        self.save_config()

    def clear_active_video_state(self, profile_name: Optional[str] = None):
        """Clears active video lock when all clips are finished and cleaned up."""
        target = self.get_channel_setting("target_clips_per_video", 3, profile_name) if profile_name else self.get("target_clips_per_video", 3)
        st = {
            "video_id": "",
            "target_clip_count": target,
            "completed_clip_count": 0,
            "status": "completed"
        }
        if profile_name:
            self.set_channel_setting("active_video_state", st, profile_name)
        else:
            self.set("active_video_state", st)

    def add_processed_video(self, video_id: str, profile_name: Optional[str] = None) -> None:
        """Appends video_id to processed history to ensure uniqueness."""
        history = self.get_channel_setting("processed_video_ids", [], profile_name) if profile_name else self.get("processed_video_ids", [])
        if video_id not in history:
            history.append(video_id)
            if profile_name:
                self.set_channel_setting("processed_video_ids", history, profile_name)
            else:
                self.set("processed_video_ids", history)

    def record_generated_clip(self, clip_data: Dict[str, Any], profile_name: Optional[str] = None) -> None:
        """
        Keeps permanent record of every generated clip to prevent duplication,
        even after the local MP4 file is deleted after 1 hour.
        """
        history = self.get_channel_setting("generated_clips_history", [], profile_name) if profile_name else self.get("generated_clips_history", [])
        clip_record = {
            "video_id": clip_data.get("video_id"),
            "clip_index": clip_data.get("clip_index"),
            "title": clip_data.get("title"),
            "hook": clip_data.get("hook"),
            "start_time": clip_data.get("start_time"),
            "end_time": clip_data.get("end_time"),
            "duration": clip_data.get("duration"),
            "rendered_mp4_path": clip_data.get("rendered_mp4_path"),
            "created_at": clip_data.get("created_at", time.time()),
            "uploaded": clip_data.get("uploaded", False),
            "uploaded_at": clip_data.get("uploaded_at"),
            "file_deleted": False
        }
        history.append(clip_record)
        if profile_name:
            self.set_channel_setting("generated_clips_history", history, profile_name)
            self.set_channel_setting("last_generated_clip", clip_record, profile_name)
        else:
            self.set("generated_clips_history", history)
            self.set("last_generated_clip", clip_record)

    def mark_clip_uploaded(self, mp4_path: str, upload_info: Dict[str, Any], profile_name: Optional[str] = None) -> None:
        """Marks clip as uploaded in persistent history."""
        history = self.get_channel_setting("generated_clips_history", [], profile_name) if profile_name else self.get("generated_clips_history", [])
        for record in history:
            if record.get("rendered_mp4_path") == mp4_path or record.get("title") == upload_info.get("title"):
                record["uploaded"] = True
                record["uploaded_at"] = time.time()
                record["youtube_id"] = upload_info.get("id")
                record["youtube_url"] = upload_info.get("url")

        last = self.get_channel_setting("last_generated_clip", {}, profile_name) if profile_name else self.get("last_generated_clip", {})
        if last.get("rendered_mp4_path") == mp4_path:
            last["uploaded"] = True
            last["uploaded_at"] = time.time()
            last["youtube_url"] = upload_info.get("url")
            if profile_name:
                self.set_channel_setting("last_generated_clip", last, profile_name)
            else:
                self.set("last_generated_clip", last)

        if profile_name:
            self.set_channel_setting("generated_clips_history", history, profile_name)
        else:
            self.set("generated_clips_history", history)

    def schedule_file_deletion(self, file_path: str, delay_seconds: int = 3600, clip_id: str = "", immediate: bool = False) -> None:
        """
        Schedules a local clip file for deletion.

        Args:
            file_path:      Absolute path to the file to delete.
            delay_seconds:  Seconds to wait before deletion (default 1 hour = 3600s).
                            Ignored when immediate=True.
            clip_id:        Optional clip identifier for record-keeping.
            immediate:      If True, delete the file immediately (used after confirmed
                            social media upload to free disk space instantly).
        """
        if not file_path or not os.path.exists(file_path):
            return

        if immediate:
            # Instant deletion — no timer needed
            self._execute_delayed_deletion(file_path)
            return

        delete_at = time.time() + delay_seconds
        pending = self._config.get("pending_deletions", [])

        # Avoid duplicate pending entry
        pending = [p for p in pending if p.get("path") != file_path]
        pending.append({
            "path": file_path,
            "delete_at": delete_at,
            "clip_id": clip_id
        })
        self._config["pending_deletions"] = pending
        self.save_config()

        # Arm background timer
        timer = threading.Timer(delay_seconds, self._execute_delayed_deletion, args=[file_path])
        timer.daemon = True
        timer.start()

    def _execute_delayed_deletion(self, file_path: str):
        """Timer callback to safely delete file."""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"[ConfigManager] Successfully deleted uploaded clip after 1-hour window: {file_path}")
            
            # Clean matching subtitle or temp files if any
            ass_path = file_path.replace(".mp4", "_sub.ass")
            if os.path.exists(ass_path):
                os.remove(ass_path)

            self.mark_clip_deleted(file_path)
        except Exception as e:
            print(f"[ConfigManager] Error during delayed deletion of {file_path}: {e}")

    def mark_clip_deleted(self, file_path: str):
        """Updates deletion flag in history and removes from pending list."""
        history = self._config.get("generated_clips_history", [])
        for record in history:
            if record.get("rendered_mp4_path") == file_path:
                record["file_deleted"] = True

        pending = self._config.get("pending_deletions", [])
        self._config["pending_deletions"] = [p for p in pending if p.get("path") != file_path]
        self._config["generated_clips_history"] = history
        self.save_config()

    def check_and_purge_expired_files(self) -> List[str]:
        """
        Scans pending_deletions for any files that have passed their 1-hour deletion timestamp.
        Runs periodically on app start and UI cycle.
        """
        now = time.time()
        pending = self._config.get("pending_deletions", [])
        remaining = []
        purged = []

        for item in pending:
            fpath = item.get("path")
            delete_at = item.get("delete_at", 0)
            if now >= delete_at:
                try:
                    if fpath and os.path.exists(fpath):
                        os.remove(fpath)
                        purged.append(fpath)
                    ass_path = fpath.replace(".mp4", "_sub.ass") if fpath else ""
                    if ass_path and os.path.exists(ass_path):
                        os.remove(ass_path)
                except Exception as e:
                    print(f"[ConfigManager] Purge error ({fpath}): {e}")
                self.mark_clip_deleted(fpath)
            else:
                remaining.append(item)

        if purged:
            self._config["pending_deletions"] = remaining
            self.save_config()
        return purged

    def _init_deletion_timers(self):
        """Re-arms background timers for any pending deletions from previous sessions."""
        now = time.time()
        pending = self._config.get("pending_deletions", [])
        for item in pending:
            fpath = item.get("path")
            delete_at = item.get("delete_at", 0)
            remaining_secs = max(5, int(delete_at - now))
            if fpath and os.path.exists(fpath):
                timer = threading.Timer(remaining_secs, self._execute_delayed_deletion, args=[fpath])
                timer.daemon = True
                timer.start()

    def get_api_key_pool(self, profile_name: Optional[str] = None) -> List[str]:
        """Returns list of all available Groq API keys including default key from the specified or active profile."""
        if not profile_name:
            profile_name = self.get("active_profile")
            
        keys = []
        default_key = self.get_channel_setting("groq_api_key", "", profile_name).strip()
        if default_key:
            keys.append(default_key)
            
        pool = self.get_channel_setting("groq_api_keys_pool", [], profile_name)
        for k in pool:
            k_clean = k.strip()
            if k_clean and k_clean not in keys:
                keys.append(k_clean)
                
        return keys

    def verify_admin_password(self, input_password: str) -> bool:
        """Verifies provided password against stored SHA-256 hash."""
        input_hash = self.hash_password(input_password)
        return input_hash == self._config.get("admin_password_hash")

    def update_admin_password(self, new_password: str) -> None:
        """Updates admin password hash."""
        self._config["admin_password_hash"] = self.hash_password(new_password)
        self.save_config()
