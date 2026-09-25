import os
import time
import requests
import json
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# ==========================================
# 0. DIAGNOSTIC: CHECK TOKEN PERMISSIONS
# ==========================================
def inspect_token_permissions(access_token):
    print("--- 🔍 Inspecting Token Permissions ---")
    try:
        url = f"https://graph.facebook.com/v19.0/me/permissions?access_token={access_token}"
        res = requests.get(url, timeout=10).json()
        if "data" in res:
            granted = [p["permission"] for p in res["data"] if p.get("status") == "granted"]
            print(f"✅ Granted Permissions: {', '.join(granted)}")
            
            required = ["pages_manage_posts", "pages_read_engagement", "pages_show_list", "instagram_content_publish"]
            missing = [r for r in required if r not in granted]
            if missing:
                print(f"⚠️ MISSING PERMISSIONS: {', '.join(missing)}")
            else:
                print("✅ All required Facebook/Instagram permissions present!")
        elif "error" in res:
            print(f"❌ Token Debug Error: {res['error'].get('message')}")
    except Exception as e:
        print(f"⚠️ Permissions inspection notice: {e}")
    print("---------------------------------------\n")


# ==========================================
# 1. TEST FACEBOOK VIDEO UPLOAD
# ==========================================
def test_facebook_upload(fb_page_id, access_token, video_path):
    print("--- Testing Facebook Video Upload ---")
    if not os.path.exists(video_path):
        print(f"❌ Video file not found: {video_path}")
        return

    # STEP 1: Auto-exchange User/System User Token for the actual Page Access Token
    print(f"Resolving Page Access Token for Page ID: {fb_page_id}...")
    page_token = access_token
    try:
        token_lookup = requests.get(
            f"https://graph.facebook.com/v19.0/{fb_page_id}?fields=access_token,name&access_token={access_token}",
            timeout=10
        ).json()
        
        if "access_token" in token_lookup:
            page_token = token_lookup["access_token"]
            page_name = token_lookup.get("name", "Page")
            print(f"✅ Successfully acquired Page Access Token for '{page_name}'!")
        elif "error" in token_lookup:
            print(f"⚠️ Page Token Exchange Notice: {token_lookup['error'].get('message')}")
            # Fallback: check /me/accounts
            accounts_res = requests.get(
                f"https://graph.facebook.com/v19.0/me/accounts?access_token={access_token}",
                timeout=10
            ).json()
            if "data" in accounts_res:
                for page in accounts_res["data"]:
                    if str(page.get("id")) == str(fb_page_id):
                        page_token = page.get("access_token")
                        print(f"✅ Found Page Token via /me/accounts for '{page.get('name')}'!")
                        break
    except Exception as e:
        print(f"⚠️ Page token resolution notice: {e}")

    # STEP 2: Upload Video using the resolved Page Token
    url = f"https://graph.facebook.com/v19.0/{fb_page_id}/videos"
    payload = {
        'access_token': page_token,
        'description': 'Test upload from API Agent'
    }
    
    print("Uploading to Facebook...")
    try:
        with open(video_path, 'rb') as f:
            files = {'source': f}
            response = requests.post(url, data=payload, files=files, timeout=120)
            data = response.json()
            
        if 'error' in data:
            print(f"❌ Facebook Upload Error: {data['error'].get('message')}")
            print(f"   Full Error Details: {data['error']}")
        else:
            print(f"✅ Facebook Upload Successful! Video ID: {data.get('id')}")
    except Exception as e:
        print(f"❌ Facebook Request Exception: {e}")


# ==========================================
# 2. TEST INSTAGRAM REEL UPLOAD (LOCAL FILE)
# ==========================================
def test_instagram_upload(ig_user_id, access_token, video_path):
    print("\n--- Testing Instagram Reel Upload ---")
    if not os.path.exists(video_path):
        print(f"❌ Video file not found: {video_path}")
        return
        
    print("Step 1: Initializing Resumable Upload Session...")
    init_url = f"https://graph.facebook.com/v19.0/{ig_user_id}/media"
    init_payload = {
        'upload_type': 'resumable',
        'media_type': 'REELS',
        'caption': 'Test reel from API Agent #reels #test',
        'access_token': access_token
    }
    init_res = requests.post(init_url, data=init_payload, timeout=20).json()
    
    if 'error' in init_res:
        print(f"❌ Instagram Init Error: {init_res['error'].get('message')}")
        return
        
    container_id = init_res.get('id')
    upload_uri = init_res.get('uri', f"https://rupload.facebook.com/ig-api-upload/v19.0/{container_id}")
    print(f"✅ Container created: {container_id}")
    
    print("Step 2: Uploading video bytes...")
    file_size = os.path.getsize(video_path)
    
    headers = {
        'Authorization': f'OAuth {access_token}',
        'offset': '0',
        'file_size': str(file_size),
        'Content-Type': 'application/octet-stream'
    }
    
    with open(video_path, 'rb') as f:
        up_response = requests.post(upload_uri, headers=headers, data=f, timeout=120)
        
    try:
        upload_res = up_response.json()
        if isinstance(upload_res, dict) and 'error' in upload_res:
            print(f"❌ Instagram Byte Upload Error: {upload_res['error'].get('message')}")
            return
    except Exception:
        pass # Ruploader often returns offset without a JSON payload on success
        
    print("✅ Video uploaded. Waiting for Instagram processing...")
    
    # Poll for status
    status_url = f"https://graph.facebook.com/v19.0/{container_id}?fields=status_code,status&access_token={access_token}"
    is_ready = False
    for attempt in range(15):
        time.sleep(6)
        status_res = requests.get(status_url, timeout=15).json()
        status = status_res.get('status_code')
        if status == 'FINISHED':
            print("✅ Processing FINISHED.")
            is_ready = True
            break
        elif status == 'ERROR':
            print(f"❌ Instagram Processing Error: {status_res}")
            return
            
        print(f"Status: {status}... waiting (attempt {attempt+1}/15)")
        
    if not is_ready:
        print("❌ Instagram processing timed out.")
        return

    print("Step 3: Publishing Reel...")
    publish_url = f"https://graph.facebook.com/v19.0/{ig_user_id}/media_publish"
    publish_payload = {
        'creation_id': container_id,
        'access_token': access_token
    }
    pub_res = requests.post(publish_url, data=publish_payload, timeout=20).json()
    
    if 'error' in pub_res:
        print(f"❌ Instagram Publish Error: {pub_res['error'].get('message')}")
    else:
        print(f"✅ Instagram Reel Published! ID: {pub_res.get('id')}")


