import os
import time
import json
import logging
import threading
import webbrowser
from typing import Optional, List, Dict, Any, Tuple, Callable
import customtkinter as ctk
from tkinter import filedialog, messagebox
from config import ConfigManager
from ui.precheck_modal import SystemPrecheckModal
from core.sheets_logger import (
    GoogleSheetsLogger,
    get_service_account_email,
    extract_spreadsheet_id,
    construct_spreadsheet_url
)


class AdminPasswordDialog(ctk.CTkToplevel):
    """
    Password prompt modal to restrict access to AMB Enterprise admin settings.
    """

    def __init__(self, parent, config_manager: ConfigManager, on_success_callback):
        super().__init__(parent)
        self.config_manager = config_manager
        self.on_success_callback = on_success_callback

        self.title("Admin Authentication")
        self.geometry("380x220")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        # Center modal over parent
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (380 // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (220 // 2)
        self.geometry(f"+{x}+{y}")

        self._build_ui()

    def _build_ui(self):
        title_label = ctk.CTkLabel(
            self,
            text="🔒 Admin Access Required",
            font=ctk.CTkFont(size=18, weight="bold")
        )
        title_label.pack(pady=(20, 10))

        sub_label = ctk.CTkLabel(
            self,
            text="Enter admin password to access system settings:",
            font=ctk.CTkFont(size=12),
            text_color="gray"
        )
        sub_label.pack(pady=(0, 15))

        self.password_entry = ctk.CTkEntry(
            self,
            placeholder_text="Enter Admin Password",
            show="*",
            width=260
        )
        self.password_entry.pack(pady=5)
        self.password_entry.focus()
        self.password_entry.bind("<Return>", lambda e: self._verify())

        self.error_label = ctk.CTkLabel(
            self,
            text="",
            text_color="#FF4D4D",
            font=ctk.CTkFont(size=11)
        )
        self.error_label.pack(pady=2)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=10)

        cancel_btn = ctk.CTkButton(
            btn_frame,
            text="Cancel",
            width=100,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self.destroy
        )
        cancel_btn.pack(side="left", padx=5)

        login_btn = ctk.CTkButton(
            btn_frame,
            text="Authenticate",
            width=140,
            command=self._verify
        )
        login_btn.pack(side="left", padx=5)

    def _verify(self):
        entered_pass = self.password_entry.get().strip()
        if self.config_manager.verify_admin_password(entered_pass):
            self.destroy()
            self.on_success_callback()
        else:
            self.error_label.configure(text="Invalid password. Default is 'admin123'")


