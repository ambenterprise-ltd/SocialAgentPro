"""
Chrome Caption Renderer for AMB Enterprise YouTube Content Agent.
Natively processes Right-to-Left (RTL) text and complex ligatures (e.g. Jameel Noori Nastaleeq)
using a headless Chromium browser instance via Playwright, generating transparent PNG sequences,
and compositing them onto video clips via MoviePy ImageClip objects.
"""

import os
import sys
import json
import logging
from typing import Dict, Any, List, Optional, Tuple, Union

# MoviePy 1.x and 2.x compatibility bridge
try:
    from moviepy import ImageClip, CompositeVideoClip, VideoFileClip, Clip
except ImportError:
    try:
        from moviepy.editor import ImageClip, CompositeVideoClip, VideoFileClip
        from moviepy.Clip import Clip
    except ImportError:
        ImageClip = None
        CompositeVideoClip = None
        VideoFileClip = None
        Clip = None

# Ensure MoviePy 2.x supports .set_position, .set_start, .set_end, .set_duration, .set_opacity on all clip types
for cls in [c for c in (Clip, ImageClip, VideoFileClip, CompositeVideoClip) if c is not None]:
    if hasattr(cls, "with_position") and not hasattr(cls, "set_position"):
        cls.set_position = lambda self, pos, relative=False: self.with_position(pos, relative=relative)
    if hasattr(cls, "with_start") and not hasattr(cls, "set_start"):
        cls.set_start = lambda self, t: self.with_start(t)
    if hasattr(cls, "with_end") and not hasattr(cls, "set_end"):
        cls.set_end = lambda self, t: self.with_end(t)
    if hasattr(cls, "with_duration") and not hasattr(cls, "set_duration"):
        cls.set_duration = lambda self, t: self.with_duration(t)
    if hasattr(cls, "with_opacity") and not hasattr(cls, "set_opacity"):
        cls.set_opacity = lambda self, op: self.with_opacity(op)


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUTPUT_DIR = os.path.join(BASE_DIR, "output", "temp_captions")
DEFAULT_URDU_FONT_PATH = os.path.join(BASE_DIR, "urdu fonts", "JameelNooriNastaleeq.ttf")

COLOR_MAP = {
    "yellow": "#FFFF00",
    "white": "#FFFFFF",
    "cyan": "#00FFFF",
    "green": "#00FF00",
    "neon green": "#39FF14",
    "red": "#FF3333",
    "blue": "#3399FF",
    "gold": "#FFD700",
    "amber": "#FFBF00",
    "magenta": "#FF00FF"
}


