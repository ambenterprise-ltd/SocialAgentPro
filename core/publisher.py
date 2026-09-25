import os
import json
import time
import logging
import requests
from typing import Dict, Any, List, Optional, Tuple, Callable
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

    @staticmethod
    def build_video_metadata(
        title: str,
        hook: str = "",
        rationale: str = "",
        channel_name: str = "",
        hashtags: Optional[List[str]] = None
    ) -> Tuple[str, str, List[str]]:
        """
        Builds optimized title, rich description with targeted viral hashtags, and tags for YouTube Shorts.
        Completely eliminates external credit branding.
        """
        niche_tags_map = {
            "Wealth Secrets": [
                "#shorts", "#wealth", "#money", "#mindset", "#billionaire",
                "#business", "#success", "#investing", "#financialfreedom", "#podcast"
            ],
            "Khao Pakistan": [
                "#shorts", "#pakistanifood", "#streetfood", "#foodvlog", "#desi",
                "#foodie", "#delicious", "#foodreview", "#khaopakistan"
            ],
            "Nutrilogic Way": [
                "#shorts", "#gym", "#bodybuilding", "#fitness", "#workout",
                "#motivation", "#supplements", "#creatine", "#preworkout", "#gains"
            ]
        }

        # Resolve channel-specific base hashtags
        base_hashtags = []
        c_lower = str(channel_name).lower()
        if "wealth" in c_lower or "business" in c_lower or "money" in c_lower:
            base_hashtags = list(niche_tags_map["Wealth Secrets"])
        elif "khao" in c_lower or "food" in c_lower:
            base_hashtags = list(niche_tags_map["Khao Pakistan"])
        elif "nutri" in c_lower or "gym" in c_lower or "health" in c_lower:
            base_hashtags = list(niche_tags_map["Nutrilogic Way"])
        else:
            base_hashtags = ["#shorts", "#viral", "#trending", "#podcast", "#insights", "#motivation"]

        # Merge with custom hashtags from LLM or caller
        merged_hashtags: List[str] = []
        seen = set()
        all_candidates = (hashtags or []) + base_hashtags
        for tag in all_candidates:
            clean_tag = str(tag).strip()
            if not clean_tag:
                continue
            if not clean_tag.startswith("#"):
                clean_tag = f"#{clean_tag}"
            if clean_tag.lower() not in seen:
                seen.add(clean_tag.lower())
                merged_hashtags.append(clean_tag)

        # 1. Format Title: append #shorts if room permits (< 90 chars)
        clean_title = (title or "Viral Short").strip()
        if "#shorts" not in clean_title.lower() and len(clean_title) <= 90:
            final_title = f"{clean_title} #shorts"
        else:
            final_title = clean_title[:100]

        # 2. Format Description: clean synopsis + CTA + hashtags (Zero AMB credit!)
        desc_text = (hook or rationale or clean_title).strip()
        # Clean up any trailing cutoffs or incomplete sentences
        if desc_text.endswith((" to", " the", " a", " an", " of", " in", " on", " with", " s")):
            desc_text = desc_text.rsplit(" ", 1)[0] + "..."

        hashtag_block = " ".join(merged_hashtags[:8])
        final_desc = f"{desc_text}\n\nSubscribe for more daily insights!\n\n{hashtag_block}".strip()

        # 3. Format SEO Tags
        seo_tags = ["Shorts", "YouTube Shorts", "Viral"]
        for h in merged_hashtags:
            clean_word = h.lstrip("#").strip()
            if clean_word and clean_word not in seo_tags:
                seo_tags.append(clean_word)

        return final_title, final_desc, seo_tags

    def upload_short(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: Optional[List[str]] = None,
        privacy_status: str = "public",
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Uploads generated video to YouTube with chunked resumable upload and live progress telemetry.
        Returns a dict with keys: id, url, title, status, platform.
        Raises RuntimeError on authentication or upload failure so caller safely avoids deleting local file.
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

        default_tags = ["Shorts", "Viral", "YouTube Shorts"]
        if tags:
            default_tags.extend(tags)

        body = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": list(set(default_tags)),
                "categoryId": "22",  # People & Blogs / Business
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            }
        }

        file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        self.logger.info(
            f"📤 [Publisher] Uploading '{title}' ({file_size_mb:.1f} MB) to YouTube..."
        )

        # 1MB chunk size for smooth progress tracking
        chunk_size = 1024 * 1024
        media = MediaFileUpload(video_path, chunksize=chunk_size, resumable=True, mimetype="video/mp4")

        request = self.youtube_service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        response = None
        last_pct = -1

        while response is None:
            status, response = request.next_chunk()
            if status:
                pct = round(status.progress() * 100, 1)
                int_pct = int(pct)
                if progress_callback:
                    progress_callback(pct, f"Uploading to YouTube: {pct:.1f}%")
                if int_pct != last_pct and int_pct in (25, 50, 75, 100):
                    last_pct = int_pct
                    self.logger.info(f"[Publisher] YouTube Upload: {pct:.0f}%")

        if progress_callback:
            progress_callback(100.0, "YouTube Upload Complete (100%)")

        video_id = response.get("id")
        if not video_id:
            raise RuntimeError(
                "[Publisher] YouTube upload completed but no video ID returned in response. "
                "The upload may have failed silently."
            )

        video_url = f"https://www.youtube.com/watch?v={video_id}"
        self.logger.info("✅ [Publisher] YouTube Short published! Live URL: %s", video_url)

        return {
            "id": video_id,
            "url": video_url,
            "title": title,
            "status": privacy_status,
            "platform": self.PLATFORM,
        }


