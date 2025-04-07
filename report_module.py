import asyncio
import random
from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.account import ReportPeerRequest
from telethon.tl.types import (
    InputReportReasonSpam, InputReportReasonViolence, InputReportReasonPornography,
    InputReportReasonChildAbuse, InputReportReasonOther
)
from db import get_all_sessions, delete_session_by_string, is_admin
from config import API_ID, API_HASH, ADMIN_ID
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import socks

reporting_tasks = {}
targets = {}
selected_reasons = {}
joined_once = set()
user_proxies = {}
active_usernames_list = []
dead_usernames_list = []

class ReportStates(StatesGroup):
    waiting_for_target = State()

def get_random_device_info():
    device_models = ["iPhone 13", "iPhone 14 Pro", "Samsung S22", "Pixel 6", "Xiaomi 12", "OnePlus 10"]
    system_versions = ["iOS 16.4", "iOS 16.5", "Android 12", "Android 13", "MIUI 14"]
    app_versions = ["9.5.1", "9.6.2", "9.4.3", "9.3.1"]

    return {
        "device_model": random.choice(device_models),
        "system_version": random.choice(system_versions),
        "app_version": random.choice(app_versions),
        "lang_code": "en",
        "system_lang_code": "en-US"
    }

def get_safe_client(session_str=None, user_id=None):
    device_info = get_random_device_info()
    proxy = None
    if user_id in user_proxies:
        host, port, user, passwd = user_proxies[user_id]
        proxy = (socks.SOCKS5, host, int(port), True, user, passwd)

    return TelegramClient(
        StringSession(session_str) if session_str else StringSession(),
        API_ID,
        API_HASH,
        device_model=device_info["device_model"],
        system_version=device_info["system_version"],
        app_version=device_info["app_version"],
        lang_code=device_info["lang_code"],
        system_lang_code=device_info["system_lang_code"],
        proxy=proxy
    )

def register_report_handlers(dp):
    @dp.message_handler(commands=["add_proxy"])
    async def add_proxy_cmd(message: types.Message):
        if not is_admin(message.from_user.id):
            return await message.reply("❌ Only admins can use this.")

    @dp.message_handler(commands=["start_report"])
    async def start_report_cmd(message: types.Message):
        if not is_admin(message.from_user.id):
            return await message.reply("❌ Only admins can use this command.")
        await message.reply("🎯 Send the @username or ID of the group/user to report:")
        await ReportStates.waiting_for_target.set()

    @dp.message_handler(state=ReportStates.waiting_for_target)
    async def receive_target(message: types.Message, state: FSMContext):
        targets[message.from_user.id] = message.text.strip()
        selected_reasons[message.from_user.id] = set()
        await message.reply("☑️ Choose reasons to report:", reply_markup=get_reason_buttons(set()))
        await state.finish()

    @dp.callback_query_handler(lambda c: c.data.startswith("toggle_") or c.data == "confirm")
    async def reason_selection(call: types.CallbackQuery):
        user_id = call.from_user.id
        if user_id not in selected_reasons:
            return await call.answer("❌ Use /start_report first")

        if call.data == "confirm":
            reasons = list(selected_reasons[user_id])
            if not reasons:
                return await call.answer("⚠️ Select at least one reason")
            await call.message.edit_text("🚀 Report started!")
            await start_mass_report(user_id, targets[user_id], reasons, call.bot)
            return await call.answer()

        reason = call.data.replace("toggle_", "")
        if reason in selected_reasons[user_id]:
            selected_reasons[user_id].remove(reason)
        else:
            selected_reasons[user_id].add(reason)
        await call.message.edit_reply_markup(reply_markup=get_reason_buttons(selected_reasons[user_id]))
        await call.answer()

    @dp.message_handler(commands=["check_sessions"])
    async def check_sessions_cmd(message: types.Message):
        global active_usernames_list, dead_usernames_list
        active_usernames_list = []
        dead_usernames_list = []

        if not is_admin(message.from_user.id):
            return await message.reply("❌ Only admins can use this command.")

        await message.reply("🔍 Checking all sessions, please wait...")

        sessions = get_all_sessions()
        total = len(sessions)
        valid = 0
        dead = 0

        for uid, session_str in sessions:
            try:
                client = get_safe_client(session_str, message.from_user.id)
                await client.connect()
                me = await client.get_me()
                username = me.username
                if username:
                    active_usernames_list.append(f"🟢 @{username}")
                else:
                    active_usernames_list.append(f"🟢 UID {uid}")
                valid += 1
                await client.disconnect()
            except:
                dead_usernames_list.append(f"🔴 UID {uid} (error, not deleted)")
                dead += 1

        summary = (
            f"✅ **Session Check Completed**\n\n"
            f"🔢 **Total Sessions:** {total}\n"
            f"🟢 **Active:** {valid}\n"
            f"🔴 **Dead:** {dead}"
        )
        await message.reply(summary, parse_mode="Markdown")

        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("🟢 View Active Users", callback_data="show_active_users"),
            InlineKeyboardButton("🔴 View Dead Users", callback_data="show_dead_users")
        )
        await message.reply("⬇️ Choose below to view:", reply_markup=keyboard)

    @dp.callback_query_handler(lambda c: c.data == "show_active_users")
    async def show_active_users(call: types.CallbackQuery):
        if not active_usernames_list:
            await call.message.edit_text("⚠️ No active sessions.")
        else:
            text = "\n".join(active_usernames_list[:50])
            from aiogram.utils.markdown import escape_md
            escaped_text = escape_md(text)
            await call.message.edit_text(f"🟢 *Active Users:*\n\n{escaped_text}", parse_mode="MarkdownV2")

    @dp.callback_query_handler(lambda c: c.data == "show_dead_users")
    async def show_dead_users(call: types.CallbackQuery):
        if not dead_usernames_list:
            await call.message.edit_text("✅ No dead sessions.")
        else:
            text = "\n".join(dead_usernames_list[:50])
            await call.message.edit_text(f"🔴 **Dead Users:**\n\n{text}", parse_mode="Markdown")

    @dp.message_handler(commands=["delete_session"])
    async def delete_specific_session(message: types.Message):
        if not is_admin(message.from_user.id):
            return await message.reply("❌ Only admins can use this command.")
        args = message.get_args()
        if not args:
            return await message.reply("❗ Usage: `/delete_session <uid>`", parse_mode="Markdown")
        uid = args.strip()
        success = delete_session_by_string(uid)
        if success:
            await message.reply(f"✅ Session with UID `{uid}` deleted.", parse_mode="Markdown")
        else:
            await message.reply(f"❌ Session with UID `{uid}` not found.", parse_mode="Markdown")

