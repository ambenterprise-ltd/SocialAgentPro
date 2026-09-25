import os
import sys
import json
import re
import logging
import subprocess

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

import arabic_reshaper
from bidi.algorithm import get_display


def reshape_rtl_text(text: str) -> str:
    """
    Connects cursive Perso-Arabic / Urdu / Arabic characters using arabic_reshaper
    and corrects Right-to-Left visual orientation using bidi.algorithm.get_display.
    """
    if not text:
        return text
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)


# Backward-compatible alias
reshape_urdu_text = reshape_rtl_text


# Base project directory anchor
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Constant for the Urdu font path routed directly to the existing folder
URDU_FONT_PATH = os.path.join(BASE_DIR, "urdu fonts", "JameelNooriNastaleeq.ttf")


def get_language_from_settings() -> str:
    """Reads the active language state directly from settings.json."""
    settings_file = os.path.join(BASE_DIR, "settings.json")
    if os.path.exists(settings_file):
        try:
            with open(settings_file, "r", encoding="utf-8") as sf:
                data = json.load(sf)
                prof = data.get("active_profile", "Default")
                chan_cfg = data.get("profiles", {}).get(prof, {}) or data.get("channel_profiles", {}).get(prof, {})
                return chan_cfg.get("caption_language") or chan_cfg.get("language") or "English"
        except Exception:
            pass
    return "English"


def get_font_from_settings() -> str:
    """Reads the active caption font setting directly from settings.json."""
    settings_file = os.path.join(BASE_DIR, "settings.json")
    if os.path.exists(settings_file):
        try:
            with open(settings_file, "r", encoding="utf-8") as sf:
                data = json.load(sf)
                prof = data.get("active_profile", "Default")
                chan_cfg = data.get("profiles", {}).get(prof, {}) or data.get("channel_profiles", {}).get(prof, {})
                return chan_cfg.get("caption_font") or "Arial Black"
        except Exception:
            pass
    return "Arial Black"