class FacebookPublisher:
    """
    Publishing Engine for Facebook Page Reels and Videos using Meta Graph API.
    """
    PLATFORM = "facebook"

    def __init__(self, access_token: str, page_id: str, logger: Optional[logging.Logger] = None):
        self.access_token = (access_token or "").strip()
        self.page_id = (page_id or "").strip()
        self.logger = logger or logging.getLogger("AMBEnterprise")

    def _resolve_page_access_token(self) -> str:
        """
        Meta Graph API strictly requires a Page Access Token to publish reels/videos to a Facebook Page.
        If a User Access Token is provided, this automatically exchanges it for the Page Access Token.
        """
        # Step 1: Query /{page_id}?fields=access_token,name directly
        try:
            url = f"https://graph.facebook.com/v19.0/{self.page_id}"
            params = {
                "fields": "access_token,name",
                "access_token": self.access_token
            }
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                page_token = data.get("access_token")
                page_name = data.get("name", self.page_id)
                if page_token:
                    self.logger.info(f"[Facebook Publisher] Successfully acquired Page Access Token for '{page_name}' ({self.page_id}).")
                    return page_token
        except Exception as e:
            self.logger.debug(f"[Facebook Publisher] Direct page token fetch exception: {e}")

        # Step 2: Fallback to /me/accounts
        try:
            url = "https://graph.facebook.com/v19.0/me/accounts"
            params = {"access_token": self.access_token}
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                for page in data.get("data", []):
                    if str(page.get("id")) == str(self.page_id):
                        page_token = page.get("access_token")
                        if page_token:
                            self.logger.info(f"[Facebook Publisher] Resolved Page Access Token from /me/accounts for '{page.get('name')}'.")
                            return page_token
        except Exception as e:
            self.logger.debug(f"[Facebook Publisher] /me/accounts query exception: {e}")

        return self.access_token

    def upload_reel(
        self,
        video_path: str,
        title: str,
        description: str,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Uploads vertical video directly to Facebook Page Reels.
        Uses the Meta Graph API video_reels / videos endpoint with an automatically resolved Page Token.
        """
        if not self.access_token:
            raise ValueError("[Facebook Publisher] Meta / Facebook Access Token is missing.")
        if not self.page_id:
            raise ValueError("[Facebook Publisher] Facebook Page ID is missing.")
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"[Facebook Publisher] Video file not found: '{video_path}'")

        effective_token = self._resolve_page_access_token()

        file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        self.logger.info(
            f"📤 [Publisher] Uploading '{title}' ({file_size_mb:.1f} MB) to Facebook Page ({self.page_id})..."
        )

        if progress_callback:
            progress_callback(10.0, "Initializing Facebook Reels Upload...")

        video_id = None
        # Strategy 1: Video Reels Resumable API
        try:
            start_url = f"https://graph.facebook.com/v19.0/{self.page_id}/video_reels"
            start_res = requests.post(
                start_url,
                data={
                    "upload_phase": "start",
                    "access_token": effective_token
                },
                timeout=30
            )
            start_data = start_res.json()
            if "video_id" in start_data and "upload_url" in start_data:
                video_id = start_data["video_id"]
                upload_url = start_data["upload_url"]
                file_size = os.path.getsize(video_path)

                if progress_callback:
                    progress_callback(30.0, "Uploading video binary to Facebook Reels...")

                with open(video_path, "rb") as f:
                    up_headers = {
                        "Authorization": f"OAuth {effective_token}",
                        "offset": "0",
                        "file_size": str(file_size),
                        "Content-Type": "application/octet-stream"
                    }
                    up_res = requests.post(upload_url, headers=up_headers, data=f, timeout=180)
                    up_res.raise_for_status()

                if progress_callback:
                    progress_callback(80.0, "Publishing Facebook Reel...")

                finish_res = requests.post(
                    start_url,
                    data={
                        "upload_phase": "finish",
                        "video_id": video_id,
                        "video_state": "PUBLISHED",
                        "description": description,
                        "title": title[:100],
                        "access_token": effective_token
                    },
                    timeout=30
                )
                finish_data = finish_res.json()
                if not finish_data.get("success", False) and "video_id" not in finish_data:
                    self.logger.warning("[Facebook Publisher] Reel finish note: %s", finish_data)
        except Exception as e:
            self.logger.warning("[Facebook Publisher] Reels endpoint note (%s). Falling back to direct video upload...", e)
            video_id = None

        # Strategy 2: Direct Graph Video Upload (Fallback & Standard)
        if not video_id:
            try:
                if progress_callback:
                    progress_callback(40.0, "Uploading via Facebook direct video endpoint...")
                direct_url = f"https://graph-video.facebook.com/v19.0/{self.page_id}/videos"
                with open(video_path, "rb") as f:
                    files = {"source": f}
                    data = {
                        "access_token": effective_token,
                        "title": title[:100],
                        "description": description
                    }
                    resp = requests.post(direct_url, data=data, files=files, timeout=240)
                    resp_data = resp.json()
                    if "error" in resp_data:
                        raise RuntimeError(f"Facebook Graph API Error: {resp_data['error'].get('message', resp_data['error'])}")
                    video_id = resp_data.get("id")
            except Exception as direct_err:
                raise RuntimeError(f"[Facebook Publisher] Upload failed: {direct_err}")

        if not video_id:
            raise RuntimeError("[Facebook Publisher] Upload failed to return a valid Facebook video ID.")

        video_url = f"https://www.facebook.com/{video_id}"
        self.logger.info("✅ [Publisher] Facebook Reel published! Live URL: %s", video_url)
        if progress_callback:
            progress_callback(100.0, "Facebook Upload Complete (100%)")

        return {
            "id": video_id,
            "url": video_url,
            "title": title,
            "platform": self.PLATFORM
        }


class InstagramPublisher:
    """
    Publishing Engine for Instagram Professional Reels using Meta Graph API.
    """
    PLATFORM = "instagram"

    def __init__(self, access_token: str, instagram_account_id: str, logger: Optional[logging.Logger] = None):
        self.access_token = (access_token or "").strip()
        self.instagram_account_id = (instagram_account_id or "").strip()
        self.logger = logger or logging.getLogger("AMBEnterprise")

    def upload_reel(
        self,
        video_path: str,
        caption: str,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Uploads vertical video directly to Instagram Reels via Graph API Resumable Container Upload.
        """
        if not self.access_token:
            raise ValueError("[Instagram Publisher] Meta / Instagram Access Token is missing.")
        if not self.instagram_account_id:
            raise ValueError("[Instagram Publisher] Instagram Profile / Account ID is missing.")
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"[Instagram Publisher] Video file not found: '{video_path}'")

        file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        file_size = os.path.getsize(video_path)
        self.logger.info(
            f"📤 [Publisher] Uploading Reel ({file_size_mb:.1f} MB) to Instagram ({self.instagram_account_id})..."
        )

        if progress_callback:
            progress_callback(10.0, "Initializing Instagram Reels Container...")

        # Step 1: Create Resumable Reels Media Container
        init_url = f"https://graph.facebook.com/v19.0/{self.instagram_account_id}/media"
        init_data = {
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption[:2200],
            "access_token": self.access_token
        }
        init_resp = requests.post(init_url, data=init_data, timeout=30)
        init_json = init_resp.json()
        if "error" in init_json:
            raise RuntimeError(f"Instagram Container Creation Error: {init_json['error'].get('message', init_json['error'])}")

        container_id = init_json.get("id")
        rupload_uri = init_json.get("uri")

        if not container_id or not rupload_uri:
            raise RuntimeError(f"[Instagram Publisher] Failed to obtain container ID or upload URI: {init_json}")

        if progress_callback:
            progress_callback(30.0, "Uploading video data to Instagram...")

        # Step 2: Upload Video Binary to rupload_uri
        with open(video_path, "rb") as f:
            headers = {
                "Authorization": f"OAuth {self.access_token}",
                "offset": "0",
                "file_size": str(file_size),
                "Content-Type": "application/octet-stream"
            }
            upload_resp = requests.post(rupload_uri, headers=headers, data=f, timeout=240)
            if upload_resp.status_code not in (200, 201):
                raise RuntimeError(f"Instagram Video Upload Error (HTTP {upload_resp.status_code}): {upload_resp.text}")

        if progress_callback:
            progress_callback(65.0, "Waiting for Instagram media processing...")

        # Step 3: Poll Container Status until FINISHED
        status_url = f"https://graph.facebook.com/v19.0/{container_id}"
        max_attempts = 20
        is_ready = False
        for attempt in range(max_attempts):
            time.sleep(3)
            stat_resp = requests.get(
                status_url,
                params={"fields": "status_code,status", "access_token": self.access_token},
                timeout=20
            )
            stat_json = stat_resp.json()
            code = stat_json.get("status_code", "")
            if code == "FINISHED":
                is_ready = True
                break
            elif code in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Instagram media processing failed with status '{code}': {stat_json}")
            self.logger.debug("[Instagram Publisher] Media processing... (%s/20)", attempt + 1)

        if not is_ready:
            self.logger.warning("[Instagram Publisher] Container processing timed out. Attempting publish anyway...")

        if progress_callback:
            progress_callback(85.0, "Publishing Reel to Instagram...")

        # Step 4: Publish Container
        publish_url = f"https://graph.facebook.com/v19.0/{self.instagram_account_id}/media_publish"
        pub_resp = requests.post(
            publish_url,
            data={"creation_id": container_id, "access_token": self.access_token},
            timeout=30
        )
        pub_json = pub_resp.json()
        if "error" in pub_json:
            raise RuntimeError(f"Instagram Publish Error: {pub_json['error'].get('message', pub_json['error'])}")

        media_id = pub_json.get("id")
        if not media_id:
            raise RuntimeError(f"[Instagram Publisher] No media ID returned on publish: {pub_json}")

        # Step 5: Query Permalink
        media_url = f"https://www.instagram.com/reel/{media_id}"
        try:
            permalink_resp = requests.get(
                f"https://graph.facebook.com/v19.0/{media_id}",
                params={"fields": "permalink", "access_token": self.access_token},
                timeout=15
            )
            p_json = permalink_resp.json()
            if "permalink" in p_json:
                media_url = p_json["permalink"]
        except Exception:
            pass

        self.logger.info("✅ [Publisher] Instagram Reel published! Live URL: %s", media_url)
        if progress_callback:
            progress_callback(100.0, "Instagram Upload Complete (100%)")

        return {
            "id": media_id,
            "url": media_url,
            "title": caption,
            "platform": self.PLATFORM
        }


