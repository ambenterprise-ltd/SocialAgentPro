import os
import re
import json
import time
import shutil
import logging
import threading
from typing import Dict, Any, Optional, List, Tuple

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False


def get_service_account_email(json_path: str) -> str:
    """Extracts client_email from a Google service account JSON file."""
    if not json_path or not os.path.exists(json_path):
        return ""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return str(data.get("client_email", "")).strip()
    except Exception:
        return ""


def extract_spreadsheet_id(input_str: str) -> str:
    """Extracts Google Spreadsheet ID from a full URL, or returns the clean ID string."""
    clean = (input_str or "").strip()
    if not clean:
        return ""
    if "docs.google.com/spreadsheets/d/" in clean:
        match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", clean)
        if match:
            return match.group(1)
    return clean


def construct_spreadsheet_url(id_or_url: str) -> str:
    """Builds a canonical public edit link for a Google Spreadsheet ID or URL."""
    clean = (id_or_url or "").strip()
    if not clean:
        return ""
    if clean.startswith("http://") or clean.startswith("https://"):
        return clean
    if len(clean) >= 20 and " " not in clean and "/" not in clean:
        return f"https://docs.google.com/spreadsheets/d/{clean}/edit"
    return ""


class GoogleSheetsLogger:
    """
    Automated Multi-Channel Google Sheets Logger & Disaster Recovery Engine for AMB Enterprise.
    - Logs video creation, publishing platforms, public links, and time intervals
      for each channel on separate worksheets/tabs within the SAME Google Sheet file.
    - Provides 1-click Global Backup of agency profiles & settings to a dedicated worksheet.
    - Provides 1-click Global Restore from Google Sheets to recreate local configurations.
    """

    DEFAULT_SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    HEADER_ROW = [
        "Timestamp (When)",
        "Video Title (What)",
        "Duration (s)",
        "Platforms (Where)",
        "YouTube Public URL",
        "Facebook Reel URL",
        "Instagram Reel URL",
        "Last Video Timestamp",
        "Next Video Scheduled",
        "Status"
    ]

    BACKUP_HEADER_ROW = [
        "Backup ID (Timestamp)",
        "Backup Date & Time",
        "Active Profile",
        "Total Channels",
        "Channel Names",
        "Full Settings Payload (JSON)",
        "Status"
    ]

    def __init__(
        self,
        service_account_json_path_or_config: Any,
        spreadsheet_id_or_profile: str = "",
        logger: Optional[logging.Logger] = None
    ):
        if hasattr(service_account_json_path_or_config, "get_channel_setting"):
            # ConfigManager instance provided
            cfg = service_account_json_path_or_config
            prof = spreadsheet_id_or_profile or cfg.get_active_profile_name()
            self.service_account_path = (cfg.get_channel_setting("google_sheets_json_path", "", prof) or cfg.get("google_sheets_json_path", "")).strip()
            raw_id = (cfg.get_channel_setting("google_spreadsheet_id", "", prof) or cfg.get("google_spreadsheet_id", "")).strip()
            raw_url = (cfg.get_channel_setting("google_sheet_url", "", prof) or cfg.get("google_sheet_url", "")).strip()

            if raw_url and not raw_id:
                raw_id = extract_spreadsheet_id(raw_url)
            self.spreadsheet_id_or_name = raw_id or raw_url
            self.spreadsheet_url = raw_url or construct_spreadsheet_url(self.spreadsheet_id_or_name)
        else:
            self.service_account_path = (str(service_account_json_path_or_config or "")).strip()
            raw_target = (str(spreadsheet_id_or_profile or "")).strip()
            self.spreadsheet_id_or_name = extract_spreadsheet_id(raw_target) or raw_target
            self.spreadsheet_url = construct_spreadsheet_url(raw_target)

        self.logger = logger or logging.getLogger("AMBEnterprise")
        self._client = None
        self._spreadsheet = None

    def is_configured(self) -> bool:
        """Returns True if service account file exists and spreadsheet target is specified."""
        return bool(
            self.service_account_path
            and os.path.exists(self.service_account_path)
            and self.spreadsheet_id_or_name
        )

    def get_service_account_email(self) -> str:
        """Returns client_email extracted from the configured service account JSON."""
        return get_service_account_email(self.service_account_path)

    def get_spreadsheet_url(self) -> str:
        """Returns the canonical public URL for the spreadsheet."""
        if hasattr(self, "spreadsheet_url") and self.spreadsheet_url:
            return self.spreadsheet_url
        return construct_spreadsheet_url(self.spreadsheet_id_or_name)

    def test_connection(self) -> Tuple[bool, str]:
        """Tests connecting to Google Sheets and opening the target spreadsheet."""
        if not GSPREAD_AVAILABLE:
            return False, "gspread library is not installed."
        if not self.service_account_path or not os.path.exists(self.service_account_path):
            return False, f"Service account JSON file not found: '{self.service_account_path}'"
        if not self.spreadsheet_id_or_name:
            return False, "Spreadsheet ID, URL, or Sheet Name is not specified."

        try:
            creds = Credentials.from_service_account_file(self.service_account_path, scopes=self.DEFAULT_SCOPES)
            client = gspread.authorize(creds)
            sheet = self._open_target_spreadsheet(client)
            return True, f"Connected to Google Sheet: '{sheet.title}' ({len(sheet.worksheets())} tabs)"
        except Exception as e:
            return False, f"Google Sheets connection failed: {e}"

    def _get_client(self):
        """Initializes or returns authorized gspread client."""
        if self._client is None:
            if not os.path.exists(self.service_account_path):
                raise FileNotFoundError(f"Service account file not found: {self.service_account_path}")
            creds = Credentials.from_service_account_file(self.service_account_path, scopes=self.DEFAULT_SCOPES)
            self._client = gspread.authorize(creds)
        return self._client

    def _open_target_spreadsheet(self, client):
        """Opens spreadsheet by URL, key/ID, or title."""
        target = self.spreadsheet_id_or_name
        if "docs.google.com/spreadsheets/d/" in target:
            return client.open_by_url(target)
        if len(target) >= 20 and " " not in target and "/" not in target:
            try:
                return client.open_by_key(target)
            except Exception:
                pass
        return client.open(target)

    def log_video_upload_async(self, *args, **kwargs) -> None:
        """Launches logging in a background daemon thread to avoid blocking pipeline or UI."""
        if not self.is_configured():
            self.logger.debug("[GoogleSheets] Sheets logging skipped: Not configured.")
            return

        t = threading.Thread(
            target=self.log_video_upload_sync,
            args=args,
            kwargs=kwargs,
            daemon=True
        )
        t.start()

    def log_video_upload_sync(
        self,
        channel_name: str,
        clip_data: Optional[Dict[str, Any]] = None,
        upload_results: Optional[Dict[str, Any]] = None,
        last_video_time: float = 0.0,
        next_scheduled_time: float = 0.0,
        **kwargs
    ) -> bool:
        """
        Appends video publishing log row to the designated channel worksheet tab.
        Creates worksheet and headers automatically if not already present.
        Supports both pipeline style and manual upload style arguments.
        """
        if not GSPREAD_AVAILABLE:
            self.logger.warning("[GoogleSheets] gspread library unavailable. Cannot log to Google Sheets.")
            return False

        try:
            client = self._get_client()
            spreadsheet = self._open_target_spreadsheet(client)

            # Get or create worksheet tab for this specific channel
            worksheet_name = str(channel_name).strip() or "General"
            try:
                ws = spreadsheet.worksheet(worksheet_name)
            except gspread.WorksheetNotFound:
                self.logger.info(f"[GoogleSheets] Creating new worksheet tab '{worksheet_name}' in sheet '{spreadsheet.title}'...")
                ws = spreadsheet.add_worksheet(title=worksheet_name, rows="200", cols="20")
                ws.append_row(self.HEADER_ROW)

            existing_values = ws.get_all_values()
            if not existing_values:
                ws.append_row(self.HEADER_ROW)

            # Extract fields flexibly
            now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

            # Title
            title = kwargs.get("video_title")
            if not title and clip_data:
                title = clip_data.get("title")
            if not title and upload_results:
                title = upload_results.get("title")
            title = title or "Untitled Short"

            # Duration
            duration = kwargs.get("duration")
            if duration is None and clip_data:
                duration = clip_data.get("duration", 0)
            try:
                duration_str = f"{float(duration or 0):.1f}"
            except Exception:
                duration_str = "0.0"

            # Platforms
            platforms = kwargs.get("platforms")
            if not platforms and upload_results:
                platforms = upload_results.get("platforms", [])
            if isinstance(platforms, list):
                platforms_str = ", ".join(p.capitalize() for p in platforms) if platforms else "Local Only"
            else:
                platforms_str = str(platforms or "Local Only")

            # URLs
            yt_url = kwargs.get("youtube_url")
            fb_url = kwargs.get("facebook_url")
            ig_url = kwargs.get("instagram_url")

            if upload_results:
                yt_url = yt_url or upload_results.get("youtube_url") or (upload_results.get("url") if upload_results.get("platform") == "youtube" else "")
                fb_url = fb_url or upload_results.get("facebook_url") or (upload_results.get("url") if upload_results.get("platform") == "facebook" else "")
                ig_url = ig_url or upload_results.get("instagram_url") or (upload_results.get("url") if upload_results.get("platform") == "instagram" else "")

            # Timestamps
            last_time_str = kwargs.get("last_video_ts")
            if not last_time_str:
                last_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_video_time)) if last_video_time > 0 else "N/A"

            next_time_str = kwargs.get("next_video_ts")
            if not next_time_str:
                next_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(next_scheduled_time)) if next_scheduled_time > 0 else "Manual Mode"

            # Status
            status = kwargs.get("status")
            if not status:
                success_count = upload_results.get("success_count", 0) if upload_results else 0
                status = "PUBLISHED" if success_count > 0 else "RENDERED_LOCAL"
                if upload_results and upload_results.get("errors"):
                    status = f"PARTIAL_ERROR: {list(upload_results['errors'].keys())}"

            row = [
                now_str,
                title,
                duration_str,
                platforms_str,
                yt_url or "-",
                fb_url or "-",
                ig_url or "-",
                last_time_str,
                next_time_str,
                status
            ]

            ws.append_row(row)
            self.logger.info(f"[GoogleSheets] Logged entry for '{title}' to tab '{worksheet_name}' in Google Sheet '{spreadsheet.title}'.")
            return True

        except Exception as e:
            self.logger.warning(f"[GoogleSheets] Error logging to Google Sheets: {e}")
            return False

    def backup_agency_profiles_to_sheet(self, config_manager) -> Tuple[bool, str]:
        """
        Takes a full backup of all agency profiles and global settings from settings.json
        and saves it into a dedicated 'Agency_Config_Backup' worksheet tab in Google Sheets.
        """
        if not GSPREAD_AVAILABLE:
            return False, "gspread library is not available."
        if not self.is_configured():
            return False, "Google Sheets is not configured (JSON path or Spreadsheet ID missing)."

        try:
            client = self._get_client()
            spreadsheet = self._open_target_spreadsheet(client)

            backup_tab_name = "Agency_Config_Backup"
            try:
                ws = spreadsheet.worksheet(backup_tab_name)
            except gspread.WorksheetNotFound:
                self.logger.info(f"[GoogleSheets] Creating new backup worksheet '{backup_tab_name}'...")
                ws = spreadsheet.add_worksheet(title=backup_tab_name, rows="200", cols="10")
                ws.append_row(self.BACKUP_HEADER_ROW)

            existing_values = ws.get_all_values()
            if not existing_values:
                ws.append_row(self.BACKUP_HEADER_ROW)

            # Get current full settings
            settings_dict = getattr(config_manager, "_config", {}).copy()
            active_profile = config_manager.get_active_profile_name()
            profiles_list = config_manager.get_profiles_list()

            now_ts = int(time.time())
            now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts))
            payload_json = json.dumps(settings_dict, ensure_ascii=False)

            new_row = [
                str(now_ts),
                now_str,
                active_profile,
                str(len(profiles_list)),
                ", ".join(profiles_list),
                payload_json,
                "Active Backup"
            ]

            # Insert at row 2 so the newest backup is always at the very top of the table
            ws.insert_row(new_row, index=2)

            msg = f"Successfully backed up {len(profiles_list)} profile(s) to '{backup_tab_name}' tab at {now_str}!"
            self.logger.info(f"[GoogleSheets] {msg}")
            return True, msg

        except Exception as e:
            err_msg = f"Failed to back up to Google Sheets: {e}"
            self.logger.error(f"[GoogleSheets] {err_msg}")
            return False, err_msg

    def restore_agency_profiles_from_sheet(self, config_manager) -> Tuple[bool, str]:
        """
        Restores agency profiles and settings from the latest backup in the 'Agency_Config_Backup'
        worksheet tab in Google Sheets.
        """
        if not GSPREAD_AVAILABLE:
            return False, "gspread library is not available."
        if not self.is_configured():
            return False, "Google Sheets is not configured (JSON path or Spreadsheet ID missing)."

        try:
            client = self._get_client()
            spreadsheet = self._open_target_spreadsheet(client)

            backup_tab_name = "Agency_Config_Backup"
            try:
                ws = spreadsheet.worksheet(backup_tab_name)
            except gspread.WorksheetNotFound:
                return False, f"Backup tab '{backup_tab_name}' was not found in the spreadsheet."

            rows = ws.get_all_values()
            if len(rows) < 2:
                return False, f"No backup records found in '{backup_tab_name}'."

            # Row index 1 (2nd row in 0-indexed list) is the newest backup
            backup_row = rows[1]
            if len(backup_row) < 6:
                return False, "Backup record row in sheet is incomplete."

            backup_time_str = backup_row[1]
            payload_json = backup_row[5]

            if not payload_json or not payload_json.strip().startswith("{"):
                return False, "Invalid or empty JSON payload in backup record."

            restored_config = json.loads(payload_json)
            if not isinstance(restored_config, dict) or "profiles" not in restored_config:
                return False, "Restored payload is missing 'profiles' dictionary."

            # Make a local backup of settings.json before overwriting
            local_cfg_path = getattr(config_manager, "config_path", "")
            if local_cfg_path and os.path.exists(local_cfg_path):
                bak_path = local_cfg_path + f".bak_{int(time.time())}"
                try:
                    shutil.copy2(local_cfg_path, bak_path)
                except Exception as bak_err:
                    self.logger.warning(f"Could not create local backup copy: {bak_err}")

            # Write restored config to file
            with open(local_cfg_path, "w", encoding="utf-8") as f:
                json.dump(restored_config, f, indent=4, ensure_ascii=False)

            # Reload config_manager
            config_manager.load_config()

            num_profiles = len(config_manager.get_profiles_list())
            msg = f"Successfully restored {num_profiles} profile(s) from backup ({backup_time_str})!"
            self.logger.info(f"[GoogleSheets] {msg}")
            return True, msg

        except Exception as e:
            err_msg = f"Failed to restore from Google Sheets: {e}"
            self.logger.error(f"[GoogleSheets] {err_msg}")
            return False, err_msg
