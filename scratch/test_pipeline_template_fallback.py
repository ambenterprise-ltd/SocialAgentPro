import os
import sys
import logging

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from config import ConfigManager
from core.composer import FFmpegComposer

# Setup mock logger to test warning captures
logger = logging.getLogger("TestLogger")
captured_warnings = []
class WarningCaptureHandler(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.WARNING:
            captured_warnings.append(record.getMessage())

logger.addHandler(WarningCaptureHandler())
logger.setLevel(logging.DEBUG)

cm = ConfigManager()

# Simulate Pipeline Phase 6 fallback logic:
def simulate_phase6_template_resolution(chan_name, force_bad_path=None):
    if force_bad_path:
        raw_tpl_path = force_bad_path
    else:
        raw_tpl_path = cm.get_template_path(chan_name)
    
    resolved_tpl_path = cm.resolve_asset_path(raw_tpl_path)

    final_tpl_path = None
    if resolved_tpl_path and os.path.exists(resolved_tpl_path):
        final_tpl_path = resolved_tpl_path
        logger.info(f"[Live Activity Log] 🎨 Loaded dynamic template overlay for '{chan_name}': {os.path.basename(final_tpl_path)}")
    else:
        default_fallback = cm.resolve_asset_path("assets/wealth secret template (2).jpg")
        if default_fallback and os.path.exists(default_fallback):
            logger.warning(
                f"[Live Activity Log] ⚠️ Template Warning: Dynamic template '{raw_tpl_path}' for channel '{chan_name}' "
                f"not found on disk. Falling back to default asset: {default_fallback}"
            )
            final_tpl_path = default_fallback
        else:
            logger.warning(
                f"[Live Activity Log] ⚠️ Template Warning: Dynamic template '{raw_tpl_path}' for channel '{chan_name}' "
                f"not found on disk. Proceeding with compositing without template overlay."
            )
            final_tpl_path = None

    composer = FFmpegComposer(
        output_width=1080,
        output_height=1920,
        template_path=final_tpl_path,
        logger=logger
    )
    return final_tpl_path, composer

# Test A: Khao Pakistan normal resolution
tpl, comp = simulate_phase6_template_resolution("Khao Pakistan")
assert os.path.basename(tpl) == "khao pakistan template.jpg", f"Unexpected template: {tpl}"
print(f"✅ Normal Khao Pakistan resolved: {tpl}")

# Test B: Simulated missing template with fallback
tpl_bad, comp_bad = simulate_phase6_template_resolution("Khao Pakistan", force_bad_path="assets/does_not_exist.jpg")
assert any("Template Warning" in w for w in captured_warnings), "Warning was not logged!"
assert "wealth secret template (2).jpg" in tpl_bad, f"Did not fall back to default: {tpl_bad}"
print(f"✅ Simulated missing template safely fell back to: {tpl_bad}")
print(f"   Logged warning: {captured_warnings[-1]}")

# Test C: No template at all (both missing and None fallback)
comp_none = FFmpegComposer(template_path=None, logger=logger)
vx, vy, vw, vh, overlay = comp_none.detect_template_viewport(None)
assert overlay is None
print("✅ FFmpegComposer initialized with template_path=None without error")

print("\n🎉 ALL PIPELINE FALLBACK TESTS PASSED!")
