import os
import sys
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
from config import ConfigManager
from core.composer import FFmpegComposer, create_moviepy_template_clip

cm = ConfigManager()

# Test 1: Exact filenames in dictionary and config
channels = {
    "Wealth Secrets": "wealth secret template (2).jpg",
    "Khao Pakistan": "khao pakistan template.jpg",
    "Nutrilogic Way": "nutrilogic way template.jpg"
}

for ch, expected_fn in channels.items():
    ctx = cm.get_channel_context(ch)
    assert ctx["template_filename"] == expected_fn, f"Filename mismatch for {ch}: {ctx.get('template_filename')}"
    tpl_path = cm.get_template_path(ch)
    assert os.path.exists(tpl_path), f"Template file does not exist on disk for {ch}: {tpl_path}"
    assert os.path.basename(tpl_path) == expected_fn, f"Resolved basename mismatch: {os.path.basename(tpl_path)} vs {expected_fn}"
    print(f"✅ Channel '{ch}' mapped to '{expected_fn}' -> Found at: {tpl_path}")

# Test 2: Viewport detection on all 3 templates
composer = FFmpegComposer(output_width=1080, output_height=1920)
for ch, expected_fn in channels.items():
    tpl_path = cm.get_template_path(ch)
    vx, vy, vw, vh, overlay = composer.detect_template_viewport(tpl_path)
    assert overlay and os.path.exists(overlay), f"Overlay generation failed for {ch}"
    assert vw == 1080 and vh > 500, f"Invalid viewport detected for {ch}: {vw}x{vh}"
    print(f"✅ Viewport for '{ch}': vx={vx}, vy={vy}, vw={vw}, vh={vh}, overlay={os.path.basename(overlay)}")

# Test 3: Safety check / missing template handling
vx, vy, vw, vh, overlay = composer.detect_template_viewport("nonexistent_template.jpg")
assert overlay is None, f"Expected None overlay for missing template, got: {overlay}"
assert vw == 1080 and vh == 1920, f"Expected full screen viewport for missing template"
print("✅ Missing template viewport returned safe defaults without crashing")

# Test 4: MoviePy template clip helper
clip = create_moviepy_template_clip("nonexistent_template.jpg")
assert clip is None, "Expected None for missing template in create_moviepy_template_clip"
print("✅ MoviePy template clip helper handled nonexistent file safely")

print("\n🎉 ALL DYNAMIC TEMPLATE MAPPING TESTS PASSED SUCCESSFULLY!")
