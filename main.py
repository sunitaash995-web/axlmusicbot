"""
AXLMUSICBOT — Telegram VC Music Bot
Plays music in voice chats + sends audio files
Built for Railway deployment
"""

import os
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
import yt_dlp

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AXLMUSICBOT")

# Config from environment variables
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# VC support (optional - requires py-tgcalls + SESSION_STRING)
# Compatibility shims: py-tgcalls expects names removed from pyrogram 2.0.x
try:
    import pyrogram.errors as _pge
    _fallback = getattr(_pge, 'GroupcallAddParticipantsFailed', Exception)
    for _name in ('GroupcallForbidden', 'GroupcallInvalid'):
        if not hasattr(_pge, _name):
            setattr(_pge, _name, _fallback)
    
    # InputGroupCallSlug was removed from pyrogram.raw.types
    # Recreate it: inputGroupCallSlug#c5af1d61 slug:string = InputGroupCall
    import pyrogram.raw.types as _prt
    if not hasattr(_prt, 'InputGroupCallSlug'):
        from pyrogram.raw.core import TLObject
        class InputGroupCallSlug(TLObject):
            ID = 0xc5af1d61
            QUALNAME = "types.InputGroupCallSlug"
            def __init__(self, *, slug: str):
                self.slug = slug
        _prt.InputGroupCallSlug = InputGroupCallSlug
    
    # PhoneCallDiscardReasonMigrateConferenceCall was removed
    # Recreate it: phoneCallDiscardReasonMigrateConferenceCall#0e1e1ad8 = PhoneCallDiscardReason
    if not hasattr(_prt, 'PhoneCallDiscardReasonMigrateConferenceCall'):
        from pyrogram.raw.core import TLObject
        class PhoneCallDiscardReasonMigrateConferenceCall(TLObject):
            ID = 0x0e1e1ad8
            QUALNAME = "types.PhoneCallDiscardReasonMigrateConferenceCall"
            def __init__(self):
                pass
        _prt.PhoneCallDiscardReasonMigrateConferenceCall = PhoneCallDiscardReasonMigrateConferenceCall
except Exception:
    pass

try:
    from pytgcalls import PyTgCalls
    from pytgcalls.types import MediaStream
    VC_AVAILABLE = True
except ImportError:
    VC_AVAILABLE = False
    logger.warning("py-tgcalls not installed - VC features disabled")

# yt-dlp options for audio extraction
# Uses cookies (user's YouTube login) to bypass bot detection
# Cookies come from YT_COOKIES env var (secure) or cookies.txt file
import os as _os

def _get_cookie_file():
    # Option 1: YT_COOKIES env var (secure - for Railway)
    cookies_data = _os.getenv("YT_COOKIES", "")
    if cookies_data:
        # Write to /tmp (not committed to git)
        path = "/tmp/cookies.txt"
        try:
            with open(path, "w") as f:
                f.write(cookies_data)
            return path
        except Exception:
            pass
    # Option 2: Local cookies.txt file (for development)
    local_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "cookies.txt")
    if _os.path.exists(local_path):
        return local_path
    return None

_COOKIE_FILE = _get_cookie_file()

YTDL_OPTS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "extract_flat": False,
    "default_search": "ytsearch",
    "noplaylist": True,
    **({"cookiefile": _COOKIE_FILE} if _COOKIE_FILE else {}),
}

# Get ffmpeg binary path for downloads (imageio-ffmpeg bundled)
def _get_ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None

_FFMPEG_EXE = _get_ffmpeg_exe()

