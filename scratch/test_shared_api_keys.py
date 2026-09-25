import sys
import os

sys.path.insert(0, os.path.abspath("."))

from config import ConfigManager
from ui.main_window import MainWindow
from ui.admin_modal import AdminSettingsModal

def test_shared_api_keys():
    print("=== Testing Shared Groq API Keys Across All Channels ===")
    cfg = ConfigManager()

    # Verify ConfigManager level
    shared_primary = cfg.get_shared_groq_api_key()
    shared_pool = cfg.get_shared_groq_keys_pool()
    print(f"ConfigManager Shared Primary: {shared_primary[:15]}...")
    print(f"ConfigManager Shared Pool Count: {len(shared_pool)}")
    assert bool(shared_primary), "Primary key should not be empty!"
    assert len(shared_pool) > 0, "Pool should have keys!"

    for channel in ["Wealth Secrets", "Khao Pakistan", "Nutrilogic Way"]:
        pool = cfg.get_api_key_pool(channel)
        key = cfg.get_channel_setting("groq_api_key", "", channel)
        assert bool(key), f"{channel} should have primary groq key!"
        assert len(pool) == len(shared_pool) + 1, f"{channel} pool count mismatch: {len(pool)}"
        print(f"Channel '{channel}' verified with {len(pool)} total keys.")

    # Verify UI Modal level
    root = MainWindow(cfg)
    root.withdraw()

    try:
        modal = AdminSettingsModal(root, cfg)
        modal.withdraw()

        # Check initial keys (Wealth Secrets)
        modal._on_profile_switched("Wealth Secrets")
        ws_key = modal.groq_key_entry.get().strip()
        ws_pool_lines = [k.strip() for k in modal.keys_textbox.get("1.0", "end").splitlines() if k.strip()]
        assert ws_key == shared_primary, "Modal should display primary key on Wealth Secrets"
        assert len(ws_pool_lines) == len(shared_pool), "Modal should display pool keys on Wealth Secrets"
        print("UI Modal Wealth Secrets: Keys present OK.")

        # Switch to Khao Pakistan
        modal._on_profile_switched("Khao Pakistan")
        kp_key = modal.groq_key_entry.get().strip()
        kp_pool_lines = [k.strip() for k in modal.keys_textbox.get("1.0", "end").splitlines() if k.strip()]
        assert kp_key == shared_primary, f"Khao Pakistan primary key disappeared! Got: '{kp_key}'"
        assert len(kp_pool_lines) == len(shared_pool), f"Khao Pakistan pool keys disappeared! Got {len(kp_pool_lines)} keys"
        print(f"UI Modal Khao Pakistan: Keys STAY VISIBLE! ({len(kp_pool_lines)} pool keys) OK.")

        # Switch to Nutrilogic Way
        modal._on_profile_switched("Nutrilogic Way")
        nw_key = modal.groq_key_entry.get().strip()
        nw_pool_lines = [k.strip() for k in modal.keys_textbox.get("1.0", "end").splitlines() if k.strip()]
        assert nw_key == shared_primary, f"Nutrilogic Way primary key disappeared! Got: '{nw_key}'"
        assert len(nw_pool_lines) == len(shared_pool), f"Nutrilogic Way pool keys disappeared! Got {len(nw_pool_lines)} keys"
        print(f"UI Modal Nutrilogic Way: Keys STAY VISIBLE! ({len(nw_pool_lines)} pool keys) OK.")

        modal.destroy()
    finally:
        root.destroy()

    print("\nALL SHARED API KEY TESTS PASSED PERFECTLY!")

if __name__ == "__main__":
    test_shared_api_keys()
