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
from db import get_all_sessions, delete_session_by_string, is_admin, save_user_proxies, get_user_proxies
from config import API_ID, API_HASH, ADMIN_ID

reporting_tasks = {}
targets = {}
selected_reasons = {}
joined_once = set()
report_stats = {}  # user_id: {sent: int, dead: int, total: int}
report_message_id = {}  # user_id: message_id of live report panel

class ProxyState(StatesGroup):
    waiting_for_proxy = State()

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

def update_stats_panel(user_id, bot):
    async def _update():
        if user_id not in report_stats or user_id not in report_message_id:
            return
        stats = report_stats[user_id]
        text = f"📊 *Live Report Stats*\n\n"
        text += f"📤 Sent Reports: `{stats.get('sent', 0)}`\n"
        text += f"💀 Dead Sessions: `{stats.get('dead', 0)}`\n"
        text += f"🧾 Total Sessions: `{stats.get('total', 0)}`"
        try:
            await bot.edit_message_text(chat_id=user_id, message_id=report_message_id[user_id], text=text, parse_mode="Markdown")
        except:
            pass
    return _update()

def register_report_handlers(dp):
    @dp.message_handler(commands=["add_proxy"])
    async def add_proxy_cmd(message: types.Message):
        if not is_admin(message.from_user.id):
            return await message.reply("❌ Only admins can add proxies.")
        await message.reply("📡 Send proxies in format: type,ip,port\nExample:\nsocks5,1.2.3.4,1080")
        await ProxyState.waiting_for_proxy.set()

    @dp.message_handler(state=ProxyState.waiting_for_proxy)
    async def save_proxy_input(message: types.Message, state: FSMContext):
        lines = message.text.strip().split("\n")
        proxies = []
        for line in lines:
            try:
                t, ip, port = line.strip().split(",")
                proxies.append((t.strip(), ip.strip(), int(port.strip())))
            except:
                continue
        save_user_proxies(message.from_user.id, proxies)
        await message.reply(f"✅ Saved {len(proxies)} proxies.")
        await state.finish()

    @dp.message_handler(commands=["start_report"])
    async def start_report_cmd(message: types.Message):
        if not is_admin(message.from_user.id):
            return await message.reply("❌ Only admins can use this.")
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
            stats_text = "📊 *Live Report Stats*\n\n📤 Sent Reports: `0`\n💀 Dead Sessions: `0`\n🧾 Total Sessions: `...`"
            msg = await call.message.answer(stats_text, parse_mode="Markdown")
            report_message_id[user_id] = msg.message_id
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
            return await message.reply("❌ Only admins can stop reports.")
        if user_id in reporting_tasks:
            for client, task in reporting_tasks[user_id]:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                await client.disconnect()
            reporting_tasks.pop(user_id)
            await message.reply("🛑 Report stopped.")
        else:
            await message.reply("⚠️ No active reporting found.")

    @dp.message_handler(commands=["check_sessions"])
    async def check_sessions(message: types.Message):
        if not is_admin(message.from_user.id):
            return await message.reply("❌ Only admins can use this.")
        sessions = get_all_sessions()
        alive, dead = [], []
        for uid, sess in sessions:
            try:
                client = TelegramClient(StringSession(sess), API_ID, API_HASH)
                await client.connect()
                user = await client.get_me()
                name = user.username or user.first_name or user.phone
                link = f"[{name}](https://t.me/{user.username})" if user.username else name
                alive.append(link)
                await client.disconnect()
            except:
                delete_session_by_string(sess)
                dead.append(str(uid))
        reply = f"✅ Alive: {len(alive)}\n" + "\n".join(alive)
        if dead:
            reply += f"\n\n💀 Dead: {len(dead)}\n" + "\n".join(dead)
        await message.reply(reply, parse_mode="Markdown")

    @dp.message_handler(commands=["status_report"])
    async def status_report(message: types.Message):
        user_id = message.from_user.id
        if not is_admin(user_id):
            return await message.reply("❌ Only admins can use this.")
        stats = report_stats.get(user_id)
        if not stats:
            return await message.reply("ℹ️ No active report found.")
        text = (
            "📊 *Live Report Stats*\n\n"
            f"📤 Sent Reports: `{stats.get('sent', 0)}`\n"
            f"💀 Dead Sessions: `{stats.get('dead', 0)}`\n"
            f"🧾 Total Sessions: `{stats.get('total', 0)}`"
        )
        await message.reply(text, parse_mode="Markdown")

async def start_mass_report(user_id, target, reasons, bot):
    sessions = get_all_sessions()
    if not sessions:
        await bot.send_message(user_id, "❌ No sessions available.")
        return

    proxies = get_user_proxies(user_id)
    report_stats[user_id] = {"sent": 0, "dead": 0, "total": len(sessions)}
    for idx, (uid, session_str) in enumerate(sessions):
        try:
            proxy = proxies[idx % len(proxies)] if proxies else None
            client = TelegramClient(StringSession(session_str), API_ID, API_HASH, proxy=proxy) if proxy else TelegramClient(StringSession(session_str), API_ID, API_HASH)
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
                    await bot.send_message(user_id, f"✅ {uname} joined, reported & left.")
                    joined_once.add(session_str)
                except Exception as e:
                    await bot.send_message(user_id, f"⚠️ {uname} couldn't join: {e}")
            else:
                await bot.send_message(user_id, f"⚠️ {uname} already joined. Skipping join.")

            task = asyncio.create_task(report_loop(client, target, user_id, uname, reasons, session_str, bot))
            if user_id not in reporting_tasks:
                reporting_tasks[user_id] = []
            reporting_tasks[user_id].append((client, task))

        except Exception as e:
            delete_session_by_string(session_str)
            report_stats[user_id]["dead"] += 1
            await update_stats_panel(user_id, bot)
            await bot.send_message(ADMIN_ID, f"❌ Session {uid} deleted: {e}")

async def report_loop(client, target, user_id, uname, reasons, session_str, bot):
    try:
        while True:
            reason = random.choice(reasons)
            try:
                entity = await client.get_entity(target)
                await client(ReportPeerRequest(peer=entity, reason=reasons_map[reason], message="Reported"))
                report_stats[user_id]["sent"] += 1
                await update_stats_panel(user_id, bot)
                await bot.send_message(user_id, f"📣 {uname} reported with {reason}")
            except Exception as e:
                delete_session_by_string(session_str)
                report_stats[user_id]["dead"] += 1
                await update_stats_panel(user_id, bot)
                await bot.send_message(ADMIN_ID, f"⚠️ {uname} removed from system: {e}")
                break
            await asyncio.sleep(random.randint(3, 7))
    except Exception as e:
        delete_session_by_string(session_str)
        report_stats[user_id]["dead"] += 1
        await update_stats_panel(user_id, bot)
        await bot.send_message(ADMIN_ID, f"❌ {uname} crashed: {e}")