# Initialize clients
# Bot client (handles commands) - uses BOT_TOKEN
bot = Client(
    "axlmusicbot_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

# User client (for VC) - uses SESSION_STRING
user = None
if SESSION_STRING:
    user = Client(
        "axlmusicbot_user",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION_STRING,
    )

# For backwards compatibility with handlers
app = bot

# Download ffprobe binary at runtime (Railway doesn't have it)
def _ensure_ffprobe():
    """Download static ffprobe if not available. Returns path or None."""
    import shutil
    import os
    
    if shutil.which("ffprobe"):
        return shutil.which("ffprobe")
    
    # Try to download static ffprobe
    ffprobe_path = "/tmp/ffprobe"
    if os.path.exists(ffprobe_path):
        os.environ["PATH"] = "/tmp:" + os.environ["PATH"]
        return ffprobe_path
    
    try:
        import urllib.request
        import tarfile
        
        logger.info("📥 Downloading ffprobe...")
        # BtbN FFmpeg builds (includes ffprobe)
        url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
        
        tar_path = "/tmp/ffmpeg.tar.xz"
        # Download with timeout
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            with open(tar_path, "wb") as f:
                f.write(resp.read())
        
        logger.info("📦 Extracting ffprobe...")
        with tarfile.open(tar_path, "r:xz") as tar:
            # Find ffprobe in the archive
            for member in tar.getmembers():
                if member.name.endswith("/bin/ffprobe"):
                    member.name = "ffprobe"  # Extract to /tmp/ffprobe
                    tar.extract(member, "/tmp")
                    break
        
        if os.path.exists("/tmp/ffprobe"):
            os.chmod("/tmp/ffprobe", 0o755)
            os.environ["PATH"] = "/tmp:" + os.environ["PATH"]
            logger.info("✅ ffprobe downloaded: /tmp/ffprobe")
            # Cleanup
            try:
                os.remove(tar_path)
            except:
                pass
            return "/tmp/ffprobe"
    except Exception as e:
        logger.warning(f"ffprobe download failed: {e}")
    
    return None

# VC client (attaches to user client)
pytgcalls = None
if VC_AVAILABLE and user:
    try:
        # Ensure ffprobe is available BEFORE creating PyTgCalls
        _ensure_ffprobe()
        
        pytgcalls = PyTgCalls(user)
        logger.info("✅ py-tgcalls ready (VC mode)")
        # Check ffmpeg/ffprobe availability
        import shutil
        _ffmpeg = shutil.which("ffmpeg")
        _ffprobe = shutil.which("ffprobe")
        logger.info(f"🎬 ffmpeg: {_ffmpeg or 'NOT FOUND'}")
        logger.info(f"🎬 ffprobe: {_ffprobe or 'NOT FOUND'}")
        
        # Try imageio-ffmpeg as fallback for ffmpeg binary
        if not _ffmpeg:
            try:
                import imageio_ffmpeg
                _ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
                logger.info(f"🎬 imageio-ffmpeg: {_ffmpeg_path}")
                # Add to PATH so py-tgcalls can find it
                import os
                os.environ["PATH"] = os.path.dirname(_ffmpeg_path) + ":" + os.environ["PATH"]
                # Create symlink for ffmpeg
                _ffmpeg = shutil.which("ffmpeg") or _ffmpeg_path
            except Exception as e:
                logger.warning(f"imageio-ffmpeg not available: {e}")
    except Exception as e:
        logger.warning(f"PyTgCalls init failed (VC disabled): {e}")
        pytgcalls = None

# Queue per chat
queues = {}

# ============================================================================
# AXLMUSIC METHOD: Direct YouTube InnerTube API (no yt-dlp, no cookies, no bot check)
# Same as Axlmusic app's InnerTubeMusicApi.kt - tries multiple player clients
# ============================================================================
import json as _json
import urllib.request as _ureq

# Player clients (from Axlmusic's InnerTubeMusicApi - public YouTube clients)
# Each has name, version, api_key, user_agent
_INNERTUBE_CLIENTS = [
    {
        "name": "ANDROID",
        "version": "20.10.38",
        "api_key": "AIzaSyA8eiZmM1FaDVjRy-df2KTyQ-qi4oJ6tGs",
        "user_agent": "com.google.android.youtube/20.10.38 (Linux; U; Android 14) gzip",
    },
    {
        "name": "WEB",
        "version": "2.20241008.00.00",
        "api_key": "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    },
]

def _innertube_search(query: str) -> dict:
    """Search YouTube WITHOUT yt-dlp (avoids ffprobe issues).
    Uses YouTube search page HTML and regex to extract video ID."""
    import re
    
    # Use YouTube search page directly
    search_url = f"https://www.youtube.com/results?search_query={_ureq.quote(query)}"
    req = _ureq.Request(
        search_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    
    try:
        with _ureq.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
        
        # Extract video IDs from HTML
        # Look for "videoId":"XXXXXXXXXXX" patterns
        video_ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
        if video_ids:
            # Get title too
            title_match = re.search(r'"title":{"runs":\[{"text":"([^"]{5,100})"', html)
            title = title_match.group(1) if title_match else "Unknown"
            return {"id": video_ids[0], "title": title}
    except Exception as e:
        logger.warning(f"HTML search failed: {e}")
    
    raise Exception("No video found")

def _innertube_player(video_id: str) -> dict:
    """Get direct audio URL via InnerTube player API (Axlmusic method)"""
    last_error = None
    
    for client in _INNERTUBE_CLIENTS:
        try:
            url = f"https://www.youtube.com/youtubei/v1/player?key={client['api_key']}&prettyPrint=false"
            
            payload = {
                "context": {
                    "client": {
                        "clientName": client["name"],
                        "clientVersion": client["version"],
                        "hl": "en",
                        "gl": "US",
                    }
                },
                "videoId": video_id,
                "contentCheckOk": True,
                "racyCheckOk": True,
            }
            
            req = _ureq.Request(
                url,
                data=_json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "User-Agent": client["user_agent"]},
            )
            
            with _ureq.urlopen(req, timeout=15) as resp:
                data = _json.loads(resp.read())
            
            # Check playability
            status = data.get("playabilityStatus", {}).get("status")
            if status != "OK":
                last_error = f"Playability: {status}"
                continue
            
            # Extract audio formats
            streaming = data.get("streamingData", {})
            formats = streaming.get("adaptiveFormats", [])
            
            best_audio = None
            for f in formats:
                mime = f.get("mimeType", "")
                if "audio" in mime and f.get("url"):
                    # Prefer opus, then any audio
                    if not best_audio or ("opus" in mime and "opus" not in best_audio.get("mimeType", "")):
                        best_audio = f
            
            if best_audio:
                details = data.get("videoDetails", {})
                return {
                    "url": best_audio["url"],
                    "title": details.get("title", "Unknown"),
                    "duration": int(details.get("lengthSeconds", 0)),
                    "thumbnail": "",
                    "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
                }
            last_error = "No audio format found"
        except Exception as e:
            last_error = str(e)
            continue
    
    raise Exception(f"InnerTube failed: {last_error}")

def get_audio_url(query: str) -> dict:
    """Get audio URL - tries InnerTube first (no yt-dlp, no ffprobe issues),
    then yt-dlp with cookies as fallback.
    
    If query is a URL, extract video ID directly.
    Otherwise, search via HTML (no yt-dlp!).
    """
    # Extract video ID if URL provided
    video_id = None
    title_hint = None
    
    if "youtube.com/watch" in query or "youtu.be/" in query:
        import re
        m = re.search(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})", query)
        if m:
            video_id = m.group(1)
    
    # Step 1: Get video ID via HTML search (NO yt-dlp!)
    if not video_id:
        try:
            result = _innertube_search(query)
            video_id = result["id"]
            title_hint = result["title"]
        except Exception as e:
            logger.warning(f"HTML search failed: {e}")
    
    # Step 2: Get audio URL via InnerTube player (NO yt-dlp!)
    if video_id:
        try:
            result = _innertube_player(video_id)
            if result["title"] == "Unknown" and title_hint:
                result["title"] = title_hint
            result["thumbnail"] = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
            logger.info(f"InnerTube success: {result['title']}")
            return result
        except Exception as e:
            logger.warning(f"InnerTube player failed: {str(e)[:100]}")
    
    # Step 3: Fallback to yt-dlp with cookies (may hit ffprobe/bot issues)
    logger.warning("Falling back to yt-dlp with cookies")
    try:
        with yt_dlp.YoutubeDL(YTDL_OPTS) as ydl:
            if video_id:
                url = f"https://www.youtube.com/watch?v={video_id}"
            elif not query.startswith("http"):
                url = f"ytsearch1:{query}"
            else:
                url = query
            info = ydl.extract_info(url, download=False)
            if "entries" in info:
                info = info["entries"][0]
            return {
                "url": info["url"],
                "title": info.get("title", title_hint or "Unknown"),
                "duration": info.get("duration", 0),
                "thumbnail": info.get("thumbnail", ""),
                "webpage_url": info.get("webpage_url", ""),
            }
    except Exception as e:
        raise Exception(f"All methods failed: {str(e)[:150]}")

@app.on_message(filters.command("start"))
async def start_cmd(client, message: Message):
    await message.reply_text(
        "🎵 **AXLMUSICBOT** — Tera khud ka music bot!\n\n"
        "**Commands:**\n"
        "`/play <gaana>` — VC me bajao / file bhejo\n"
        "`/vplay <gaana>` — Voice chat me play karo\n"
        "`/stop` — VC se niklo\n"
        "`/skip` — Agla gaana\n"
        "`/pause` — Roko\n"
        "`/resume` — Phir se bajao\n"
        "`/queue` — List dekho\n\n"
        "Made with ❤️ by AXL"
    )

@app.on_message(filters.command("play"))
async def play_cmd(client, message: Message):
    """Download and send as audio file"""
    if len(message.command) < 2:
        await message.reply_text("❌ Gaane ka naam to bata! `/play lagao jaan`")
        return

    query = " ".join(message.command[1:])
    status = await message.reply_text(f"🔍 **{query}** dhoond raha hoon...")

    try:
        # Get audio info
        info = get_audio_url(query)
        await status.edit_text(f"⬇️ **{info['title']}** download ho raha hai...")

        # Download the audio file
        # Use bundled ffmpeg binary for MP3 conversion
        dl_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "outtmpl": f"/tmp/%(title)s.%(ext)s",
            **({"ffmpeg_location": _FFMPEG_EXE} if _FFMPEG_EXE else {}),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        }
        with yt_dlp.YoutubeDL(dl_opts) as ydl:
            dl_info = ydl.extract_info(info["webpage_url"], download=True)
            filename = ydl.prepare_filename(dl_info).rsplit(".", 1)[0] + ".mp3"

        # Send as audio
        await status.edit_text(f"📤 **{info['title']}** bhej raha hoon...")
        await message.reply_audio(
            audio=filename,
            title=info["title"],
            duration=info["duration"],
            caption=f"🎵 **{info['title']}**\n\nVia AXLMUSICBOT 🤖",
        )
        await status.delete()

        # Cleanup
        if os.path.exists(filename):
            os.remove(filename)

    except Exception as e:
        logger.error(f"Play error: {e}")
        await status.edit_text(f"❌ Error: {str(e)[:200]}")

