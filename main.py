import subprocess
import time
from telegram import Update, InputMediaPhoto
from telegram.ext import Application, CommandHandler, ContextTypes
import requests
import json
import logging
import asyncio

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Get the token from .env file
with open(".env", "r") as f:
    lines = f.readlines()
    TOKEN = lines[0].strip()
    GEMINI_API_KEY = lines[1].strip()
    ALLOWED_USERNAME = lines[2].strip()  # Get the allowed username from the .env file

application = Application.builder().token(TOKEN).build()
manual_activity = None
previous_artwork_identifier = None
last_used_image_path = "out.jpg"
previous_media_info = None
previous_manual_activity = None
message_id_to_update = None  # Store the message ID to be updated
last_sent_manual_activity = None  # Track the last manual activity that was sent

# Function to get currently playing media
def get_currently_playing_media():
    global manual_activity
    if manual_activity:
        return manual_activity, None

    result = subprocess.run(['nowplaying-cli', 'get-raw'], capture_output=True, text=True)
    if result.returncode != 0:
        return "Failed to get currently playing media", None

    try:
        media_info = {}
        for line in result.stdout.splitlines():
            if " = " in line:
                key, value = line.split(" = ", 1)
                media_info[key.strip()] = value.strip().strip('"')

        title = media_info.get("kMRMediaRemoteNowPlayingInfoTitle", "Something... IDK")
        album = media_info.get("kMRMediaRemoteNowPlayingInfoAlbum", " ")
        artist = media_info.get("kMRMediaRemoteNowPlayingInfoArtist", "Unknown Artist")
        playback_rate = media_info.get("kMRMediaRemoteNowPlayingInfoPlaybackRate", "0")
        artwork_identifier = media_info.get("kMRMediaRemoteNowPlayingInfoArtworkIdentifier", None)

        title = title.split(";", 1)[0].replace('"', '')
        album = album.split(";", 1)[0].replace('"', '')
        artist = artist.split(";", 1)[0].replace('"', '')
        playback_rate = playback_rate.split(";", 1)[0].replace('"', '')

        # Determine play or pause emoji
        play_pause_emoji = "▶️" if playback_rate == "1" else "⏸️"

        title = f"{play_pause_emoji} {title} - {album}\n🎤 {artist}"

        return f"{title}", artwork_identifier
    except Exception as e:
        logging.error(f"Error parsing media info: {e}")
        return f"Error parsing media info: {e}", None

# Function to extract and save album art
def extract_and_save_album_art(filename="out.jpg"):
    try:
        # Run the command to extract and decode the artwork data
        command = "nowplaying-cli get artworkData | base64 --decode | ffmpeg -y -i pipe:0 out.jpg"
        subprocess.run(command, shell=True, check=True)
        return filename
    except subprocess.CalledProcessError as e:
        logging.error(f"Error extracting and saving album art: {e}")
        return None

# Function to send a message with retry logic
async def send_message(chat_id, message, context, image=None):
    retries = 5
    for i in range(retries):
        try:
            if image:
                return await context.bot.send_photo(chat_id=chat_id, photo=open(image, 'rb'), caption=message)
            else:
                return await context.bot.send_message(chat_id=chat_id, text=message)
        except Exception as e:
            logging.error(f"Error sending message: {e}")
            if i < retries - 1:
                await asyncio.sleep(2 ** i)  # Exponential backoff
            else:
                raise

# Define the activity command
async def activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global previous_artwork_identifier, last_used_image_path, previous_media_info, message_id_to_update
    try:
        chat_id = update.effective_chat.id
        media_info, artwork_identifier = get_currently_playing_media()

        # Check if the new media info or artwork identifier is different from the previous one
        if media_info != previous_media_info or artwork_identifier != previous_artwork_identifier or not manual_activity:
            previous_media_info = media_info
            previous_artwork_identifier = artwork_identifier
            album_art_path = extract_and_save_album_art()

            message = await send_message(chat_id, media_info, context, image=album_art_path)
            message_id_to_update = message.message_id  # Store the message ID to update it later
        elif manual_activity:
            message = await send_message(chat_id, manual_activity, context)
            message_id_to_update = message.message_id  # Store the message ID to update it later
        else:
            message = await send_message(chat_id, media_info, context, image=last_used_image_path)
            message_id_to_update = message.message_id  # Store the message ID to update it later

        # Start the update loop
        asyncio.create_task(update_activity_loop(context, chat_id, message_id_to_update))
    except Exception as e:
        logging.error(f"Error in activity command: {e}")

