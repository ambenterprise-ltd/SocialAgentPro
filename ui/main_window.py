import os
import glob
import time
import queue
import logging
import threading
import subprocess
from typing import Optional, List, Dict, Any, Callable, Tuple
import customtkinter as ctk
from tkinter import messagebox

from config import ConfigManager
from ui.admin_modal import AdminPasswordDialog, AdminSettingsModal
from core.pipeline import ShortsAutomationPipeline
from core.publisher import MultiPlatformPublisher, YouTubePublisher

# Configure CustomTkinter dark theme defaults
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class TextHandler(logging.Handler):
    """
    Custom Logging Handler that directs log records safely into a Thread Queue.
    This prevents cross-thread UI access violations in Tkinter.
    """

    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        msg = self.format(record)
        self.log_queue.put(("LOG", record.levelname, msg))


class MainWindow(ctk.CTk):
    """
    Main CustomTkinter GUI Dashboard for AMB Enterprise.
    Features a clean, streamlined dashboard, 1-short-per-generation pipeline,
    manual upload with 1-hour deletion, live progress, and periodic autopilot.
    """

    def __init__(self, config_manager: ConfigManager):
        super().__init__()
        self.config_manager = config_manager
        self.log_queue = queue.Queue()
        self.worker_thread = None
        self.manual_upload_thread = None
        self.stop_requested = False
        self.last_autopilot_run = time.time()
        self.last_purge_check = 0

        # Configure Window
        self.title("AMB Enterprise - YouTube Content Agent")
        self.geometry("980x780")
        self.minsize(860, 660)
        self.configure(fg_color="#0B0C0E")

        # Load Brand Logo
        self.logo_image = None
        self._load_brand_logo()

        # Setup Logging Interceptor
        self._setup_logging()

        # Build UI layout
        self._build_ui()

        # Start periodic log queue consumer and maintenance on UI mainloop
        self.after(100, self._process_log_queue)

    def _load_brand_logo(self):
        """Loads the AMB Enterprise brand logo image for the header and window icon."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logo_path = os.path.join(base_dir, "assets", "amb_logo.png")
        if os.path.exists(logo_path):
            try:
                from PIL import Image, ImageTk
                pil_logo = Image.open(logo_path)
                self.logo_image = ctk.CTkImage(light_image=pil_logo, dark_image=pil_logo, size=(55, 32))
                try:
                    icon_photo = ImageTk.PhotoImage(pil_logo)
                    self.wm_iconphoto(True, icon_photo)
                except Exception:
                    pass
            except Exception as e:
                self.logger.debug(f"[MainWindow] Could not load brand logo: {e}")

    def _setup_logging(self):
        """Sets up application logger to pipe into thread queue."""
        self.logger = logging.getLogger("AMBEnterprise")
        self.logger.setLevel(logging.INFO)
        text_handler = TextHandler(self.log_queue)
        formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
        text_handler.setFormatter(formatter)
        self.logger.addHandler(text_handler)

    def _build_ui(self):
        # Header Bar
        header_frame = ctk.CTkFrame(self, corner_radius=0, height=60, fg_color="#1B1E23", border_color="#2C353D", border_width=1)
        header_frame.pack(fill="x", side="top")

        if self.logo_image:
            logo_label = ctk.CTkLabel(header_frame, text="", image=self.logo_image)
            logo_label.pack(side="left", padx=(20, 10), pady=14)
            title_text = "AMB ENTERPRISE"
            title_padx = (0, 20)
        else:
            title_text = "⚡ AMB ENTERPRISE"
            title_padx = (20, 20)

        title_label = ctk.CTkLabel(
            header_frame,
            text=title_text,
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#FFFFFF"
        )
        title_label.pack(side="left", padx=title_padx, pady=15)

        subtitle_label = ctk.CTkLabel(
            header_frame,
            text="Multi-Channel Shorts Engine",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8B949E"
        )
        subtitle_label.pack(side="left", padx=(0, 20), pady=15)

        self.admin_btn = ctk.CTkButton(
            header_frame,
            text="⚙️ Admin Settings",
            width=140,
            corner_radius=4,
            fg_color="#2C353D",
            hover_color="#1E252B",
            text_color="#FFFFFF",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self._on_admin_click
        )
        self.admin_btn.pack(side="right", padx=(10, 20), pady=15)

        self.status_badge = ctk.CTkLabel(
            header_frame,
            text=" READY ",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color="#00A8B5",
            corner_radius=4,
            text_color="#FFFFFF"
        )
        self.status_badge.pack(side="right", padx=(0, 10), pady=15)

        # Profile Selector directly on Main Dashboard
        profile_selector_frame = ctk.CTkFrame(header_frame, fg_color="transparent")
        profile_selector_frame.pack(side="right", padx=(10, 15), pady=12)

        ctk.CTkLabel(
            profile_selector_frame,
            text="Channel Profile:",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#FFFFFF"
        ).pack(side="left", padx=(0, 6))

        self.dashboard_profile_var = ctk.StringVar(value=self.config_manager.get_active_profile_name())
        self.dashboard_profile_menu = ctk.CTkOptionMenu(
            profile_selector_frame,
            variable=self.dashboard_profile_var,
            values=self.config_manager.get_profiles_list(),
            command=self._on_dashboard_profile_change,
            width=180,
            height=32,
            fg_color="#1F6AA5",
            button_color="#144870",
            button_hover_color="#0F3856",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
        )
        self.dashboard_profile_menu.pack(side="left")

        # Status & State Summary Bar (Integrated Telemetry Banner)
        summary_frame = ctk.CTkFrame(
            self,
            fg_color="#1B1E23",
            border_color="#2C353D",
            border_width=1,
            corner_radius=4
        )
        summary_frame.pack(fill="x", padx=20, pady=(15, 8))

        self.state_info_label = ctk.CTkLabel(
            summary_frame,
            text="⚡ Mode: 1 Short / Run | Smart Resume: Ready | Auto-Pilot: Checking...",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#00A8B5"
        )
        self.state_info_label.pack(side="left", padx=15, pady=8)

        self._refresh_state_info()

        # Main Input Control Panel (Card Elevation)
        input_frame = ctk.CTkFrame(
            self,
            fg_color="#1B1E23",
            border_width=1,
            border_color="#2C353D",
            corner_radius=4
        )
        input_frame.pack(fill="x", padx=20, pady=10)

        active_prof = self.config_manager.get_active_profile_name()
        initial_ctx = self.config_manager.get_channel_context(active_prof)
        content_type = initial_ctx.get("content_type", "podcast")

        self.url_label = ctk.CTkLabel(
            input_frame,
            text=f"{content_type.title()} YouTube URL (or leave blank to Auto-Discover fresh {content_type}):",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#FFFFFF"
        )
        self.url_label.pack(anchor="w", padx=15, pady=(15, 6))

        entry_box = ctk.CTkFrame(input_frame, fg_color="transparent")
        entry_box.pack(fill="x", padx=15, pady=(0, 12))

        self.url_entry = ctk.CTkEntry(
            entry_box,
            placeholder_text="https://www.youtube.com/watch?v=... (Leave empty to auto-fetch new video)",
            width=680,
            height=34,
            corner_radius=4,
            border_width=1,
            border_color="#2C353D",
            fg_color="#1B1E23",
            text_color="#FFFFFF",
            font=ctk.CTkFont(family="Segoe UI", size=12)
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.autofetch_btn = ctk.CTkButton(
            entry_box,
            text="🎲 Auto-Fetch Video",
            width=160,
            height=34,
            corner_radius=4,
            fg_color="#2C353D",
            hover_color="#1E252B",
            text_color="#FFFFFF",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            command=self._autofetch_click
        )
        self.autofetch_btn.pack(side="left")

        # Topic Focus row
        topic_frame = ctk.CTkFrame(input_frame, fg_color="transparent")
        topic_frame.pack(fill="x", padx=15, pady=(0, 12))

        ctk.CTkLabel(
            topic_frame,
            text="Topic Focus:",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#FFFFFF"
        ).pack(side="left", padx=(0, 10))
        self.topic_entry = ctk.CTkEntry(
            topic_frame,
            width=420,
            height=34,
            corner_radius=4,
            border_width=1,
            border_color="#2C353D",
            fg_color="#1B1E23",
            text_color="#FFFFFF",
            font=ctk.CTkFont(family="Segoe UI", size=12)
        )
        self.topic_entry.insert(0, self.config_manager.get("topic_focus", "wealth, business secrets, and money concepts"))
        self.topic_entry.pack(side="left", fill="x", expand=True)

        # Caption Settings row: Caption Language, Caption Color & Caption Font (CTkComboBox)
        caption_settings_frame = ctk.CTkFrame(input_frame, fg_color="transparent")
        caption_settings_frame.pack(fill="x", padx=15, pady=(0, 15))

        ctk.CTkLabel(
            caption_settings_frame,
            text="🌐 Language:",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#FFFFFF"
        ).pack(side="left", padx=(0, 6))
        self.caption_languages = [
            "English", "Urdu", "Spanish", "French", "German",
            "Hindi", "Arabic", "Chinese", "Japanese", "Portuguese", "Russian"
        ]
        self.caption_language_var = ctk.StringVar(value=self.config_manager.get_caption_language())
        self.caption_language_combo = ctk.CTkComboBox(
            caption_settings_frame,
            values=self.caption_languages,
            variable=self.caption_language_var,
            width=125,
            height=34,
            corner_radius=4,
            border_width=1,
            border_color="#2C353D",
            fg_color="#1B1E23",
            text_color="#FFFFFF",
            button_color="#2C353D",
            button_hover_color="#1E252B",
            dropdown_fg_color="#1B1E23",
            dropdown_hover_color="#2C353D",
            dropdown_text_color="#FFFFFF",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            dropdown_font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._on_caption_language_change,
            state="readonly"
        )
        self.caption_language_combo.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(
            caption_settings_frame,
            text="🎨 Color:",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#FFFFFF"
        ).pack(side="left", padx=(0, 6))
        self.caption_colors = ["White", "Yellow", "Neon Green"]
        self.caption_color_var = ctk.StringVar(value=self.config_manager.get_caption_color())
        self.caption_color_combo = ctk.CTkComboBox(
            caption_settings_frame,
            values=self.caption_colors,
            variable=self.caption_color_var,
            width=120,
            height=34,
            corner_radius=4,
            border_width=1,
            border_color="#2C353D",
            fg_color="#1B1E23",
            text_color="#FFFFFF",
            button_color="#2C353D",
            button_hover_color="#1E252B",
            dropdown_fg_color="#1B1E23",
            dropdown_hover_color="#2C353D",
            dropdown_text_color="#FFFFFF",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            dropdown_font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._on_caption_color_change,
            state="readonly"
        )
        self.caption_color_combo.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(
            caption_settings_frame,
            text="🔤 Font:",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#FFFFFF"
        ).pack(side="left", padx=(0, 6))
        all_fonts = self.config_manager.get_supported_caption_fonts()
        cur_font = self.config_manager.get_caption_font()
        if cur_font not in all_fonts:
            all_fonts = all_fonts + [cur_font]
        self.caption_font_var = ctk.StringVar(value=cur_font)
        self.caption_font_combo = ctk.CTkComboBox(
            caption_settings_frame,
            values=all_fonts,
            variable=self.caption_font_var,
            width=180,
            height=34,
            corner_radius=4,
            border_width=1,
            border_color="#2C353D",
            fg_color="#1B1E23",
            text_color="#FFFFFF",
            button_color="#2C353D",
            button_hover_color="#1E252B",
            dropdown_fg_color="#1B1E23",
            dropdown_hover_color="#2C353D",
            dropdown_text_color="#FFFFFF",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            dropdown_font=ctk.CTkFont(family="Segoe UI", size=12),
            command=self._on_caption_font_change
        )
        self.caption_font_combo.pack(side="left")

        # Compatibility aliases
        self.language_var = self.caption_language_var
        self.language_menu = self.caption_language_combo

        # Action Buttons Frame
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 10))

        self.start_new_btn = ctk.CTkButton(
            btn_frame,
            text="🚀 Generate from NEW Video",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            fg_color="#00A8B5",
            hover_color="#008C99",
            text_color="#FFFFFF",
            height=42,
            corner_radius=4,
            command=self._start_processing_new_video
        )
        self.start_new_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.start_btn = ctk.CTkButton(
            btn_frame,
            text="🔁 Generate from CURRENT Video",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            fg_color="#D4C5B0",
            hover_color="#BBAA94",
            text_color="#0B0C0E",
            height=42,
            corner_radius=4,
            command=self._start_processing
        )
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            btn_frame,
            text="🛑 Stop",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            fg_color="#8B2525",
            hover_color="#6E1D1D",
            text_color="#FFFFFF",
            height=42,
            width=90,
            corner_radius=4,
            state="disabled",
            command=self._stop_processing
        )
        self.stop_btn.pack(side="left", padx=(0, 8))

        # Manual Upload Button
        self.manual_upload_btn = ctk.CTkButton(
            btn_frame,
            text="📤 Manual Upload Latest Short",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            fg_color="#2C353D",
            hover_color="#1E252B",
            text_color="#FFFFFF",
            height=42,
            corner_radius=4,
            command=self._on_manual_upload_click
        )
        self.manual_upload_btn.pack(side="left", padx=(0, 8))

        self.open_output_btn = ctk.CTkButton(
            btn_frame,
            text="📁 Open Channel Folder",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            fg_color="#2C353D",
            hover_color="#1E252B",
            text_color="#FFFFFF",
            height=42,
            corner_radius=4,
            command=self._open_output_directory
        )
        self.open_output_btn.pack(side="left")

        # Download Progress Bar Frame
        progress_frame = ctk.CTkFrame(
            self,
            fg_color="#1B1E23",
            border_width=1,
            border_color="#2C353D",
            corner_radius=4
        )
        progress_frame.pack(fill="x", padx=20, pady=(0, 10))

        self.progress_label = ctk.CTkLabel(
            progress_frame,
            text="📥 Download Progress: 0%",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#FFFFFF"
        )
        self.progress_label.pack(anchor="w", padx=15, pady=(8, 2))

        self.progress_bar = ctk.CTkProgressBar(
            progress_frame,
            height=12,
            corner_radius=4,
            progress_color="#00A8B5",
            fg_color="#0B0C0E"
        )
        self.progress_bar.set(0.0)
        self.progress_bar.pack(fill="x", padx=15, pady=(0, 10))

        # Execution Log Console
        console_frame = ctk.CTkFrame(
            self,
            fg_color="#1B1E23",
            border_width=1,
            border_color="#2C353D",
            corner_radius=4
        )
        console_frame.pack(fill="both", expand=True, padx=20, pady=(0, 15))

        console_label = ctk.CTkLabel(
            console_frame,
            text="📊 Live Activity Log",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#FFFFFF"
        )
        console_label.pack(anchor="w", padx=15, pady=(10, 4))

        self.console_textbox = ctk.CTkTextbox(
            console_frame,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color="#000000",
            text_color="#39FF14",
            corner_radius=4,
            border_width=1,
            border_color="#2C353D",
            wrap="none"
        )
        self.console_textbox.tag_config("default", foreground="#39FF14")
        self.console_textbox.tag_config("INFO", foreground="#39FF14")
        self.console_textbox.tag_config("WARNING", foreground="#FFB300")
        self.console_textbox.tag_config("ERROR", foreground="#FF4444")
        self.console_textbox.tag_config("DEBUG", foreground="#76FF03")
        self.console_textbox.pack(fill="both", expand=True, padx=15, pady=(0, 12))

        self.logger.info("AMB Enterprise initialized. Multi-Channel profiles ready with template viewport compositing.")

    def refresh_dashboard_ui(self, channel_name: Optional[str] = None):
        """
        Dynamically refreshes the main dashboard UI fields to match the active channel context.
        Automatically clears and overwrites the 'Topic Focus' text input box with the niche-specific string
        from the context dictionary, updates language/fonts, and refreshes the live telemetry banner.
        """
        active_prof = channel_name or self.config_manager.get_active_profile_name()
        self.config_manager.set_active_profile(active_prof)

        # Retrieve context dictionary mapping for the active profile
        ctx = self.config_manager.get_channel_context(active_prof)
        target_topic = ctx.get("topic_focus", "wealth, business secrets, and money concepts")

        # Automatically clear and overwrite the 'Topic Focus' text input box
        self.topic_entry.delete(0, "end")
        self.topic_entry.insert(0, target_topic)

        # Update language/color/font dropdowns to match profile configuration
        if hasattr(self, "language_var"):
            self.language_var.set(self.config_manager.get_language(active_prof))
        if hasattr(self, "caption_language_var"):
            self.caption_language_var.set(self.config_manager.get_caption_language(active_prof))
        if hasattr(self, "caption_language_combo"):
            self.caption_language_combo.set(self.config_manager.get_caption_language(active_prof))

        # Update dashboard profile dropdown selector
        if hasattr(self, "dashboard_profile_var"):
            self.dashboard_profile_var.set(active_prof)
        if hasattr(self, "dashboard_profile_menu"):
            self.dashboard_profile_menu.configure(values=self.config_manager.get_profiles_list())
            self.dashboard_profile_menu.set(active_prof)

        # Dynamically update URL input label to match channel's content_type
        content_type = ctx.get("content_type", "podcast")
        if hasattr(self, "url_label"):
            self.url_label.configure(
                text=f"{content_type.title()} YouTube URL (or leave blank to Auto-Discover fresh {content_type}):"
            )

        # Refresh telemetry strip
        self._refresh_state_info()

        niche = ctx.get("niche_name", "Content Production")
        self.logger.info(
            f"[Dashboard] Dynamic Context Switched to '{active_prof}' ({niche}). "
            f"Topic Focus set to: '{target_topic}'"
        )

    def _on_dashboard_profile_change(self, selected_profile: str):
        """Switches active profile and dynamically updates dashboard UI."""
        self.refresh_dashboard_ui(selected_profile)

    def _on_caption_language_change(self, selected_lang: str):
        """Updates caption language setting directly to settings.json when modified by the admin."""
        self.config_manager.set_caption_language(selected_lang)
        self.logger.info(f"[Language] Caption Language set to '{selected_lang}' for profile '{self.config_manager.get_active_profile_name()}'.")
        # Auto-suggest native Nastaleeq font if Urdu is chosen
        if selected_lang.strip().lower() == "urdu":
            cur_font = self.config_manager.get_caption_font()
            if not cur_font or cur_font == "Arial Black":
                self.config_manager.set_caption_font("Jameel Noori Nastaleeq")
                if hasattr(self, "caption_font_var"):
                    self.caption_font_var.set("Jameel Noori Nastaleeq")
                if hasattr(self, "caption_font_combo"):
                    self.caption_font_combo.set("Jameel Noori Nastaleeq")
                self.logger.info("[Language] Auto-selected 'Jameel Noori Nastaleeq' font for Urdu.")

    def _on_caption_color_change(self, selected_color: str):
        """Updates caption color setting directly to settings.json when modified by the admin."""
        self.config_manager.set_caption_color(selected_color)
        self.logger.info(f"[Caption] Caption Color set to '{selected_color}' for profile '{self.config_manager.get_active_profile_name()}'.")

    def _on_caption_font_change(self, selected_font: str):
        """Updates caption font setting directly to settings.json when modified by the admin."""
        self.config_manager.set_caption_font(selected_font)
        self.logger.info(f"[Caption] Caption Font set to '{selected_font}' for profile '{self.config_manager.get_active_profile_name()}'.")

    def _on_language_change(self, selected_lang: str):
        """Updates language setting when changed in dropdown."""
        self._on_caption_language_change(selected_lang)

    def _refresh_state_info(self):
        """Updates the status summary strip on the dashboard."""
        current_prof = self.config_manager.get_active_profile_name()
        current_lang = self.config_manager.get_caption_language(current_prof)
        current_color = self.config_manager.get_caption_color(current_prof)
        current_font = self.config_manager.get_caption_font(current_prof)
        if hasattr(self, "caption_language_var"):
            self.caption_language_var.set(current_lang)
        if hasattr(self, "caption_language_combo"):
            self.caption_language_combo.set(current_lang)
        if hasattr(self, "language_var"):
            self.language_var.set(current_lang)
        if hasattr(self, "caption_color_var"):
            self.caption_color_var.set(current_color)
        if hasattr(self, "caption_color_combo"):
            self.caption_color_combo.set(current_color)
        if hasattr(self, "caption_font_var"):
            self.caption_font_var.set(current_font)
        if hasattr(self, "caption_font_combo"):
            all_fonts = self.config_manager.get_supported_caption_fonts()
            if current_font not in all_fonts:
                all_fonts = all_fonts + [current_font]
            self.caption_font_combo.configure(values=all_fonts)
            self.caption_font_combo.set(current_font)
        active_state = self.config_manager.get_channel_setting("active_video_state", {}, current_prof)
        active_vid = active_state.get("video_id", "")
        completed = active_state.get("completed_clip_count", 0)
        target = active_state.get("target_clip_count", 3)

        autopilot_on = self.config_manager.get_channel_setting("auto_pilot", False, current_prof)
        interval_h = self.config_manager.get_channel_setting("autopilot_interval_hours", 2, current_prof)

        if active_vid:
            video_txt = f"Video: {active_vid} (Clip {completed + 1} of {target})"
        else:
            video_txt = "Next Run: Fresh Discovery"

        now = time.time()
        if autopilot_on:
            next_run = float(self.config_manager.get_channel_setting("next_autopilot_run", 0, current_prof) or 0)
            if next_run <= 0:
                interval_secs = float(interval_h) * 3600
                last_run = float(self.config_manager.get_channel_setting("last_autopilot_run", 0, current_prof) or 0)
                if last_run > 0:
                    next_run = last_run + interval_secs
                else:
                    next_run = now + interval_secs
                self.config_manager.set_channel_setting("next_autopilot_run", next_run, current_prof)

            remaining_sec = max(0, int(next_run - now))
            hrs = remaining_sec // 3600
            mins = (remaining_sec % 3600) // 60
            secs = remaining_sec % 60
            if hrs > 0:
                countdown_str = f"{hrs}h {mins:02d}m {secs:02d}s"
            else:
                countdown_str = f"{mins}m {secs:02d}s"
            ap_txt = f"⏱️ Next Video in: {countdown_str} (Auto-Pilot: {interval_h}h)"
        else:
            ap_txt = "Auto-Pilot: Off"
        
        import threading
        
        def update_hw_label():
            hw_settings = self.config_manager.get_hardware_settings()
            hw_summary = hw_settings.get("summary", "")
            self._cached_hw_summary = hw_summary
            try:
                self.after(0, lambda: self._apply_hw_summary(hw_summary, video_txt, ap_txt, current_prof))
            except Exception:
                pass
            
        threading.Thread(target=update_hw_label, daemon=True).start()
        
    def _apply_hw_summary(self, hw_summary, video_txt, ap_txt, current_prof):
        self._cached_hw_summary = hw_summary
        self.state_info_label.configure(text=f"📺 [{current_prof}] | {video_txt} | {ap_txt} | ⚡ {hw_summary}")

    def _refresh_countdown_display(self):
        """Live updates remaining autopilot countdown and channel name on dashboard banner every second."""
        current_prof = self.config_manager.get_active_profile_name()
        autopilot_on = self.config_manager.get_channel_setting("auto_pilot", False, current_prof)
        interval_h = self.config_manager.get_channel_setting("autopilot_interval_hours", 2, current_prof)
        now = time.time()

        active_state = self.config_manager.get_channel_setting("active_video_state", {}, current_prof)
        active_vid = active_state.get("video_id", "")
        completed = active_state.get("completed_clip_count", 0)
        target = active_state.get("target_clip_count", 3)
        video_txt = f"Video: {active_vid} (Clip {completed + 1} of {target})" if active_vid else "Next Run: Fresh Discovery"

        if autopilot_on:
            next_run = float(self.config_manager.get_channel_setting("next_autopilot_run", 0, current_prof) or 0)
            if next_run > 0:
                remaining_sec = max(0, int(next_run - now))
                hrs = remaining_sec // 3600
                mins = (remaining_sec % 3600) // 60
                secs = remaining_sec % 60
                if hrs > 0:
                    countdown_str = f"{hrs}h {mins:02d}m {secs:02d}s"
                else:
                    countdown_str = f"{mins}m {secs:02d}s"
                ap_txt = f"⏱️ Next Video in: {countdown_str} (Auto-Pilot: {interval_h}h)"
            else:
                ap_txt = f"Auto-Pilot: Active ({interval_h}h)"
        else:
            ap_txt = "Auto-Pilot: Off"

        hw_summary = getattr(self, "_cached_hw_summary", "")
        self.state_info_label.configure(text=f"📺 [{current_prof}] | {video_txt} | {ap_txt} | ⚡ {hw_summary}")

    def _autofetch_click(self):
        """Clears URL field to trigger automatic YouTube discovery based on active channel."""
        self.url_entry.delete(0, "end")
        active_prof = self.config_manager.get_active_profile_name()
        ctx = self.config_manager.get_channel_context(active_prof)
        content_type = ctx.get("content_type", "video")
        self.logger.info(f"URL cleared. Auto-discovery will find a fresh {content_type} on next run.")

    def _on_admin_click(self):
        """Triggers password dialog before opening Admin Settings."""
        def open_admin():
            AdminSettingsModal(
                self,
                self.config_manager,
                on_save_callback=self.refresh_dashboard_ui,
                on_profile_switch_callback=self.refresh_dashboard_ui
            )
            self._refresh_state_info()

        AdminPasswordDialog(self, self.config_manager, on_success_callback=open_admin)

    def _process_log_queue(self):
        """
        Consumes messages from log_queue and writes them to CTkTextbox / updates progress bar.
        Also runs background 1-hour expiration purges and multi-channel background autopilot.
        """
        while not self.log_queue.empty():
            try:
                item = self.log_queue.get_nowait()
                if item[0] == "PROGRESS":
                    pct = item[1]
                    self.progress_bar.set(pct / 100.0)
                    self.progress_label.configure(text=f"📥 Download Progress: {pct:.1f}%")
                elif item[0] == "LOG":
                    level, msg = item[1], item[2]
                    tag = level if level in ("INFO", "WARNING", "ERROR", "DEBUG") else "default"
                    self.console_textbox.insert("end", msg + "\n", tag)
                    self.console_textbox.see("end")
            except queue.Empty:
                break

        now = time.time()
        # Periodic 1-hour deletion check every 15 seconds
        if now - self.last_purge_check >= 15:
            self.last_purge_check = now
            purged = self.config_manager.check_and_purge_expired_files()
            if purged:
                self.logger.info(f"[Auto-Cleanup] Purged {len(purged)} file(s) after 1-hour expiration window.")
            self._refresh_state_info()

        # Update live countdown display every second
        if now - getattr(self, "_last_countdown_tick", 0) >= 1.0:
            self._last_countdown_tick = now
            self._refresh_countdown_display()

        # Multi-Channel Background Autopilot ("working on multiple channels in backtime")
        if not (self.worker_thread and self.worker_thread.is_alive()):
            for prof_name in self.config_manager.get_profiles_list():
                if self.config_manager.get_channel_setting("auto_pilot", False, prof_name):
                    interval_secs = float(self.config_manager.get_channel_setting("autopilot_interval_hours", 2, prof_name)) * 3600
                    next_run = float(self.config_manager.get_channel_setting("next_autopilot_run", 0, prof_name) or 0)

                    # Initialize next_run safely if uninitialized (NEVER trigger instantly)
                    if next_run <= 0:
                        next_run = now + interval_secs
                        self.config_manager.set_channel_setting("last_autopilot_run", now, prof_name)
                        self.config_manager.set_channel_setting("next_autopilot_run", next_run, prof_name)
                        continue

                    if now >= next_run:
                        self.logger.info(
                            f"[Auto-Pilot] Channel '{prof_name}' interval reached ({self.config_manager.get_channel_setting('autopilot_interval_hours', 2, prof_name)}h). "
                            f"Triggering scheduled 1-short background generation..."
                        )
                        self.config_manager.set_channel_setting("last_autopilot_run", now, prof_name)
                        self.config_manager.set_channel_setting("next_autopilot_run", now + interval_secs, prof_name)
                        self.config_manager.save_config()
                        self._start_processing(target_profile=prof_name)
                        break

        self.after(100, self._process_log_queue)

    def _start_processing_new_video(self):
        """Resets active video state, wipes cache, and then starts the pipeline for fresh discovery."""
        current_prof = self.config_manager.get_active_profile_name()
        
        # Purge the actual disk cache so pipeline doesn't reuse the old video
        dirs = self.config_manager.get_channel_output_dirs(current_prof)
        source_dir = dirs["source_videos"]
        import shutil
        if os.path.exists(source_dir):
            for item in os.listdir(source_dir):
                item_path = os.path.join(source_dir, item)
                if os.path.isdir(item_path):
                    try:
                        shutil.rmtree(item_path, ignore_errors=True)
                    except:
                        pass
        
        self.config_manager.reset_active_video_state(current_prof)
        self._refresh_state_info()
        self._start_processing()

    def _start_processing(self, target_profile: Optional[str] = None):
        """Initiates 1 short pipeline run in a background daemon thread."""
        if self.worker_thread and self.worker_thread.is_alive():
            self.logger.warning("Pipeline is already executing.")
            return

        active_prof = target_profile or self.config_manager.get_active_profile_name()
        url = self.url_entry.get().strip()

        self.stop_requested = False
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.manual_upload_btn.configure(state="disabled")
        self.admin_btn.configure(state="disabled")
        self.autofetch_btn.configure(state="disabled")
        self.status_badge.configure(text=" PROCESSING ", fg_color="#D4C5B0", text_color="#0B0C0E")

        self.progress_bar.set(0.0)
        self.progress_label.configure(text="📥 Download Progress: 0%")

        topic = self.topic_entry.get().strip() or self.config_manager.get_channel_setting("topic_focus", "", active_prof)
        lang = self.language_var.get().strip() if hasattr(self, "language_var") else self.config_manager.get_language(active_prof)
        self.logger.info(f"Initiating 1 Short Generation cycle for Channel Profile: '{active_prof}' (Language: '{lang}')...")

        # Spawn background processing thread
        self.worker_thread = threading.Thread(
            target=self._run_pipeline_worker,
            args=(url, topic, active_prof, lang),
            daemon=True
        )
        self.worker_thread.start()

    def _stop_processing(self):
        """Requests cancellation of worker thread."""
        if self.worker_thread and self.worker_thread.is_alive():
            self.stop_requested = True
            self.logger.warning("Stop request sent. Halting workflow...")
            self.stop_btn.configure(state="disabled")

    def _run_pipeline_worker(self, url: str, topic: str, profile_name: str, language: str = "English"):
        """
        Background worker procedure. Runs ShortsAutomationPipeline for the given profile and language.
        """
        try:
            def _progress_cb(percentage: float, msg: str):
                self.log_queue.put(("PROGRESS", percentage))

            pipeline = ShortsAutomationPipeline(self.config_manager, logger=self.logger)
            results = pipeline.run(
                url=url,
                topic_focus=topic,
                channel_profile=profile_name,
                language=language,
                stop_checker=lambda: self.stop_requested,
                progress_callback=_progress_cb
            )

            if results:
                self.logger.info(f"[Worker] Single Short generated successfully for '{profile_name}': {results[0].get('rendered_mp4_path')}")
                self._reset_ui_state(success=True)
            else:
                self._reset_ui_state(success=False)

        except Exception as e:
            self.logger.error(f"[Worker] Pipeline execution failed: {e}")
            self._reset_ui_state(success=False)

    def _on_manual_upload_click(self):
        """
        Handles Manual Upload of the latest generated video short across all enabled social platforms.
        """
        if self.manual_upload_thread and self.manual_upload_thread.is_alive():
            self.logger.warning("Manual upload is already in progress.")
            return

        current_prof = self.config_manager.get_active_profile_name()
        yt_oauth = self.config_manager.get_channel_setting("youtube_oauth_json_path", "", current_prof).strip()
        meta_tok = (
            self.config_manager.get_channel_setting("meta_access_token", "", current_prof) or
            self.config_manager.get_channel_setting("facebook_access_token", "", current_prof)
        ).strip()
        fb_page_id = self.config_manager.get_channel_setting("facebook_page_id", "", current_prof).strip()
        ig_account_id = self.config_manager.get_channel_setting("instagram_account_id", "", current_prof).strip()

        has_yt = bool(yt_oauth and os.path.exists(yt_oauth))
        has_fb = bool(meta_tok and fb_page_id)
        has_ig = bool(meta_tok and ig_account_id)

        if not (has_yt or has_fb or has_ig):
            messagebox.showwarning(
                "Upload Credentials Required",
                f"No upload credentials configured for channel '{current_prof}'!\n\n"
                "Please open Admin Settings -> API Keys & Auth to configure your YouTube OAuth JSON "
                "or Meta Access Token with Facebook Page ID / Instagram Account ID."
            )
            return

        self.manual_upload_btn.configure(state="disabled")
        self.logger.info(f"[Manual Upload] Locating latest rendered video short for channel '{current_prof}'...")

        self.manual_upload_thread = threading.Thread(
            target=self._run_manual_upload_worker,
            args=(current_prof,),
            daemon=True
        )
        self.manual_upload_thread.start()

    def _run_manual_upload_worker(self, profile_name: str):
        """Worker thread for manual multi-platform upload and 1-hour deletion scheduling for a profile."""
        try:
            dirs = self.config_manager.get_channel_output_dirs(profile_name)
            shorts_dir = dirs["shorts_clips"]

            target_mp4 = None
            clip_title = f"{profile_name} Short"
            clip_hook = ""
            clip_rationale = ""
            clip_hashtags = []

            last_clip = self.config_manager.get_channel_setting("last_generated_clip", {}, profile_name)
            if last_clip and last_clip.get("rendered_mp4_path") and os.path.exists(last_clip["rendered_mp4_path"]):
                target_mp4 = last_clip["rendered_mp4_path"]
                clip_title = last_clip.get("title", clip_title)
                clip_hook = last_clip.get("hook", "")
                clip_rationale = last_clip.get("rationale", "")
                clip_hashtags = last_clip.get("hashtags", [])

            # Fallback: scan channel's shorts_clips directory for newest MP4
            if not target_mp4:
                all_mp4s = glob.glob(os.path.join(shorts_dir, "**", "*.mp4"), recursive=True)
                if all_mp4s:
                    target_mp4 = max(all_mp4s, key=os.path.getmtime)

            if not target_mp4 or not os.path.exists(target_mp4):
                self.logger.error(f"[Manual Upload] No rendered video short found to upload for '{profile_name}'.")
                self.after(0, lambda: messagebox.showerror("No Video Found", f"No rendered video short found for '{profile_name}'. Please generate a short first!"))
                self.after(0, lambda: self.manual_upload_btn.configure(state="normal"))
                return

            def _upload_progress_cb(pct: float, msg: str):
                self.log_queue.put(("PROGRESS", pct / 100.0))
                self.log_queue.put(("LOG", "INFO", f"[Manual Upload] {msg}"))

            self.logger.info(f"[Manual Upload] Publishing '{os.path.basename(target_mp4)}' across enabled social platforms for '{profile_name}'...")
            multi_pub = MultiPlatformPublisher(self.config_manager, profile_name, logger=self.logger)
            upload_res = multi_pub.publish_all(
                video_path=target_mp4,
                title=clip_title,
                hook=clip_hook,
                rationale=clip_rationale,
                channel_name=profile_name,
                hashtags=clip_hashtags,
                progress_callback=_upload_progress_cb
            )

            success_count = upload_res.get("success_count", 0)
            if success_count > 0:
                self.config_manager.mark_clip_uploaded(target_mp4, upload_res, profile_name=profile_name)
                platforms_list = [p.capitalize() for p in upload_res.get("platforms", [])]
                platforms_str = ", ".join(platforms_list)
                self.logger.info(f"[Manual Upload] Successfully published to: {platforms_str}!")
                print(f"\n[Multi-Platform Success] Successfully published for '{profile_name}': {platforms_str}\n")

                # Schedule deletion in 1 hour
                self.logger.info("[Manual Upload] Clip scheduled for automatic local deletion in 1 hour (record kept permanently).")
                self.config_manager.schedule_file_deletion(target_mp4, delay_seconds=3600, clip_id=f"manual_{profile_name}")

                self.after(0, lambda: self.status_badge.configure(text=" UPLOADED ", fg_color="#00A8B5", text_color="#FFFFFF"))
                self.after(0, lambda: self.progress_label.configure(text=f"✅ Published to {platforms_str}"))
            else:
                err_dict = upload_res.get("errors", {})
                err_msg = ", ".join(f"{k}: {v}" for k, v in err_dict.items()) if err_dict else "No platforms enabled or credentials provided."
                self.logger.warning(f"[Manual Upload] Upload not completed: {err_msg}")
                self.after(0, lambda: messagebox.showwarning("Upload Warning", f"Upload could not be completed:\n\n{err_msg}"))

        except Exception as e:
            self.logger.error(f"[Manual Upload] Upload failed: {e}")
            self.after(0, lambda: messagebox.showerror("Upload Error", f"Failed to upload video: {e}"))
        finally:
            self.after(0, lambda: self.manual_upload_btn.configure(state="normal"))
            self.after(0, self._refresh_state_info)

    def _reset_ui_state(self, success: bool = True):
        """Resets controls and status badge on main thread."""
        def update():
            self.start_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            self.manual_upload_btn.configure(state="normal")
            self.admin_btn.configure(state="normal")
            self.autofetch_btn.configure(state="normal")
            if success:
                self.status_badge.configure(text=" READY ", fg_color="#00A8B5", text_color="#FFFFFF")
                self.progress_bar.set(1.0)
                self.progress_label.configure(text="📥 Progress: 100% (Complete)")
            else:
                self.status_badge.configure(text=" STOPPED / ERROR ", fg_color="#8B2525", text_color="#FFFFFF")
            self._refresh_state_info()

        self.after(0, update)

    def _open_output_directory(self):
        """Opens local shorts_clips output folder for the active channel profile."""
        current_prof = self.config_manager.get_active_profile_name()
        dirs = self.config_manager.get_channel_output_dirs(current_prof)
        shorts_dir = dirs["shorts_clips"]
        os.makedirs(shorts_dir, exist_ok=True)
        try:
            if os.name == 'nt':
                os.startfile(shorts_dir)
            else:
                subprocess.Popen(['open' if os.uname().sysname == 'Darwin' else 'xdg-open', shorts_dir])
        except Exception as e:
            self.logger.error(f"Could not open directory: {e}")