@app.on_message(filters.command("vplay"))
async def vplay_cmd(client, message: Message):
    """Play in voice chat"""
    if not pytgcalls:
        await message.reply_text(
            "❌ VC feature abhi available nahi hai!\n"
            "Session string add karo Railway variables me."
        )
        return

    if len(message.command) < 2:
        await message.reply_text("❌ Gaane ka naam to bata! `/vplay lagao jaan`")
        return

    query = " ".join(message.command[1:])
    chat_id = message.chat.id
    status = await message.reply_text(f"🔍 **{query}** dhoond raha hoon...")

    try:
        info = get_audio_url(query)

        # Add to queue
        if chat_id not in queues:
            queues[chat_id] = []
        queues[chat_id].append(info)

        # Join VC and play (play auto-joins in py-tgcalls 2.x)
        try:
            await pytgcalls.play(chat_id, MediaStream(info["url"]))
            logger.info(f"Playing in VC {chat_id}")
        except Exception as play_err:
                logger.error(f"Play failed: {play_err}")
                raise play_err

        await status.edit_text(
            f"🎵 **VC me baj raha hai:**\n"
            f"**{info['title']}**\n"
            f"⏱️ {info['duration']//60}:{info['duration']%60:02d}"
        )

    except Exception as e:
        logger.error(f"VPlay error: {e}")
        await status.edit_text(f"❌ Error: {str(e)[:200]}")

