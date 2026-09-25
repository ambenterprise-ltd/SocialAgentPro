import os
import threading
from typing import Optional, Dict, Any, List
import customtkinter as ctk

from config import ConfigManager
from core.precheck import SystemPrechecker


class SystemPrecheckModal(ctk.CTkToplevel):
    """
    Modern Diagnostic & Pre-Flight Verification Modal for AMB Enterprise.
    Visually reports health of libraries, binaries, GPU, AI brain, connected social platforms,
    and Google Sheets integration before production video generation runs.
    """

    def __init__(self, parent, config_manager: ConfigManager, profile_name: Optional[str] = None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.profile_name = profile_name or self.config_manager.get_active_profile_name()
        self.prechecker = SystemPrechecker(self.config_manager)

        self.title("AMB Enterprise - System Pre-Flight Diagnostics")
        self.geometry("640x720")
        self.minsize(580, 600)
        self.transient(parent)
        self.grab_set()

        # Center modal over parent
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (640 // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (720 // 2)
        self.geometry(f"+{x}+{y}")

        self._build_ui()
        self._start_diagnostic_thread()

    def _build_ui(self):
        # Header Frame
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(15, 10))

        title_lbl = ctk.CTkLabel(
            header_frame,
            text="🩺 System Pre-Flight Diagnostics",
            font=ctk.CTkFont(size=20, weight="bold")
        )
        title_lbl.pack(anchor="w")

        self.subtitle_lbl = ctk.CTkLabel(
            header_frame,
            text=f"Checking all libraries, AI brain, and connected pages for profile: '{self.profile_name}'...",
            font=ctk.CTkFont(size=12),
            text_color="gray"
        )
        self.subtitle_lbl.pack(anchor="w", pady=(2, 0))

        # Status Progress Bar
        self.progress_bar = ctk.CTkProgressBar(self, height=8, mode="indeterminate")
        self.progress_bar.pack(fill="x", padx=20, pady=(0, 10))
        self.progress_bar.start()

        # Scrollable list for diagnostic results
        self.scroll_frame = ctk.CTkScrollableFrame(self, fg_color="#181A1B", corner_radius=8)
        self.scroll_frame.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        # Bottom Action Bar
        bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        bottom_frame.pack(fill="x", padx=20, pady=(5, 15))

        self.summary_badge = ctk.CTkLabel(
            bottom_frame,
            text="Running diagnostics...",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#58A6FF"
        )
        self.summary_badge.pack(side="left")

        close_btn = ctk.CTkButton(
            bottom_frame,
            text="Close",
            width=100,
            command=self.destroy
        )
        close_btn.pack(side="right")

        self.rerun_btn = ctk.CTkButton(
            bottom_frame,
            text="🔄 Re-Run Check",
            width=130,
            fg_color="#3A3D40",
            hover_color="#4E5256",
            command=self._start_diagnostic_thread
        )
        self.rerun_btn.pack(side="right", padx=(0, 10))

    def _start_diagnostic_thread(self):
        """Runs the diagnostics in a background thread to keep UI completely responsive."""
        self.rerun_btn.configure(state="disabled")
        self.progress_bar.pack(fill="x", padx=20, pady=(0, 10))
        self.progress_bar.start()

        # Clear previous items
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()

        loading_lbl = ctk.CTkLabel(
            self.scroll_frame,
            text="Checking libraries, GPU, Groq Cloud, YouTube, Facebook, Instagram & Google Sheets...",
            font=ctk.CTkFont(size=12),
            text_color="gray"
        )
        loading_lbl.pack(pady=30)

        t = threading.Thread(target=self._run_diagnostics_worker, daemon=True)
        t.start()

    def _run_diagnostics_worker(self):
        """Worker executing precheck and dispatching results back to UI thread."""
        results = self.prechecker.run_all_checks(profile_name=self.profile_name)
        self.after(0, lambda: self._render_results(results))

    def _render_results(self, results: Dict[str, Any]):
        """Renders diagnostic cards on the UI thread."""
        self.progress_bar.stop()
        self.progress_bar.pack_forget()
        self.rerun_btn.configure(state="normal")

        # Clear loading label
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()

        checks = results.get("checks", [])
        categories = {}
        for c in checks:
            cat = c.get("category", "General")
            categories.setdefault(cat, []).append(c)

        for cat_name, cat_checks in categories.items():
            # Category Header
            cat_header = ctk.CTkLabel(
                self.scroll_frame,
                text=cat_name,
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color="#58A6FF"
            )
            cat_header.pack(anchor="w", padx=5, pady=(12, 4))

            # Group card frame
            card_frame = ctk.CTkFrame(self.scroll_frame, fg_color="#21262D", corner_radius=6)
            card_frame.pack(fill="x", padx=5, pady=(0, 6))

            for idx, item in enumerate(cat_checks):
                item_row = ctk.CTkFrame(card_frame, fg_color="transparent")
                item_row.pack(fill="x", padx=10, pady=6)

                status = item.get("status", "PASS")
                if status == "PASS":
                    badge_text = "PASS"
                    badge_color = "#2EA043"
                elif status == "WARN":
                    badge_text = "WARN"
                    badge_color = "#D29922"
                else:
                    badge_text = "FAIL"
                    badge_color = "#F85149"

                badge = ctk.CTkLabel(
                    item_row,
                    text=f" {badge_text} ",
                    font=ctk.CTkFont(size=11, weight="bold"),
                    fg_color=badge_color,
                    text_color="#FFFFFF",
                    corner_radius=4,
                    width=50
                )
                badge.pack(side="left", padx=(0, 10))

                content_col = ctk.CTkFrame(item_row, fg_color="transparent")
                content_col.pack(side="left", fill="x", expand=True)

                name_lbl = ctk.CTkLabel(
                    content_col,
                    text=item.get("name", "Check"),
                    font=ctk.CTkFont(size=12, weight="bold"),
                    anchor="w"
                )
                name_lbl.pack(anchor="w")

                msg_lbl = ctk.CTkLabel(
                    content_col,
                    text=item.get("message", ""),
                    font=ctk.CTkFont(size=11),
                    text_color="#8B949E" if status == "PASS" else ("#E3B341" if status == "WARN" else "#FF7B72"),
                    wraplength=480,
                    justify="left",
                    anchor="w"
                )
                msg_lbl.pack(anchor="w")

                if idx < len(cat_checks) - 1:
                    sep = ctk.CTkFrame(card_frame, height=1, fg_color="#30363D")
                    sep.pack(fill="x", padx=10, pady=(2, 2))

        # Update Summary Badge
        p_cnt = results.get("pass_count", 0)
        w_cnt = results.get("warn_count", 0)
        f_cnt = results.get("fail_count", 0)
        crit_ok = results.get("critical_ok", True)

        if f_cnt == 0 and w_cnt == 0:
            summary_txt = f"✅ All Systems Operational ({p_cnt} Passed)"
            summary_color = "#2EA043"
        elif f_cnt == 0:
            summary_txt = f"⚡ Operational ({p_cnt} Passed, {w_cnt} Warnings)"
            summary_color = "#D29922"
        elif crit_ok:
            summary_txt = f"⚠️ Minor Issues ({p_cnt} Passed, {w_cnt} Warnings, {f_cnt} Non-Critical Failures)"
            summary_color = "#D29922"
        else:
            summary_txt = f"❌ Critical Failure Detected ({f_cnt} Critical Error(s))"
            summary_color = "#F85149"

        self.summary_badge.configure(text=summary_txt, text_color=summary_color)
        self.subtitle_lbl.configure(text=f"Diagnostics completed for channel '{self.profile_name}'")
