import os
from typing import Optional, List, Dict, Any, Tuple
import customtkinter as ctk
from tkinter import filedialog, messagebox
from config import ConfigManager


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

    def __init__(self, parent, config_manager: ConfigManager):
        super().__init__(parent)
        self.config_manager = config_manager

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

        cancel_btn = ctk.CTkButton(
            footer,
            text="Cancel",
            width=120,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self.destroy
        )
        cancel_btn.pack(side="right", padx=(10, 0))

        save_btn = ctk.CTkButton(
            footer,
            text="💾 Save Settings",
            width=150,
            fg_color="#1F6AA5",
            hover_color="#144870",
            command=self._save_all_settings
        )
        save_btn.pack(side="right")

    def _build_api_keys_and_auth_tab(self):
        frame = ctk.CTkScrollableFrame(self.tab_api)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        # Primary Groq Key
        ctk.CTkLabel(
            frame,
            text="Default Groq API Key (Primary for this Profile)",
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
            text="Multiple Groq API Keys Pool (Failover Rotation)",
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
        self.token_status_label.pack(anchor="w", pady=(0, 10))

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
            placeholder_text="assets/wealth_secrets_template.png",
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
        groq_key = self.config_manager.get_channel_setting("groq_api_key", "", profile_name)
        self.groq_key_entry.delete(0, "end")
        self.groq_key_entry.insert(0, groq_key)

        pool_keys = self.config_manager.get_channel_setting("groq_api_keys_pool", [], profile_name)
        self.keys_textbox.delete("1.0", "end")
        self.keys_textbox.insert("1.0", "\n".join(pool_keys))

        yt_path = self.config_manager.get_channel_setting("youtube_oauth_json_path", "", profile_name)
        self.yt_path_entry.delete(0, "end")
        self.yt_path_entry.insert(0, yt_path)

        tpl_path = self.config_manager.get_channel_setting("template_path", "assets/wealth_secrets_template.png", profile_name)
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

    def _on_profile_switched(self, selected_profile: str):
        """Called when a user switches the profile dropdown."""
        self.config_manager.set_active_profile(selected_profile)
        self._load_fields_for_profile(selected_profile)

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

    def _clear_history(self):
        current_prof = self.active_profile_var.get()
        if messagebox.askyesno("Clear History", f"Reset processed history for '{current_prof}'? This clears video and clip duplication logs for this channel."):
            self.config_manager.set_channel_setting("processed_video_ids", [], current_prof)
            self.config_manager.set_channel_setting("generated_clips_history", [], current_prof)
            self.history_label.configure(text="Total Unique Videos Processed: 0 | Total Clips Recorded: 0")

    def _save_all_settings(self):
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

            # Save channel-specific settings
            self.config_manager.set_channel_setting("groq_api_key", groq_key, active_prof)
            self.config_manager.set_channel_setting("groq_api_keys_pool", pool_keys, active_prof)
            self.config_manager.set_channel_setting("youtube_oauth_json_path", yt_path, active_prof)
            self.config_manager.set_channel_setting("template_path", tpl_path, active_prof)
            self.config_manager.set_channel_setting("resolution_preset", res_preset, active_prof)
            self.config_manager.set_channel_setting("resolution_width", width, active_prof)
            self.config_manager.set_channel_setting("resolution_height", height, active_prof)
            self.config_manager.set_channel_setting("youtube_download_resolution", yt_res, active_prof)
            self.config_manager.set_channel_setting("enable_captions", captions_on, active_prof)
            self.config_manager.set_channel_setting("enable_face_tracking", face_on, active_prof)
            self.config_manager.set_channel_setting("autopilot_interval_hours", interval_h, active_prof)
            self.config_manager.set_channel_setting("target_clips_per_video", target_clips, active_prof)
            self.config_manager.set_channel_setting("auto_pilot", autopilot, active_prof)
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

            messagebox.showinfo("Settings Saved", f"Settings for channel profile '{active_prof}' updated successfully!")
            self.destroy()

        except Exception as e:
            messagebox.showerror("Save Error", f"Failed to save settings: {e}")
