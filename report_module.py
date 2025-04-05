import asyncio
import random
from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.account import ReportPeerRequest
from telethon.tl.types import (
    InputReportReasonSpam, InputReportReasonViolence, InputReportReasonPornography,
    InputReportReasonChildAbuse, InputReportReasonOther
)
from db import get_all_sessions, delete_session_by_string, is_admin
from config import API_ID, API_HASH

class MassReportState(StatesGroup):
    waiting_for_target = State()

reporting_tasks = {}
target_ids = {}
selected_reasons = {}

# Inline buttons for selecting reasons
def get_reason_buttons(selected):
    reasons = ["Spam", "Violence", "Pornography", "Child Abuse", "Other"]
    buttons = [
        types.InlineKeyboardButton(
            f"{'✅' if r in selected else '☑️'} {r}", callback_data=f"toggle_{r}"
        )
        for r in reasons
    ]
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
        alive_users = []
        dead_users = []
        dead_session_strings = []

        for uid, session_str in sessions:
            try:
                client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
                await client.connect()
                me = await client.get_me()
                username = me.username or me.first_name or "No Username"
                mention = f"@{username}" if me.username else username
                alive_users.append(f"{uid}: {mention}")

                await client.disconnect()
                alive += 1
            except:
                dead_users.append(f"{uid}")
                dead_session_strings.append(session_str)
                dead += 1

        # Buttons
        buttons = types.InlineKeyboardMarkup(row_width=1)
        if alive_users:
            buttons.add(types.InlineKeyboardButton("👤 Show Active Users", callback_data="show_active_users"))
        if dead_users:
            buttons.add(types.InlineKeyboardButton("💀 Show Dead Users", callback_data="show_dead_users"))
            buttons.add(types.InlineKeyboardButton("🗑️ Delete Dead Sessions", callback_data="delete_dead"))

        # Store for callback
        message.bot._alive_users = alive_users
        message.bot._dead_users = dead_users
        message.bot._dead_session_strings = dead_session_strings

        await message.reply(
            f"📊 Session Report:\n✅ Active: {alive}\n❌ Dead: {dead}\n📦 Total: {total}",
            reply_markup=buttons
        )

    @dp.callback_query_handler(lambda call: call.data in ["show_active_users", "show_dead_users", "delete_dead"])
    async def handle_session_buttons(call: types.CallbackQuery):
        if call.data == "show_active_users":
            users = getattr(call.bot, "_alive_users", [])
            await call.message.answer("👤 Active Users:\n" + "\n".join(users or ["None"]))
        elif call.data == "show_dead_users":
            users = getattr(call.bot, "_dead_users", [])
            await call.message.answer("💀 Dead Users:\n" + "\n".join(users or ["None"]))
        elif call.data == "delete_dead":
            dead_sessions = getattr(call.bot, "_dead_session_strings", [])
            for session in dead_sessions:
                delete_session_by_string(session)
            await call.message.answer(f"🗑️ Deleted {len(dead_sessions)} dead sessions.")
        await call.answer()

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

# Core logic for reporting
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