class MultiPlatformPublisher:
    """
    Unified multi-platform publishing engine for AMB Enterprise.
    Dispatches video uploads to YouTube, Facebook, and Instagram based on configured credentials
    and user toggle switches.
    """

    def __init__(self, config_manager, profile_name: str, logger: Optional[logging.Logger] = None):
        self.config_manager = config_manager
        self.profile_name = profile_name
        self.logger = logger or logging.getLogger("AMBEnterprise")

    def publish_all(
        self,
        video_path: str,
        title: str,
        hook: str = "",
        rationale: str = "",
        channel_name: str = "",
        hashtags: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Publishes the generated clip to all enabled & configured platforms:
        - YouTube Shorts
        - Facebook Reels (Page)
        - Instagram Reels (Professional)
        """
        results: Dict[str, Any] = {
            "success_count": 0,
            "platforms": [],
            "youtube": None,
            "facebook": None,
            "instagram": None,
            "errors": {}
        }

        # Check toggles
        upload_yt = self.config_manager.get_channel_setting("upload_to_youtube", True, self.profile_name)
        upload_fb = self.config_manager.get_channel_setting("upload_to_facebook", True, self.profile_name)
        upload_ig = self.config_manager.get_channel_setting("upload_to_instagram", True, self.profile_name)

        # Build clean metadata & viral hashtags
        yt_title, yt_desc, yt_tags = YouTubePublisher.build_video_metadata(
            title=title,
            hook=hook,
            rationale=rationale,
            channel_name=channel_name or self.profile_name,
            hashtags=hashtags
        )

        # ── 1. YOUTUBE UPLOAD ─────────────────────────────────────────────
        yt_oauth = self.config_manager.get_channel_setting("youtube_oauth_json_path", "", self.profile_name).strip()
        if upload_yt:
            if yt_oauth and os.path.exists(yt_oauth):
                try:
                    yt_pub = YouTubePublisher(client_secrets_file=yt_oauth, logger=self.logger)
                    privacy = self.config_manager.get_channel_setting("youtube_privacy_status", "public", self.profile_name)
                    yt_res = yt_pub.upload_short(
                        video_path=video_path,
                        title=yt_title,
                        description=yt_desc,
                        tags=yt_tags,
                        privacy_status=privacy,
                        progress_callback=progress_callback
                    )
                    results["youtube"] = yt_res
                    results["platforms"].append("youtube")
                    results["success_count"] += 1
                except Exception as e:
                    self.logger.warning("[MultiPublisher] YouTube upload failed: %s", e)
                    results["errors"]["youtube"] = str(e)
            else:
                self.logger.info("[MultiPublisher] YouTube upload enabled but OAuth client_secret.json missing.")
        else:
            self.logger.info("[MultiPublisher] YouTube upload disabled by switch toggle.")

        # ── 2. FACEBOOK REELS UPLOAD ──────────────────────────────────────
        meta_tok = (
            self.config_manager.get_channel_setting("meta_access_token", "", self.profile_name) or
            self.config_manager.get_channel_setting("facebook_access_token", "", self.profile_name)
        ).strip()
        fb_page_id = self.config_manager.get_channel_setting("facebook_page_id", "", self.profile_name).strip()

        if upload_fb:
            if meta_tok and fb_page_id:
                try:
                    fb_pub = FacebookPublisher(access_token=meta_tok, page_id=fb_page_id, logger=self.logger)
                    fb_res = fb_pub.upload_reel(
                        video_path=video_path,
                        title=yt_title,
                        description=yt_desc,
                        progress_callback=progress_callback
                    )
                    results["facebook"] = fb_res
                    results["platforms"].append("facebook")
                    results["success_count"] += 1
                except Exception as e:
                    self.logger.warning("[MultiPublisher] Facebook upload failed: %s", e)
                    results["errors"]["facebook"] = str(e)
            else:
                self.logger.info("[MultiPublisher] Facebook upload enabled but Meta token or Page ID missing.")
        else:
            self.logger.info("[MultiPublisher] Facebook upload disabled by switch toggle.")

        # ── 3. INSTAGRAM REELS UPLOAD ─────────────────────────────────────
        ig_account_id = self.config_manager.get_channel_setting("instagram_account_id", "", self.profile_name).strip()

        if upload_ig:
            if meta_tok and ig_account_id:
                try:
                    ig_pub = InstagramPublisher(access_token=meta_tok, instagram_account_id=ig_account_id, logger=self.logger)
                    ig_res = ig_pub.upload_reel(
                        video_path=video_path,
                        caption=yt_desc,
                        progress_callback=progress_callback
                    )
                    results["instagram"] = ig_res
                    results["platforms"].append("instagram")
                    results["success_count"] += 1
                except Exception as e:
                    self.logger.warning("[MultiPublisher] Instagram upload failed: %s", e)
                    results["errors"]["instagram"] = str(e)
            else:
                self.logger.info("[MultiPublisher] Instagram upload enabled but Meta token or Account ID missing.")
        else:
            self.logger.info("[MultiPublisher] Instagram upload disabled by switch toggle.")

        # Map URLs for backward compatibility
        if results.get("youtube"):
            results["id"] = results["youtube"].get("id")
            results["url"] = results["youtube"].get("url")
            results["youtube_url"] = results["youtube"].get("url")
            results["youtube_id"] = results["youtube"].get("id")
        if results.get("facebook"):
            results["facebook_url"] = results["facebook"].get("url")
            if "url" not in results:
                results["url"] = results["facebook"].get("url")
        if results.get("instagram"):
            results["instagram_url"] = results["instagram"].get("url")
            if "url" not in results:
                results["url"] = results["instagram"].get("url")

        results["title"] = yt_title
        return results