# Function to update the activity message every 10 seconds
async def update_activity_loop(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    global previous_artwork_identifier, last_used_image_path, previous_media_info, previous_manual_activity, last_sent_manual_activity
    sleep_time = 10
    loop = True
    while loop:
        try:
            media_info, artwork_identifier = get_currently_playing_media()

            # Check if manual activity is set and has changed
            if manual_activity != None:
                if manual_activity != last_sent_manual_activity:  # Only send if it's different
                    if manual_activity == "Working":
                        album_art_path = "working.jpeg"
                    elif manual_activity == "Sleeping":
                        album_art_path = "sleeping.jpeg"
                    elif manual_activity == "Do not Disturb":
                        album_art_path = "dnd.jpeg"
                    else:
                        album_art_path = "logo.jpeg"
                    media = InputMediaPhoto(media=open(album_art_path, 'rb'), caption=manual_activity)
                    await context.bot.edit_message_media(chat_id=chat_id, message_id=message_id, media=media)
                    await context.bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=manual_activity)
                    last_sent_manual_activity = manual_activity  # Update the last sent manual activity
                    sleep_time = 60
                loop = False
            else:
                # Check if the new media info or artwork identifier is different from the previous one
                if artwork_identifier != previous_artwork_identifier or media_info != previous_media_info:
                    previous_media_info = media_info
                    previous_artwork_identifier = artwork_identifier
                    album_art_path = extract_and_save_album_art()

                    if album_art_path:
                        last_used_image_path = album_art_path
                        media = InputMediaPhoto(media=open(album_art_path, 'rb'), caption=media_info)
                    else:
                        media = InputMediaPhoto(media=open(last_used_image_path, 'rb'), caption=media_info)

                    await context.bot.edit_message_media(chat_id=chat_id, message_id=message_id, media=media)
                    await context.bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=media_info)
                    sleep_time = 10

        except Exception as e:
            logging.error(media_info)
            logging.error(f"Error in update_activity_loop: {e}")
        await asyncio.sleep(sleep_time)

# Define the clear command to reset manual activity and update the message
async def clear_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global manual_activity, previous_manual_activity, message_id_to_update, last_sent_manual_activity
    try:
        chat_id = update.effective_chat.id
        user = update.effective_user

        # Check if the user is allowed to execute this command
        if user.username != ALLOWED_USERNAME:
            await context.bot.send_message(chat_id=chat_id, text="You are not authorized to use this command.")
            return

        manual_activity = None  # Clear the manual activity
        previous_manual_activity = None  # Reset the previous manual activity
        last_sent_manual_activity = None  # Reset the last sent manual activity to allow updates

        # Fetch the currently playing media again to update the message
        media_info, artwork_identifier = get_currently_playing_media()
        album_art_path = extract_and_save_album_art()

        if album_art_path:
            media = InputMediaPhoto(media=open(album_art_path, 'rb'), caption=media_info)
        else:
            media = InputMediaPhoto(media=open(last_used_image_path, 'rb'), caption=media_info)

        # Update the same message that was originally sent by the `/activity` command
        if message_id_to_update:
            await context.bot.edit_message_media(chat_id=chat_id, message_id=message_id_to_update, media=media)
            await context.bot.edit_message_caption(chat_id=chat_id, message_id=message_id_to_update, caption=media_info)
        else:
            await context.bot.send_message(chat_id=chat_id, text="No activity message found to update.")
    except Exception as e:
        logging.error(f"Error in clear_activity command: {e}")

# Define the start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
        await context.bot.send_message(chat_id=chat_id, text="Hello! I'm a bot that can send you a random cat picture. Just type /cat to get one!")
    except Exception as e:
        logging.error(f"Error in start command: {e}")

# Define the cat command
async def cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
        response = requests.get("https://api.thecatapi.com/v1/images/search")
        cat_url = response.json()[0]["url"]
        await context.bot.send_photo(chat_id=chat_id, photo=cat_url)
    except Exception as e:
        logging.error(f"Error in cat command: {e}")

# Define the set command
async def set_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        global previous_manual_activity
        previous_manual_activity = None
        global manual_activity
        chat_id = update.effective_chat.id
        user = update.effective_user

        # Check if the user is allowed to execute this command
        if user.username != ALLOWED_USERNAME:
            await context.bot.send_message(chat_id=chat_id, text="You are not authorized to use this command.")
            return

        manual_activity = update.message.text.split(' ', 1)[1]
        await context.bot.send_message(chat_id=chat_id, text=f"Activity set to: {manual_activity}")
    except Exception as e:
        logging.error(f"Error in set_activity command: {e}")

# Define the Gemini AI chat command
async def chat_with_gemini(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
        user_message = update.message.text.split(' ', 1)[1]

        headers = {
            'Content-Type': 'application/json',
        }
        data = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": user_message
                        }
                    ]
                }
            ]
        }

        response = requests.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}',
            headers=headers,
            data=json.dumps(data)
        )

        if response.status_code == 200:
            gemini_response = response.json()
            reply_text = gemini_response['candidates'][0]['content']['parts'][0]['text']
        else:
            reply_text = "Failed to get a response from Gemini AI."

        await context.bot.send_message(chat_id=chat_id, text=reply_text)
    except Exception as e:
        logging.error(f"Error in chat_with_gemini command: {e}")

# Add the handlers to the application
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("cat", cat))
application.add_handler(CommandHandler("activity", activity))
application.add_handler(CommandHandler("set", set_activity))
application.add_handler(CommandHandler("clear", clear_activity))
application.add_handler(CommandHandler("chat", chat_with_gemini))

# Start the Bot
print("Bot started!")
application.run_polling()