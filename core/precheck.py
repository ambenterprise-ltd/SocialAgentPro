import os
import sys
import shutil
import logging
import subprocess
import requests
from typing import Dict, Any, List, Optional, Tuple

from config import ConfigManager


class PrecheckResult(dict):
    """
    Diagnostic result supporting both dict-style access (item['name'])
    and attribute-style access (item.name, item.details, item.status).
    """
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.__dict__.update(kwargs)
        if "message" in kwargs and "details" not in kwargs:
            self["details"] = kwargs["message"]
            self.__dict__["details"] = kwargs["message"]
        elif "details" in kwargs and "message" not in kwargs:
            self["message"] = kwargs["details"]
            self.__dict__["message"] = kwargs["details"]

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"'PrecheckResult' has no attribute '{name}'")


class SystemPrechecker:
    """
    Comprehensive System Pre-Flight Diagnostic & Pre-Check Engine for AMB Enterprise.
    Verifies binaries, python libraries, AI brain connectivity, active channel assets,
    connected social platforms (YouTube, Facebook, Instagram), and Google Sheets logging.
    """

    def __init__(
        self,
        config_manager: ConfigManager,
        profile_name: Optional[Any] = None,
        logger: Optional[logging.Logger] = None
    ):
        self.config_manager = config_manager
        if isinstance(profile_name, logging.Logger):
            self.logger = profile_name
            self.default_profile = None
        else:
            self.default_profile = profile_name if isinstance(profile_name, str) else None
            self.logger = logger or logging.getLogger("AMBEnterprise")

    def run_all_checks(self, profile_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Runs full pre-flight verification across all systems for the specified channel profile.
        Returns a dictionary with 'all_passed', 'critical_ok', and list of 'checks'.
        """
        prof = profile_name or self.default_profile or self.config_manager.get_active_profile_name()
        checks: List[PrecheckResult] = []

        # 1. System & Media Binaries
        checks.append(PrecheckResult(**self._check_ffmpeg()))
        checks.append(PrecheckResult(**self._check_ffprobe()))
        checks.append(PrecheckResult(**self._check_ytdlp()))
        checks.append(PrecheckResult(**self._check_hardware_gpu()))

        # 2. AI Brain / Groq API
        checks.append(PrecheckResult(**self._check_groq_api(prof)))

        # 3. Channel Assets & Output Directories
        checks.append(PrecheckResult(**self._check_channel_assets(prof)))
        checks.append(PrecheckResult(**self._check_output_directories(prof)))

        # 4. Connected Platforms & Publishing
        checks.append(PrecheckResult(**self._check_youtube_oauth(prof)))
        checks.append(PrecheckResult(**self._check_facebook_page(prof)))
        checks.append(PrecheckResult(**self._check_instagram_account(prof)))
        checks.append(PrecheckResult(**self._check_google_sheets(prof)))

        # Tally results
        has_critical_failure = any(c.get("status") == "FAIL" and c.get("critical", False) for c in checks)
        all_passed = all(c.get("status") == "PASS" for c in checks)

        summary = {
            "all_passed": all_passed,
            "critical_ok": not has_critical_failure,
            "profile_name": prof,
            "checks": checks,
            "pass_count": sum(1 for c in checks if c.get("status") == "PASS"),
            "warn_count": sum(1 for c in checks if c.get("status") == "WARN"),
            "fail_count": sum(1 for c in checks if c.get("status") == "FAIL")
        }

        self.logger.debug(
            f"[Pre-Check] Diagnostic complete for '{prof}'. Passed: {summary['pass_count']}, "
            f"Warnings: {summary['warn_count']}, Failures: {summary['fail_count']} (Critical OK: {summary['critical_ok']})"
        )
        if has_critical_failure:
            self.logger.error(f"[Pre-Check] Critical dependency failure detected for '{prof}'!")
        return summary

    def has_critical_failures(self, profile_name: Optional[str] = None) -> Tuple[bool, List[PrecheckResult]]:
        """
        Runs diagnostics and returns (has_critical, critical_issues_list).
        """
        summary = self.run_all_checks(profile_name)
        critical_issues = [
            c for c in summary["checks"]
            if c.get("status") == "FAIL" and c.get("critical", False)
        ]
        return len(critical_issues) > 0, critical_issues

    def _check_ffmpeg(self) -> Dict[str, Any]:
        """Verifies FFmpeg binary is accessible and executable."""
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            candidate_dirs = [
                r"C:\ffmpeg\bin",
                r"C:\Program Files\ffmpeg\bin",
                r"C:\Users\Dell\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.1-full_build\bin",
                r"C:\Users\Dell\Downloads\ffmpeg-2026-08-30-git-818cecc6e1-essentials_build\ffmpeg-2026-08-30-git-818cecc6e1-essentials_build\bin",
            ]
            for c_dir in candidate_dirs:
                c_exe = os.path.join(c_dir, "ffmpeg.exe")
                if os.path.isfile(c_exe):
                    ffmpeg_bin = c_exe
                    os.environ["PATH"] = c_dir + os.pathsep + os.environ.get("PATH", "")
                    break

        if not ffmpeg_bin:
            return {
                "category": "System & Tools",
                "name": "FFmpeg Binary",
                "status": "FAIL",
                "critical": True,
                "message": "FFmpeg is not installed or not found in system PATH."
            }
        try:
            res = subprocess.run([ffmpeg_bin, "-version"], capture_output=True, text=True, timeout=5)
            first_line = res.stdout.split("\n")[0] if res.stdout else "FFmpeg Detected"
            return {
                "category": "System & Tools",
                "name": "FFmpeg Binary",
                "status": "PASS",
                "critical": True,
                "message": f"Operational: {first_line[:60]}"
            }
        except Exception as e:
            return {
                "category": "System & Tools",
                "name": "FFmpeg Binary",
                "status": "FAIL",
                "critical": True,
                "message": f"FFmpeg execution error: {e}"
            }

    def _check_ffprobe(self) -> Dict[str, Any]:
        """Verifies FFprobe binary is accessible."""
        ffprobe_bin = shutil.which("ffprobe")
        if not ffprobe_bin:
            candidate_dirs = [
                r"C:\ffmpeg\bin",
                r"C:\Program Files\ffmpeg\bin",
                r"C:\Users\Dell\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.1-full_build\bin",
                r"C:\Users\Dell\Downloads\ffmpeg-2026-08-30-git-818cecc6e1-essentials_build\ffmpeg-2026-08-30-git-818cecc6e1-essentials_build\bin",
            ]
            for c_dir in candidate_dirs:
                c_exe = os.path.join(c_dir, "ffprobe.exe")
                if os.path.isfile(c_exe):
                    ffprobe_bin = c_exe
                    os.environ["PATH"] = c_dir + os.pathsep + os.environ.get("PATH", "")
                    break

        if not ffprobe_bin:
            return {
                "category": "System & Tools",
                "name": "FFprobe Binary",
                "status": "WARN",
                "critical": False,
                "message": "FFprobe not found in system PATH. Some metadata probes may be limited."
            }
        return {
            "category": "System & Tools",
            "name": "FFprobe Binary",
            "status": "PASS",
            "critical": False,
            "message": f"Operational: Located at {ffprobe_bin}"
        }

    def _check_ytdlp(self) -> Dict[str, Any]:
        """Verifies yt-dlp Python engine is installed."""
        try:
            import yt_dlp
            ver = getattr(yt_dlp, "__version__", "Installed")
            return {
                "category": "System & Tools",
                "name": "yt-dlp Engine",
                "status": "PASS",
                "critical": True,
                "message": f"Operational: yt-dlp version {ver}"
            }
        except ImportError:
            return {
                "category": "System & Tools",
                "name": "yt-dlp Engine",
                "status": "FAIL",
                "critical": True,
                "message": "yt-dlp library is missing. Install with 'pip install yt-dlp'."
            }

    def _check_hardware_gpu(self) -> Dict[str, Any]:
        """Verifies GPU acceleration and hardware encoder status."""
        try:
            hw = self.config_manager.get_hardware_settings()
            has_gpu = hw.get("has_gpu", False)
            summary = hw.get("summary", "CPU Mode")
            if has_gpu:
                return {
                    "category": "System & Tools",
                    "name": "GPU Acceleration",
                    "status": "PASS",
                    "critical": False,
                    "message": f"GPU Active: {summary}"
                }
            else:
                return {
                    "category": "System & Tools",
                    "name": "GPU Acceleration",
                    "status": "WARN",
                    "critical": False,
                    "message": f"CPU Mode Active: {summary} (Processing will use multi-core CPU)."
                }
        except Exception as e:
            return {
                "category": "System & Tools",
                "name": "GPU Acceleration",
                "status": "WARN",
                "critical": False,
                "message": f"Hardware probe notice: {e}"
            }

    def _check_groq_api(self, profile_name: str) -> Dict[str, Any]:
        """Verifies primary Groq API key is valid and can communicate with Groq cloud."""
        key = self.config_manager.get_shared_groq_api_key().strip()
        if not key:
            return {
                "category": "AI Brain & LLM",
                "name": "Groq LLM API Key",
                "status": "FAIL",
                "critical": True,
                "message": "No Groq API Key found. Add your key in Admin Settings -> API Keys & Auth."
            }

        try:
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            resp = requests.get("https://api.groq.com/openai/v1/models", headers=headers, timeout=8)
            if resp.status_code == 200:
                models = resp.json().get("data", [])
                pool_count = len(self.config_manager.get_shared_groq_keys_pool())
                pool_info = f" (+{pool_count} backup keys in pool)" if pool_count else ""
                return {
                    "category": "AI Brain & LLM",
                    "name": "Groq LLM API Key",
                    "status": "PASS",
                    "critical": True,
                    "message": f"Connected to Groq Cloud ({len(models)} models available){pool_info}."
                }
            elif resp.status_code == 401:
                return {
                    "category": "AI Brain & LLM",
                    "name": "Groq LLM API Key",
                    "status": "FAIL",
                    "critical": True,
                    "message": "Groq API Key rejected (401 Unauthorized). Please check your key."
                }
            else:
                return {
                    "category": "AI Brain & LLM",
                    "name": "Groq LLM API Key",
                    "status": "WARN",
                    "critical": False,
                    "message": f"Groq API returned HTTP {resp.status_code}: {resp.text[:80]}"
                }
        except Exception as e:
            return {
                "category": "AI Brain & LLM",
                "name": "Groq LLM API Key",
                "status": "WARN",
                "critical": False,
                "message": f"Could not reach Groq Cloud: {e}"
            }

    def _check_channel_assets(self, profile_name: str) -> Dict[str, Any]:
        """Verifies template overlay image exists for the channel."""
        raw_tpl = self.config_manager.get_template_path(profile_name)
        resolved = self.config_manager.resolve_asset_path(raw_tpl)
        if resolved and os.path.exists(resolved):
            return {
                "category": "Channel Profile",
                "name": f"Channel Template ({profile_name})",
                "status": "PASS",
                "critical": False,
                "message": f"Template loaded: {os.path.basename(resolved)}"
            }
        else:
            return {
                "category": "Channel Profile",
                "name": f"Channel Template ({profile_name})",
                "status": "WARN",
                "critical": False,
                "message": f"Template '{raw_tpl}' not found on disk. Engine will use default/clean compositing."
            }

    def _check_output_directories(self, profile_name: str) -> Dict[str, Any]:
        """Verifies output folder structure is writable."""
        try:
            dirs = self.config_manager.get_channel_output_dirs(profile_name)
            shorts_dir = dirs.get("shorts_clips")
            if not os.path.exists(shorts_dir):
                os.makedirs(shorts_dir, exist_ok=True)
            # Test write
            test_file = os.path.join(shorts_dir, ".test_write.tmp")
            with open(test_file, "w") as f:
                f.write("ok")
            os.remove(test_file)
            return {
                "category": "Channel Profile",
                "name": "Disk Storage & Directories",
                "status": "PASS",
                "critical": True,
                "message": f"Output directories verified writable at: {os.path.dirname(shorts_dir)}"
            }
        except Exception as e:
            return {
                "category": "Channel Profile",
                "name": "Disk Storage & Directories",
                "status": "FAIL",
                "critical": True,
                "message": f"Cannot write to output directory: {e}"
            }

    def _check_youtube_oauth(self, profile_name: str) -> Dict[str, Any]:
        """Checks YouTube OAuth JSON configuration."""
        yt_on = self.config_manager.get_channel_setting("upload_to_youtube", True, profile_name)
        yt_path = self.config_manager.get_channel_setting("youtube_oauth_json_path", "", profile_name).strip()

        if not yt_on:
            return {
                "category": "Connected Platforms",
                "name": "YouTube Shorts",
                "status": "WARN",
                "critical": False,
                "message": "YouTube upload is disabled by toggle switch."
            }

        if yt_path and os.path.exists(yt_path):
            return {
                "category": "Connected Platforms",
                "name": "YouTube Shorts",
                "status": "PASS",
                "critical": False,
                "message": f"OAuth client_secret.json verified: {os.path.basename(yt_path)}"
            }
        else:
            return {
                "category": "Connected Platforms",
                "name": "YouTube Shorts",
                "status": "WARN",
                "critical": False,
                "message": "OAuth client_secret.json missing or invalid. YouTube Shorts will save locally."
            }

    def _check_facebook_page(self, profile_name: str) -> Dict[str, Any]:
        """Checks Facebook Page credentials and Page Access Token resolution."""
        fb_on = self.config_manager.get_channel_setting("upload_to_facebook", True, profile_name)
        meta_tok = (
            self.config_manager.get_channel_setting("meta_access_token", "", profile_name) or
            self.config_manager.get_channel_setting("facebook_access_token", "", profile_name)
        ).strip()
        page_id = self.config_manager.get_channel_setting("facebook_page_id", "", profile_name).strip()

        if not fb_on:
            return {
                "category": "Connected Platforms",
                "name": "Facebook Reels (Page)",
                "status": "WARN",
                "critical": False,
                "message": "Facebook upload is disabled by toggle switch."
            }

        if not meta_tok or not page_id:
            return {
                "category": "Connected Platforms",
                "name": "Facebook Reels (Page)",
                "status": "WARN",
                "critical": False,
                "message": "Meta Access Token or Facebook Page ID not configured."
            }

        try:
            # Query Page to verify token & ID
            url = f"https://graph.facebook.com/v19.0/{page_id}"
            params = {"fields": "name,access_token", "access_token": meta_tok}
            resp = requests.get(url, params=params, timeout=8)
            if resp.status_code == 200:
                page_info = resp.json()
                page_name = page_info.get("name", page_id)
                has_page_token = bool(page_info.get("access_token"))
                token_desc = "Page Access Token resolved" if has_page_token else "User Token linked"
                return {
                    "category": "Connected Platforms",
                    "name": "Facebook Reels (Page)",
                    "status": "PASS",
                    "critical": False,
                    "message": f"Connected to Facebook Page '{page_name}' ({page_id}) [{token_desc}]."
                }
            else:
                return {
                    "category": "Connected Platforms",
                    "name": "Facebook Reels (Page)",
                    "status": "WARN",
                    "critical": False,
                    "message": f"Facebook Page query returned HTTP {resp.status_code}: {resp.text[:80]}"
                }
        except Exception as e:
            return {
                "category": "Connected Platforms",
                "name": "Facebook Reels (Page)",
                "status": "WARN",
                "critical": False,
                "message": f"Facebook network verification error: {e}"
            }

    def _check_instagram_account(self, profile_name: str) -> Dict[str, Any]:
        """Checks Instagram Professional Account credentials."""
        ig_on = self.config_manager.get_channel_setting("upload_to_instagram", True, profile_name)
        meta_tok = (
            self.config_manager.get_channel_setting("meta_access_token", "", profile_name) or
            self.config_manager.get_channel_setting("instagram_access_token", "", profile_name)
        ).strip()
        ig_id = self.config_manager.get_channel_setting("instagram_account_id", "", profile_name).strip()

        if not ig_on:
            return {
                "category": "Connected Platforms",
                "name": "Instagram Reels",
                "status": "WARN",
                "critical": False,
                "message": "Instagram upload is disabled by toggle switch."
            }

        if not meta_tok or not ig_id:
            return {
                "category": "Connected Platforms",
                "name": "Instagram Reels",
                "status": "WARN",
                "critical": False,
                "message": "Meta Access Token or Instagram Account ID not configured."
            }

        try:
            url = f"https://graph.facebook.com/v19.0/{ig_id}"
            params = {"fields": "username,name", "access_token": meta_tok}
            resp = requests.get(url, params=params, timeout=8)
            if resp.status_code == 200:
                ig_info = resp.json()
                username = ig_info.get("username", ig_id)
                return {
                    "category": "Connected Platforms",
                    "name": "Instagram Reels",
                    "status": "PASS",
                    "critical": False,
                    "message": f"Connected to Instagram Account @{username} ({ig_id})."
                }
            else:
                return {
                    "category": "Connected Platforms",
                    "name": "Instagram Reels",
                    "status": "WARN",
                    "critical": False,
                    "message": f"Instagram query returned HTTP {resp.status_code}: {resp.text[:80]}"
                }
        except Exception as e:
            return {
                "category": "Connected Platforms",
                "name": "Instagram Reels",
                "status": "WARN",
                "critical": False,
                "message": f"Instagram network verification error: {e}"
            }

    def _check_google_sheets(self, profile_name: str) -> Dict[str, Any]:
        """Checks Google Sheets Service Account credentials and target spreadsheet."""
        sheets_on = self.config_manager.get_channel_setting("enable_google_sheets_logging", False, profile_name)
        sheets_json = (
            self.config_manager.get_channel_setting("google_sheets_json_path", "", profile_name) or
            self.config_manager.get("google_sheets_json_path", "")
        ).strip()
        sheet_target = (
            self.config_manager.get_channel_setting("google_sheet_url", "", profile_name) or
            self.config_manager.get("google_sheet_url", "") or
            self.config_manager.get_channel_setting("google_spreadsheet_id", "", profile_name) or
            self.config_manager.get("google_spreadsheet_id", "")
        ).strip()

        if not sheets_json:
            return {
                "category": "Connected Platforms",
                "name": "Google Sheets Logger",
                "status": "WARN",
                "critical": False,
                "message": "Google Sheets Service Account JSON not configured. (Logs will save locally only)."
            }

        if not os.path.exists(sheets_json):
            return {
                "category": "Connected Platforms",
                "name": "Google Sheets Logger",
                "status": "WARN",
                "critical": False,
                "message": f"Service Account JSON file not found at: '{sheets_json}'"
            }

        try:
            from core.sheets_logger import GoogleSheetsLogger, get_service_account_email
            logger = GoogleSheetsLogger(sheets_json, spreadsheet_id_or_profile=sheet_target or "Social Agent Pro Logs")
            ok, msg = logger.test_connection()
            sa_email = get_service_account_email(sheets_json)
            email_tag = f" [Email: {sa_email}]" if sa_email else ""
            if ok:
                return {
                    "category": "Connected Platforms",
                    "name": "Google Sheets Logger",
                    "status": "PASS",
                    "critical": False,
                    "message": f"Operational: {msg}{email_tag}"
                }
            else:
                return {
                    "category": "Connected Platforms",
                    "name": "Google Sheets Logger",
                    "status": "WARN",
                    "critical": False,
                    "message": f"Google Sheets connection notice: {msg}"
                }
        except Exception as e:
            return {
                "category": "Connected Platforms",
                "name": "Google Sheets Logger",
                "status": "WARN",
                "critical": False,
                "message": f"Google Sheets check error: {e}"
            }
