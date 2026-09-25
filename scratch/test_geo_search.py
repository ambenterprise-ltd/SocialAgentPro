import yt_dlp

opts = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": True,
    "nocheckcertificate": True,
    "geo_bypass": True,
    "geo_bypass_country": "US",
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
}

query = "wealth, business secrets, and money concepts American podcast interview US"
with yt_dlp.YoutubeDL(opts) as ydl:
    res = ydl.extract_info(f"ytsearch10:{query}", download=False)
    entries = res.get("entries", [])
    print(f"Results for query: '{query}' ({len(entries)} items):")
    for e in entries:
        print(f" - [{e.get('duration', 0)}s] {e.get('title')} ({e.get('id')})")
