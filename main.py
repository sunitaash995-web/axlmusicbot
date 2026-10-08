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
# Compatibility shim: py-tgcalls expects GroupcallForbidden in pyrogram.errors
# but newer pyrogram versions removed it. Add it back as an alias.
try:
    import pyrogram.errors as _pge
    if not hasattr(_pge, 'GroupcallForbidden'):
        # Alias to a similar existing error
        _pge.GroupcallForbidden = getattr(_pge, 'GroupcallAddParticipantsFailed', Exception)
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
YTDL_OPTS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "extract_flat": False,
    "default_search": "ytsearch",
    "noplaylist": True,
}

# Initialize clients
app = Client(
    "axlmusicbot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING if SESSION_STRING else None,
    bot_token=BOT_TOKEN if BOT_TOKEN and not SESSION_STRING else None,
)

# VC client (only if available and session string provided)
pytgcalls = None
if VC_AVAILABLE and SESSION_STRING:
    try:
        pytgcalls = PyTgCalls(app)
    except Exception as e:
        logger.warning(f"PyTgCalls init failed (VC disabled): {e}")
        pytgcalls = None

# Queue per chat
queues = {}

def get_audio_url(query: str) -> dict:
    """Search YouTube and get direct audio URL + metadata"""
    with yt_dlp.YoutubeDL(YTDL_OPTS) as ydl:
        # If query is not a URL, search
        if not query.startswith("http"):
            query = f"ytsearch1:{query}"
        info = ydl.extract_info(query, download=False)
        if "entries" in info:
            info = info["entries"][0]
        return {
            "url": info["url"],
            "title": info.get("title", "Unknown"),
            "duration": info.get("duration", 0),
            "thumbnail": info.get("thumbnail", ""),
            "webpage_url": info.get("webpage_url", ""),
        }

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
        dl_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "outtmpl": f"/tmp/%(title)s.%(ext)s",
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

        # If not already playing, start
        try:
            await pytgcalls.play(chat_id, MediaStream(info["url"]))
        except Exception:
            # Already in call, just queued
            pass

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
    await app.start()
    if pytgcalls:
        await pytgcalls.start()
        logger.info("🎵 AXLMUSICBOT is online with VC support!")
    else:
        logger.info("🎵 AXLMUSICBOT is online (file mode)!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    app.run(main())