class AdminSettingsModal(ctk.CTkToplevel):
    """
    Tabbed Admin Settings Modal for AMB Enterprise featuring:
    - Multi-Channel Profile switcher (+ and - buttons) on top of tabs
    - Tab 1: API Keys & Auth (Groq API Primary + Pool, and YouTube OAuth Credentials)
    - Tab 2: Customization & Engine (Custom Template Browse, GPU auto-detection across profiles,
      Autopilot 1-24h interval, resolution dropdown, and clips configuration)
    """

    def __init__(
        self,
        parent,
        config_manager: ConfigManager,
        on_save_callback: Optional[Callable[[str], None]] = None,
        on_profile_switch_callback: Optional[Callable[[str], None]] = None
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.on_save_callback = on_save_callback
        self.on_profile_switch_callback = on_profile_switch_callback
        self.logger = logging.getLogger("AMBEnterprise")

        self.title("AMB Enterprise - Channel Profiles & Admin Settings")
        self.geometry("600x760")
        self.minsize(580, 720)
        self.transient(parent)
        self.grab_set()

        # Center modal over parent
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (600 // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (760 // 2)
        self.geometry(f"+{x}+{y}")

        self._build_ui()
        self._load_fields_for_profile(self.config_manager.get_active_profile_name())

    def _build_ui(self):
        # Header
        header = ctk.CTkLabel(
            self,
            text="⚙️ AMB Enterprise Admin Panel",
            font=ctk.CTkFont(size=20, weight="bold")
        )
        header.pack(pady=(12, 6))

        # --- PROFILE MANAGEMENT BAR (ON TOP OF TABS) ---
        profile_bar = ctk.CTkFrame(self, fg_color="#1E232A", corner_radius=8)
        profile_bar.pack(fill="x", padx=20, pady=(0, 10))

        ctk.CTkLabel(
            profile_bar,
            text="📺 Channel Profile:",
            font=ctk.CTkFont(size=13, weight="bold")
        ).pack(side="left", padx=(12, 6), pady=8)

        self.active_profile_var = ctk.StringVar(value=self.config_manager.get_active_profile_name())
        self.profile_dropdown = ctk.CTkOptionMenu(
            profile_bar,
            variable=self.active_profile_var,
            values=self.config_manager.get_profiles_list(),
            command=self._on_profile_switched,
            width=210
        )
        self.profile_dropdown.pack(side="left", padx=5, pady=8)

        add_profile_btn = ctk.CTkButton(
            profile_bar,
            text="➕ New",
            width=70,
            fg_color="#238636",
            hover_color="#2EA043",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._add_profile_click
        )
        add_profile_btn.pack(side="left", padx=4, pady=8)

        del_profile_btn = ctk.CTkButton(
            profile_bar,
            text="➖ Delete",
            width=75,
            fg_color="#DA3633",
            hover_color="#F85149",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._delete_profile_click
        )
        del_profile_btn.pack(side="left", padx=4, pady=8)

        precheck_btn = ctk.CTkButton(
            profile_bar,
            text="🩺 Pre-Check",
            width=100,
            fg_color="#1F6FEB",
            hover_color="#388BFD",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._open_precheck_dialog
        )
        precheck_btn.pack(side="right", padx=(4, 10), pady=8)

        # --- CTkTabview Container (2 Tabs) ---
        self.tabview = ctk.CTkTabview(self, width=560, height=580)
        self.tabview.pack(padx=20, pady=(0, 10), fill="both", expand=True)

        self.tab_api = self.tabview.add("🔑 API Keys & Auth")
        self.tab_custom = self.tabview.add("🎨 Customization & Engine")

        # Build tabs
        self._build_api_keys_and_auth_tab()
        self._build_customization_tab()

        # Footer Action Buttons
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=20, pady=8)

        # Global Backup and Restore buttons on left side
        backup_footer_btn = ctk.CTkButton(
            footer,
            text="☁️ Backup to Sheet",
            width=140,
            fg_color="#238636",
            hover_color="#2EA043",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._global_backup_to_sheet
        )
        backup_footer_btn.pack(side="left", padx=(0, 8))

        restore_footer_btn = ctk.CTkButton(
            footer,
            text="📥 Restore from Sheet",
            width=150,
            fg_color="#8957E5",
            hover_color="#A371F7",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._global_restore_from_sheet
        )
        restore_footer_btn.pack(side="left")

        cancel_btn = ctk.CTkButton(
            footer,
            text="Cancel",
            width=110,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self.destroy
        )
        cancel_btn.pack(side="right", padx=(10, 0))

        save_btn = ctk.CTkButton(
            footer,
            text="💾 Save Settings",
            width=140,
            fg_color="#1F6AA5",
            hover_color="#144870",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._save_all_settings
        )
        save_btn.pack(side="right")

    def _build_api_keys_and_auth_tab(self):
        frame = ctk.CTkScrollableFrame(self.tab_api)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        # Primary Groq Key
        ctk.CTkLabel(
            frame,
            text="Default Groq API Key (Shared Across All Channels)",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(5, 2))

        self.groq_key_entry = ctk.CTkEntry(
            frame,
            placeholder_text="gsk_...",
            width=480,
            show="*"
        )
        self.groq_key_entry.pack(anchor="w", pady=(0, 5))

        self.show_key_var = ctk.BooleanVar(value=False)
        show_key_chk = ctk.CTkCheckBox(
            frame,
            text="Show Primary Key",
            variable=self.show_key_var,
            command=self._toggle_key_visibility
        )
        show_key_chk.pack(anchor="w", pady=(0, 15))

        # Groq Keys Pool
        ctk.CTkLabel(
            frame,
            text="Multiple Groq API Keys Pool (Failover Rotation - Shared Across All Channels)",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(5, 2))

        ctk.CTkLabel(
            frame,
            text="Enter additional backup Groq keys (one per line):",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        ).pack(anchor="w", pady=(0, 5))

        self.keys_textbox = ctk.CTkTextbox(frame, width=480, height=90)
        self.keys_textbox.pack(anchor="w", pady=(0, 20))

        # --- YOUTUBE OAUTH CREDENTIALS ---
        ctk.CTkLabel(
            frame,
            text="📺 Channel YouTube OAuth 2.0 Credentials (client_secret.json)",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(5, 2))

        ctk.CTkLabel(
            frame,
            text="Select the Google Cloud OAuth client secret JSON specific to this channel:",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        ).pack(anchor="w", pady=(0, 5))

        yt_box = ctk.CTkFrame(frame, fg_color="transparent")
        yt_box.pack(fill="x", pady=(0, 10))

        self.yt_path_entry = ctk.CTkEntry(
            yt_box,
            placeholder_text="Path to client_secret.json...",
            width=360
        )
        self.yt_path_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        browse_btn = ctk.CTkButton(
            yt_box,
            text="Browse JSON...",
            width=110,
            command=self._browse_oauth_json
        )
        browse_btn.pack(side="left")

        # YouTube Auth Token Status Badge
        self.token_status_label = ctk.CTkLabel(
            frame,
            text="Checking YouTube OAuth...",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.token_status_label.pack(anchor="w", pady=(0, 15))

        # --- META GRAPH API CREDENTIALS (FACEBOOK & INSTAGRAM) ---
        ctk.CTkLabel(
            frame,
            text="🌐 Meta Graph API Credentials (Facebook & Instagram Reels Publishing)",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(10, 2))

        ctk.CTkLabel(
            frame,
            text="Meta Access Token (Single unified token for both Facebook & Instagram):",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        ).pack(anchor="w", pady=(0, 2))

        self.meta_token_entry = ctk.CTkEntry(
            frame,
            placeholder_text="EAAB... (Paste your Meta User / Page Access Token here)",
            width=480
        )
        self.meta_token_entry.pack(anchor="w", pady=(0, 10))

        ctk.CTkLabel(
            frame,
            text="Facebook Profile / Page ID:",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        ).pack(anchor="w", pady=(0, 2))

        self.fb_page_id_entry = ctk.CTkEntry(
            frame,
            placeholder_text="e.g. 1029384756...",
            width=480
        )
        self.fb_page_id_entry.pack(anchor="w", pady=(0, 10))

        ctk.CTkLabel(
            frame,
            text="Instagram Profile / Account ID:",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        ).pack(anchor="w", pady=(0, 2))

        self.ig_account_id_entry = ctk.CTkEntry(
            frame,
            placeholder_text="e.g. 1784140599...",
            width=480
        )
        self.ig_account_id_entry.pack(anchor="w", pady=(0, 15))

        # --- GOOGLE SHEETS AUTOMATED LOGGING & CLOUD DISASTER RECOVERY ---
        ctk.CTkLabel(
            frame,
            text="📊 Google Sheets Automated Logging & Cloud Disaster Recovery",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(10, 2))

        ctk.CTkLabel(
            frame,
            text="Browse Google Service Account .json to automatically log each channel on separate tabs in the same Google Sheet:",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        ).pack(anchor="w", pady=(0, 5))

        sheets_box = ctk.CTkFrame(frame, fg_color="transparent")
        sheets_box.pack(fill="x", pady=(0, 8))

        self.sheets_path_entry = ctk.CTkEntry(
            sheets_box,
            placeholder_text="Path to Google Service Account .json...",
            width=360
        )
        self.sheets_path_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        browse_sheets_btn = ctk.CTkButton(
            sheets_box,
            text="Browse JSON...",
            width=110,
            command=self._browse_sheets_json
        )
        browse_sheets_btn.pack(side="left")

        # Service Account Email for Editor Permission (Highlight Box)
        email_card = ctk.CTkFrame(frame, fg_color="#161B22", border_color="#30363D", border_width=1, corner_radius=6)
        email_card.pack(fill="x", pady=(0, 10), padx=2)

        ctk.CTkLabel(
            email_card,
            text="📧 Service Account Email (Editor Permission Required):",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#58A6FF"
        ).pack(anchor="w", padx=10, pady=(8, 2))

        ctk.CTkLabel(
            email_card,
            text="Share your Google Spreadsheet with this email and assign 'Editor' access so the agent can write logs and backups:",
            font=ctk.CTkFont(size=11),
            text_color="#8B949E"
        ).pack(anchor="w", padx=10, pady=(0, 6))

        email_row = ctk.CTkFrame(email_card, fg_color="transparent")
        email_row.pack(fill="x", padx=10, pady=(0, 8))

        self.service_email_entry = ctk.CTkEntry(
            email_row,
            placeholder_text="(Select a valid Service Account JSON file above)",
            font=ctk.CTkFont(family="Consolas", size=11),
            state="readonly"
        )
        self.service_email_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.copy_email_btn = ctk.CTkButton(
            email_row,
            text="📋 Copy Email",
            width=110,
            fg_color="#21262D",
            hover_color="#30363D",
            border_color="#388BFD",
            border_width=1,
            command=self._copy_service_account_email
        )
        self.copy_email_btn.pack(side="left")

        self.open_cloud_btn = ctk.CTkButton(
            email_row,
            text="☁️ View on Cloud",
            width=120,
            fg_color="#1F6AA5",
            hover_color="#144870",
            command=self._open_google_cloud_service_accounts
        )
        self.open_cloud_btn.pack(side="left", padx=(6, 0))

        # Google Sheet Link / URL field
        ctk.CTkLabel(
            frame,
            text="🔗 Google Sheet Link / URL (Auto-opens Editor Verification page when pasted):",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#FFFFFF"
        ).pack(anchor="w", pady=(4, 2))

        url_box = ctk.CTkFrame(frame, fg_color="transparent")
        url_box.pack(fill="x", pady=(0, 8))

        self.sheets_url_entry = ctk.CTkEntry(
            url_box,
            placeholder_text="https://docs.google.com/spreadsheets/d/1BxiMVs.../edit",
            width=360
        )
        self.sheets_url_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.sheets_url_entry.bind("<KeyRelease>", self._on_sheets_url_changed)
        self.sheets_url_entry.bind("<Control-v>", lambda e: self.after(50, self._on_sheets_url_changed))
        self.sheets_url_entry.bind("<<Paste>>", lambda e: self.after(50, self._on_sheets_url_changed))

        self.open_sheet_btn = ctk.CTkButton(
            url_box,
            text="🔗 Open Sheet",
            width=110,
            fg_color="#1F6AA5",
            hover_color="#144870",
            command=self._open_sheet_in_browser
        )
        self.open_sheet_btn.pack(side="left")

        # Spreadsheet ID (Auto-extracted)
        ctk.CTkLabel(
            frame,
            text="Google Spreadsheet ID or Name (Auto-extracted from URL):",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        ).pack(anchor="w", pady=(0, 2))

        self.sheets_id_entry = ctk.CTkEntry(
            frame,
            placeholder_text="Spreadsheet ID (e.g. 1BxiMVs0XRA5...) or Name",
            width=480
        )
        self.sheets_id_entry.pack(anchor="w", pady=(0, 8))
        self.sheets_id_entry.bind("<KeyRelease>", self._on_sheets_id_changed)

        # Toggle Switch & Test Connection Button
        sheets_ctrl_row = ctk.CTkFrame(frame, fg_color="transparent")
        sheets_ctrl_row.pack(fill="x", pady=(0, 10))

        self.enable_sheets_var = ctk.BooleanVar(value=True)
        sheets_switch = ctk.CTkSwitch(
            sheets_ctrl_row,
            text="Enable Real-Time Google Sheets Logging",
            variable=self.enable_sheets_var,
            font=ctk.CTkFont(size=12, weight="bold")
        )
        sheets_switch.pack(side="left")

        test_sheets_btn = ctk.CTkButton(
            sheets_ctrl_row,
            text="🔌 Test Connection",
            width=140,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self._test_sheets_connection
        )
        test_sheets_btn.pack(side="right")

        self.sheets_status_label = ctk.CTkLabel(
            frame,
            text="Google Sheets Logging: Not configured",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        )
        self.sheets_status_label.pack(anchor="w", pady=(0, 12))

        # --- CLOUD BACKUP & DISASTER RECOVERY CARD ---
        backup_card = ctk.CTkFrame(frame, fg_color="#1C2128", border_color="#30363D", border_width=1, corner_radius=6)
        backup_card.pack(fill="x", pady=(0, 15), padx=2)

        ctk.CTkLabel(
            backup_card,
            text="☁️ Agency Profile Cloud Backup & Restore (Google Sheet)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#FFFFFF"
        ).pack(anchor="w", padx=10, pady=(8, 2))

        ctk.CTkLabel(
            backup_card,
            text="Save complete backup of all channel profiles & settings to 'Agency_Config_Backup' tab in your Google Sheet, or restore settings anytime with 1 click:",
            font=ctk.CTkFont(size=11),
            text_color="#8B949E"
        ).pack(anchor="w", padx=10, pady=(0, 8))

        backup_btns_row = ctk.CTkFrame(backup_card, fg_color="transparent")
        backup_btns_row.pack(fill="x", padx=10, pady=(0, 8))

        self.backup_btn_card = ctk.CTkButton(
            backup_btns_row,
            text="☁️ Global Backup to Sheet",
            width=190,
            fg_color="#238636",
            hover_color="#2EA043",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._global_backup_to_sheet
        )
        self.backup_btn_card.pack(side="left", padx=(0, 10))

        self.restore_btn_card = ctk.CTkButton(
            backup_btns_row,
            text="📥 Global Restore from Sheet",
            width=200,
            fg_color="#8957E5",
            hover_color="#A371F7",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._global_restore_from_sheet
        )
        self.restore_btn_card.pack(side="left")

        self.backup_status_label = ctk.CTkLabel(
            backup_card,
            text="Backup: Ready (Saves all channel profiles to 'Agency_Config_Backup' tab)",
            font=ctk.CTkFont(size=11),
            text_color="#8B949E"
        )
        self.backup_status_label.pack(anchor="w", padx=10, pady=(0, 8))

    def _build_customization_tab(self):
        frame = ctk.CTkScrollableFrame(self.tab_custom)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        # --- CHANNEL TEMPLATE OVERLAY ---
        ctk.CTkLabel(
            frame,
            text="🖼️ Channel Video Template (Overlay & Blue Box Viewport)",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(5, 2))

        ctk.CTkLabel(
            frame,
            text="The engine fits your video in the blue box with header and footer on top:",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        ).pack(anchor="w", pady=(0, 5))

        tpl_frame = ctk.CTkFrame(frame, fg_color="transparent")
        tpl_frame.pack(fill="x", pady=(0, 15))

        self.tpl_path_entry = ctk.CTkEntry(
            tpl_frame,
            placeholder_text="assets/wealth secret template (2).jpg",
            width=340
        )
        self.tpl_path_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        browse_tpl_btn = ctk.CTkButton(
            tpl_frame,
            text="Browse Template...",
            width=130,
            command=self._browse_template_image
        )
        browse_tpl_btn.pack(side="left")

        # --- MULTI-PLATFORM AUTO-UPLOADING (YOUTUBE, FACEBOOK, INSTAGRAM) ---
        ctk.CTkLabel(
            frame,
            text="🚀 Multi-Platform Publishing Toggles",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(5, 2))

        ctk.CTkLabel(
            frame,
            text="Turn automatic publishing ON or OFF individually for each social platform:",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        ).pack(anchor="w", pady=(0, 6))

        self.upload_yt_var = ctk.BooleanVar(value=True)
        yt_upload_switch = ctk.CTkSwitch(
            frame,
            text="Upload to YouTube Shorts",
            variable=self.upload_yt_var,
            font=ctk.CTkFont(size=12, weight="bold")
        )
        yt_upload_switch.pack(anchor="w", pady=(0, 6))

        self.upload_fb_var = ctk.BooleanVar(value=True)
        fb_upload_switch = ctk.CTkSwitch(
            frame,
            text="Upload to Facebook Reels (Page)",
            variable=self.upload_fb_var,
            font=ctk.CTkFont(size=12, weight="bold")
        )
        fb_upload_switch.pack(anchor="w", pady=(0, 6))

        self.upload_ig_var = ctk.BooleanVar(value=True)
        ig_upload_switch = ctk.CTkSwitch(
            frame,
            text="Upload to Instagram Reels (Professional Account)",
            variable=self.upload_ig_var,
            font=ctk.CTkFont(size=12, weight="bold")
        )
        ig_upload_switch.pack(anchor="w", pady=(0, 15))

        # --- HARDWARE ENGINE & GPU STATUS ---
        ctk.CTkLabel(
            frame,
            text="⚡ Dynamic Hardware Auto-Detection & GPU Acceleration",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(5, 2))

        hw_info = self.config_manager.get_hardware_settings()
        gpu_detected = hw_info.get("has_gpu", False)
        hw_summary = hw_info.get("summary", "")

        status_color = "#2EA043" if gpu_detected else "#D29922"

        self.gpu_badge = ctk.CTkLabel(
            frame,
            text=f"Live Hardware Status: {hw_summary}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=status_color
        )
        self.gpu_badge.pack(anchor="w", pady=(0, 10))

        ctk.CTkLabel(frame, text="Hardware Performance Profile:").pack(anchor="w", pady=(0, 2))
        self.hw_options = [
            "⚡ Auto-Detect & Maximum Performance (GPU Priority)",
            "High-End / Maximum Performance (GPU Float16 / Large-v3 Whisper)",
            "Balanced Performance (GPU / Medium Whisper)",
            "Low-End / Conservative (Lightweight / Small Whisper)"
        ]
        self.hw_dropdown = ctk.CTkOptionMenu(frame, values=self.hw_options, width=480)
        
        current_hw_prof = self.config_manager.get("hardware_profile", self.hw_options[0])
        if current_hw_prof not in self.hw_options:
            current_hw_prof = self.hw_options[0]
            
        self.hw_dropdown.set(current_hw_prof)
        self.hw_dropdown.pack(anchor="w", pady=(0, 15))

        # --- AUTOPILOT ---
        ctk.CTkLabel(
            frame,
            text="⏱️ Autopilot Automation & Interval",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(5, 2))

        self.autopilot_var = ctk.BooleanVar(value=False)
        autopilot_switch = ctk.CTkSwitch(
            frame,
            text="Enable Autonomous Autopilot for this Profile",
            variable=self.autopilot_var,
            font=ctk.CTkFont(size=12, weight="bold")
        )
        autopilot_switch.pack(anchor="w", pady=(0, 8))

        interval_row = ctk.CTkFrame(frame, fg_color="transparent")
        interval_row.pack(fill="x", pady=(0, 15))

        ctk.CTkLabel(
            interval_row,
            text="Auto-Pilot Interval:",
            font=ctk.CTkFont(size=12)
        ).pack(side="left", padx=(0, 10))

        self.interval_options = [f"{i} hour" if i == 1 else f"{i} hours" for i in range(1, 25)]
        self.interval_dropdown = ctk.CTkOptionMenu(
            interval_row,
            values=self.interval_options,
            width=160
        )
        self.interval_dropdown.set("2 hours")
        self.interval_dropdown.pack(side="left")

        # --- YOUTUBE DOWNLOAD RESOLUTION ---
        ctk.CTkLabel(
            frame,
            text="📥 YouTube Source Download Resolution",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(5, 2))

        self.yt_res_options = ["1080p", "720p", "480p", "360p"]
        self.yt_res_dropdown = ctk.CTkOptionMenu(
            frame,
            values=self.yt_res_options,
            width=480
        )
        self.yt_res_dropdown.set("480p")
        self.yt_res_dropdown.pack(anchor="w", pady=(0, 15))
        
        # --- CAPTIONS & FACE TRACKING ---
        ctk.CTkLabel(
            frame,
            text="🎬 Visuals, Captions & AI Face Tracking",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(5, 2))

        self.enable_captions_var = ctk.BooleanVar(value=True)
        captions_switch = ctk.CTkSwitch(
            frame,
            text="Burn-in ASS Captions (Animated Subtitles)",
            variable=self.enable_captions_var,
            font=ctk.CTkFont(size=12, weight="bold")
        )
        captions_switch.pack(anchor="w", pady=(0, 8))

        self.face_tracker_var = ctk.BooleanVar(value=True)
        face_tracker_switch = ctk.CTkSwitch(
            frame,
            text="AI Face Tracker (Dynamic Auto-Crop on Speaker)",
            variable=self.face_tracker_var,
            font=ctk.CTkFont(size=12, weight="bold")
        )
        face_tracker_switch.pack(anchor="w", pady=(0, 15))

        # --- OUTPUT RESOLUTION ---
        ctk.CTkLabel(
            frame,
            text="📱 Output Video Resolution Preset",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(5, 2))

        self.res_options = [
            "1080x1920 (9:16 Vertical Shorts)",
            "720x1280 (9:16 Vertical Shorts)",
            "1920x1080 (16:9 Landscape - 1080p)",
            "1280x720 (16:9 Landscape - 720p)",
            "854x480 (16:9 Landscape - 480p)",
            "640x360 (16:9 Landscape - 360p)",
            "426x240 (16:9 Landscape - 240p)",
            "1080x1080 (1:1 Square)"
        ]

        self.res_dropdown = ctk.CTkOptionMenu(
            frame,
            values=self.res_options,
            width=480
        )
        self.res_dropdown.set(self.res_options[0])
        self.res_dropdown.pack(anchor="w", pady=(0, 15))

        # --- CAPTION LANGUAGE & COLOR ---
        ctk.CTkLabel(
            frame,
            text="🌐 Caption Language",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(5, 2))

        self.caption_language_options = [
            "English", "Urdu", "Spanish", "French", "German",
            "Hindi", "Arabic", "Chinese", "Japanese", "Portuguese", "Russian"
        ]
        self.language_options = self.caption_language_options
        self.caption_language_var = ctk.StringVar(value="English")
        self.caption_language_combo = ctk.CTkComboBox(
            frame,
            values=self.caption_language_options,
            variable=self.caption_language_var,
            width=480,
            state="readonly",
            command=self._on_admin_caption_language_change
        )
        self.caption_language_combo.set("English")
        self.caption_language_combo.pack(anchor="w", pady=(0, 15))
        self.channel_language_dropdown = self.caption_language_combo

        ctk.CTkLabel(
            frame,
            text="🎨 Caption Color",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(5, 2))

        self.caption_color_options = ["White", "Yellow", "Neon Green"]
        self.caption_color_var = ctk.StringVar(value="Yellow")
        self.caption_color_combo = ctk.CTkComboBox(
            frame,
            values=self.caption_color_options,
            variable=self.caption_color_var,
            width=480,
            state="readonly",
            command=self._on_admin_caption_color_change
        )
        self.caption_color_combo.set("Yellow")
        self.caption_color_combo.pack(anchor="w", pady=(0, 15))

        # --- CAPTION FONT & BROWSE ---
        ctk.CTkLabel(
            frame,
            text="🔤 Caption Font",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(5, 2))

        ctk.CTkLabel(
            frame,
            text="Select standard font preset or click Browse to choose custom .ttf / .otf font file:",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        ).pack(anchor="w", pady=(0, 5))

        font_frame = ctk.CTkFrame(frame, fg_color="transparent")
        font_frame.pack(fill="x", pady=(0, 15))

        self.caption_font_options = self.config_manager.get_supported_caption_fonts()
        self.caption_font_var = ctk.StringVar(value=self.config_manager.get_caption_font())
        self.caption_font_combo = ctk.CTkComboBox(
            font_frame,
            values=self.caption_font_options,
            variable=self.caption_font_var,
            width=330,
            command=self._on_admin_caption_font_change
        )
        self.caption_font_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))

        browse_font_btn = ctk.CTkButton(
            font_frame,
            text="Browse Font...",
            width=130,
            command=self._browse_font_file
        )
        browse_font_btn.pack(side="left")

        # --- CLIPS & SMART RESUME ---
        ctk.CTkLabel(
            frame,
            text="🎬 Smart Resume: Clips Per Video",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(5, 2))

        clips_row = ctk.CTkFrame(frame, fg_color="transparent")
        clips_row.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            clips_row,
            text="Clips to extract before downloading new video:",
            font=ctk.CTkFont(size=12)
        ).pack(side="left", padx=(0, 10))

        self.clips_per_vid_dropdown = ctk.CTkOptionMenu(
            clips_row,
            values=["3", "4", "5"],
            width=100
        )
        self.clips_per_vid_dropdown.set("3")
        self.clips_per_vid_dropdown.pack(side="left")

        self.reuse_var = ctk.BooleanVar(value=True)
        reuse_switch = ctk.CTkSwitch(
            frame,
            text="Smart Resume Lock: Finish 1 Video Completely Before New Download",
            variable=self.reuse_var,
            font=ctk.CTkFont(size=11, weight="bold")
        )
        reuse_switch.pack(anchor="w", pady=(0, 8))

        self.cleanup_var = ctk.BooleanVar(value=True)
        cleanup_switch = ctk.CTkSwitch(
            frame,
            text="Auto-Delete Heavy Source Videos & Temp Audio After Clips Finish",
            variable=self.cleanup_var,
            font=ctk.CTkFont(size=11, weight="bold")
        )
        cleanup_switch.pack(anchor="w", pady=(0, 15))

        # Target Channel
        ctk.CTkLabel(
            frame,
            text="🎯 Target YouTube Channel (Autopilot Source)",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(5, 2))

        self.target_channel_entry = ctk.CTkEntry(frame, width=480, placeholder_text="https://www.youtube.com/@ChannelName")
        self.target_channel_entry.pack(anchor="w", pady=(0, 10))
        
        fetch_type_row = ctk.CTkFrame(frame, fg_color="transparent")
        fetch_type_row.pack(fill="x", pady=(0, 15))
        
        ctk.CTkLabel(
            fetch_type_row,
            text="Fetch Strategy (Always Unique):",
            font=ctk.CTkFont(size=12)
        ).pack(side="left", padx=(0, 10))
        
        self.fetch_type_dropdown = ctk.CTkOptionMenu(
            fetch_type_row,
            values=["Viral (Most Viewed)", "Random"],
            width=200
        )
        self.fetch_type_dropdown.set("Viral (Most Viewed)")
        self.fetch_type_dropdown.pack(side="left")

        # History
        ctk.CTkLabel(
            frame,
            text="📜 Processed History & Duplication Records",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(5, 2))

        self.history_label = ctk.CTkLabel(
            frame,
            text="Total Unique Videos Processed: 0 | Total Clips Recorded: 0",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        )
        self.history_label.pack(anchor="w", pady=(0, 5))

        ctk.CTkButton(
            frame,
            text="Clear Processed History",
            width=160,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self._clear_history
        ).pack(anchor="w", pady=(0, 15))

        # Password
        ctk.CTkLabel(
            frame,
            text="🔒 Change Admin Password",
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", pady=(5, 2))

        self.new_pass_entry = ctk.CTkEntry(
            frame,
            placeholder_text="New Password (leave blank to keep current)",
            show="*",
            width=480
        )
        self.new_pass_entry.pack(anchor="w", pady=(0, 10))

    def _load_fields_for_profile(self, profile_name: str):
        """Populates all modal inputs with the settings of the selected profile."""
        # Groq API Keys (Shared Across Channels)
        groq_key = self.config_manager.get_shared_groq_api_key()
        self.groq_key_entry.delete(0, "end")
        self.groq_key_entry.insert(0, groq_key)

        pool_keys = self.config_manager.get_shared_groq_keys_pool()
        self.keys_textbox.delete("1.0", "end")
        self.keys_textbox.insert("1.0", "\n".join(pool_keys))

        yt_path = self.config_manager.get_channel_setting("youtube_oauth_json_path", "", profile_name)
        self.yt_path_entry.delete(0, "end")
        self.yt_path_entry.insert(0, yt_path)

        tpl_path = self.config_manager.get_template_path(profile_name)
        self.tpl_path_entry.delete(0, "end")
        self.tpl_path_entry.insert(0, tpl_path)

        current_interval_h = self.config_manager.get_channel_setting("autopilot_interval_hours", 2, profile_name)
        default_interval_str = f"{current_interval_h} hour" if current_interval_h == 1 else f"{current_interval_h} hours"
        if default_interval_str not in self.interval_options:
            default_interval_str = "2 hours"
        self.interval_dropdown.set(default_interval_str)

        current_preset = self.config_manager.get_channel_setting("resolution_preset", "1080x1920 (9:16 Vertical Shorts)", profile_name)
        if current_preset in self.res_options:
            self.res_dropdown.set(current_preset)
        else:
            self.res_dropdown.set(self.res_options[0])

        yt_res = self.config_manager.get_channel_setting("youtube_download_resolution", "480p", profile_name)
        if yt_res in self.yt_res_options:
            self.yt_res_dropdown.set(yt_res)

        current_lang = self.config_manager.get_caption_language(profile_name)
        if hasattr(self, "caption_language_var"):
            self.caption_language_var.set(current_lang)
        if hasattr(self, "caption_language_combo"):
            self.caption_language_combo.set(current_lang)
        if hasattr(self, "channel_language_dropdown"):
            self.channel_language_dropdown.set(current_lang)

        current_color = self.config_manager.get_caption_color(profile_name)
        if hasattr(self, "caption_color_var"):
            self.caption_color_var.set(current_color)
        if hasattr(self, "caption_color_combo"):
            self.caption_color_combo.set(current_color)

        current_font = self.config_manager.get_caption_font(profile_name)
        if hasattr(self, "caption_font_var"):
            self.caption_font_var.set(current_font)
        if hasattr(self, "caption_font_combo"):
            current_vals = list(self.caption_font_combo.cget("values"))
            if current_font and current_font not in current_vals:
                current_vals.insert(0, current_font)
                self.caption_font_combo.configure(values=current_vals)
            self.caption_font_combo.set(current_font)
            
        captions_on = self.config_manager.get_channel_setting("enable_captions", True, profile_name)
        self.enable_captions_var.set(captions_on)
        
        face_on = self.config_manager.get_channel_setting("enable_face_tracking", True, profile_name)
        self.face_tracker_var.set(face_on)

        target_clips = self.config_manager.get_channel_setting("target_clips_per_video", 3, profile_name)
        self.clips_per_vid_dropdown.set(str(target_clips))

        autopilot_on = self.config_manager.get_channel_setting("auto_pilot", False, profile_name)
        self.autopilot_var.set(autopilot_on)

        target_url = self.config_manager.get_channel_setting("target_channel_url", "", profile_name)
        self.target_channel_entry.delete(0, "end")
        self.target_channel_entry.insert(0, target_url)
        
        fetch_strat = self.config_manager.get_channel_setting("fetch_strategy", "Viral (Most Viewed)", profile_name)
        self.fetch_type_dropdown.set(fetch_strat)

        hist = self.config_manager.get_channel_setting("processed_video_ids", [], profile_name)
        clips = self.config_manager.get_channel_setting("generated_clips_history", [], profile_name)
        self.history_label.configure(text=f"Total Unique Videos Processed: {len(hist)} | Total Clips Recorded: {len(clips)}")

        # Check OAuth token status
        if yt_path and os.path.exists(yt_path):
            self.token_status_label.configure(text="✅ YouTube OAuth client_secret.json Configured", text_color="#2EA043")
        else:
            self.token_status_label.configure(text="⚠️ OAuth client_secret.json missing (Shorts will save locally on PC)", text_color="#D29922")

        # Meta Graph API Credentials (Facebook & Instagram)
        meta_tok = (
            self.config_manager.get_channel_setting("meta_access_token", "", profile_name) or
            self.config_manager.get_channel_setting("facebook_access_token", "", profile_name) or
            self.config_manager.get_channel_setting("instagram_access_token", "", profile_name)
        )
        fb_page_id = self.config_manager.get_channel_setting("facebook_page_id", "", profile_name)
        ig_account_id = self.config_manager.get_channel_setting("instagram_account_id", "", profile_name)

        if hasattr(self, "meta_token_entry"):
            self.meta_token_entry.delete(0, "end")
            self.meta_token_entry.insert(0, meta_tok)
        if hasattr(self, "fb_page_id_entry"):
            self.fb_page_id_entry.delete(0, "end")
            self.fb_page_id_entry.insert(0, fb_page_id)
        if hasattr(self, "ig_account_id_entry"):
            self.ig_account_id_entry.delete(0, "end")
            self.ig_account_id_entry.insert(0, ig_account_id)

        # Multi-Platform Upload Toggles
        if hasattr(self, "upload_yt_var"):
            self.upload_yt_var.set(self.config_manager.get_channel_setting("upload_to_youtube", True, profile_name))
        if hasattr(self, "upload_fb_var"):
            self.upload_fb_var.set(self.config_manager.get_channel_setting("upload_to_facebook", True, profile_name))
        if hasattr(self, "upload_ig_var"):
            self.upload_ig_var.set(self.config_manager.get_channel_setting("upload_to_instagram", True, profile_name))

        # Google Sheets Service Account & Settings
        sheets_path = (
            self.config_manager.get_channel_setting("google_sheets_json_path", "", profile_name) or
            self.config_manager.get("google_sheets_json_path", "")
        )
        sheets_id = (
            self.config_manager.get_channel_setting("google_spreadsheet_id", "", profile_name) or
            self.config_manager.get("google_spreadsheet_id", "")
        )
        sheets_url = (
            self.config_manager.get_channel_setting("google_sheet_url", "", profile_name) or
            self.config_manager.get("google_sheet_url", "")
        )
        if not sheets_url and sheets_id:
            sheets_url = construct_spreadsheet_url(sheets_id)
        if not sheets_id and sheets_url:
            sheets_id = extract_spreadsheet_id(sheets_url)

        sheets_enabled = self.config_manager.get_channel_setting("enable_google_sheets_logging", True, profile_name)

        if hasattr(self, "sheets_path_entry"):
            self.sheets_path_entry.delete(0, "end")
            self.sheets_path_entry.insert(0, sheets_path)
        if hasattr(self, "sheets_id_entry"):
            self.sheets_id_entry.delete(0, "end")
            self.sheets_id_entry.insert(0, sheets_id)
        if hasattr(self, "sheets_url_entry"):
            self.sheets_url_entry.delete(0, "end")
            self.sheets_url_entry.insert(0, sheets_url)
        if hasattr(self, "enable_sheets_var"):
            self.enable_sheets_var.set(sheets_enabled)

        # Update service account email display
        sa_email = get_service_account_email(sheets_path)
        if hasattr(self, "service_email_entry"):
            self.service_email_entry.configure(state="normal")
            self.service_email_entry.delete(0, "end")
            if sa_email:
                self.service_email_entry.insert(0, sa_email)
            else:
                project_id = self._get_active_project_id()
                self.service_email_entry.insert(0, f"service-account@{project_id}.iam.gserviceaccount.com")
            self.service_email_entry.configure(state="readonly")
        if hasattr(self, "copy_email_btn"):
            self.copy_email_btn.configure(state="normal" if sa_email else "normal")

        if hasattr(self, "sheets_status_label"):
            if sheets_path and os.path.exists(sheets_path):
                self.sheets_status_label.configure(text=f"✅ Google Service Account JSON Configured ({os.path.basename(sheets_path)})", text_color="#2EA043")
            else:
                self.sheets_status_label.configure(text="⚠️ Service account .json missing (logs will save locally only)", text_color="#D29922")

    def _on_profile_switched(self, selected_profile: str):
        """Called when a user switches the profile dropdown."""
        self.config_manager.set_active_profile(selected_profile)
        self._load_fields_for_profile(selected_profile)
        if getattr(self, "on_profile_switch_callback", None):
            try:
                self.on_profile_switch_callback(selected_profile)
            except Exception:
                pass

    def _on_admin_caption_language_change(self, val: str):
        """Immediately writes selected caption language directly to settings.json when modified by admin."""
        prof = self.active_profile_var.get() if hasattr(self, "active_profile_var") else None
        clean = val.strip()
        self.config_manager.set_caption_language(clean, prof)
        if clean.lower() == "urdu" and hasattr(self, "caption_font_combo"):
            cur_font = self.caption_font_combo.get().strip()
            if cur_font in ("Arial Black", "", "Default"):
                self.caption_font_combo.set("Jameel Noori Nastaleeq")
                self.config_manager.set_caption_font("Jameel Noori Nastaleeq", prof)

    def _on_admin_caption_color_change(self, val: str):
        """Immediately writes selected caption color directly to settings.json when modified by admin."""
        prof = self.active_profile_var.get() if hasattr(self, "active_profile_var") else None
        self.config_manager.set_caption_color(val.strip(), prof)

    def _on_admin_caption_font_change(self, val: str):
        """Immediately writes selected caption font directly to settings.json when modified by admin."""
        prof = self.active_profile_var.get() if hasattr(self, "active_profile_var") else None
        clean = str(val).strip()
        self.config_manager.set_caption_font(clean, prof)

    def _browse_font_file(self):
        """Browse for custom font file (.ttf or .otf)."""
        path = filedialog.askopenfilename(
            title="Select Custom Caption Font (.ttf or .otf)",
            filetypes=[("Font Files", "*.ttf;*.otf"), ("TrueType Fonts", "*.ttf"), ("OpenType Fonts", "*.otf"), ("All Files", "*.*")]
        )
        if path:
            norm_path = os.path.normpath(path).replace("\\", "/")
            font_family = norm_path
            try:
                from PIL import ImageFont
                fnt = ImageFont.truetype(norm_path, 40)
                family_name = fnt.getname()[0]
                if family_name:
                    font_family = family_name
            except Exception:
                font_family = os.path.splitext(os.path.basename(norm_path))[0]

            current_vals = list(self.caption_font_combo.cget("values"))
            if norm_path not in current_vals:
                current_vals.insert(0, norm_path)
            self.caption_font_combo.configure(values=current_vals)
            self.caption_font_combo.set(norm_path)
            if hasattr(self, "caption_font_var"):
                self.caption_font_var.set(norm_path)
            self._on_admin_caption_font_change(norm_path)
            messagebox.showinfo("Font Selected", f"Selected font '{font_family}'\nPath: {norm_path}")

    def _add_profile_click(self):
        """Prompt to add a new channel profile."""
        dialog = ctk.CTkInputDialog(text="Enter new channel name (e.g. Crypto Insights):", title="Add Channel Profile")
        new_name = dialog.get_input()
        if new_name and new_name.strip():
            clean = new_name.strip()
            if self.config_manager.create_profile(clean):
                all_profs = self.config_manager.get_profiles_list()
                self.profile_dropdown.configure(values=all_profs)
                self.active_profile_var.set(clean)
                self._load_fields_for_profile(clean)
                messagebox.showinfo("Profile Created", f"Channel profile '{clean}' created successfully!")
            else:
                messagebox.showerror("Profile Exists", f"Profile '{clean}' already exists!")

    def _delete_profile_click(self):
        """Delete current profile."""
        current_prof = self.active_profile_var.get()
        if len(self.config_manager.get_profiles_list()) <= 1:
            messagebox.showwarning("Cannot Delete", "You cannot delete the only remaining profile!")
            return

        if messagebox.askyesno("Delete Profile", f"Are you sure you want to delete profile '{current_prof}'?\nAll settings for this channel will be removed."):
            self.config_manager.delete_profile(current_prof)
            all_profs = self.config_manager.get_profiles_list()
            new_active = self.config_manager.get_active_profile_name()
            self.profile_dropdown.configure(values=all_profs)
            self.active_profile_var.set(new_active)
            self._load_fields_for_profile(new_active)
            messagebox.showinfo("Profile Deleted", f"Profile '{current_prof}' was deleted. Switched to '{new_active}'.")

    def _browse_template_image(self):
        """Browse for custom template overlay image."""
        path = filedialog.askopenfilename(
            title="Select Custom Shorts Template Image",
            filetypes=[("Image Files", "*.png;*.jpg;*.jpeg;*.webp"), ("All Files", "*.*")]
        )
        if path:
            self.tpl_path_entry.delete(0, "end")
            self.tpl_path_entry.insert(0, path)

    def _toggle_key_visibility(self):
        show_char = "" if self.show_key_var.get() else "*"
        self.groq_key_entry.configure(show=show_char)

    def _browse_oauth_json(self):
        path = filedialog.askopenfilename(
            title="Select YouTube OAuth JSON File",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")]
        )
        if path:
            self.yt_path_entry.delete(0, "end")
            self.yt_path_entry.insert(0, path)

    def _browse_sheets_json(self):
        path = filedialog.askopenfilename(
            title="Select Google Sheets Service Account JSON File",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")]
        )
        if path:
            self.sheets_path_entry.delete(0, "end")
            self.sheets_path_entry.insert(0, path)

            # Update email display immediately
            sa_email = get_service_account_email(path)
            if hasattr(self, "service_email_entry"):
                self.service_email_entry.configure(state="normal")
                self.service_email_entry.delete(0, "end")
                if sa_email:
                    self.service_email_entry.insert(0, sa_email)
                else:
                    self.service_email_entry.insert(0, "(Could not find client_email in JSON)")
                self.service_email_entry.configure(state="readonly")
            if hasattr(self, "copy_email_btn"):
                self.copy_email_btn.configure(state="normal" if sa_email else "disabled")

            self._test_sheets_connection()

    def _get_active_project_id(self) -> str:
        """Resolves the Google Cloud project ID from the active channel's OAuth credentials."""
        cur_prof = self.active_profile_var.get() if hasattr(self, "active_profile_var") else self.config_manager.get_active_profile_name()
        oauth_path = self.config_manager.get_channel_setting("youtube_oauth_json_path", "", cur_prof)
        if oauth_path and os.path.exists(oauth_path):
            try:
                with open(oauth_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    inst = data.get("installed") or data.get("web") or {}
                    pid = inst.get("project_id", "")
                    if pid:
                        return pid
            except Exception:
                pass
        return "wealth-secret-509214"

    def _open_google_cloud_service_accounts(self):
        """Opens Google Cloud Console Service Accounts page directly for the active project."""
        project_id = self._get_active_project_id()
        console_url = f"https://console.cloud.google.com/iam-admin/serviceaccounts?project={project_id}"
        try:
            webbrowser.open(console_url)
            if hasattr(self, "sheets_status_label"):
                self.sheets_status_label.configure(
                    text=f"☁️ Opened Google Cloud Console for project '{project_id}'. Copy your Service Account email or create key.",
                    text_color="#58A6FF"
                )
        except Exception as e:
            messagebox.showerror("Error", f"Could not open browser: {e}")

    def _on_sheets_url_changed(self, event=None):
        """Auto-extracts spreadsheet ID and automatically opens editor verification page when URL is pasted."""
        if not hasattr(self, "sheets_url_entry") or not hasattr(self, "sheets_id_entry"):
            return
        url_text = self.sheets_url_entry.get().strip()
        if url_text:
            extracted_id = extract_spreadsheet_id(url_text)
            if extracted_id:
                if extracted_id != url_text:
                    self.sheets_id_entry.delete(0, "end")
                    self.sheets_id_entry.insert(0, extracted_id)

                # Auto-open editor verification page when a valid Google Sheet link is pasted
                if "docs.google.com/spreadsheets/d/" in url_text:
                    last_opened = getattr(self, "_last_opened_sheet_id", None)
                    if last_opened != extracted_id:
                        self._last_opened_sheet_id = extracted_id
                        edit_url = f"https://docs.google.com/spreadsheets/d/{extracted_id}/edit?usp=sharing"
                        try:
                            webbrowser.open(edit_url)
                            if hasattr(self, "sheets_status_label"):
                                self.sheets_status_label.configure(
                                    text="🌐 Opened Editor Verification page in browser! Make sure to grant 'Editor' access to the Service Account email.",
                                    text_color="#58A6FF"
                                )
                        except Exception:
                            pass

    def _on_sheets_id_changed(self, event=None):
        """Auto-constructs Google Sheet URL when a user enters an ID."""
        if not hasattr(self, "sheets_url_entry") or not hasattr(self, "sheets_id_entry"):
            return
        id_text = self.sheets_id_entry.get().strip()
        url_text = self.sheets_url_entry.get().strip()
        if id_text and not url_text:
            full_url = construct_spreadsheet_url(id_text)
            if full_url:
                self.sheets_url_entry.delete(0, "end")
                self.sheets_url_entry.insert(0, full_url)

    def _open_sheet_in_browser(self):
        """Opens the configured Google Sheet link in the user's default web browser."""
        url = self.sheets_url_entry.get().strip() if hasattr(self, "sheets_url_entry") else ""
        if not url:
            sheet_id = self.sheets_id_entry.get().strip() if hasattr(self, "sheets_id_entry") else ""
            if sheet_id:
                url = construct_spreadsheet_url(sheet_id)

        if url and (url.startswith("http://") or url.startswith("https://")):
            try:
                webbrowser.open(url)
            except Exception as e:
                messagebox.showerror("Error", f"Could not open browser: {e}")
        else:
            messagebox.showwarning(
                "No URL Configured",
                "Please paste a valid Google Sheet link or enter a Spreadsheet ID first."
            )

    def _copy_service_account_email(self):
        """Copies the Service Account email to the clipboard for granting Google Sheet Editor permissions."""
        if not hasattr(self, "service_email_entry"):
            return
        email = self.service_email_entry.get().strip()
        if not email or "@" not in email:
            messagebox.showwarning("No Email Found", "Please browse and select a valid Google Service Account .json file first.")
            return

        try:
            self.clipboard_clear()
            self.clipboard_append(email)
            if hasattr(self, "copy_email_btn"):
                orig_text = self.copy_email_btn.cget("text")
                self.copy_email_btn.configure(text="✅ Copied!", fg_color="#238636")
                self.after(2000, lambda: self.copy_email_btn.configure(text=orig_text, fg_color="#21262D"))
        except Exception as e:
            messagebox.showinfo("Email", f"Service Account Email:\n\n{email}")

    def _test_sheets_connection(self):
        path = self.sheets_path_entry.get().strip() if hasattr(self, "sheets_path_entry") else ""
        target = self.sheets_id_entry.get().strip() if hasattr(self, "sheets_id_entry") else ""
        sheet_url = self.sheets_url_entry.get().strip() if hasattr(self, "sheets_url_entry") else ""

        if not target and sheet_url:
            target = extract_spreadsheet_id(sheet_url)
            if target and hasattr(self, "sheets_id_entry"):
                self.sheets_id_entry.delete(0, "end")
                self.sheets_id_entry.insert(0, target)

        if not path or not os.path.exists(path):
            self.sheets_status_label.configure(text="⚠️ Service account .json file not selected or missing.", text_color="#D29922")
            messagebox.showwarning("JSON Missing", "Please select a valid Google Service Account .json file first.")
            return
        if not target:
            self.sheets_status_label.configure(text="⚠️ Please enter a Google Spreadsheet ID, URL, or Name.", text_color="#D29922")
            messagebox.showwarning("Spreadsheet Missing", "Please enter a Google Spreadsheet Link, ID, or Name to test connection.")
            return

        try:
            from core.sheets_logger import GoogleSheetsLogger
            logger = GoogleSheetsLogger(path, spreadsheet_id_or_profile=target)
            ok, msg = logger.test_connection()
            if ok:
                self.sheets_status_label.configure(text=f"✅ {msg}", text_color="#2EA043")
                messagebox.showinfo("Google Sheets Connected", f"Successfully verified Google Sheets access!\n\n{msg}")
            else:
                self.sheets_status_label.configure(text=f"❌ {msg}", text_color="#F85149")
                messagebox.showwarning("Connection Notice", f"Google Sheets connection issue:\n\n{msg}")
        except Exception as e:
            self.sheets_status_label.configure(text=f"❌ Error: {e}", text_color="#F85149")
            messagebox.showerror("Connection Error", f"Google Sheets connection error:\n{e}")

    def _global_backup_to_sheet(self):
        """Performs 1-click cloud backup of all agency profiles to Google Sheets."""
        self._save_all_settings(silent=True)
        active_prof = self.active_profile_var.get() if hasattr(self, "active_profile_var") else None
        sheets_logger = GoogleSheetsLogger(self.config_manager, active_prof)

        if not sheets_logger.is_configured():
            messagebox.showwarning(
                "Google Sheets Not Configured",
                "Cannot perform cloud backup because Google Sheets is not configured.\n\n"
                "Please configure a valid Google Service Account .json file and Google Sheet Link / ID."
            )
            return

        if hasattr(self, "backup_status_label"):
            self.backup_status_label.configure(text="⏳ Backing up agency profiles to Google Sheets...", text_color="#58A6FF")

        def run_backup():
            ok, msg = sheets_logger.backup_agency_profiles_to_sheet(self.config_manager)
            if ok:
                self.after(0, lambda: messagebox.showinfo("Cloud Backup Succeeded", msg))
                if hasattr(self, "backup_status_label"):
                    self.after(0, lambda: self.backup_status_label.configure(text=f"✅ {msg}", text_color="#2EA043"))
            else:
                self.after(0, lambda: messagebox.showerror("Cloud Backup Failed", msg))
                if hasattr(self, "backup_status_label"):
                    self.after(0, lambda: self.backup_status_label.configure(text=f"❌ {msg}", text_color="#F85149"))

        threading.Thread(target=run_backup, daemon=True).start()

    def _global_restore_from_sheet(self):
        """Performs 1-click restore of agency profiles and configurations from Google Sheets."""
        active_prof = self.active_profile_var.get() if hasattr(self, "active_profile_var") else None
        sheets_logger = GoogleSheetsLogger(self.config_manager, active_prof)

        if not sheets_logger.is_configured():
            messagebox.showwarning(
                "Google Sheets Not Configured",
                "Cannot restore because Google Sheets is not configured.\n\n"
                "Please configure a valid Google Service Account .json file and Google Sheet Link / ID."
            )
            return

        confirm = messagebox.askyesno(
            "Confirm Global Cloud Restore",
            "⚠️ Are you sure you want to restore all agency channel profiles and settings from Google Sheets?\n\n"
            "This will download the latest backup saved in your Google Sheet ('Agency_Config_Backup' tab) "
            "and restore all channels, topics, prompts, intervals, and platform configurations.\n\n"
            "(A local backup copy of your current settings.json will be saved automatically).\n\n"
            "Do you want to proceed?"
        )
        if not confirm:
            return

        if hasattr(self, "backup_status_label"):
            self.backup_status_label.configure(text="⏳ Restoring agency profiles from Google Sheets...", text_color="#58A6FF")

        def run_restore():
            ok, msg = sheets_logger.restore_agency_profiles_from_sheet(self.config_manager)
            if ok:
                def on_success():
                    # Refresh active profile and load all restored fields into UI
                    restored_active = self.config_manager.get_active_profile_name()
                    self.active_profile_var.set(restored_active)
                    self.profile_dropdown.configure(values=self.config_manager.get_profiles_list())
                    self._load_fields_for_profile(restored_active)
                    if getattr(self, "on_save_callback", None):
                        try:
                            self.on_save_callback(restored_active)
                        except Exception:
                            pass
                    if hasattr(self, "backup_status_label"):
                        self.backup_status_label.configure(text=f"✅ {msg}", text_color="#2EA043")
                    messagebox.showinfo("Cloud Restore Succeeded", f"{msg}\n\nAll channel profiles and settings have been restored successfully!")

                self.after(0, on_success)
            else:
                self.after(0, lambda: messagebox.showerror("Cloud Restore Failed", msg))
                if hasattr(self, "backup_status_label"):
                    self.after(0, lambda: self.backup_status_label.configure(text=f"❌ {msg}", text_color="#F85149"))

        threading.Thread(target=run_restore, daemon=True).start()

    def _open_precheck_dialog(self):
        active_prof = self.active_profile_var.get() if hasattr(self, "active_profile_var") else None
        SystemPrecheckModal(self, self.config_manager, profile_name=active_prof)

    def _clear_history(self):
        current_prof = self.active_profile_var.get()
        if messagebox.askyesno("Clear History", f"Reset processed history for '{current_prof}'? This clears video and clip duplication logs for this channel."):
            self.config_manager.set_channel_setting("processed_video_ids", [], current_prof)
            self.config_manager.set_channel_setting("generated_clips_history", [], current_prof)
            self.history_label.configure(text="Total Unique Videos Processed: 0 | Total Clips Recorded: 0")

    def _save_all_settings(self, silent: bool = False):
        try:
            active_prof = self.active_profile_var.get()
            self.config_manager.set_active_profile(active_prof)

            groq_key = self.groq_key_entry.get().strip()
            pool_text = self.keys_textbox.get("1.0", "end").strip()
            pool_keys = [k.strip() for k in pool_text.split("\n") if k.strip()]

            yt_path = self.yt_path_entry.get().strip()
            tpl_path = self.tpl_path_entry.get().strip()
            res_preset = self.res_dropdown.get()
            hw_profile = self.hw_dropdown.get()
            reuse_first = self.reuse_var.get()
            cleanup_temp = self.cleanup_var.get()

            # Parse width & height from dropdown directly
            width, height = self.config_manager.get_resolution_dimensions(res_preset)

            # Parse autopilot interval
            interval_str = self.interval_dropdown.get()
            interval_h = int(interval_str.split()[0]) if interval_str else 2

            # Target clips per video
            target_clips = int(self.clips_per_vid_dropdown.get())
            autopilot = self.autopilot_var.get()
            
            yt_res = self.yt_res_dropdown.get()
            captions_on = self.enable_captions_var.get()
            face_on = self.face_tracker_var.get()

            target_channel = self.target_channel_entry.get().strip()
            fetch_strat = self.fetch_type_dropdown.get()

            new_pass = self.new_pass_entry.get().strip()

            # Save shared Groq API keys (global & synced to all channels)
            self.config_manager.set_shared_groq_keys(groq_key, pool_keys)
            self.config_manager.set_channel_setting("youtube_oauth_json_path", yt_path, active_prof)
            self.config_manager.set_channel_setting("template_path", tpl_path, active_prof)
            self.config_manager.set_channel_setting("resolution_preset", res_preset, active_prof)
            self.config_manager.set_channel_setting("resolution_width", width, active_prof)
            self.config_manager.set_channel_setting("resolution_height", height, active_prof)
            self.config_manager.set_channel_setting("youtube_download_resolution", yt_res, active_prof)
            self.config_manager.set_channel_setting("enable_captions", captions_on, active_prof)
            self.config_manager.set_channel_setting("enable_face_tracking", face_on, active_prof)
            # Save Meta Graph API Credentials (Single token, two profile IDs)
            meta_token = self.meta_token_entry.get().strip() if hasattr(self, "meta_token_entry") else ""
            fb_page_id = self.fb_page_id_entry.get().strip() if hasattr(self, "fb_page_id_entry") else ""
            ig_account_id = self.ig_account_id_entry.get().strip() if hasattr(self, "ig_account_id_entry") else ""

            self.config_manager.set_channel_setting("meta_access_token", meta_token, active_prof)
            self.config_manager.set_channel_setting("facebook_access_token", meta_token, active_prof)
            self.config_manager.set_channel_setting("instagram_access_token", meta_token, active_prof)
            self.config_manager.set_channel_setting("facebook_page_id", fb_page_id, active_prof)
            self.config_manager.set_channel_setting("instagram_account_id", ig_account_id, active_prof)

            # Save Multi-Platform Upload Toggles
            upload_yt = self.upload_yt_var.get() if hasattr(self, "upload_yt_var") else True
            upload_fb = self.upload_fb_var.get() if hasattr(self, "upload_fb_var") else True
            upload_ig = self.upload_ig_var.get() if hasattr(self, "upload_ig_var") else True
            self.config_manager.set_channel_setting("upload_to_youtube", upload_yt, active_prof)
            self.config_manager.set_channel_setting("upload_to_facebook", upload_fb, active_prof)
            self.config_manager.set_channel_setting("upload_to_instagram", upload_ig, active_prof)

            # Save Google Sheets Automated Logging Settings
            sheets_path = self.sheets_path_entry.get().strip() if hasattr(self, "sheets_path_entry") else ""
            sheets_id = self.sheets_id_entry.get().strip() if hasattr(self, "sheets_id_entry") else ""
            sheets_url = self.sheets_url_entry.get().strip() if hasattr(self, "sheets_url_entry") else ""
            sheets_enabled = self.enable_sheets_var.get() if hasattr(self, "enable_sheets_var") else True

            if not sheets_id and sheets_url:
                sheets_id = extract_spreadsheet_id(sheets_url)
            if not sheets_url and sheets_id:
                sheets_url = construct_spreadsheet_url(sheets_id)

            self.config_manager.set_channel_setting("google_sheets_json_path", sheets_path, active_prof)
            self.config_manager.set_channel_setting("google_spreadsheet_id", sheets_id, active_prof)
            self.config_manager.set_channel_setting("google_sheet_url", sheets_url, active_prof)
            self.config_manager.set_channel_setting("enable_google_sheets_logging", sheets_enabled, active_prof)
            self.config_manager.set("google_sheets_json_path", sheets_path)
            self.config_manager.set("google_spreadsheet_id", sheets_id)
            self.config_manager.set("google_sheet_url", sheets_url)

            old_autopilot = self.config_manager.get_channel_setting("auto_pilot", False, active_prof)
            old_interval = self.config_manager.get_channel_setting("autopilot_interval_hours", 2, active_prof)

            self.config_manager.set_channel_setting("autopilot_interval_hours", interval_h, active_prof)
            self.config_manager.set_channel_setting("target_clips_per_video", target_clips, active_prof)
            self.config_manager.set_channel_setting("auto_pilot", autopilot, active_prof)

            now = time.time()
            interval_secs = float(interval_h) * 3600
            if autopilot:
                last_run = float(self.config_manager.get_channel_setting("last_autopilot_run", 0, active_prof) or 0)
                # When turning on or interval changed or uninitialized, start countdown from now (do not trigger immediately)
                if not old_autopilot or interval_h != old_interval or last_run == 0:
                    self.config_manager.set_channel_setting("last_autopilot_run", now, active_prof)
                    next_run = now + interval_secs
                    self.config_manager.set_channel_setting("next_autopilot_run", next_run, active_prof)
                    scheduled_str = time.strftime("%H:%M:%S", time.localtime(next_run))
                    self.logger.info(
                        f"[Auto-Pilot] Autopilot ENABLED for '{active_prof}'. Selected interval: {interval_h} hour(s). "
                        f"Next automated video scheduled in {interval_h} hour(s) at {scheduled_str}."
                    )
                else:
                    next_run = last_run + interval_secs
                    self.config_manager.set_channel_setting("next_autopilot_run", next_run, active_prof)
            else:
                self.config_manager.set_channel_setting("next_autopilot_run", 0, active_prof)
                if old_autopilot:
                    self.logger.info(f"[Auto-Pilot] Autopilot DISABLED for '{active_prof}'.")
            self.config_manager.set_channel_setting("target_channel_url", target_channel, active_prof)
            self.config_manager.set_channel_setting("fetch_strategy", fetch_strat, active_prof)
            if hasattr(self, "caption_language_combo"):
                self.config_manager.set_caption_language(self.caption_language_combo.get().strip(), active_prof)
            elif hasattr(self, "channel_language_dropdown"):
                self.config_manager.set_language(self.channel_language_dropdown.get(), active_prof)

            if hasattr(self, "caption_color_combo"):
                self.config_manager.set_caption_color(self.caption_color_combo.get().strip(), active_prof)

            if hasattr(self, "caption_font_combo"):
                self.config_manager.set_caption_font(self.caption_font_combo.get().strip(), active_prof)

            # Save global settings
            self.config_manager.set("hardware_profile", hw_profile)
            self.config_manager.set("reuse_cached_video_first", reuse_first)
            self.config_manager.set("auto_cleanup_temp_files", cleanup_temp)

            if new_pass:
                self.config_manager.update_admin_password(new_pass)

            if self.on_save_callback:
                try:
                    self.on_save_callback(active_prof)
                except Exception as cb_err:
                    pass

            if silent:
                return True

            messagebox.showinfo("Settings Saved", f"Settings for channel profile '{active_prof}' updated successfully!")
            self.destroy()
            return True

        except Exception as e:
            if not silent:
                messagebox.showerror("Save Error", f"Failed to save settings: {e}")
            return False