# ==========================================
# 3. TEST YOUTUBE UPLOAD
# ==========================================
def test_youtube_upload(client_secrets_file, video_path):
    print("\n--- Testing YouTube Video Upload ---")
    if not os.path.exists(video_path):
        print(f"❌ Video file not found: {video_path}")
        return

    # Check for client_secret.json in current directory and fallback folders
    possible_paths = [
        client_secrets_file,
        os.path.join("..", client_secrets_file),
        os.path.join("..", "Quran Agent Latest", client_secrets_file),
        os.path.join("..", "Quran Agent Latest", "credentials", "Main Page", client_secrets_file)
    ]
    resolved_secret = None
    for p in possible_paths:
        if os.path.exists(p):
            resolved_secret = p
            break

    if not resolved_secret:
        print(f"⚠️ YouTube Notice: '{client_secrets_file}' was not found in this folder.")
        print("   -> To enable YouTube: Copy 'client_secret.json' from your Google Cloud Console into this directory.")
        return

    SCOPES = ['https://www.googleapis.com/auth/youtube.upload', 'https://www.googleapis.com/auth/youtube.readonly']
    credentials = None
    token_cache_file = 'token.json'

    if os.path.exists(token_cache_file):
        try:
            credentials = Credentials.from_authorized_user_file(token_cache_file, SCOPES)
        except Exception:
            credentials = None
        
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            print("🔑 Opening browser for YouTube OAuth Login...")
            flow = InstalledAppFlow.from_client_secrets_file(resolved_secret, SCOPES)
            credentials = flow.run_local_server(port=0)
            with open(token_cache_file, 'w') as token:
                token.write(credentials.to_json())

    try:
        youtube = build('youtube', 'v3', credentials=credentials)
        print("Uploading to YouTube...")
        
        body = {
            'snippet': {
                'title': 'Test Upload - API Agent',
                'description': 'Test upload from API Agent #Shorts',
                'categoryId': '22'
            },
            'status': {
                'privacyStatus': 'private'
            }
        }
        
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media
        )
        
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"Uploaded {int(status.progress() * 100)}%")

        print(f"✅ YouTube Upload Successful! Video ID: {response['id']}")
            
    except Exception as e:
        print(f"❌ YouTube API Request Failed: {e}")


# ==========================================
# 4. RUN THE TESTS
# ==========================================
if __name__ == "__main__":
    FB_PAGE_ID = "1408132262375091" 
    INSTA_ACCOUNT_ID = "17841416986453947"
    META_ACCESS_TOKEN = "EAAYDrjLZAMS4BSkSYmilOvDo39memZAPLYl4dofEAFKwGrSD7JynS79yI8SGAS3zshBuh21b4LDcvRVZBwhbFn1b3LYBWlFNDaiVJ45qwsoE72Y0avyFXRcx5oUDBben1Qe5V4YvZCcMK6f4lV9GbB0PZBIyd9ZBTKePuKZC70CVTWm4dtAkZC18ob6N8WBtPgZDZD" 
    
    YT_CLIENT_SECRETS_FILE = "client_secret.json" 
    VIDEO_FILE = "1.mp4"

    print("========================================")
    print("🚀 STARTING UPLOAD CREDENTIAL TESTER")
    print("========================================\n")
    
    inspect_token_permissions(META_ACCESS_TOKEN)
    test_facebook_upload(FB_PAGE_ID, META_ACCESS_TOKEN, VIDEO_FILE)
    test_instagram_upload(INSTA_ACCOUNT_ID, META_ACCESS_TOKEN, VIDEO_FILE)
    test_youtube_upload(YT_CLIENT_SECRETS_FILE, VIDEO_FILE)
    
    print("\n========================================")
    print("🏁 Upload Tests Complete!")
    print("========================================")