@app.on_message(filters.command("stop"))
async def stop_cmd(client, message: Message):
    chat_id = message.chat.id
    try:
        await pytgcalls.leave_group_call(chat_id)
        queues.pop(chat_id, None)
        await message.reply_text("⏹️ VC se nikal gaya!")
    except Exception as e:
        await message.reply_text(f"❌ {str(e)[:100]}")

@app.on_message(filters.command(["skip", "next"]))
async def skip_cmd(client, message: Message):
    chat_id = message.chat.id
    try:
        if chat_id in queues and len(queues[chat_id]) > 1:
            queues[chat_id].pop(0)
            next_song = queues[chat_id][0]
            await pytgcalls.play(chat_id, MediaStream(next_song["url"]))
            await message.reply_text(f"⏭️ **Agla:** {next_song['title']}")
        else:
            await pytgcalls.leave_group_call(chat_id)
            queues.pop(chat_id, None)
            await message.reply_text("⏭️ Queue khatam, VC se nikal gaya!")
    except Exception as e:
        await message.reply_text(f"❌ {str(e)[:100]}")

@app.on_message(filters.command("pause"))
async def pause_cmd(client, message: Message):
    try:
        await pytgcalls.pause_stream(message.chat.id)
        await message.reply_text("⏸️ Ruk gaya!")
    except Exception as e:
        await message.reply_text(f"❌ {str(e)[:100]}")

