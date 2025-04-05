import os
import logging
import asyncio
import random
from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError
from telethon.tl.functions.account import ReportPeerRequest
from telethon.tl.types import (
    InputReportReasonSpam, InputReportReasonViolence, InputReportReasonPornography,
    InputReportReasonChildAbuse, InputReportReasonOther
)
from db import get_session, get_all_sessions, delete_session_by_string, is_admin
from config import API_ID, API_HASH, ADMIN_ID

class MassReportState(StatesGroup):
    waiting_for_target = State()
    waiting_for_reasons = State()

reporting_tasks = {}
target_ids = {}
selected_reasons = {}

# 🔘 Create inline buttons for selecting reasons
def get_reason_buttons(selected):
    buttons = []
    all_reasons = ["Spam", "Violence", "Pornography", "Child Abuse", "Other"]
    for r in all_reasons:
        text = f"✅ {r}" if r in selected else f"☑️ {r}"
        buttons.append(types.InlineKeyboardButton(text, callback_data=f"toggle_{r}"))
    buttons.append(types.InlineKeyboardButton("✅ Confirm", callback_data="confirm_reasons"))
    return types.InlineKeyboardMarkup(row_width=2).add(*buttons)

def register_report_handlers(dp):

    @dp.message_handler(commands=["start_report"])
    async def start_report(message: types.Message):
        if not is_admin(message.from_user.id):
            return await message.reply("❌ Only admins can use this command.")
        await message.reply("🆔 Send target username or ID:")
        target_ids[message.from_user.id] = None
        selected_reasons[message.from_user.id] = set()
        await MassReportState.waiting_for_target.set()

    @dp.message_handler(state=MassReportState.waiting_for_target)
    async def get_target(message: types.Message, state: FSMContext):
        target_ids[message.from_user.id] = message.text.strip()
        await message.reply("🔘 Select reasons to report:", reply_markup=get_reason_buttons(set()))
        await state.finish()

    @dp.callback_query_handler(lambda call: call.data.startswith("toggle_") or call.data == "confirm_reasons")
    async def handle_reason_selection(call: types.CallbackQuery):
        user_id = call.from_user.id
        if user_id not in selected_reasons:
            return await call.answer("❌ Start with /start_report")
        if call.data == "confirm_reasons":
            reasons = list(selected_reasons[user_id])
            if not reasons:
                return await call.answer("⚠️ Select at least 1 reason")
            await call.message.edit_text("✅ Report started.")
            await call.answer()
            await start_mass_report(user_id, target_ids[user_id], reasons, call.bot)
        else:
            reason = call.data.replace("toggle_", "")
            selected_reasons[user_id].symmetric_difference_update({reason})
            await call.message.edit_reply_markup(reply_markup=get_reason_buttons(selected_reasons[user_id]))
            await call.answer()

    @dp.message_handler(commands=["check_sessions"])
    async def check_sessions(message: types.Message):
        if not is_admin(message.from_user.id):
            return await message.reply("❌ Only admins can use this command.")

        sessions = get_all_sessions()
        total = len(sessions)
        alive, dead = 0, 0
        dead_sessions = []
        active_usernames = []

        for uid, session_str in sessions:
            try:
                client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
                await client.connect()
                me = await client.get_me()
                active_usernames.append(me.username or me.first_name)
                await client.disconnect()
                alive += 1
            except:
                dead += 1
                dead_sessions.append(session_str)

        buttons = types.InlineKeyboardMarkup()
        if dead_sessions:
            buttons.add(types.InlineKeyboardButton("🗑️ Delete Dead Sessions", callback_data="delete_dead"))
        if active_usernames:
            buttons.add(types.InlineKeyboardButton("👤 Show Usernames", callback_data="show_usernames"))

        await message.reply(f"📊 Session Report:\n✅ Active: {alive}\n❌ Dead: {dead}\n📦 Total: {total}", reply_markup=buttons)

        @dp.callback_query_handler(lambda call: call.data == "delete_dead")
        async def delete_dead_sessions(call: types.CallbackQuery):
            count = 0
            for session_str in dead_sessions:
                delete_session_by_string(session_str)
                count += 1
            await call.message.edit_text(f"🗑️ Deleted {count} dead sessions.")
            await call.answer("✅ Cleaned")

        @dp.callback_query_handler(lambda call: call.data == "show_usernames")
        async def show_usernames_handler(call: types.CallbackQuery):
            await call.message.answer("👥 Active Usernames:\n" + "\n".join(active_usernames))
            await call.answer()


async def start_mass_report(user_id, target, reasons, bot):
    reason_objs = {
        "Spam": InputReportReasonSpam(),
        "Violence": InputReportReasonViolence(),
        "Pornography": InputReportReasonPornography(),
        "Child Abuse": InputReportReasonChildAbuse(),
        "Other": InputReportReasonOther()
    }

    sessions = get_all_sessions()
    if not sessions:
        await bot.send_message(user_id, "❌ No sessions found.")
        return

    for uid, session_string in sessions:
        try:
            client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
            await client.start()
            me = await client.get_me()
            task = asyncio.create_task(report_loop(client, target, reasons, me.username, reason_objs, bot, user_id))
            if user_id not in reporting_tasks:
                reporting_tasks[user_id] = []
            reporting_tasks[user_id].append((client, task))
        except Exception as e:
            await bot.send_message(user_id, f"❌ Error in session: {e}")


async def report_loop(client, target_id, reasons, user, reason_objs, bot, user_id):
    while True:
        reason = random.choice(reasons)
        try:
            await client(ReportPeerRequest(peer=target_id, reason=reason_objs[reason], message="Reported by bot"))
            await bot.send_message(user_id, f"✅ {user} reported - {reason}")
        except Exception as e:
            await bot.send_message(user_id, f"❌ Error by {user}: {str(e)}")
        await asyncio.sleep(random.randint(0, 60))

# Stop handler
def register_stop_handler(dp):
    @dp.message_handler(commands=["stop_report"])
    async def stop_report(message: types.Message):
        user_id = message.from_user.id
        if not is_admin(user_id):
            return await message.reply("❌ Only admins can stop reporting.")

        if user_id in reporting_tasks:
            for client, task in reporting_tasks[user_id]:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                await client.disconnect()
            reporting_tasks.pop(user_id)
            await message.answer("🛑 Reporting stopped.")
        else:
            await message.answer("⚠️ No active reporting.")
