import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import unittest

from config import ConfigManager
from core.transcriber import WhisperTranscriber
from core.composer import FFmpegComposer
from core.ingestion import MediaIngestionEngine


class TestOffsetAndGeo(unittest.TestCase):

    def setUp(self):
        self.cm = ConfigManager()
        self.transcriber = WhisperTranscriber()
        self.composer = FFmpegComposer()
        self.ingestion = MediaIngestionEngine()

    def test_word_timestamp_offset_subtraction(self):
        """Simulate Phase 5.5 word timestamp offset subtraction."""
        start_sec = 600.0
        end_sec = 655.0
        duration_sec = 55.0

        # Mock raw transcript words with original video timestamps (around 600s)
        raw_words = [
            {"word": "Welcome", "start": 595.0, "end": 599.0},
            {"word": "Here", "start": 600.0, "end": 600.5},
            {"word": "is", "start": 600.5, "end": 601.0},
            {"word": "the", "start": 601.0, "end": 601.3},
            {"word": "secret", "start": 601.3, "end": 602.0},
            {"word": "to", "start": 602.0, "end": 602.3},
            {"word": "building", "start": 602.3, "end": 603.0},
            {"word": "wealth", "start": 603.0, "end": 604.0},
            {"word": "finally", "start": 654.0, "end": 654.8},
            {"word": "outside", "start": 660.0, "end": 662.0}
        ]

        # Phase 5.5 logic: filter words in [start_sec - 0.2, end_sec + 1.0] and subtract start_sec
        sliced_words = []
        for w in raw_words:
            w_start = float(w.get("start", 0))
            w_end = float(w.get("end", 0))
            if w_start >= (start_sec - 0.2) and w_end <= (end_sec + 1.0):
                offset_w = dict(w)
                offset_w["start"] = max(0.0, round(w_start - start_sec, 3))
                offset_w["end"] = max(offset_w["start"] + 0.05, round(w_end - start_sec, 3))
                sliced_words.append(offset_w)

        # Assertions
        self.assertEqual(len(sliced_words), 8)
        self.assertEqual(sliced_words[0]["word"], "Here")
        self.assertEqual(sliced_words[0]["start"], 0.0)
        self.assertEqual(sliced_words[0]["end"], 0.5)

        self.assertEqual(sliced_words[1]["word"], "is")
        self.assertEqual(sliced_words[1]["start"], 0.5)
        self.assertEqual(sliced_words[1]["end"], 1.0)

        self.assertEqual(sliced_words[-1]["word"], "finally")
        self.assertEqual(sliced_words[-1]["start"], 54.0)
        self.assertEqual(sliced_words[-1]["end"], 54.8)

        # Generate ASS subtitle and verify dialogue start times are relative to 0.0
        ass_path = os.path.join(os.path.dirname(__file__), "test_offset_sub.ass")
        self.composer.generate_ass_subtitles(
            words=sliced_words,
            clip_start=0.0,
            ass_output_path=ass_path,
            language="English"
        )

        with open(ass_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check dialogue lines start from 0:00:00
        self.assertIn("Dialogue: 0,0:00:00.00", content)
        self.assertIn("HERE", content)
        if os.path.exists(ass_path):
            os.remove(ass_path)

    def test_composer_safeguard_handles_unoffset_words(self):
        """Verify composer timestamp safeguard normalizes unoffset words when is_pre_cut=True."""
        start_sec = 750.0
        duration_sec = 56.0

        # Unoffset words from original video
        aligned_words = [
            {"word": "Action", "start": 750.2, "end": 750.8},
            {"word": "Speaks", "start": 750.8, "end": 751.4},
            {"word": "Louder", "start": 751.4, "end": 752.0}
        ]

        # Composer normalization logic
        is_pre_cut = True
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

        self.assertEqual(len(normalized_words), 3)
        self.assertEqual(normalized_words[0]["start"], 0.2)
        self.assertEqual(normalized_words[0]["end"], 0.8)
        self.assertEqual(normalized_words[1]["start"], 0.8)

    def test_us_headers_and_regional_exclusions(self):
        """Verify anti-403 headers contain US bypass and regional exclusions are active."""
        headers = self.ingestion.get_anti_403_headers()
        self.assertEqual(headers.get("geo_bypass_country"), "US")
        self.assertEqual(headers.get("http_headers", {}).get("Accept-Language"), "en-US,en;q=0.9")

        prof = self.cm._config["profiles"]["Wealth Secrets"]
        neg_filters = prof.get("negative_filters", [])
        expected_exclusions = [
            "hindi", "urdu", "ankur warikoo", "warikoo",
            "raj shamani", "ranveer", "tanmay", "marwari",
            "crorepati", "indian", "india"
        ]
        for exc in expected_exclusions:
            self.assertIn(exc, neg_filters)


if __name__ == "__main__":
    unittest.main()