@app.on_message(filters.command("resume"))
async def resume_cmd(client, message: Message):
    try:
        await pytgcalls.resume_stream(message.chat.id)
        await message.reply_text("▶️ Phir se baj raha hai!")
    except Exception as e:
        await message.reply_text(f"❌ {str(e)[:100]}")

@app.on_message(filters.command("queue"))
async def queue_cmd(client, message: Message):
    chat_id = message.chat.id
    if chat_id not in queues or not queues[chat_id]:
        await message.reply_text("📭 Queue khaali hai!")
        return

    text = "📋 **Queue:**\n\n"
    for i, song in enumerate(queues[chat_id][:10], 1):
        text += f"{i}. {song['title']}\n"
    await message.reply_text(text)

async def main():
    # Handle FloodWait (Telegram rate limit from too many redeploys)
    # Wait instead of crashing
    from pyrogram.errors import FloodWait
    max_retries = 5
    for attempt in range(max_retries):
        try:
            await bot.start()
            break
        except FloodWait as e:
            wait_time = e.value + 10
            logger.warning(f"⏳ FloodWait: waiting {wait_time}s (attempt {attempt+1}/{max_retries})")
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"Bot start failed: {e}")
            raise
    else:
        logger.error("❌ Bot failed to start after retries")
        return
    
    logger.info("🤖 Bot client started")
    if user:
        try:
            await user.start()
            logger.info("👤 User client started")
        except FloodWait as e:
            logger.warning(f"⏳ User client FloodWait: waiting {e.value + 10}s")
            await asyncio.sleep(e.value + 10)
            await user.start()
            logger.info("👤 User client started (after wait)")
    if pytgcalls:
        await pytgcalls.start()
        logger.info("🎵 AXLMUSICBOT is online with VC support!")
    else:
        logger.info("🎵 AXLMUSICBOT is online (file mode)!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    bot.run(main())
