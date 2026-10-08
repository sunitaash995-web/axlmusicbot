# AXLMUSICBOT 🎵

Telegram VC Music Bot — plays music in voice chats + sends audio files.

## Commands
- `/start` — Bot info
- `/play <song>` — Download & send as audio file
- `/vplay <song>` — Play in voice chat
- `/stop` — Leave VC
- `/skip` — Next song
- `/pause` / `/resume` — Control playback
- `/queue` — Show queue

## Railway Deployment

### Environment Variables (set in Railway dashboard):
- `API_ID` — Your Telegram api_id
- `API_HASH` — Your Telegram api_hash
- `SESSION_STRING` — Pyrogram session string (for userbot/VC)
  OR
- `BOT_TOKEN` — Bot token from @BotFather (for file-only mode)

### Getting SESSION_STRING:
```python
from pyrogram import Client
async with Client("test", api_id=YOUR_ID, api_hash="YOUR_HASH") as app:
    print(await app.export_session_string())
```
Run this once, login with your phone number + OTP, copy the string.

### Deploy:
1. Push to GitHub
2. Connect to Railway
3. Add environment variables
4. Deploy! 🚂

## Local Run
```bash
pip install -r requirements.txt
export API_ID=xxx API_HASH=xxx SESSION_STRING=xxx
python main.py
```
