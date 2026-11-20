import logging
import os
import time
import asyncio
import re
import yt_dlp
import shutil
import html
import subprocess
import uuid
from datetime import datetime
from dotenv import load_dotenv
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

load_dotenv()

# ലോഗിങ് സെറ്റ് ചെയ്യുക
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# എൻവയോൺമെന്റ് വേരിയബിളുകൾ
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_IDS = set(map(int, os.getenv("ALLOWED_USER_IDS", "").split(","))) if os.getenv("ALLOWED_USER_IDS") else set()

USER_URLS = {}
USER_THUMBNAILS = {}
USER_STATES = {}
active_downloads = {}
CANCEL_REQUESTS = set()

PLATFORMS = {
    "global":   ["netflix","prime","disney","hbomax","hulu","apple","peacock","paramount"],
    "indian":   ["jiocinema","hotstar","sonyliv","zee5","altbalaji","erosnow","manoramamax","hoichoi"],
    "regional": ["aha","mxplayer","voot","shemaroo","sun_nxt","yupp_tv","stage"],
}
ALL_PLATFORMS = [p for group in PLATFORMS.values() for p in group]
QUALITIES = ["4K", "1080p", "720p", "480p"]

def is_allowed(user_id: int) -> bool:
    return not ALLOWED_IDS or user_id in ALLOWED_IDS

def get_video_duration(file_path):
    try:
        parser = createParser(file_path)
        if parser:
            metadata = extractMetadata(parser)
            if metadata and metadata.has("duration"):
                return int(metadata.get("duration").seconds)
    except Exception:
        pass
    return 0

