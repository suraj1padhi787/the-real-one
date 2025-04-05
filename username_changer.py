import asyncio
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import UpdateUsernameRequest as ChannelUsernameUpdate
from telethon.tl.types import PeerChannel
from db import get_session
from config import API_ID, API_HASH

running_tasks = {}

async def start_username_changer(user_id, group_username, usernames, interval):
    session_string = get_session(user_id)
    if not session_string:
        return "❌ Session not found. Please login using /start."

    client = TelegramClient(StringSession(session_string), int(API_ID), API_HASH)
    await client.connect()

    try:
        entity = await client.get_entity(f"@{group_username}")
        channel_id = entity.id
        access_hash = entity.access_hash
        peer = PeerChannel(channel_id)
    except Exception as e:
        return f"❌ Failed to fetch group: {e}"

    index = 0

    async def changer():
        nonlocal index
        while True:
            new_username = usernames[index % len(usernames)]
            try:
                await client(ChannelUsernameUpdate(channel=peer, username=new_username))
                print(f"[✅] Changed to {new_username}")
            except Exception as e:
                print(f"[❌] Failed to change username: {e}")
            index += 1
            await asyncio.sleep(interval)

    task = asyncio.create_task(changer())
    running_tasks[user_id] = {
        "task": task,
        "client": client
    }

    return "✅ Group username changer started."

async def stop_username_changer(user_id):
    if user_id in running_tasks:
        running_tasks[user_id]["task"].cancel()
        await running_tasks[user_id]["client"].disconnect()
        del running_tasks[user_id]
        return "🛑 Username changer stopped."
    return "⚠️ No active changer running."