def resolve_caption_font(caption_font: Optional[str] = None, language: Optional[str] = None) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Resolves caption font input into:
    (font_family_name, fonts_dir_for_ffmpeg, resolved_font_path_for_pil)

    - If a specific file path (.ttf/.otf) is provided or exists, inspects font family via PIL.
    - If Urdu/Arabic is selected with default font or Jameel Noori Nastaleeq, resolves to JameelNooriNastaleeq.ttf.
    - Otherwise, returns standard font family name with system font resolution.
    """
    if not caption_font or str(caption_font).strip() in ("", "Default"):
        caption_font = get_font_from_settings()

    caption_font = str(caption_font).strip()
    is_urdu = str(language or "").strip().lower() in ("ur", "urdu")

    # Check if caption_font contains a file path in parentheses like "Font Name (C:/path/font.ttf)"
    font_path_candidate = caption_font
    if "(" in caption_font and caption_font.endswith(")"):
        extracted = caption_font.split("(", 1)[1].rstrip(")")
        if os.path.exists(extracted):
            font_path_candidate = extracted

    # If it's a file on disk
    if os.path.exists(font_path_candidate) and os.path.isfile(font_path_candidate):
        norm_path = os.path.abspath(font_path_candidate)
        fonts_dir = os.path.dirname(norm_path)
        try:
            pil_f = ImageFont.truetype(norm_path, 40)
            family_name = pil_f.getname()[0]
        except Exception:
            family_name = os.path.splitext(os.path.basename(norm_path))[0]
        return family_name, fonts_dir, norm_path

    # Check if Urdu or Jameel Noori Nastaleeq is requested
    font_lower = caption_font.lower()
    if "jameel" in font_lower or "nastaleeq" in font_lower or (is_urdu and caption_font in ("Arial Black", "Default", "")):
        urdu_path = FFmpegComposer.get_urdu_font_path()
        urdu_dir = FFmpegComposer.get_urdu_fonts_dir()
        return "Jameel Noori Nastaleeq", urdu_dir, urdu_path

    # Standard system font name (e.g. Arial Black, Montserrat, Roboto, Impact, Arial, etc.)
    font_family_name = caption_font
    # Attempt to locate Windows font path for PIL measurements
    win_fonts = os.environ.get("WINDIR", "C:/Windows") + "/Fonts"
    candidates = [
        os.path.join(win_fonts, f"{font_family_name}.ttf"),
        os.path.join(win_fonts, f"{font_family_name}.otf"),
        os.path.join(win_fonts, f"{font_family_name.replace(' ', '')}.ttf"),
        os.path.join(win_fonts, "arialbd.ttf"),
        os.path.join(win_fonts, "arial.ttf")
    ]
    resolved_path = None
    for cand in candidates:
        if os.path.exists(cand):
            resolved_path = cand
            break

    return font_family_name, None, resolved_path

from core.chrome_caption_renderer import (
    generate_caption_html_template,
    render_urdu_captions_playwright,
    composite_captions_moviepy,
    render_and_export_urdu_short,
    is_urdu_language
)


def create_moviepy_subtitle_clip(
    text: str,
    duration: float,
    font: Optional[str] = None,
    fontsize: int = 58,
    color: str = 'white',
    bg_color: Tuple[int, int, int] = (0, 0, 0),
    padding: int = 20,
    opacity: float = 0.6,
    relative_position: Tuple[str, float] = ('center', 0.75),
    language: Optional[str] = None
):
    """
    Generates a dynamic cinematic subtitle clip with a semi-transparent background box.
    - CONDITIONAL PIPELINE ROUTING:
      If language is set to 'Urdu', bypasses MoviePy TextClip and triggers the Headless Chrome
      (Playwright) rendering engine to natively process Right-to-Left (RTL) text and Jameel Noori
      Nastaleeq complex ligatures as transparent PNGs.
      If any other language is selected, seamlessly routes through the standard TextClip engine.
    """
    # MoviePy 1.x and 2.x import bridge
    try:
        from moviepy import ImageClip, TextClip, ColorClip, CompositeVideoClip
    except ImportError:
        try:
            from moviepy.editor import ImageClip, TextClip, ColorClip, CompositeVideoClip
        except ImportError:
            print("[MoviePy Warning] moviepy is not installed in the current environment.")
            return None

    # Patch ImageClip for MoviePy 2.x compatibility if needed
    if not hasattr(ImageClip, "set_position") and hasattr(ImageClip, "with_position"):
        ImageClip.set_position = lambda self, pos, relative=False: self.with_position(pos, relative=relative)
    if not hasattr(ImageClip, "set_start") and hasattr(ImageClip, "with_start"):
        ImageClip.set_start = lambda self, t: self.with_start(t)
    if not hasattr(ImageClip, "set_end") and hasattr(ImageClip, "with_end"):
        ImageClip.set_end = lambda self, t: self.with_end(t)
    if not hasattr(ImageClip, "set_duration") and hasattr(ImageClip, "with_duration"):
        ImageClip.set_duration = lambda self, t: self.with_duration(t)

    target_lang = language or get_language_from_settings()
    is_urdu = is_urdu_language(target_lang)

    # 4. Conditional Pipeline Routing: Headless Chrome for Urdu
    if is_urdu:
        temp_dir = os.path.join(BASE_DIR, "output", "temp_captions")
        caption_items = render_urdu_captions_playwright(
            transcription_segments=[{"text": text, "start": 0.0, "end": duration}],
            output_dir=temp_dir,
            caption_color=color,
            clip_start=0.0
        )
        if caption_items and os.path.exists(caption_items[0]["png_path"]):
            png_path = caption_items[0]["png_path"]
            img_clip = (
                ImageClip(png_path, transparent=True)
                .set_start(0.0)
                .set_duration(duration)
                .set_end(duration)
                .set_position(relative_position, relative=True)
            )
            img_clip.start = 0.0
            img_clip.duration = duration
            img_clip.end = duration
            print(f"[MoviePy Headless Chrome Engine] Rendered Urdu caption via Playwright: '{text}' ({duration}s) placed at {relative_position}")
            return img_clip

    # Standard TextClip rendering for non-Urdu languages
    try:
        txt_clip = TextClip(text, fontsize=fontsize, color=color, font=font)
        if hasattr(txt_clip, "with_duration"):
            txt_clip = txt_clip.with_duration(duration)
        else:
            txt_clip = txt_clip.set_duration(duration)

        txt_w, txt_h = txt_clip.size
        box_w = txt_w + (padding * 2)
        box_h = txt_h + (padding * 2)

        bg_clip = ColorClip(size=(box_w, box_h), color=bg_color)
        if hasattr(bg_clip, "with_opacity"):
            bg_clip = bg_clip.with_opacity(opacity).with_duration(duration)
        else:
            bg_clip = bg_clip.set_opacity(opacity).set_duration(duration)

        combined = CompositeVideoClip(
            [bg_clip, txt_clip.set_position("center")],
            size=(box_w, box_h)
        )
        if hasattr(combined, "with_duration"):
            combined = combined.with_duration(duration).with_position(relative_position, relative=True)
        else:
            combined = combined.set_duration(duration).set_position(relative_position, relative=True)

        return combined
    except Exception as te:
        print(f"[MoviePy TextClip Warning] Standard TextClip rendering note: {te}")
        return None


def create_moviepy_template_clip(
    template_path: Optional[str],
    size: Tuple[int, int] = (1080, 1920),
    duration: Optional[float] = None
):
    """
    Creates a MoviePy ImageClip for branded template overlay with os.path.exists check.
    Returns None safely if template is missing or invalid.
    """
    if not template_path or not os.path.exists(template_path):
        return None
    try:
        try:
            from moviepy import ImageClip
        except ImportError:
            from moviepy.editor import ImageClip
        clip = ImageClip(template_path)
        if hasattr(clip, "resized"):
            clip = clip.resized(size)
        elif hasattr(clip, "resize"):
            clip = clip.resize(size)
        if duration is not None:
            if hasattr(clip, "with_duration"):
                clip = clip.with_duration(duration)
            elif hasattr(clip, "set_duration"):
                clip = clip.set_duration(duration)
        return clip
    except Exception:
        return None


class FFmpegComposer:
    """
    Video Compositing Engine for AMB Enterprise.
    Handles rendering 9:16 vertical shorts (1080x1920) focused on speaking character face,
    generating ASS word-by-word animated subtitles, and rendering final MP4 clips.
    Applies multi-threaded maximum hardware acceleration based on the active hardware profile.
    """

    def __init__(
        self,
        output_width: int = 1080,
        output_height: int = 1920,
        template_path: Optional[str] = "assets/wealth secret template (2).jpg",
        ffmpeg_threads: str = "0",
        ffmpeg_preset: str = "fast",
        ffmpeg_encoder_args: Optional[List[str]] = None,
        logger: Optional[logging.Logger] = None
    ):
        self.output_width = output_width
        self.output_height = output_height
        self.template_path = template_path
        self.ffmpeg_threads = ffmpeg_threads
        self.ffmpeg_preset = ffmpeg_preset
        self.ffmpeg_encoder_args = ffmpeg_encoder_args or ["-c:v", "libx264", "-preset", ffmpeg_preset, "-crf", "18"]
        self.logger = logger or logging.getLogger("AMBEnterprise")
        self._ensure_template_exists()

    @staticmethod
    def get_urdu_font_path() -> str:
        """Returns the absolute path to JameelNooriNastaleeq.ttf."""
        if os.path.exists(URDU_FONT_PATH):
            return URDU_FONT_PATH
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        root_font = os.path.join(project_root, "urdu fonts", "JameelNooriNastaleeq.ttf")
        if os.path.exists(root_font):
            return root_font
        alt = os.path.join(os.path.dirname(URDU_FONT_PATH), "Jameel Noori Nastaleeq Regular.ttf")
        if os.path.exists(alt):
            return alt
        return URDU_FONT_PATH

    @staticmethod
    def get_urdu_fonts_dir() -> str:
        """Returns the absolute path to the urdu fonts directory, ensuring font is available."""
        target_path = FFmpegComposer.get_urdu_font_path()
        urdu_dir = os.path.dirname(target_path)
        os.makedirs(urdu_dir, exist_ok=True)
        if not os.path.exists(target_path):
            zip_path = os.path.join(urdu_dir, "jameel-noori-nastaleeq-regular.zip")
            if os.path.exists(zip_path):
                import zipfile
                try:
                    with zipfile.ZipFile(zip_path, "r") as z:
                        for item in z.namelist():
                            if item.endswith(".ttf"):
                                with open(target_path, "wb") as f_out:
                                    f_out.write(z.read(item))
                                break
                except Exception:
                    pass
        return os.path.abspath(urdu_dir)

    def _ensure_template_exists(self):
        """Generates default 9:16 overlay template PNG (1080x1920) if missing and path provided."""
        if not self.template_path:
            return
        if not os.path.exists(self.template_path):
            dir_name = os.path.dirname(self.template_path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            self.logger.info(f"[Composer] Creating default 9:16 template image ({self.output_width}x{self.output_height}): {self.template_path}")
            try:
                img = Image.new("RGBA", (self.output_width, self.output_height), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)

                # Top branding header accent line
                draw.rectangle([0, 0, self.output_width, 8], fill=(0, 210, 255, 255))
                
                # Save template
                img.save(self.template_path, "PNG")
                self.logger.info("[Composer] Template PNG initialized successfully.")
            except Exception as e:
                self.logger.warning(f"[Composer] Could not initialize fallback template image ({e}).")

    def detect_speaker_face_offset(self, video_path: str, start_sec: float) -> float:
        """
        Attempts to detect speaker face center ratio (0.0 to 1.0) using OpenCV Haar Cascade.
        Falls back to 0.5 (center crop) if face detection is unavailable or no face is found.
        """
        try:
            import cv2
            cap = cv2.VideoCapture(video_path)
            cap.set(cv2.CAP_PROP_POS_MSEC, (start_sec + 2.0) * 1000)
            ret, frame = cap.read()
            cap.release()

            if ret and frame is not None:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                face_cascade = cv2.CascadeClassifier(cascade_path)
                faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))

                if len(faces) > 0:
                    largest_face = max(faces, key=lambda rect: rect[2] * rect[3])
                    x, y, w, h = largest_face
                    face_center_x = x + (w / 2.0)
                    frame_w = frame.shape[1]
                    ratio = face_center_x / frame_w
                    self.logger.info(f"[Composer] Speaker face detected! X-center ratio: {ratio:.2f}")
                    return ratio
        except Exception as e:
            self.logger.debug(f"[Composer] Face detection fallback ({e}). Using center focus.")

        return 0.5

    def format_ass_timestamp(self, seconds: float) -> str:
        """Converts seconds into ASS subtitle timestamp format: H:MM:SS.cs"""
        hrs = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        cs = int(round((seconds - int(seconds)) * 100))
        if cs >= 100:
            secs += 1
            cs = 0
        return f"{hrs}:{mins:02d}:{secs:02d}.{cs:02d}"

    def generate_ass_subtitles(
        self,
        words: List[Dict[str, Any]],
        clip_start: float,
        ass_output_path: str,
        words_per_line: int = 3,
        viewport_y: int = 0,
        viewport_h: Optional[int] = None,
        language: str = "en",
        caption_color: str = "Yellow",
        caption_font: Optional[str] = None
    ) -> str:
        """
        Generates Advanced SubStation Alpha (.ass) file with animated subtitles.
        Coordinates are locked to ('center', 'center') on the canvas (Alignment: 5).
        Solid black outline stroke (thickness >= 2-3px) is applied with ZERO drop shadow.
        Dynamically applies the chosen caption font (e.g. Jameel Noori Nastaleeq, Arial Black, custom TTF).
        When language is Urdu, applies RTL character joining and bidi reordering,
        strictly uses Jameel Noori Nastaleeq or user selected font, and expands line spacing & padding.
        When language is English or other LTR languages, bypasses RTL reshaping.
        """
        out_dir = os.path.dirname(ass_output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        if not language:
            language = get_language_from_settings()

        if not caption_font:
            caption_font = get_font_from_settings()

        font_family, fonts_dir, pil_font_path = resolve_caption_font(caption_font, language)

        is_urdu = str(language).strip().lower() in ("ur", "urdu")
        is_english = str(language).strip().lower() in ("en", "english")
        is_cjk = str(language).strip().lower() in ("zh", "chinese", "ja", "japanese")
        
        # Dynamic Primary Color Mapping (AABBGGRR in ASS)
        color_lower = str(caption_color).strip().lower()
        if "white" in color_lower:
            primary_color = "&H00FFFFFF"
        elif "green" in color_lower:
            primary_color = "&H0014FF39"
        else: # Yellow
            primary_color = "&H0000FFFF"

        outline = 3 # Solid black stroke >= 2-3px
        shadow = 0  # Strictly NO drop shadow

        font_name = font_family
        if is_urdu or "nastaleeq" in font_name.lower():
            # Phase 4 & 5: Strict Nastaleeq typography and expanded bounding box/spacing for Urdu
            font_size = 68
            spacing = 0  # Cursive scripts MUST have spacing=0 to maintain unbroken OpenType ligatures
            bold = 0     # Jameel Noori Nastaleeq is a Regular font; bold=0 prevents font fallback
            scale_y = 100
            margin_l = 60
            margin_r = 60
            margin_v = 40
            effective_wpl = min(words_per_line, 3)
        else:
            # Universal styling for chosen font preset
            font_size = 58
            spacing = 0
            bold = -1
            scale_y = 100
            margin_l = 40
            margin_r = 40
            margin_v = 20
            effective_wpl = words_per_line

        # Cinematic Bounding Box Configuration:
        # BorderStyle: 3 creates an opaque/semi-transparent background box wrapping the text.
        # Outline: 20 creates the exact 20-pixel padding margin on all sides (top, bottom, left, right).
        # BackColour & OutlineColour: &H66000000 set 60% opacity black (in ASS &HAABBGGRR, 0x66 alpha = 102/255 = 40% transparency = 60% opacity).
        # Alignment: 5 (Middle Center anchor)
        # Position locked at ('center', 0.75) -> X = 540, Y = 1440 (relative Y = 0.75 on 1920 canvas)
        box_padding = 20
        box_border_style = 3
        box_color = "&H66000000"
        pos_x = int(self.output_width / 2)
        pos_y = int(self.output_height * 0.75)
        pos_tag = f"{{\\pos({pos_x},{pos_y})}}"

        ass_header = f"""[Script Info]