def download_youtube_thumbnail(thumbnail_url, user_id):
    os.makedirs("thumbnails", exist_ok=True)
    thumb_path = f"thumbnails/yt_{user_id}.jpg"
    try:
        import requests
        response = requests.get(thumbnail_url, timeout=10)
        if response.status_code == 200:
            with open(thumb_path, "wb") as f:
                f.write(response.content)
            if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
                converted_thumb = f"thumbnails/thumb_{user_id}.jpg"
                result = subprocess.run(
                    ["ffmpeg", "-y", "-i", thumb_path, "-vf",
                     "scale=320:320:force_original_aspect_ratio=decrease",
                     "-q:v", "5", converted_thumb],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                if os.path.exists(thumb_path):
                    os.remove(thumb_path)
                if result.returncode == 0 and os.path.exists(converted_thumb):
                    return converted_thumb
                elif os.path.exists(thumb_path):
                    return thumb_path
    except Exception as e:
        logger.error(f"Error downloading thumbnail: {e}")
    return None

def generate_thumbnail(video_path, user_id):
    os.makedirs("thumbnails", exist_ok=True)
    thumb_path = f"thumbnails/auto_{user_id}.jpg"
    try:
        cmd = [
            "ffmpeg", "-ss", "00:00:05", "-i", video_path,
            "-vframes", "1", "-q:v", "2", thumb_path, "-y"
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except Exception as e:
        logger.error(f"Error generating thumbnail: {e}")
    return None

def get_progress_bar(percentage):
    try:
        pct = float(percentage.replace('%', '').strip())
    except:
        pct = 0.0
    completed = int(pct // 10)
    remaining = 10 - completed
    return "█" * completed + "░" * remaining

def get_progress_hook(client, message, user_id, title="Downloading"):
    last_update_time = 0
    def progress_hook(d):
        nonlocal last_update_time
        if user_id in CANCEL_REQUESTS:
            raise Exception("Task cancelled by user.")
        if d['status'] == 'downloading':
            current_time = time.time()
            if current_time - last_update_time > 3:
                percentage = d.get('_percent_str', '0%').strip()
                speed = d.get('_speed_str', 'N/A').strip()
                eta = d.get('_eta_str', 'N/A').strip()
                bar = get_progress_bar(percentage)
                cancel_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✖️ Cancel Task", callback_data=f"cancel_dl_{user_id}")]
                ])
                try:
                    asyncio.run_coroutine_threadsafe(
                        message.edit_text(
                            f"📥 {title}...\n\n"
                            f"[{bar}] {percentage}\n"
                            f"🚀 Speed: {speed}\n"
                            f"⏱️ ETA: {eta}",
                            reply_markup=cancel_keyboard
                        ),
                        client.loop
                    )
                except Exception:
                    pass
                last_update_time = current_time
    return progress_hook

app = Client(
    "jiohotstar_bot",
    api_id=int(API_ID) if API_ID and API_ID.isdigit() else 0,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    if not is_allowed(message.from_user.id):
        await message.reply_text("⛔ You are not authorised to use this bot.")
        return

    text = (
        "🎬 OTT Downloader Bot\n\n"
        "Download from Zee5 & JioHotstar Downloading other OTT platforms directly via Telegram.\n\n"
        "Quick commands:\n"
        "/rip <platform> <url> — Start a download\n"
        "/status — View active downloads\n"
        "/platforms — List all supported platforms\n"
        "/help — Full help"
    )
    await message.reply_text(text)

@app.on_message(filters.command("help") & filters.private)
async def help_cmd(client, message):
    if not is_allowed(message.from_user.id):
        return

    text = (
        "📖 Full Command Reference\n\n"
        "Download:\n"
        "/rip <platform> <url> [quality]\n"
        "  platform → hotstar, jiocinema, netflix, etc.\n"
        "  quality  → 4K, 1080p, 720p, 480p\n\n"
        "Other commands:\n"
        "/status — Active downloads\n"
        "/cancel <job_id> — Cancel a download\n"
        "/platforms — All supported platforms"
    )
    await message.reply_text(text)

@app.on_message(filters.command("platforms") & filters.private)
async def platforms_cmd(client, message):
    if not is_allowed(message.from_user.id):
        return

    text = (
        "📡 Supported Platforms\n\n"
        "🌍 Global\n"
        f"{' · '.join(PLATFORMS['global'])}\n\n"
        "🇮🇳 Indian\n"
        f"{' · '.join(PLATFORMS['indian'])}\n\n"
        "🗺️ Regional\n"
        f"{' · '.join(PLATFORMS['regional'])}"
    )
    await message.reply_text(text)

@app.on_message(filters.command("rip") & filters.private)
async def rip_cmd(client, message):
    if not is_allowed(message.from_user.id):
        await message.reply_text("⛔ Not authorised.")
        return

    args = message.command[1:]
    if len(args) < 2:
        await message.reply_text(
            "❌ Usage: /rip <platform> <url> [quality]\n\n"
            "Example: /rip Zee5 https://... 1080p"
        )
        return

    platform = args[0].lower()
    url      = args[1]
    quality  = args[2].upper() if len(args) > 2 and args[2].upper() in QUALITIES else "1080p"

    if platform not in ALL_PLATFORMS:
        await message.reply_text(
            f"❌ Unknown platform {platform}.\nUse /platforms to see all supported ones."
        )
        return

    job_id   = str(uuid.uuid4())[:8]
    started  = datetime.now().strftime("%H:%M:%S")

    status_msg = await message.reply_text(
        f"⬇️ Download started!\n\n"
        f"🆔 Job ID: {job_id}\n"
        f"📡 Platform: {platform}\n"
        f"🎯 Quality: {quality}\n"
        f"🕐 Started: {started}"
    )
    asyncio.create_task(run_rip_download(job_id, platform, url, quality, status_msg, message.from_user.id))

async def run_rip_download(job_id, platform, url, quality, status_msg, user_id):
    script = f"scripts/{_platform_folder(platform)}/{platform}.py"
    cmd = ["python", script, "--url", url, "--quality", quality]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        active_downloads[job_id] = {"process": proc}
        stdout, stderr = await proc.communicate()

        if user_id in CANCEL_REQUESTS:
            await status_msg.edit_text("❌ Task Cancelled successfully!")
            return

        if proc.returncode == 0:
            await status_msg.edit_text(f"✅ Download complete!\n🆔 Job {job_id} finished successfully.")
        else:
            err = stderr.decode()[-300:] if stderr else "Unknown error"
            await status_msg.edit_text(f"❌ Download failed!\n🆔 Job {job_id}\n\nError:\n{err}")
    except Exception as e:
        await status_msg.edit_text(f"💥 Unexpected error in job {job_id}:\n{str(e)}")

def _platform_folder(platform: str) -> str:
    if platform in PLATFORMS["global"]:   return "global"
    if platform in PLATFORMS["indian"]:   return "indian"
    if platform in PLATFORMS["regional"]: return "regional"
    return "global"

@app.on_message(filters.command("status") & filters.private)
async def status_cmd(client, message):
    if not is_allowed(message.from_user.id):
        return

    if not active_downloads:
        await message.reply_text("📭 No active downloads right now.")
        return

    lines = ["📊 Active Downloads Status\n"]
    for jid in list(active_downloads.keys())[-10:]:
        lines.append(f"⬇️ Job ID: {jid} — Running")

    await message.reply_text("\n".join(lines))

@app.on_message(filters.command("cancel") & filters.private)
async def cancel_cmd(client, message):
    if not is_allowed(message.from_user.id):
        return

    args = message.command[1:]
    if not args:
        await message.reply_text("Usage: /cancel <job_id>")
        return

    job_id = args[0]
    if job_id not in active_downloads:
        await message.reply_text(f"❌ Job {job_id} not found.")
        return

    info = active_downloads[job_id]
    if info.get("process"):
        info["process"].terminate()
    await message.reply_text(f"🚫 Job {job_id} cancelled.")

@app.on_callback_query(filters.regex("^cancel_dl_"))
async def cancel_task_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    target_user_id = int(callback_query.data.split("_")[2])
    if user_id == target_user_id:
        CANCEL_REQUESTS.add(target_user_id)
        await callback_query.answer("⚠️ Task cancellation requested...", show_alert=True)
    else:
        await callback_query.answer("❌ You are not authorized to cancel this task!", show_alert=True)

@app.on_message(filters.private & ~filters.command(["start", "help", "platforms", "rip", "status", "cancel"]))
async def handle_user_inputs(client, message):
    user_id = message.from_user.id
    if not is_allowed(user_id):
        return

    text = message.text.strip() if message.text else ""
    url_match = re.search(r'https?://[^\s]+', text)
    extracted_url = url_match.group(0) if url_match else text

    if any(p in extracted_url for p in ["zee5.com", "zee5.onelink.me", "zee5.com"]):
        parts = text.split("-n")
        url = url_match.group(0) if url_match else parts[0].strip()
        custom_name = parts[1].strip() if len(parts) > 1 else None

        USER_URLS[user_id] = {"url": url, "custom_name": custom_name}
        
        status_msg = await message.reply_text("⏳ Fetching video info & original thumbnail... Please wait!")

        cookie_file_path = "cookies.txt" if os.path.exists("cookies.txt") else None

        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'noplaylist': True,
                'cookiefile': cookie_file_path,
                'nocheckcertificate': True
            }

            def extract_info_fast():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)

            info = await asyncio.to_thread(extract_info_fast)
            title = info.get('title', 'OTT Video')
            thumbnail_url = info.get('thumbnail')
            if not thumbnail_url and 'thumbnails' in info:
                thumbnails = info.get('thumbnails', [])
                if thumbnails:
                    thumbnail_url = thumbnails[-1].get('url')

            yt_thumb_path = None
            if thumbnail_url:
                yt_thumb_path = await asyncio.to_thread(download_youtube_thumbnail, thumbnail_url, user_id)
            if yt_thumb_path:
                USER_THUMBNAILS[f"yt_{user_id}"] = yt_thumb_path

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎵 Extract MP3 Audio", callback_data="dl|audio|mp3|all")],
                [
                    InlineKeyboardButton("📹 240p", callback_data="dl|video|240"),
                    InlineKeyboardButton("📹 360p", callback_data="dl|video|360")
                ],
                [
                    InlineKeyboardButton("📹 480p", callback_data="dl|video|480"),
                    InlineKeyboardButton("📹 576p", callback_data="dl|video|576")
                ],
                [
                    InlineKeyboardButton("📹 720p", callback_data="dl|video|720"),
                    InlineKeyboardButton("📹 1080p", callback_data="dl|video|1080")
                ]
            ])

            caption_text = f"🎬 <b>{title}</b>\n\n👇 <b>Select your preferred quality:</b>"

            if thumbnail_url:
                try:
                    await message.reply_photo(photo=thumbnail_url, caption=caption_text, reply_markup=keyboard)
                    await status_msg.delete()
                    return
                except Exception:
                    pass

            await status_msg.edit_text(caption_text, reply_markup=keyboard)

        except Exception as e:
            logging.exception("Info extraction error: %s", e)
            await status_msg.edit_text(f"❌ Could not fetch video info.\n\nReason: <code>{html.escape(str(e)[:500])}</code>", parse_mode="HTML")
    else:
        await message.reply_text("❌ Please send a valid OTT link or use /rip command.")