def get_reason_buttons(selected):
    buttons = [
        types.InlineKeyboardButton(f"{'✅' if r in selected else '☑️'} {r}", callback_data=f"toggle_{r}")
        for r in reasons_map.keys()
    ]
    buttons.append(types.InlineKeyboardButton("🚀 Confirm", callback_data="confirm"))
    return types.InlineKeyboardMarkup(row_width=2).add(*buttons)

reasons_map = {
    "Spam": InputReportReasonSpam(),
    "Violence": InputReportReasonViolence(),
    "Pornography": InputReportReasonPornography(),
    "Child Abuse": InputReportReasonChildAbuse(),
    "Other": InputReportReasonOther()
}

def register_stop_handler(dp):
    @dp.message_handler(commands=["stop_report"])
    async def stop_report_cmd(message: types.Message):
        user_id = message.from_user.id
        if not is_admin(user_id):
            return await message.reply("❌ Only admins can stop reporting.")
        if user_id in reporting_tasks and reporting_tasks[user_id]:
            for client, task in reporting_tasks[user_id]:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                await client.disconnect()
            reporting_tasks.pop(user_id)
            await message.reply("🛑 Reporting stopped.")
        else:
            await message.reply("⚠️ No active reporting found.")

async def start_mass_report(user_id, target, reasons, bot):
    sessions = get_all_sessions()
    if not sessions:
        await bot.send_message(user_id, "❌ No sessions available.")
        return
    for uid, session_str in sessions:
        try:
            client = get_safe_client(session_str, user_id)
            await client.connect()
            me = await client.get_me()
            uname = me.username or me.first_name or str(uid)
            if session_str not in joined_once:
                try:
                    entity = await client.get_entity(target)
                    await client(JoinChannelRequest(entity))
                    await asyncio.sleep(2)
                    await client(ReportPeerRequest(peer=entity, reason=random.choice([reasons_map[r] for r in reasons]), message="Reported"))
                    await asyncio.sleep(2)
                    await client(LeaveChannelRequest(entity))
                    await bot.send_message(user_id, f"✅ {uname} joined, reported & left {target}")
                    joined_once.add(session_str)
                except Exception as e:
                    await bot.send_message(user_id, f"⚠️ {uname} couldn't join: {e}")
            task = asyncio.create_task(report_loop(client, target, user_id, uname, reasons, session_str, bot))
            if user_id not in reporting_tasks:
                reporting_tasks[user_id] = []
            reporting_tasks[user_id].append((client, task))
        except Exception as e:
            await bot.send_message(ADMIN_ID, f"⚠️ Session {uid} error: {e} (not deleted)")

async def report_loop(client, target, user_id, uname, reasons, session_str, bot):
    try:
        while True:
            reason = random.choice(reasons)
            try:
                entity = await client.get_entity(target)
                await client(ReportPeerRequest(peer=entity, reason=reasons_map[reason], message="Reported"))
                await bot.send_message(user_id, f"📣 {uname} reported with {reason}")
            except Exception as e:
                await bot.send_message(ADMIN_ID, f"⚠️ {uname} failed during loop: {e} (not deleted)")
                break
            await asyncio.sleep(random.randint(3, 7))
    except Exception as e:
        await bot.send_message(ADMIN_ID, f"❌ {uname} crashed with error: {e} (not deleted)")