Title: AMB Enterprise 9:16 Shorts Animated Subtitles
ScriptType: v4.00+
WrapStyle: 0
PlayResX: {self.output_width}
PlayResY: {self.output_height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{font_name},{font_size},{primary_color},&H00FFFFFF,{box_color},{box_color},{bold},0,0,0,100,{scale_y},{spacing},0,{box_border_style},{box_padding},0,5,{margin_l},{margin_r},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        lines_data = []
        current_line = []

        for w in words:
            rel_start = max(0.0, float(w.get("start", 0.0)) - clip_start)
            rel_end = max(rel_start + 0.05, float(w.get("end", 0.0)) - clip_start)
            raw_word = str(w.get("word", "")).strip()
            if not raw_word:
                continue

            # Clean raw word from stray formatting artifacts
            clean_word = raw_word.strip("\"'()[]{}<>~`*")
            if not clean_word:
                clean_word = raw_word

            word_str = clean_word.upper() if is_english else clean_word

            current_line.append({
                "word": word_str,
                "start": rel_start,
                "end": rel_end
            })

            # Check sentence and phrase boundary conditions
            has_major_punct = any(raw_word.endswith(p) for p in [".", "!", "?", "۔", "؟", "。", "！", "？"])
            has_minor_punct = any(raw_word.endswith(p) for p in [",", "،", ";", ":"])

            # Break line if target word count reached, major punctuation hit, or minor punctuation with >= 2 words
            if len(current_line) >= effective_wpl or has_major_punct or (has_minor_punct and len(current_line) >= 2):
                lines_data.append(current_line)
                current_line = []

        if current_line:
            # If trailing line is a single orphan word with short duration, merge into previous line if possible
            if len(current_line) == 1 and lines_data and len(lines_data[-1]) < (effective_wpl + 1):
                lines_data[-1].append(current_line[0])
            else:
                lines_data.append(current_line)

        # Anti-flicker smoothing & overlap prevention between consecutive subtitle lines
        for i in range(len(lines_data) - 1):
            curr_end = lines_data[i][-1]["end"]
            next_start = lines_data[i + 1][0]["start"]
            if curr_end > next_start:
                lines_data[i][-1]["end"] = max(lines_data[i][0]["start"] + 0.1, next_start)
            elif (next_start - curr_end) <= 0.35:
                # Close tiny gaps to eliminate annoying subtitle box flickering between spoken words
                lines_data[i][-1]["end"] = max(curr_end, round(next_start - 0.04, 3))

        # Dynamic bounding box measurement & mathematical proof printout
        try:
            if pil_font_path and os.path.exists(pil_font_path):
                pil_font = ImageFont.truetype(pil_font_path, font_size)
            elif is_urdu:
                pil_font = ImageFont.truetype(self.get_urdu_font_path(), font_size)
            else:
                pil_font = ImageFont.load_default()

            sample_text = ""
            if lines_data:
                sample_words = [item["word"] for item in lines_data[0]]
                sample_text = " ".join(sample_words)

            if sample_text:
                bbox = pil_font.getbbox(sample_text)
                txt_w = bbox[2] - bbox[0]
                diff_h = bbox[3] - bbox[1]
                if diff_h > 0:
                    txt_h = diff_h
                else:
                    try:
                        ascent, descent = pil_font.getmetrics()
                        txt_h = ascent + descent
                    except Exception:
                        txt_h = font_size
            else:
                txt_w, txt_h = 350, font_size

            box_w = txt_w + (box_padding * 2)
            box_h = txt_h + (box_padding * 2)

            print(f"[Cinematic Bounding Box Verification]")
            print(f"  Sample Subtitle Text: '{sample_text}'")
            print(f"  Text Dimensions: Width = {txt_w}px, Height = {txt_h}px")
            print(f"  Background Box Dimensions (+20px padding): Width = {box_w}px, Height = {box_h}px")
            print(f"  Mathematical Proof: Box is larger than text by 20px on all sides: {box_w > txt_w and box_h > txt_h} (W: +{box_w - txt_w}px, H: +{box_h - txt_h}px)")
            print(f"  Alpha Channel (Opacity): 0.6 (60% Opacity Black &H66000000)")
            print(f"  Placement: Locked at ('center', 0.75) -> X: {pos_x}px, Y: {pos_y}px")
        except Exception as e:
            print(f"[Cinematic Bounding Box Warning] Measurement note: {e}")

        dialogue_events = []

        for line in lines_data:
            if not line:
                continue

            line_start = line[0]["start"]
            line_end = max(line_start + 0.2, line[-1]["end"])

            line_start_str = self.format_ass_timestamp(line_start)
            line_end_str = self.format_ass_timestamp(line_end)

            if is_urdu:
                # libass uses HarfBuzz + FriBidi natively for OpenType fonts (Jameel Noori Nastaleeq).
                # Passing standard raw Unicode allows HarfBuzz to execute Jameel Noori's native
                # OpenType GSUB tables without missing glyph errors (which previously triggered ArialMT fallback).
                raw_line = " ".join(item["word"] for item in line)
                dialogue = f"Dialogue: 0,{line_start_str},{line_end_str},Karaoke,,0,0,0,,{pos_tag}{raw_line}"
            elif is_english:
                # English raw text with smooth progressive karaoke fill (\kf)
                karaoke_text = ""
                for idx, item in enumerate(line):
                    if idx < len(line) - 1:
                        dur_sec = max(0.05, line[idx + 1]["start"] - item["start"])
                    else:
                        dur_sec = max(0.05, item["end"] - item["start"])
                    dur_cs = max(1, int(round(dur_sec * 100)))
                    w_str = item["word"]
                    karaoke_text += "{\\kf" + str(dur_cs) + "}" + w_str + " "
                dialogue = f"Dialogue: 0,{line_start_str},{line_end_str},Karaoke,,0,0,0,,{pos_tag}{karaoke_text.strip()}"
            else:
                # For all else conditions: pass raw text and default font directly to clip generator
                joiner = "" if is_cjk else " "
                display_line = joiner.join(item["word"] for item in line)
                dialogue = f"Dialogue: 0,{line_start_str},{line_end_str},Karaoke,,0,0,0,,{pos_tag}{display_line}"

            dialogue_events.append(dialogue)

        full_ass_content = ass_header + "\n".join(dialogue_events) + "\n"

        with open(ass_output_path, "w", encoding="utf-8") as f:
            f.write(full_ass_content)

        self.logger.info(f"[Composer] Generated ASS Subtitle file: {ass_output_path} (Lang: {language}, Font: {font_name}, MarginV: {margin_v})")
        print(f"[Composer ASS Generation] File: {ass_output_path} | Font: '{font_name}' | Language: '{language}' | Total Dialogue Lines: {len(dialogue_events)}")
        if dialogue_events:
            print(f"[Composer ASS Sample] First line: {dialogue_events[0]}")
            print(f"[Composer ASS Sample] Last line: {dialogue_events[-1]}")
        else:
            print(f"[Composer ASS WARNING] Zero dialogue events were generated! Check words input.")
        return ass_output_path

    def detect_template_viewport(self, template_path: Optional[str]) -> Tuple[int, int, int, int, Optional[str]]:
        """
        Detects the video viewing window (vx, vy, vw, vh) within the template.
        If the template has a solid blue/placeholder center (like the user's template),
        it detects the blue rectangle, creates a transparent cutout for FFmpeg overlay,
        and returns the coordinates and the transparent overlay path.
        If template is missing or None, returns full dimensions and None.
        """
        if not template_path or not os.path.exists(template_path):
            return 0, 0, self.output_width, self.output_height, None

        try:
            img = Image.open(template_path).convert("RGBA")
            w, h = img.size
            scale_y = self.output_height / h
            scale_x = self.output_width / w

            center_x = w // 2

            # 1. Check if there's already an alpha transparency window in center vertical column
            alphas = [img.getpixel((center_x, y))[3] for y in range(h)]
            trans_y = [y for y, a in enumerate(alphas) if a < 50]
            if trans_y and (max(trans_y) - min(trans_y) > h * 0.2):
                vy_raw = min(trans_y)
                vh_raw = max(trans_y) - vy_raw
                vy = int(vy_raw * scale_y)
                vh = int(vh_raw * scale_y)
                self.logger.info(f"[Composer] Detected existing transparent template viewport: Y={vy} to {vy + vh} (Height: {vh}px).")
                return 0, vy, self.output_width, vh, template_path

            # 2. Check for solid blue/colored placeholder area in the middle (user's template)
            center_pixels = [img.getpixel((center_x, y)) for y in range(h)]
            blue_y = []
            for y, (r, g, b, *_) in enumerate(center_pixels):
                # Blue dominant condition (RGB: B > R + 15 or high B with low R)
                if (b > r + 15) or (b > 45 and r < 60 and g < 75):
                    blue_y.append(y)

            if blue_y and (max(blue_y) - min(blue_y) > h * 0.25):
                vy_raw = min(blue_y)
                bottom_raw = max(blue_y)
                vh_raw = bottom_raw - vy_raw

                # Create a processed template where the blue rectangle is transparent
                processed_dir = os.path.join(os.path.dirname(os.path.abspath(template_path)), "processed")
                os.makedirs(processed_dir, exist_ok=True)
                base_name = os.path.splitext(os.path.basename(template_path))[0]
                processed_tpl_path = os.path.join(processed_dir, f"{base_name}_overlay.png")

                # Scale template to output width/height
                scaled_img = img.resize((self.output_width, self.output_height), Image.Resampling.LANCZOS)
                vy = int(vy_raw * scale_y)
                vh = int(vh_raw * scale_y)

                # Cut out the blue window by pasting fully transparent block
                transparent_box = Image.new("RGBA", (self.output_width, vh), (0, 0, 0, 0))
                scaled_img.paste(transparent_box, (0, vy))
                scaled_img.save(processed_tpl_path, "PNG")

                self.logger.info(f"[Composer] Detected blue template viewport: Y={vy} to {vy + vh} (Height: {vh}px). Generated transparent overlay: {processed_tpl_path}")
                return 0, vy, self.output_width, vh, processed_tpl_path

        except Exception as e:
            self.logger.warning(f"[Composer] Viewport detection note ({e}), using default full-screen.")

        return 0, 0, self.output_width, self.output_height, template_path if (template_path and os.path.exists(template_path)) else None

    def render_short_clip(
        self,
        source_video_path: str,
        clip_data: Dict[str, Any],
        output_mp4_path: str,
        enable_captions: bool = True,
        enable_face_tracking: bool = True,
        is_pre_cut: bool = False,
        language: Optional[str] = None,
        caption_color: Optional[str] = None,
        caption_font: Optional[str] = None
    ) -> str:
        """
        Executes multi-threaded FFmpeg pipeline to crop video centered on speaker face,
        scale and fit video directly into the template's viewing viewport (blue area),
        overlay the branded template (header + footer), and optionally burn animated ASS subtitles.
        When is_pre_cut is True, the input is a partial download segment starting at 0.0s.
        """
        os.makedirs(os.path.dirname(output_mp4_path), exist_ok=True)

        if not language:
            language = get_language_from_settings()
        if not caption_font:
            caption_font = get_font_from_settings()

        is_urdu = is_urdu_language(language)
        route_to_chrome_moviepy = bool(enable_captions and is_urdu)
        urdu_marker = output_mp4_path + ".urdu_captioned"

        if os.path.exists(output_mp4_path) and os.path.getsize(output_mp4_path) > 10000:
            if route_to_chrome_moviepy:
                if os.path.exists(urdu_marker):
                    self.logger.info(f"[Smart Resume] Fully composited Urdu Short already exists ({output_mp4_path}). Skipping render.")
                    return output_mp4_path
                else:
                    self.logger.info(f"[Composer] Found video at {output_mp4_path} without Urdu captions marker. Proceeding to composite captions...")
            else:
                self.logger.info(f"[Smart Resume] Rendered clip already exists ({output_mp4_path}). Skipping FFmpeg render.")
                return output_mp4_path

        start_sec = clip_data["start_time"]
        duration_sec = clip_data["duration"]
        aligned_words = clip_data.get("aligned_words", [])

        # If source video was already cut to the exact clip window, seek from 0
        seek_start = 0.0 if is_pre_cut else start_sec

        # Detect face X-center ratio (0.0 to 1.0)
        face_probe_time = 0.0 if is_pre_cut else start_sec
        face_x_ratio = self.detect_speaker_face_offset(source_video_path, face_probe_time) if enable_face_tracking else 0.5

        # Detect viewport in template (blue zone or transparent box)
        vx, vy, vw, vh, overlay_template_path = self.detect_template_viewport(self.template_path)
        has_template = bool(overlay_template_path and os.path.exists(overlay_template_path))
        escaped_template_path = overlay_template_path.replace("\\", "/") if has_template else ""

        target_in = "[with_template]" if has_template else "[cropped]"
        sub_filter = ""

        if enable_captions:
            # Timestamp Normalization Safeguard:
            # If is_pre_cut is True, all caption timestamps must be in [0.0, duration_sec].
            # Check if words need offset subtraction (e.g. if still unoffset with start >= start_sec - 2.0).
            normalized_words = []
            if aligned_words:
                first_word_s = float(aligned_words[0].get("start", 0.0))
                needs_offset = is_pre_cut and (first_word_s >= duration_sec or (start_sec > 2.0 and first_word_s >= (start_sec - 2.0)))
                offset_val = start_sec if needs_offset else 0.0

                for w in aligned_words:
                    w_s = max(0.0, round(float(w.get("start", 0.0)) - offset_val, 3))
                    w_e = max(w_s + 0.05, round(float(w.get("end", 0.0)) - offset_val, 3))
                    if w_s <= (duration_sec + 0.5):
                        normalized_words.append({
                            "word": w.get("word", ""),
                            "start": w_s,
                            "end": min(round(duration_sec, 3), w_e)
                        })
                aligned_words = normalized_words

            sub_clip_start = 0.0 if is_pre_cut else start_sec

            print(f"[Composer Subtitle Mapping] Clip duration: {duration_sec:.1f}s | Words received: {len(aligned_words)} | sub_clip_start: {sub_clip_start:.2f}s | Language: '{language}'")

            # 4. Conditional Pipeline Routing
            if is_urdu:
                # Route Urdu text to Headless Chrome (Playwright) + MoviePy Compositing
                route_to_chrome_moviepy = True
                self.logger.info("[Composer] 🌐 Language is 'Urdu': Routing captions to Headless Chrome (Playwright) + MoviePy engine.")
            else:
                # Standard Subtitle Pipeline for non-Urdu languages
                ass_path = output_mp4_path.replace(".mp4", "_sub.ass")
                font_family_name, fonts_dir, resolved_font_path = resolve_caption_font(caption_font, language)
                print(f"[Composer Font Resolution] Language: '{language}' | Font: '{caption_font}' -> Family: '{font_family_name}' | Path: {resolved_font_path} | fontsdir: {fonts_dir}")

                self.generate_ass_subtitles(
                    words=aligned_words,
                    clip_start=sub_clip_start,
                    ass_output_path=ass_path,
                    viewport_y=vy,
                    viewport_h=vh,
                    language=language,
                    caption_color=caption_color,
                    caption_font=caption_font
                )
                escaped_ass_path = ass_path.replace("\\", "/").replace(":", "\\:")
                if fonts_dir and os.path.exists(fonts_dir):
                    font_dir_escaped = os.path.abspath(fonts_dir).replace("\\", "/").replace(":", "\\:")
                    sub_filter = f";{target_in}subtitles='{escaped_ass_path}':fontsdir='{font_dir_escaped}'[outv]"
                else:
                    sub_filter = f";{target_in}subtitles='{escaped_ass_path}'[outv]"
        else:
            sub_filter = f";{target_in}copy[outv]"

        self.logger.info(f"[Composer] Multi-thread rendering Short (Viewport: {vw}x{vh}, HasTemplate: {has_template}, Face-Track: {enable_face_tracking}, Captions: {enable_captions}, Pre-cut: {is_pre_cut}, Lang: {language})...")

        # Build FFmpeg filter complex:
        if has_template:
            if vy == 0 and vh == self.output_height:
                filter_complex = (
                    f"[0:v]crop=w=ih*9/16:h=ih:x='min(max(0, {face_x_ratio:.3f}*iw-ow/2), iw-ow)':y=0,"
                    f"scale={self.output_width}:{self.output_height}[cropped];"
                    f"[cropped][1:v]overlay=0:0[with_template]"
                )
            else:
                filter_complex = (
                    f"[0:v]crop=w='min(iw, ih*{vw}/{vh})':h='min(ih, iw*{vh}/{vw})':"
                    f"x='min(max(0, {face_x_ratio:.3f}*iw-ow/2), iw-ow)':y=0,"
                    f"scale={vw}:{vh}[video_scaled];"
                    f"color=c=black:s={self.output_width}x{self.output_height}:d={duration_sec}[canvas];"
                    f"[canvas][video_scaled]overlay={vx}:{vy}[canvas_with_video];"
                    f"[canvas_with_video][1:v]overlay=0:0[with_template]"
                )
        else:
            filter_complex = (
                f"[0:v]crop=w=ih*9/16:h=ih:x='min(max(0, {face_x_ratio:.3f}*iw-ow/2), iw-ow)':y=0,"
                f"scale={self.output_width}:{self.output_height}[cropped]"
            )

        if enable_captions and not route_to_chrome_moviepy:
            filter_complex += sub_filter
        else:
            filter_complex += f";{target_in}copy[outv]"

        # Determine actual destination for FFmpeg render:
        # If routing to Chrome MoviePy, output FFmpeg base to a temp path so MoviePy can read it
        # and write directly to output_mp4_path without Windows file lock collisions.
        temp_base_mp4 = output_mp4_path.replace(".mp4", "_temp_base_framed.mp4") if route_to_chrome_moviepy else output_mp4_path

        skip_ffmpeg = False
        if route_to_chrome_moviepy:
            if os.path.exists(temp_base_mp4) and os.path.getsize(temp_base_mp4) > 10000:
                skip_ffmpeg = True
                self.logger.info(f"[Composer] Framed base video already exists ({temp_base_mp4}). Skipping FFmpeg pre-render.")
            elif os.path.exists(output_mp4_path) and not os.path.exists(source_video_path) and not os.path.exists(urdu_marker):
                os.replace(output_mp4_path, temp_base_mp4)
                skip_ffmpeg = True
                self.logger.info(f"[Composer] Repurposing existing base video ({output_mp4_path} -> {temp_base_mp4}) for MoviePy compositing.")

        if not skip_ffmpeg:
            ffmpeg_cmd = [
                "ffmpeg",
                "-y",
                "-threads", str(self.ffmpeg_threads),
                "-ss", str(seek_start),
                "-i", source_video_path,
            ]
            if has_template:
                ffmpeg_cmd.extend(["-i", escaped_template_path])
            ffmpeg_cmd.extend([
                "-t", str(duration_sec),
                "-filter_complex", filter_complex,
                "-map", "[outv]",
                "-map", "0:a?"
            ] + self.ffmpeg_encoder_args + [
                "-c:a", "aac",
                "-b:a", "192k",
                temp_base_mp4
            ])

            self.logger.info(f"[Composer] Executing multi-threaded FFmpeg rendering command...")

            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            stdout, stderr = process.communicate()

            if process.returncode != 0:
                self.logger.error(f"[Composer] FFmpeg execution error:\n{stderr}")
                raise RuntimeError(f"FFmpeg failed with exit code {process.returncode}")

        # 3 & 4. MoviePy Compositing for Headless Chrome Urdu Captions
        if route_to_chrome_moviepy:
            self.logger.info(f"[Composer] [Step 1] Triggering ChromeCaptionRenderer for Urdu subtitles...")
            temp_captions_dir = os.path.join(BASE_DIR, "output", "temp_captions")
            if os.path.exists(temp_captions_dir):
                shutil.rmtree(temp_captions_dir, ignore_errors=True)
            os.makedirs(temp_captions_dir, exist_ok=True)
            caption_items = []
            try:
                if aligned_words:
                    caption_items = render_urdu_captions_playwright(
                        transcription_segments=aligned_words,
                        output_dir=temp_captions_dir,
                        caption_color=caption_color or "Yellow",
                        clip_start=sub_clip_start,
                        word_highlight=True,
                        font_size=80,
                        logger=self.logger
                    )
            except Exception as e:
                self.logger.warning(f"[Composer] Playwright caption generation note: {e}")

            if caption_items and os.path.exists(temp_base_mp4):
                try:
                    try:
                        from moviepy import VideoFileClip
                    except ImportError:
                        from moviepy.editor import VideoFileClip

                    self.logger.info(f"[Composer] [Step 2 & 3] Loading {len(caption_items)} PNGs as ImageClips and compositing onto base video...")
                    base_video = VideoFileClip(temp_base_mp4)
                    final_video = composite_captions_moviepy(base_video, caption_items, relative_position=("center", 0.75))

                    self.logger.info(f"[Composer] [Step 4] Exporting final stitched .mp4 to {output_mp4_path}...")
                    final_video.write_videofile(
                        output_mp4_path,
                        fps=base_video.fps or 24,
                        codec="libx264",
                        audio_codec="aac",
                        logger=None
                    )
                    base_video.close()
                    final_video.close()

                    # Write urdu_marker
                    try:
                        with open(urdu_marker, "w", encoding="utf-8") as mf:
                            mf.write("done")
                    except Exception:
                        pass

                    self.logger.info(f"[Composer] Headless Chrome + MoviePy Urdu Short render complete! Saved to: {output_mp4_path}")

                    # Clean up temporary framed video
                    try:
                        if os.path.exists(temp_base_mp4):
                            os.remove(temp_base_mp4)
                    except Exception:
                        pass
                except Exception as e:
                    self.logger.error(f"[Composer] Chrome+MoviePy Urdu compositing note ({e}), base video preserved.")
                    if os.path.exists(temp_base_mp4) and not os.path.exists(output_mp4_path):
                        os.replace(temp_base_mp4, output_mp4_path)
            else:
                if os.path.exists(temp_base_mp4):
                    if os.path.exists(output_mp4_path):
                        os.remove(output_mp4_path)
                    os.replace(temp_base_mp4, output_mp4_path)

        self.logger.info(f"[Composer] 9:16 Short render successful! Saved to: {output_mp4_path}")
        return output_mp4_path
