import json
from youtube_transcript_api import YouTubeTranscriptApi

ytt = YouTubeTranscriptApi()
raw = ytt.list("p61_lIHiqLg").find_transcript(["en"]).fetch()

def interpolate(text, start, end):
    words = [w for w in text.split(" ") if w.strip()]
    if not words: return []
    w_dur = (end - start) / len(words)
    return [{"word": w, "start": round(start + i * w_dur, 3), "end": round(start + (i + 1) * w_dur, 3)} for i, w in enumerate(words)]

words_list = []
for i, entry in enumerate(raw):
    start = float(entry.start)
    dur = float(entry.duration)
    text = entry.text.replace("\n", " ").strip()
    if not text: continue
    if i + 1 < len(raw):
        next_start = float(raw[i+1].start)
        if next_start > start:
            end = min(start + dur, next_start)
        else:
            end = start + dur
    else:
        end = start + dur
    words_list.extend(interpolate(text, start, end))

sample = [w for w in words_list if 213 <= w["start"] <= 223]
for w in sample:
    print(f"[{w['start']:6.2f} - {w['end']:6.2f}] {w['word']}")