def resolve_hex_color(color_name_or_hex: Optional[str]) -> str:
    """Resolves color names from UI dropdowns to standard hex codes."""
    if not color_name_or_hex:
        return "#FFFF00"
    val = str(color_name_or_hex).strip().lower()
    if val.startswith("#"):
        return color_name_or_hex.strip()
    return COLOR_MAP.get(val, "#FFFF00")


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 1. Dynamic HTML/CSS Generation
# ---------------------------------------------------------------------------
def generate_caption_html_template(
    font_path: Optional[str] = None,
    caption_color: str = "#FFFF00",
    font_size: int = 80
) -> str:
    """
    Generates a local HTML template for headless Chrome rendering.
    Enforces strict typography and contrast styling:
    1. Massive 80px typography, font-weight: 900, text-align: center, line-height: 1.5.
    2. High-visibility dynamic color with fallback to #FFFF00 (Yellow) or #FFFFFF (White).
    3. Thick, sharp black outline with paint-order: stroke fill and -webkit-text-stroke: 3px #000000.
    4. Multi-layered 3D drop shadow (3px 3px 0 #000, -2px -2px 0 #000, 2px -2px 0 #000, -2px 2px 0 #000, 4px 4px 5px rgba(0,0,0,0.8)).
    5. Semi-transparent black background box (rgba(0,0,0,0.6)) with padding: 20px 40px and border-radius: 15px.
    """
    if not font_path or not os.path.exists(font_path):
        font_path = DEFAULT_URDU_FONT_PATH

    font_url = os.path.abspath(font_path).replace("\\", "/")
    hex_color = resolve_hex_color(caption_color)

    html_content = f"""<!DOCTYPE html>
<html lang="ur" dir="rtl">
<head>
<meta charset="utf-8">
<style>
@font-face {{
    font-family: 'JameelNooriNastaleeq';
    src: url('file:///{font_url}') format('truetype');
    font-weight: 900;
    font-style: normal;
}}

* {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}}

html, body {{
    background: transparent !important;
    width: 1080px;
    height: 320px;
    display: flex;
    justify-content: center;
    align-items: center;
    overflow: hidden;
}}

.caption-box {{
    display: inline-flex;
    justify-content: center;
    align-items: center;
    direction: rtl;
    background-color: rgba(0, 0, 0, 0.6);
    padding: 20px 40px;
    border-radius: 15px;
    max-width: 980px;
}}

.caption-text {{
    font-family: 'JameelNooriNastaleeq', serif;
    font-size: {font_size}px;
    font-weight: 900;
    line-height: 1.5;
    color: {hex_color};
    paint-order: stroke fill;
    -webkit-text-stroke: 3px #000000;
    text-stroke: 3px #000000;
    text-shadow: 3px 3px 0 #000, -2px -2px 0 #000, 2px -2px 0 #000, -2px 2px 0 #000, 4px 4px 5px rgba(0,0,0,0.8);
    white-space: nowrap;
    text-align: center;
    direction: rtl;
    margin: 0;
    padding: 0;
}}

.caption-word {{
    color: #FFFFFF;
    display: inline-block;
    padding: 0 5px;
    paint-order: stroke fill;
    -webkit-text-stroke: 3px #000000;
    text-stroke: 3px #000000;
    text-shadow: 3px 3px 0 #000, -2px -2px 0 #000, 2px -2px 0 #000, -2px 2px 0 #000, 4px 4px 5px rgba(0,0,0,0.8);
    transition: transform 0.1s ease;
}}

.caption-word.active {{
    color: {hex_color};
    transform: scale(1.06);
}}
</style>
</head>
<body>
<div class="caption-box" id="caption-box">
    <div class="caption-text" id="caption-text"></div>
</div>
</body>
</html>
"""
    return html_content


