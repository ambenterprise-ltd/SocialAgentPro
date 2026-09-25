import sys
import os

sys.path.insert(0, os.path.abspath("."))

from config import ConfigManager
from ui.main_window import MainWindow

def test_dynamic_labels():
    print("=== Testing Dynamic UI Labels and Logger Outputs ===")
    cfg = ConfigManager()

    # 1. Test Config Contexts
    ws_ctx = cfg.get_channel_context("Wealth Secrets")
    kp_ctx = cfg.get_channel_context("Khao Pakistan")
    nw_ctx = cfg.get_channel_context("Nutrilogic Way")

    assert ws_ctx.get("content_type") == "podcast", f"WS content_type mismatch: {ws_ctx.get('content_type')}"
    assert kp_ctx.get("content_type") == "food vlog", f"KP content_type mismatch: {kp_ctx.get('content_type')}"
    assert nw_ctx.get("content_type") == "supplement review", f"NW content_type mismatch: {nw_ctx.get('content_type')}"
    print("ConfigManager content_type verified for all 3 profiles!")

    # 2. Test MainWindow Dynamic Labels and Auto-Fetch
    root = MainWindow(cfg)
    root.withdraw()

    # Intercept log messages to verify logger output
    captured_logs = []
    class LogCaptureHandler:
        def info(self, msg, *args):
            captured_logs.append(msg % args if args else msg)
        def warning(self, msg, *args): pass
        def error(self, msg, *args): pass
        def debug(self, msg, *args): pass
    
    root.logger = LogCaptureHandler()

    try:
        # Test Khao Pakistan
        root.refresh_dashboard_ui("Khao Pakistan")
        kp_label = root.url_label.cget("text")
        expected_kp_label = "Food Vlog YouTube URL (or leave blank to Auto-Discover fresh food vlog):"
        assert kp_label == expected_kp_label, f"KP label mismatch: got '{kp_label}'"
        print(f"Khao Pakistan URL Label: '{kp_label}' OK")

        captured_logs.clear()
        root._autofetch_click()
        assert any("fresh food vlog on next run" in m for m in captured_logs), f"Log mismatch: {captured_logs}"
        print(f"Khao Pakistan Autofetch Log: '{captured_logs[-1]}' OK")

        # Test Nutrilogic Way
        root.refresh_dashboard_ui("Nutrilogic Way")
        nw_label = root.url_label.cget("text")
        expected_nw_label = "Supplement Review YouTube URL (or leave blank to Auto-Discover fresh supplement review):"
        assert nw_label == expected_nw_label, f"NW label mismatch: got '{nw_label}'"
        print(f"Nutrilogic Way URL Label: '{nw_label}' OK")

        captured_logs.clear()
        root._autofetch_click()
        assert any("fresh supplement review on next run" in m for m in captured_logs), f"Log mismatch: {captured_logs}"
        print(f"Nutrilogic Way Autofetch Log: '{captured_logs[-1]}' OK")

        # Test Wealth Secrets
        root.refresh_dashboard_ui("Wealth Secrets")
        ws_label = root.url_label.cget("text")
        expected_ws_label = "Podcast YouTube URL (or leave blank to Auto-Discover fresh podcast):"
        assert ws_label == expected_ws_label, f"WS label mismatch: got '{ws_label}'"
        print(f"Wealth Secrets URL Label: '{ws_label}' OK")

        captured_logs.clear()
        root._autofetch_click()
        assert any("fresh podcast on next run" in m for m in captured_logs), f"Log mismatch: {captured_logs}"
        print(f"Wealth Secrets Autofetch Log: '{captured_logs[-1]}' OK")

    finally:
        root.destroy()

    print("\nALL DYNAMIC LABEL & LOGGER TESTS PASSED PERFECTLY!")

if __name__ == "__main__":
    test_dynamic_labels()
