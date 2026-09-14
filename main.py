import os
import sys

# Step 1: Define Base Directory and enforce process working directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
try:
    os.chdir(BASE_DIR)
except Exception:
    pass

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from config import ConfigManager
from ui.main_window import MainWindow


def main():
    """
    AMB Enterprise Application Entry Point.
    Instantiates configuration manager and starts CustomTkinter main event loop.
    """
    print("[AMB Enterprise] Starting YouTube Content Generation Agent...")
    config_manager = ConfigManager()

    # Launch GUI
    app = MainWindow(config_manager)
    app.mainloop()


if __name__ == "__main__":
    main()
