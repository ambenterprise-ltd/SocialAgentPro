import sys
import os
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.ingestion import MediaIngestionEngine
from config import ConfigManager

cm = ConfigManager()
prof = cm._config["profiles"]["Wealth Secrets"]
ing = MediaIngestionEngine()

print("Auto search keywords:", prof["auto_search_keywords"])
print("Negative filters:", prof["negative_filters"])

results = ing.search_youtube_topic_podcasts(
    search_queries=prof["auto_search_keywords"][:2],
    limit=5,
    negative_filters=prof["negative_filters"],
    min_duration=300,
    target_count=5,
    language="en"
)

print(f"\nDiscovered {len(results)} candidate videos:")
for r in results:
    print(f" - [{r['duration']}s] {r['title']} ({r['id']})")
