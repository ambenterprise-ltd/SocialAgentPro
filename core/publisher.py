import os
import json
import logging
from typing import Dict, Any, List, Optional
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class YouTubePublisher:
    """
    Publishing Engine for AMB Enterprise using YouTube Data API v3.
    Handles OAuth2 authentication and automated video upload to YouTube.

    After a confirmed successful upload, the pipeline deletes the local file
    immediately to free disk space (especially important on EC2).
    """

    PLATFORM = "youtube"

    def __init__(self, client_secrets_file: str, logger: Optional[logging.Logger] = None):
        self.client_secrets_file = client_secrets_file
        self.logger = logger or logging.getLogger("AMBEnterprise")
        self.credentials = None
        self.youtube_service = None

    def authenticate(self, token_cache_path: str = "token.json") -> bool:
        """Authenticates with YouTube API using local client secrets and token cache."""
        if not self.client_secrets_file or not os.path.exists(self.client_secrets_file):
            self.logger.warning(
                "[Publisher] OAuth client_secret.json missing or invalid path: '%s'",
                self.client_secrets_file
            )
            return False

        try:
            if os.path.exists(token_cache_path):
                self.credentials = Credentials.from_authorized_user_file(token_cache_path, SCOPES)

            if not self.credentials or not self.credentials.valid:
                if self.credentials and self.credentials.expired and self.credentials.refresh_token:
                    self.logger.info("[Publisher] Refreshing expired YouTube OAuth credentials...")
                    self.credentials.refresh(Request())
                else:
                    self.logger.info("[Publisher] Prompting OAuth browser login for YouTube API...")
                    flow = InstalledAppFlow.from_client_secrets_file(self.client_secrets_file, SCOPES)
                    self.credentials = flow.run_local_server(port=0)

                with open(token_cache_path, "w") as token_file:
                    token_file.write(self.credentials.to_json())

            self.youtube_service = build("youtube", "v3", credentials=self.credentials)
            self.logger.info("[Publisher] YouTube API authentication successful.")
            return True

        except Exception as e:
            self.logger.error("[Publisher] YouTube Authentication failed: %s", e)
            return False

    def upload_short(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: Optional[List[str]] = None,
        privacy_status: str = "private"
    ) -> Dict[str, Any]:
        """
        Uploads generated video to YouTube.

        Returns a dict with keys: id, url, title, status, platform.
        Raises RuntimeError on authentication or upload failure so the caller
        can safely avoid deleting the local file on failure.
        """
        if not self.youtube_service:
            if not self.authenticate():
                raise RuntimeError(
                    "[Publisher] YouTube API authentication failed. Cannot upload. "
                    "Check your OAuth credentials in Admin Settings."
                )

        if not os.path.exists(video_path):
            raise RuntimeError(
                f"[Publisher] Video file not found: '{video_path}'. Cannot upload."
            )

        default_tags = ["Finance", "Wealth", "Money", "Podcast", "Business"]
        if tags:
            default_tags.extend(tags)

        body = {
            "snippet": {
                "title": title[:100],
                "description": f"{description}\n\nGenerated with AMB Enterprise Content Engine",
                "tags": list(set(default_tags)),
                "categoryId": "22",  # People & Blogs / Business
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            }
        }

        self.logger.info("[Publisher] Uploading '%s' (%s) to YouTube...", title, privacy_status)
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")

        request = self.youtube_service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                self.logger.info("[Publisher] Upload progress: %d%%", int(status.progress() * 100))

        video_id = response.get("id")
        if not video_id:
            raise RuntimeError(
                "[Publisher] YouTube upload completed but no video ID returned in response. "
                "The upload may have failed silently."
            )

        video_url = f"https://www.youtube.com/watch?v={video_id}"
        self.logger.info("[Publisher] Video uploaded successfully! URL: %s", video_url)

        return {
            "id": video_id,
            "url": video_url,
            "title": title,
            "status": privacy_status,
            "platform": self.PLATFORM,
        }
