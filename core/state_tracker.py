import json
import os
import time
import threading
import logging
from typing import Dict, Any, List, Optional, Set


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "processed_videos.json")


class VideoStateTracker:
    """
    Persistent state manager for tracking processed videos and failed transcripts.
    Ensures zero duplicate videos are processed and permanently skips videos without transcripts.
    """

    def __init__(self, db_path: Optional[str] = None, logger: Optional[logging.Logger] = None):
        if not db_path:
            self.db_path = DEFAULT_DB_PATH
        elif not os.path.isabs(db_path):
            self.db_path = os.path.join(BASE_DIR, db_path)
        else:
            self.db_path = db_path
        self.logger = logger or logging.getLogger("AMBEnterprise")
        self._data = self._load()

    def _load(self) -> Dict[str, Any]:
        """Loads state from JSON file with safe defaults."""
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        data.setdefault("processed", [])
                        data.setdefault("failed", {})
                        return data
            except Exception as e:
                self.logger.warning(f"[StateTracker] Error loading {self.db_path} ({e}), initializing fresh state.")

        return {
            "processed": [],
            "failed": {}
        }

    def _save(self) -> None:
        """Persists state atomically to disk with Windows file-lock resilience."""
        pid = os.getpid()
        tid = threading.get_ident() if hasattr(threading, "get_ident") else 0
        ts = int(time.time() * 1000)
        temp_path = f"{self.db_path}.{pid}_{tid}_{ts}.tmp"

        for attempt in range(3):
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, indent=2, ensure_ascii=False)
                
                # Atomic replace on Windows
                os.replace(temp_path, self.db_path)
                return
            except PermissionError:
                time.sleep(0.15)
            except Exception as e:
                self.logger.warning(f"[StateTracker] Attempt {attempt + 1} failed to save {self.db_path}: {e}")
                time.sleep(0.15)
            finally:
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass

        # Final direct fallback if atomic replace was blocked by external process
        try:
            with open(self.db_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"[StateTracker] Failed to save {self.db_path}: {e}")

    def get_processed_ids(self) -> Set[str]:
        """Returns set of all processed video IDs."""
        return {item["video_id"] if isinstance(item, dict) else str(item) for item in self._data.get("processed", [])}

    def get_failed_ids(self) -> Set[str]:
        """Returns set of all failed/skipped video IDs."""
        return set(self._data.get("failed", {}).keys())

    def is_processed(self, video_id: str) -> bool:
        """Returns True if the video has already been processed or failed."""
        vid = str(video_id).strip()
        return (vid in self.get_processed_ids()) or (vid in self.get_failed_ids())

    def mark_processed(self, video_id: str, title: str = "", metadata: Optional[Dict[str, Any]] = None) -> None:
        """Records a successfully completed video."""
        vid = str(video_id).strip()
        if not vid:
            return

        record = {
            "video_id": vid,
            "title": title,
            "processed_at": time.time(),
            "metadata": metadata or {}
        }

        # Remove from failed if previously marked
        if vid in self._data.get("failed", {}):
            del self._data["failed"][vid]

        # Prevent duplicate in processed list
        existing = [item for item in self._data.get("processed", []) if (item.get("video_id") if isinstance(item, dict) else item) != vid]
        existing.append(record)
        self._data["processed"] = existing
        self._save()
        self.logger.info(f"[StateTracker] Marked video '{vid}' as successfully processed.")

    def mark_failed(self, video_id: str, reason: str = "failed_no_transcript") -> None:
        """Records a video that failed permanently (e.g. no transcript available)."""
        vid = str(video_id).strip()
        if not vid:
            return

        self._data.setdefault("failed", {})[vid] = {
            "reason": reason,
            "failed_at": time.time()
        }
        self._save()
        self.logger.info(f"[StateTracker] Marked video '{vid}' as skipped ({reason}).")

    def filter_unprocessed(self, video_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filters a list of candidate video dictionaries, returning only unprocessed ones."""
        processed_set = self.get_processed_ids() | self.get_failed_ids()
        unprocessed = []
        for v in video_entries:
            vid = v.get("id") or v.get("video_id")
            if vid and str(vid).strip() not in processed_set:
                unprocessed.append(v)
        return unprocessed
