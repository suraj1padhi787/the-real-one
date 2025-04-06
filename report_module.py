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

reporting_tasks = {}
targets = {}
selected_reasons = {}
joined_once = set()

class ReportStates(StatesGroup):
    waiting_for_target = State()

reasons_map = {
    "Spam": InputReportReasonSpam(),
    "Violence": InputReportReasonViolence(),
    "Pornography": InputReportReasonPornography(),
    "Child Abuse": InputReportReasonChildAbuse(),
    "Other": InputReportReasonOther()
}

def get_reason_buttons(selected):
    buttons = [
        types.InlineKeyboardButton(f"{'✅' if r in selected else '☑️'} {r}", callback_data=f"toggle_{r}")
        for r in reasons_map.keys()
    ]
    buttons.append(types.InlineKeyboardButton("🚀 Confirm", callback_data="confirm"))
    return types.InlineKeyboardMarkup(row_width=2).add(*buttons)

def register_report_handlers(dp):
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
            client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
            await client.start()
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

                    # ✅ Start report loop even after leave
                    task = asyncio.create_task(report_loop(client, target, user_id, uname, reasons, session_str, bot))
                    if user_id not in reporting_tasks:
                        reporting_tasks[user_id] = []
                    reporting_tasks[user_id].append((client, task))
                    continue
                except Exception as e:
                    await bot.send_message(user_id, f"⚠️ {uname} couldn't join: {e}")

            task = asyncio.create_task(report_loop(client, target, user_id, uname, reasons, session_str, bot))
            if user_id not in reporting_tasks:
                reporting_tasks[user_id] = []
            reporting_tasks[user_id].append((client, task))

        except Exception as e:
            delete_session_by_string(session_str)
            await bot.send_message(ADMIN_ID, f"❌ Session {uid} deleted due to error: {e}")

async def report_loop(client, target, user_id, uname, reasons, session_str, bot):
    try:
        while True:
            reason = random.choice(reasons)
            try:
                entity = await client.get_entity(target)
                await client(ReportPeerRequest(peer=entity, reason=reasons_map[reason], message="Reported"))
                await bot.send_message(user_id, f"📣 {uname} reported with {reason}")
            except Exception as e:
                delete_session_by_string(session_str)
                await bot.send_message(ADMIN_ID, f"⚠️ {uname} removed during loop: {e}")
                break
            await asyncio.sleep(random.randint(3, 7))  # Fast reporting interval
    except Exception as e:
        delete_session_by_string(session_str)
        await bot.send_message(ADMIN_ID, f"❌ {uname} crashed and removed: {e}")