@app.on_callback_query(filters.regex(r"^dl\|video\|"))
async def select_audio_languages(client, callback_query: CallbackQuery):
    data_parts = callback_query.data.split("|")
    quality = data_parts[2]
    
    user_id = callback_query.from_user.id
    user_data = USER_URLS.get(user_id)

    if not user_data:
        await callback_query.message.edit_text("❌ Link expired. Please send the link again.")
        return

    detected_langs = ["mal", "tam", "hin", "eng", "tel", "kan"]

    audio_buttons = []
    for lang in detected_langs:
        lang_name = lang.upper()
        audio_buttons.append([
            InlineKeyboardButton(f"🔊 {lang_name} Audio", callback_data=f"dl_final|{quality}|{lang}")
        ])
    
    audio_buttons.append([
        InlineKeyboardButton("🌐 Download All Audio (Multi-Audio)", callback_data=f"dl_final|{quality}|all")
    ])
    audio_buttons.append([
        InlineKeyboardButton("🔙 Back to Quality", callback_data="back_to_quality")
    ])

    await callback_query.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(audio_buttons)
    )
    await callback_query.answer("Select audio language(s):")

@app.on_callback_query(filters.regex(r"^dl_final\|"))
async def download_final_media(client, callback_query: CallbackQuery):
    data_parts = callback_query.data.split("|")
    quality = data_parts[1]
    selected_audio = data_parts[2]
    
    user_id = callback_query.from_user.id
    user_data = USER_URLS.get(user_id)

    if not user_data:
        await callback_query.message.edit_text("❌ Link expired. Please send the link again.")
        return

    url = user_data["url"]
    custom_name = user_data["custom_name"]

    try:
        await callback_query.message.delete()
    except Exception:
        pass

    cancel_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✖️ Cancel Task", callback_data=f"cancel_dl_{user_id}")]
    ])

    status_msg = await client.send_message(
        chat_id=callback_query.message.chat.id,
        text=f"⏳ Initializing download for {quality}p (Audio: {selected_audio.upper()})... Please wait.",
        reply_markup=cancel_keyboard
    )

    if selected_audio == "all":
        format_str = f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best'
    else:
        format_str = f'bestvideo[height<={quality}]+bestaudio[language={selected_audio}]/best[height<={quality}]/best'

    ydl_opts = {
        'format': format_str,
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'merge_output_format': 'mp4',
        'progress_hooks': [get_progress_hook(client, status_msg, user_id, f"Downloading {quality}p Video")],
    }

    if os.path.exists("cookies.txt"):
        ydl_opts['cookiefile'] = "cookies.txt"

    thumb_path = None
    filename = None
    try:
        os.makedirs("downloads", exist_ok=True)
        def run_download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                if user_id in CANCEL_REQUESTS:
                    raise Exception("Task cancelled by user.")
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info)

        filename = await asyncio.to_thread(run_download)

        if user_id in CANCEL_REQUESTS:
            raise Exception("Task cancelled by user.")

        if filename and os.path.exists(filename):
            dir_name, orig_filename = os.path.split(filename)
            _, ext = os.path.splitext(orig_filename)
            
            if custom_name:
                safe_custom_name = custom_name.replace("/", "_").replace("\\", "_")
                new_filename = os.path.join(dir_name, f"{safe_custom_name}_{selected_audio.upper()}{ext}")
                if os.path.exists(new_filename):
                    os.remove(new_filename)
                os.rename(filename, new_filename)
                filename = new_filename

        await status_msg.edit_text("📤 Uploading to Telegram...", reply_markup=cancel_keyboard)

        if user_id in USER_THUMBNAILS and os.path.exists(USER_THUMBNAILS[user_id]):
            thumb_path = USER_THUMBNAILS[user_id]
        elif f"yt_{user_id}" in USER_THUMBNAILS and os.path.exists(USER_THUMBNAILS[f"yt_{user_id}"]):
            thumb_path = USER_THUMBNAILS[f"yt_{user_id}"]
            
        if not thumb_path or not os.path.exists(thumb_path):
            thumb_path = generate_thumbnail(filename, user_id)

        file_name = os.path.basename(filename)
        duration = get_video_duration(filename)

        await client.send_video(
            chat_id=callback_query.message.chat.id,
            video=filename,
            file_name=file_name,
            duration=duration,
            thumb=thumb_path,
            caption=f"📹 Here is your OTT Video ({quality}p - Audio: {selected_audio.upper()})"
        )

        await status_msg.delete()
        
    except Exception as e:
        if user_id in CANCEL_REQUESTS or "cancelled" in str(e).lower():
            await status_msg.edit_text("❌ Task Cancelled successfully!")
        else:
            await status_msg.edit_text(f"❌ Download failed: {str(e)}")
    finally:
        if filename and os.path.exists(filename):
            try:
                os.remove(filename)
            except:
                pass
        if thumb_path and "thumbnails/auto_" in thumb_path and os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
            except:
                pass
        if f"yt_{user_id}" in USER_THUMBNAILS:
            try:
                os.remove(USER_THUMBNAILS[f"yt_{user_id}"])
                del USER_THUMBNAILS[f"yt_{user_id}"]
            except:
                pass
        CANCEL_REQUESTS.discard(user_id)

if __name__ == "__main__":
    print("🤖 OTT Bot is running... Press Ctrl+C to stop.")
    app.run()