# ---------------------------------------------------------------------------
# 2. Headless Browser Screenshot Engine (Playwright)
# ---------------------------------------------------------------------------
def render_urdu_captions_playwright(
    transcription_segments: List[Dict[str, Any]],
    output_dir: Optional[str] = None,
    caption_color: str = "Yellow",
    font_path: Optional[str] = None,
    clip_start: float = 0.0,
    max_words_per_line: int = 4,
    word_highlight: bool = True,
    font_size: int = 80,
    logger: Optional[logging.Logger] = None
) -> List[Dict[str, Any]]:
    """
    Launches a headless Chromium instance via Playwright to natively process RTL
    Urdu text and complex Nastaleeq ligatures.
    Supports both phrase-level and sequential word-by-word (karaoke style) highlight rendering:
      - Massive 80px bold Nastaleeq typography with 3px black stroke & multi-layered 3D shadow.
      - Each spoken word glows in vibrant UI color while remaining words stay bold white.
      - Saves transparent PNGs into output_dir and persists timing metadata.
    """
    log = logger or logging.getLogger("ChromeCaptionRenderer")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("[ChromeCaptionRenderer] Playwright is not installed! Run: pip install playwright && playwright install chromium")
        return []

    if not output_dir:
        output_dir = DEFAULT_OUTPUT_DIR
    elif not os.path.isabs(output_dir):
        output_dir = os.path.join(BASE_DIR, output_dir)

    os.makedirs(output_dir, exist_ok=True)
    html_template = generate_caption_html_template(
        font_path=font_path,
        caption_color=caption_color,
        font_size=font_size
    )
    template_path = os.path.abspath(os.path.join(output_dir, "caption_template.html"))
    with open(template_path, "w", encoding="utf-8") as f:
        f.write(html_template)

    # 1. Normalize transcription segments into timed dialogue groups
    is_word_level = len(transcription_segments) > 0 and "word" in transcription_segments[0]
    timed_lines = []

    if is_word_level:
        current_words = []
        for w in transcription_segments:
            raw_w = str(w.get("word", "")).strip()
            if not raw_w:
                continue
            current_words.append(w)
            if len(current_words) >= max_words_per_line or raw_w.endswith((".", "!", "?", "۔", "؟", "،")):
                timed_lines.append(current_words)
                current_words = []
        if current_words:
            timed_lines.append(current_words)
    else:
        for seg in transcription_segments:
            txt = str(seg.get("text", "")).strip()
            if txt:
                timed_lines.append(seg)

    rendered_clips_data: List[Dict[str, Any]] = []

    # 2. Launch headless Chromium and generate transparent PNG sequence
    with sync_playwright() as p:
        # Linux EC2: Chromium requires --no-sandbox when running as root or in containerized env
        browser_args = []
        if sys.platform == "linux":
            browser_args = ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        browser = p.chromium.launch(headless=True, args=browser_args)
        page = browser.new_page(viewport={"width": 1080, "height": 320})
        page.goto(f"file:///{template_path.replace(os.sep, '/')}")
        page.evaluate("document.fonts.ready")

        box_locator = page.locator("#caption-box")
        clip_counter = 0

        if is_word_level and word_highlight:
            # Word-by-Word Karaoke Highlighting Mode
            log.info(f"[ChromeCaptionRenderer] Generating bold word-by-word karaoke sequence across {len(timed_lines)} lines...")
            for line_words in timed_lines:
                # Step 2: HTML Span Wrapping
                spans_html = " ".join(
                    f'<span class="caption-word" id="word-{i}">{w.get("word", "")}</span>'
                    for i, w in enumerate(line_words)
                )
                page.evaluate(f"document.getElementById('caption-text').innerHTML = {json.dumps(spans_html)};")

                for w_idx, w in enumerate(line_words):
                    # Step 3: Sequential Highlight Rendering
                    page.evaluate(f"""() => {{
                        document.querySelectorAll('.caption-word').forEach(el => el.classList.remove('active'));
                        const activeEl = document.getElementById('word-{w_idx}');
                        if (activeEl) activeEl.classList.add('active');
                    }}""")

                    png_filename = f"urdu_caption_{clip_counter:04d}.png"
                    png_path = os.path.abspath(os.path.join(output_dir, png_filename))
                    box_locator.screenshot(path=png_path, omit_background=True)

                    w_start = max(0.0, float(w.get("start", 0.0)) - clip_start)
                    if w_idx + 1 < len(line_words):
                        w_end = max(w_start + 0.1, float(line_words[w_idx + 1].get("start", 0.0)) - clip_start)
                    else:
                        w_end = max(w_start + 0.3, float(w.get("end", 0.0)) - clip_start)

                    rendered_clips_data.append({
                        "png_path": png_path,
                        "start": round(w_start, 3),
                        "end": round(w_end, 3),
                        "duration": round(w_end - w_start, 3),
                        "text": w.get("word", ""),
                        "full_line": " ".join(item.get("word", "") for item in line_words)
                    })
                    clip_counter += 1
        else:
            # Sentence / Phrase Block Mode
            log.info(f"[ChromeCaptionRenderer] Generating bold phrase-level captions for {len(timed_lines)} items...")
            for seg in timed_lines:
                text_content = seg.get("text", "") if isinstance(seg, dict) else str(seg)
                if not text_content and isinstance(seg, list):
                    text_content = " ".join(item.get("word", "") for item in seg)
                if not text_content:
                    continue

                page.evaluate(f"document.getElementById('caption-text').textContent = {json.dumps(text_content)};")
                png_filename = f"urdu_caption_{clip_counter:04d}.png"
                png_path = os.path.abspath(os.path.join(output_dir, png_filename))
                box_locator.screenshot(path=png_path, omit_background=True)

                if isinstance(seg, dict):
                    s = max(0.0, float(seg.get("start", 0.0)) - clip_start)
                    e = max(s + 0.3, float(seg.get("end", 0.0)) - clip_start)
                elif isinstance(seg, list):
                    s = max(0.0, float(seg[0].get("start", 0.0)) - clip_start)
                    e = max(s + 0.3, float(seg[-1].get("end", 0.0)) - clip_start)
                else:
                    s, e = 0.0, 2.0

                rendered_clips_data.append({
                    "png_path": png_path,
                    "start": round(s, 3),
                    "end": round(e, 3),
                    "duration": round(e - s, 3),
                    "text": text_content
                })
                clip_counter += 1

        browser.close()

    # Prevent timing overlaps
    for i in range(len(rendered_clips_data) - 1):
        if rendered_clips_data[i]["end"] > rendered_clips_data[i + 1]["start"]:
            rendered_clips_data[i]["end"] = max(
                rendered_clips_data[i]["start"] + 0.05,
                rendered_clips_data[i + 1]["start"]
            )
            rendered_clips_data[i]["duration"] = round(rendered_clips_data[i]["end"] - rendered_clips_data[i]["start"], 3)

    # Persist metadata mapping for standalone compositor recovery
    meta_path = os.path.join(output_dir, "caption_metadata.json")
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(rendered_clips_data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

    log.info(f"[ChromeCaptionRenderer] Successfully generated {len(rendered_clips_data)} bold transparent PNG caption clips in '{output_dir}'.")
    return rendered_clips_data


# ---------------------------------------------------------------------------
# 3. MoviePy Image Compositing
# ---------------------------------------------------------------------------
def composite_captions_moviepy(
    video_clip,
    caption_data: Optional[Union[List[Dict[str, Any]], str]] = None,
    relative_position: Tuple[str, float] = ("center", 0.75)
):
    """
    Reads generated transparent PNGs as MoviePy ImageClip objects,
    maps start and end times to match Whisper segment timestamps,
    and positions them at ('center', 0.75) relative to the video canvas.
    Enforces the 4 strict compositing rules:
      Rule 1: Enforce Duration (Critical) via .set_start(), .set_duration(), .set_end().
      Rule 2: Z-Index Compositing: CompositeVideoClip([main_video, *caption_image_clips]).
      Rule 3: Transparency Masking: Read alpha channel via transparent=True without opacity overwrites.
      Rule 4: Retain Placement: .set_position(('center', 0.75), relative=True) locked to lower third.
    """
    if ImageClip is None or CompositeVideoClip is None:
        raise RuntimeError("MoviePy is not installed. Please run: pip install moviepy")

    # If caption_data is a folder path string or omitted, resolve from folder
    if isinstance(caption_data, str) or caption_data is None:
        folder = caption_data if isinstance(caption_data, str) else DEFAULT_OUTPUT_DIR
        meta_file = os.path.join(folder, "caption_metadata.json")
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as mf:
                    caption_data = json.load(mf)
            except Exception:
                caption_data = []
        else:
            caption_data = []
            if os.path.exists(folder):
                for fname in sorted(os.listdir(folder)):
                    if fname.startswith("urdu_caption_") and fname.endswith(".png"):
                        caption_data.append({
                            "png_path": os.path.join(folder, fname),
                            "start": 0.0,
                            "end": 2.0
                        })

    subtitle_clips = []
    for item in caption_data:
        png_path = item["png_path"]
        if not os.path.exists(png_path):
            continue

        start_t = max(0.0, float(item["start"]))
        end_t = max(start_t + 0.1, float(item["end"]))
        duration = round(end_t - start_t, 3)

        # Step 2: Read PNG as ImageClip, chain .set_start(), .set_duration(), .set_position()
        img_clip = ImageClip(png_path, transparent=True)
        img_clip = (
            img_clip
            .set_start(start_t)
            .set_duration(duration)
            .set_end(end_t)
            .set_position(relative_position, relative=True)
        )

        # Direct attribute assignments for dual MoviePy 1.x / 2.x safety
        img_clip.start = start_t
        img_clip.duration = duration
        img_clip.end = end_t

        subtitle_clips.append(img_clip)

    print(f"[MoviePy Compositor] Compositing {len(subtitle_clips)} caption ImageClips onto video canvas at {relative_position}...")
    # Step 3: CompositeVideoClip([base_video, *caption_clips])
    final_clip = CompositeVideoClip([video_clip, *subtitle_clips])
    return final_clip


def render_and_export_urdu_short(
    base_video_path: str,
    output_mp4_path: str,
    caption_segments: Optional[List[Dict[str, Any]]] = None,
    caption_color: str = "Yellow",
    clip_start: float = 0.0,
    temp_captions_dir: Optional[str] = None,
    relative_position: Tuple[str, float] = ("center", 0.75),
    logger: Optional[logging.Logger] = None
) -> str:
    """
    Complete end-to-end 4-step pipeline:
      Step 1: Pipeline Execution Flow: Triggers ChromeCaptionRenderer to generate transparent PNGs.
      Step 2: Read PNGs as ImageClips with start, duration, and lower-third position.
      Step 3: Execute CompositeVideoClip([base_video, *caption_clips]).
      Step 4: Export: Call .write_videofile() on composite video into output_mp4_path.
    """
    log = logger or logging.getLogger("ChromeCaptionRenderer")
    target_dir = temp_captions_dir or DEFAULT_OUTPUT_DIR

    # Step 1: Trigger ChromeCaptionRenderer if caption segments are provided
    caption_items = []
    if caption_segments:
        log.info("[Pipeline Step 1] Generating Urdu caption PNG frames with Headless Chrome...")
        caption_items = render_urdu_captions_playwright(
            transcription_segments=caption_segments,
            output_dir=target_dir,
            caption_color=caption_color,
            clip_start=clip_start,
            logger=log
        )

    # If no new items, attempt reading existing items from target_dir
    if not caption_items:
        meta_file = os.path.join(target_dir, "caption_metadata.json")
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as mf:
                    caption_items = json.load(mf)
                log.info(f"[Pipeline Step 1] Loaded {len(caption_items)} cached caption frames from '{meta_file}'.")
            except Exception:
                pass

    # Step 2 & 3: Read PNGs as ImageClips and composite onto base video
    log.info(f"[Pipeline Step 2 & 3] Loading base video '{base_video_path}' and compositing {len(caption_items)} ImageClips...")
    base_clip = VideoFileClip(base_video_path)
    composite_clip = composite_captions_moviepy(base_clip, caption_items, relative_position=relative_position)

    # Step 4: Export: Call .write_videofile() to output final stitched .mp4
    log.info(f"[Pipeline Step 4] Exporting final stitched video to '{output_mp4_path}'...")
    os.makedirs(os.path.dirname(os.path.abspath(output_mp4_path)), exist_ok=True)
    composite_clip.write_videofile(
        output_mp4_path,
        fps=base_clip.fps or 24,
        codec="libx264",
        audio_codec="aac",
        logger=None
    )
    base_clip.close()
    composite_clip.close()

    # Create completion marker
    marker = output_mp4_path + ".urdu_captioned"
    try:
        with open(marker, "w", encoding="utf-8") as f:
            f.write("done")
    except Exception:
        pass

    log.info(f"[Pipeline Step 4] Video compositing complete: '{output_mp4_path}'")
    return output_mp4_path


# ---------------------------------------------------------------------------
# 4. Conditional Pipeline Routing
# ---------------------------------------------------------------------------
def is_urdu_language(language: Optional[str] = None, settings_path: Optional[str] = None) -> bool:
    """
    Reads the active language from settings.json or input parameter.
    Returns True if Urdu is selected.
    """
    if language and str(language).strip():
        return str(language).strip().lower() in ("ur", "urdu")

    if not settings_path:
        settings_path = os.path.join(BASE_DIR, "settings.json")
    elif not os.path.isabs(settings_path):
        settings_path = os.path.join(BASE_DIR, settings_path)

    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                prof = data.get("active_profile", "Default")
                chan_cfg = data.get("profiles", {}).get(prof, {}) or data.get("channel_profiles", {}).get(prof, {})
                lang = chan_cfg.get("caption_language") or chan_cfg.get("language") or "English"
                return str(lang).strip().lower() in ("ur", "urdu")
        except Exception:
            pass

    return False
