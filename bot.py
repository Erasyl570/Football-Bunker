import asyncio
import html
import json
import os
import random
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import LinkPreviewOptions
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web
import google.generativeai as genai

import database as db
import cards

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

CARD_IMAGES = {
    "position": "https://i.ibb.co/rRQQn3Jk/E17684-E2-56-F4-4-D66-8727-A0411741-F102.png", 
    "age": "https://i.ibb.co/SXVZG1m8/E13-D392-F-B727-4782-BE64-4-B3-FF6460-AE0.png",
    "price": "https://i.ibb.co/gbtmrfbt/2-E013-C9-A-F562-435-D-8445-057199144-D69.png",
    "health": "https://i.ibb.co/RktLxj4k/BE52-B7-D1-DE73-4-E85-9641-0242-E4-FA816-F.png",
    "skill": "https://i.ibb.co/jPN42k2b/0-F0274-A2-6320-4-EE8-9-F2-F-9-CB9-E2-C35209.png",
    "inventory": "https://i.ibb.co/qL8JH60p/5-A3-AD710-D4-C9-443-B-AD86-14029-ED7-D237.png",
    "secret": "https://i.ibb.co/LzJ1zstj/7-DC7-EFD5-B294-4-EC8-BE3-D-EDF77-BFD93-DF.png"
}

SPECIAL_CARD_NAMES = {
    "swap_position": "🔄 Фиксированный обмен [Позиция]",
    "swap_age": "🔄 Фиксированный обмен [Возраст]",
    "swap_price": "🔄 Фиксированный обмен [Цена]",
    "swap_health": "🔄 Фиксированный обмен [Здоровье]",
    "swap_skill": "🔄 Фиксированный обмен [Навык]",
    "swap_inventory": "🔄 Фиксированный обмен [Багаж]",
    "swap_secret": "🔄 Фиксированный обмен [Секрет]",
    "spy": "👁 Шпионаж",
    "yellow_card": "🟨 Желтая карточка",
    "flash": "📸 Вспышка (Публичность)",
    "mirror": "🪞 Зеркальный щит",
    "chaos": "🎲 Трансферный хаос"
}

SPECIAL_CARD_DESCRIPTIONS = {
    "swap_position": "Обменивает твою карту «Позиция» на позицию любого игрока.",
    "swap_age": "Обменивает твою карту «Возраст» на возраст любого игрока.",
    "swap_price": "Обменивает твою карту «Цена» на цену любого игрока.",
    "swap_health": "Обменивает твою карту «Здоровье» на здоровье любого игрока.",
    "swap_skill": "Обменивает твою карту «Навык» на навык любого игрока.",
    "swap_inventory": "Обменивает твой «Багаж» на багаж любого игрока.",
    "swap_secret": "Обменивает твой «Секрет» на секрет любого игрока.",
    "spy": "Позволяет скрыто подсмотреть 1 закрытую карту любого соперника.",
    "yellow_card": "Блокирует использование спец-карты выбранному игроку до конца игры.",
    "flash": "Принудительно вскрывает любую выбранную тобой закрытую карту соперника в общий чат.",
    "mirror": "Защита: отражает действие следующей примененной против тебя спец-карты обратно.",
    "chaos": "Случайно меняет одну твою закрытую карту с закрытой картой соперника."
}

TRAIT_LABELS = {
    "position": "Позиция", "age": "Возраст", "price": "Трансферная цена",
    "health": "Здоровье", "skill": "Навык", "inventory": "Багаж", "secret": "Секрет"
}

async def is_game_active(chat_id: int) -> bool:
    lobby = await db.get_lobby(chat_id)
    return lobby is not None and lobby[0] not in ("ended", "cancelled")

# --- ОЦЕНКА ИТОГОВ С GEMINI AI (ЧЕРЕЗ SDK С ТАЙМАУТОМ) ---
async def evaluate_game_outcome(scenario_text: str, winners_data: list) -> str:
    if not GEMINI_API_KEY:
        return "⚠️ <i>GEMINI_API_KEY не задан в переменных окружения. Оценка сценария недоступна.</i>"

    players_summary = ""
    for name, pack in winners_data:
        players_summary += (
            f"- Игрок {name}: Позиция={pack.get('position')}, Возраст={pack.get('age')}, "
            f"Цена={pack.get('price')}, Здоровье={pack.get('health')}, Навык={pack.get('skill')}, "
            f"Багаж={pack.get('inventory')}, Секрет={pack.get('secret')}\n"
        )

    prompt = (
        f"Ты — суровый, честный и объективный футбольный эксперт.\n"
        f"Оцени, смогла ли оставшаяся команда выполнить цель сценария.\n\n"
        f"СЦЕНАРИЙ И ЦЕЛЬ:\n{scenario_text}\n\n"
        f"СОСТАВ ПОБЕДИТЕЛЕЙ И ИХ КАРТОЧКИ:\n{players_summary}\n\n"
        f"Правила ответа:\n"
        f"1. Будь МАКСИМАЛЬНО честен. Если карты игроков не подходят под цель сценария — пиши ПРОВАЛ.\n"
        f"2. Ответ должен быть очень коротким (2-3 предложения).\n"
        f"3. Формат:\n"
        f"📌 <b>ВЕРДИКТ ИИ:</b> [УСПЕХ или ПРОВАЛ]\n"
        f"📝 <b>Причина:</b> [Короткое честное объяснение]"
    )

    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        # Вызов с таймаутом в 120 секунд для долгой генерации
        response = await model.generate_content_async(
            prompt,
            request_options={"timeout": 120}
        )
        if response and response.text:
            return response.text.strip()
        return "⚠️ <i>Не удалось получить вердикт от ИИ.</i>"
    except Exception as e:
        return f"⚠️ <i>Ошибка вызова AI: {e}</i>"

async def build_reveal_keyboard(chat_id: int, user_id: int):
    builder = InlineKeyboardBuilder()
    traits = [
        ("position", "💼 Позицию"), ("age", "👤 Возраст"),
        ("price", "💰 Цену"), ("health", "❤️ Здоровье"),
        ("skill", "🎯 Навык"), ("inventory", "🎒 Багаж"), ("secret", "🔍 Секрет")
    ]
    
    for trait_key, trait_label in traits:
        if not await db.is_trait_revealed(chat_id, user_id, trait_key):
            builder.button(text=trait_label, callback_data=f"reveal:{trait_key}:{chat_id}")
            
    spec_info = await db.get_player_special_info(chat_id, user_id)
    if spec_info and not spec_info[1]:
        builder.button(text="✨ Спецкарта", callback_data=f"use_spec:{chat_id}")

    builder.adjust(2)
    return builder.as_markup()

async def build_players_summary(chat_id: int) -> str:
    alive_players = await db.get_alive_players(chat_id)
    if not alive_players:
        return ""

    trait_structure = [
        ("position", "💼", "Позиция"), ("age", "👤", "Возраст"),
        ("price", "💰", "Цена"), ("health", "❤️", "Здоровье"),
        ("skill", "🎯", "Навык"), ("inventory", "🎒", "Багаж"), ("secret", "🔍", "Секрет")
    ]

    players_blocks = []
    for idx, (p_name, p_id) in enumerate(alive_players, 1):
        pack = await db.get_player_pack(chat_id, p_id) or {}
        player_lines = [f"👤 <b>{idx}. {html.escape(p_name)}</b>"]

        total_traits = len(trait_structure)
        for t_idx, (trait_key, emoji, title) in enumerate(trait_structure):
            is_last = (t_idx == total_traits - 1)
            prefix = "└" if is_last else "├"
            
            is_revealed = await db.is_trait_revealed(chat_id, p_id, trait_key)
            if is_revealed:
                val = pack.get(trait_key, "-")
                if trait_key == "age" and val != "-":
                    val = f"{val} лет"
                player_lines.append(f"{prefix} {emoji} {title}: <b>{html.escape(str(val))}</b>")
            else:
                player_lines.append(f"{prefix} {emoji} {title}: <i>🔒 Скрыто</i>")

        players_blocks.append("\n\n".join(player_lines))

    return "\n\n".join(players_blocks)

async def auto_reveal_single_player(chat_id: int, user_id: int, user_name: str, current_round: int):
    unrevealed = await db.get_unrevealed_traits(chat_id, user_id)
    if unrevealed:
        chosen_trait = random.choice(unrevealed)
        pack = await db.get_player_pack(chat_id, user_id)
        
        val = pack.get(chosen_trait, "-")
        if chosen_trait == "age" and val != "-":
            val = f"{val} лет"
        
        await db.record_reveal(chat_id, user_id, chosen_trait, current_round)
        
        safe_name = html.escape(user_name)
        safe_val = html.escape(str(val))
        trait_title = TRAIT_LABELS.get(chosen_trait, chosen_trait)
        image_url = CARD_IMAGES.get(chosen_trait, "")
        
        msg_text = (
            f'<a href="{image_url}">&#8203;</a>⏱ <b>{safe_name}</b> не успел открыть карту!\n'
            f'└ 🎲 Бот автоматически вскрыл <b>[{trait_title}]</b>: <b>{safe_val}</b>'
        )
        preview_opts = LinkPreviewOptions(is_disabled=False, url=image_url, prefer_large_media=True, show_above_text=False)
        await bot.send_message(chat_id, msg_text, parse_mode="HTML", link_preview_options=preview_opts)

async def announce_winners_and_end(chat_id: int, alive_players: list):
    await db.set_lobby_status(chat_id, "ended")
    
    all_players = await db.get_players(chat_id)
    alive_ids = {p[1] for p in alive_players}
    
    for p_name, p_id, _ in all_players:
        is_win = (p_id in alive_ids) and (len(alive_players) > 0)
        await db.update_user_stats(p_id, p_name, is_win)

    if not alive_players:
        await bot.send_message(chat_id, "❌ <b>ИГРА ОКОНЧЕНА</b>\n───────────────────\nВсе претенденты выбыли! Победителей нет.", parse_mode="HTML")
        return

    winners_str = "\n".join([f"├ 🏆 <b>{html.escape(p[0])}</b>" for p in alive_players[:-1]] + [f"└ 🏆 <b>{html.escape(alive_players[-1][0])}</b>"])
    
    await bot.send_message(
        chat_id,
        f"🎉 <b>ИГРА ОКОНЧЕНА!</b>\n───────────────────\nОставшиеся игроки претендуют на контракт:\n{winners_str}\n\n🤖 <i>ИИ-эксперт изучает все карточки и формирует вердикт...</i>",
        parse_mode="HTML"
    )

    await bot.send_chat_action(chat_id, action="typing")

    lobby = await db.get_lobby(chat_id)
    scenario_text = lobby[2] if (lobby and len(lobby) > 2 and lobby[2]) else "Цель сценария не указана."
    
    winners_data = []
    for p_name, p_id in alive_players:
        pack = await db.get_player_pack(chat_id, p_id) or {}
        winners_data.append((p_name, pack))

    ai_verdict = await evaluate_game_outcome(scenario_text, winners_data)
    
    await bot.send_message(
        chat_id,
        f"📊 <b>ИТОГИ СЦЕНАРИЯ</b>\n───────────────────\n{ai_verdict}",
        parse_mode="HTML"
    )

async def start_round_flow(chat_id: int, current_round: int):
    try:
        if not await is_game_active(chat_id): return

        alive_players = await db.get_alive_players(chat_id)
        if len(alive_players) <= 2:
            await announce_winners_and_end(chat_id, alive_players)
            return

        all_players = await db.get_players(chat_id)
        total_count = len(all_players)
        bot_info = await bot.get_me()

        await db.set_lobby_status(chat_id, "reveal_phase", current_round)
        await bot.send_message(
            chat_id,
            f"📢 <b>РАУНД {current_round} | ВСКРЫТИЕ КАРТ</b>\n───────────────────\nИгроки вытягивают по 1 карте строго по очереди.",
            parse_mode="HTML"
        )

        for p_name, p_id in alive_players:
            if not await is_game_active(chat_id): return
            await db.set_current_turn(chat_id, p_id)
            
            builder = InlineKeyboardBuilder()
            builder.button(text="📩 Открыть карту / Спец-карту", url=f"https://t.me/{bot_info.username}")
            
            safe_p_name = html.escape(p_name)
            await bot.send_message(
                chat_id,
                f"🎲 <b>ОЧЕРЕДЬ ИГРОКА: {safe_p_name}</b>\n───────────────────\n👉 <a href='tg://user?id={p_id}'><b>{safe_p_name}</b></a>, перейди в ЛС и открой 1 карту!\n⏳ <i>У тебя 40 секунд...</i>",
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )

            for _ in range(40):
                if not await is_game_active(chat_id): return
                if await db.has_revealed_in_round(chat_id, p_id, current_round): break
                await asyncio.sleep(1)

            if not await is_game_active(chat_id): return
            if not await db.has_revealed_in_round(chat_id, p_id, current_round):
                await auto_reveal_single_player(chat_id, p_id, p_name, current_round)

            await asyncio.sleep(2)

        if not await is_game_active(chat_id): return
        await db.set_current_turn(chat_id, 0)

        await db.set_lobby_status(chat_id, "discussion", current_round)
        has_voting = not (total_count in (3, 4) and current_round < 3)
        discussion_time = 60 if has_voting else 30

        discussion_msg = f"💬 <b>РАУНД {current_round} | ОБСУЖДЕНИЕ ({discussion_time} СЕКУНД)</b>\n───────────────────\n"
        discussion_msg += "Обсуждайте открытые карты и готовьтесь к голосованию!" if has_voting else "Первое голосование откроется в Раунде 3."
        
        await bot.send_message(chat_id, discussion_msg, parse_mode="HTML")
        await asyncio.sleep(discussion_time)

        if not await is_game_active(chat_id): return
        check_lobby = await db.get_lobby(chat_id)
        if check_lobby and check_lobby[0] == "discussion":
            if not has_voting:
                asyncio.create_task(start_round_flow(chat_id, current_round + 1))
            else:
                asyncio.create_task(start_voting_flow(chat_id, current_round))

    except Exception as e:
        print(f"[ERROR] Сбой в Раунде {current_round}: {e}")
        await bot.send_message(chat_id, f"⚠️ Произошел сбой при переходе в Раунд {current_round + 1}. Возобновляем поток...", parse_mode="HTML")
        await asyncio.sleep(2)
        asyncio.create_task(start_round_flow(chat_id, current_round + 1))

# --- СПЕЦ-КАРТЫ ---

@dp.callback_query(F.data.startswith("use_spec:"))
async def handle_use_special_init(callback: types.CallbackQuery):
    target_chat_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    if not await is_game_active(target_chat_id):
        return await callback.answer("❌ Игра не активна!", show_alert=True)

    alive_players = await db.get_alive_players(target_chat_id)
    alive_ids = {p[1] for p in alive_players}
    if user_id not in alive_ids:
        return await callback.answer("❌ Выбывшие участники не могут использовать спец-карты!", show_alert=True)

    spec_info = await db.get_player_special_info(target_chat_id, user_id)
    if not spec_info or spec_info[1]:
        return await callback.answer("❌ Ты уже использовал свою спец-карту!", show_alert=True)

    if spec_info[2]:
        return await callback.answer("🟨 На тебя наложена Желтая карточка! Спец-карта заблокирована.", show_alert=True)

    card_code = spec_info[0]
    other_players = [p for p in alive_players if p[1] != user_id]

    if card_code == "mirror":
        await db.update_player_special_status(target_chat_id, user_id, special_used=1, shield_active=1)
        await callback.answer("🪞 Зеркальный щит активирован!", show_alert=True)
        await bot.send_message(
            target_chat_id,
            f"🪞 <b>{html.escape(callback.from_user.first_name)}</b> активировал <b>«Зеркальный щит»</b>! Вражеский эффект отразится обратно.",
            parse_mode="HTML"
        )
        return

    builder = InlineKeyboardBuilder()
    for p_name, p_id in other_players:
        builder.button(text=f"👤 {p_name}", callback_data=f"target_spec:{card_code}:{p_id}:{target_chat_id}")
    builder.adjust(2)

    await callback.message.answer(
        f"✨ <b>Применение: {SPECIAL_CARD_NAMES.get(card_code, card_code)}</b>\nВыбери игрока-цель:",
        reply_markup=builder.as_markup(), parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("target_spec:"))
async def process_special_target(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    card_code = parts[1]
    target_user_id = int(parts[2])
    chat_id = int(parts[3])
    actor = callback.from_user

    alive_players = await db.get_alive_players(chat_id)
    if actor.id not in {p[1] for p in alive_players}:
        return await callback.answer("❌ Выбывшие игроки не могут использовать спец-карты!", show_alert=True)

    target_info = await db.get_player_special_info(chat_id, target_user_id)
    if target_info and target_info[3] == 1:
        await db.update_player_special_status(chat_id, target_user_id, shield_active=0)
        await bot.send_message(
            chat_id,
            f"🪞 <b>ЗЕРКАЛЬНЫЙ ЩИТ!</b> Игрок <a href='tg://user?id={target_user_id}'>цель</a> отразил спец-карту <b>{SPECIAL_CARD_NAMES.get(card_code, card_code)}</b> обратно в <b>{html.escape(actor.first_name)}</b>!",
            parse_mode="HTML"
        )
        target_user_id = actor.id

    target_name = await db.get_username(chat_id, target_user_id)
    await db.update_player_special_status(chat_id, actor.id, special_used=1)

    if card_code.startswith("swap_"):
        category = card_code.replace("swap_", "")
        actor_pack = await db.get_player_pack(chat_id, actor.id)
        target_pack = await db.get_player_pack(chat_id, target_user_id)

        actor_val = actor_pack.get(category, "-")
        target_val = target_pack.get(category, "-")

        actor_pack[category] = target_val
        target_pack[category] = actor_val

        await db.update_player_pack(chat_id, actor.id, actor_pack)
        await db.update_player_pack(chat_id, target_user_id, target_pack)

        actor_revealed = await db.is_trait_revealed(chat_id, actor.id, category)
        target_revealed = await db.is_trait_revealed(chat_id, target_user_id, category)

        cat_title = TRAIT_LABELS.get(category, category)

        if actor_revealed or target_revealed:
            await bot.send_message(
                chat_id,
                f"🔄 <b>ОБМЕН ХАРАКТЕРИСТИКАМИ!</b>\n"
                f"<b>{html.escape(actor.first_name)}</b> применил спец-карту «Обмен [{cat_title}]» на <b>{html.escape(target_name)}</b>!\n"
                f"Их карты <b>[{cat_title}]</b> официально поменялись местами.",
                parse_mode="HTML"
            )
        else:
            await bot.send_message(
                actor.id,
                f"🔄 <b>Анонимный обмен выполнен!</b>\nТы поменялся картами [{cat_title}] с игроком <b>{html.escape(target_name)}</b>.\n"
                f"Твое новое значение: <b>{target_val}</b>", parse_mode="HTML"
            )
            await bot.send_message(
                target_user_id,
                f"⚠️ <b>Внимание!</b> Кто-то анонимно применил на тебя «Обмен [{cat_title}]»!\n"
                f"Твое новое скрытое значение: <b>{actor_val}</b>", parse_mode="HTML"
            )
        await callback.message.edit_text("✅ Спец-карта успешно использована!")

    elif card_code == "spy":
        target_pack = await db.get_player_pack(chat_id, target_user_id)
        unrevealed = await db.get_unrevealed_traits(chat_id, target_user_id)
        if not unrevealed:
            await callback.message.answer("👁 Все карты этого игрока уже открыты!")
        else:
            chosen = random.choice(unrevealed)
            val = target_pack.get(chosen, "-")
            await callback.message.answer(
                f"👁 <b>ШПИОНАЖ:</b> У игрока <b>{html.escape(target_name)}</b> карта <b>[{TRAIT_LABELS.get(chosen, chosen)}]</b>: <code>{val}</code>",
                parse_mode="HTML"
            )
        await callback.message.edit_text("✅ Спец-карта успешно использована!")

    elif card_code == "yellow_card":
        await db.update_player_special_status(chat_id, target_user_id, is_blocked=1)
        await bot.send_message(
            chat_id,
            f"🟨 <b>ЖЕЛТАЯ КАРТОЧКА!</b> <b>{html.escape(actor.first_name)}</b> заблокировал спец-карту игрока <b>{html.escape(target_name)}</b> до конца игры!",
            parse_mode="HTML"
        )
        await callback.message.edit_text("✅ Спец-карта успешно использована!")

    elif card_code == "flash":
        unrevealed = await db.get_unrevealed_traits(chat_id, target_user_id)
        if not unrevealed:
            await callback.message.edit_text(f"📸 У игрока {html.escape(target_name)} нет закрытых карт!")
        else:
            builder = InlineKeyboardBuilder()
            for u_trait in unrevealed:
                builder.button(
                    text=f"📸 {TRAIT_LABELS.get(u_trait, u_trait)}", 
                    callback_data=f"act_flash:{u_trait}:{target_user_id}:{chat_id}"
                )
            builder.adjust(2)
            await callback.message.edit_text(
                f"📸 <b>ВСПЫШКА:</b> Выбери, какую закрытую карту игрока <b>{html.escape(target_name)}</b> принудительно вскрыть в общий чат:",
                reply_markup=builder.as_markup(), parse_mode="HTML"
            )

    elif card_code == "chaos":
        actor_unrevealed = await db.get_unrevealed_traits(chat_id, actor.id)
        target_unrevealed = await db.get_unrevealed_traits(chat_id, target_user_id)

        if actor_unrevealed and target_unrevealed:
            a_trait = random.choice(actor_unrevealed)
            t_trait = random.choice(target_unrevealed)

            actor_pack = await db.get_player_pack(chat_id, actor.id)
            target_pack = await db.get_player_pack(chat_id, target_user_id)

            actor_pack[a_trait], target_pack[t_trait] = target_pack[t_trait], actor_pack[a_trait]

            await db.update_player_pack(chat_id, actor.id, actor_pack)
            await db.update_player_pack(chat_id, target_user_id, target_pack)

            await bot.send_message(actor.id, f"🎲 <b>Трансферный хаос:</b> Твоя закрытая карта [{TRAIT_LABELS.get(a_trait, a_trait)}] случайно поменялась с закрытой картой {target_name}!", parse_mode="HTML")
            await bot.send_message(target_user_id, f"🎲 <b>Трансферный хаос:</b> Твоя закрытая карта [{TRAIT_LABELS.get(t_trait, t_trait)}] случайно поменялась с чужой закрытой картой!", parse_mode="HTML")
        await callback.message.edit_text("✅ Спец-карта успешно использована!")

    await callback.answer()

@dp.callback_query(F.data.startswith("act_flash:"))
async def process_flash_reveal(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    trait = parts[1]
    target_user_id = int(parts[2])
    chat_id = int(parts[3])
    actor = callback.from_user

    alive_players = await db.get_alive_players(chat_id)
    if actor.id not in {p[1] for p in alive_players}:
        return await callback.answer("❌ Выбывшие игроки не могут вскрывать карты!", show_alert=True)

    target_name = await db.get_username(chat_id, target_user_id)
    pack = await db.get_player_pack(chat_id, target_user_id)
    val = pack.get(trait, "-")
    if trait == "age" and val != "-":
        val = f"{val} лет"

    await db.record_reveal(chat_id, target_user_id, trait, 0)

    image_url = CARD_IMAGES.get(trait, "")
    msg_text = (
        f'<a href="{image_url}">&#8203;</a>📸 <b>ВСПЫШКА!</b> '
        f'<b>{html.escape(actor.first_name)}</b> принудительно вскрывает у '
        f'<b>{html.escape(target_name)}</b> карту <b>[{TRAIT_LABELS.get(trait, trait)}]</b>:\n'
        f'└ 👉 <b>{html.escape(str(val))}</b>'
    )
    preview_opts = LinkPreviewOptions(is_disabled=False, url=image_url, prefer_large_media=True, show_above_text=False)

    await bot.send_message(chat_id, msg_text, parse_mode="HTML", link_preview_options=preview_opts)
    await callback.message.edit_text(f"✅ Ты успешно вскрыл карту [{TRAIT_LABELS.get(trait, trait)}] игрока {html.escape(target_name)}!")
    await callback.answer()

# --- СТАРТ ИГРЫ И ВЫДАЧА СПЕЦ-КАРТ ---

@dp.callback_query(F.data == "start_game")
async def start_game(callback: types.CallbackQuery):
    try:
        chat_id = callback.message.chat.id
        players = await db.get_players(chat_id)
        num_players = len(players)
        
        if num_players < 3:
            return await callback.answer(f"⚠️ Сейчас участников: {num_players}. Нужно минимум 3 человека!", show_alert=True)

        await callback.answer("Игра начинается!")
        
        scen = cards.generate_scenario(num_players)
        await db.update_lobby_scenario(chat_id, scen["text"], 1)

        packs = cards.generate_game_packs(num_players, scen["line"])
        special_cards_pool = list(SPECIAL_CARD_NAMES.keys())

        if len(special_cards_pool) >= num_players:
            assigned_cards = random.sample(special_cards_pool, num_players)
        else:
            assigned_cards = [random.choice(special_cards_pool) for _ in range(num_players)]

        failed_pm_players = []

        for idx, (p_name, p_id, _) in enumerate(players):
            pack = packs[idx]
            await db.add_player(chat_id, p_id, p_name, pack)

            assigned_spec = assigned_cards[idx]
            await db.set_player_special_card(chat_id, p_id, assigned_spec)
            
            spec_title = SPECIAL_CARD_NAMES.get(assigned_spec, "Спецкарта")
            spec_desc = SPECIAL_CARD_DESCRIPTIONS.get(assigned_spec, "Описание отсутствует.")

            pm_card_text = (
                f"📋 <b>ТВОЯ КАРТОЧКА ИГРОКА</b>\n───────────────────\n"
                f"💼 <b>Позиция:</b> <code>{html.escape(str(pack['position']))}</code>\n"
                f"👤 <b>Возраст:</b> <code>{pack['age']} лет</code>\n"
                f"💰 <b>Цена:</b> <code>{pack['price']}</code>\n"
                f"❤️ <b>Здоровье:</b> <code>{html.escape(str(pack['health']))}</code>\n"
                f"🎯 <b>Навык:</b> <code>{html.escape(str(pack['skill']))}</code>\n"
                f"🎒 <b>Багаж:</b> <code>{html.escape(str(pack['inventory']))}</code>\n"
                f"🔍 <b>Секрет:</b> <i>{html.escape(str(pack['secret']))}</i>\n"
                f"───────────────────\n"
                f"✨ <b>Спец-карта:</b> <b>{spec_title}</b>\n"
                f"ℹ️ <b>Что делает:</b> <i>{spec_desc}</i>\n"
                f"───────────────────\n"
                f"👇 <i>Используй кнопки ниже во время своего хода:</i>"
            )
            
            markup = await build_reveal_keyboard(chat_id, p_id)
            try:
                await bot.send_message(p_id, pm_card_text, reply_markup=markup, parse_mode="HTML")
            except Exception:
                failed_pm_players.append(f"<a href='tg://user?id={p_id}'>{html.escape(p_name)}</a>")

        players_list_str = "\n".join([f"├ 👤 {html.escape(p[0])}" for p in players[:-1]] + [f"└ 👤 {html.escape(players[-1][0])}"])
        pm_warning = f"\n\n⚠️ <b>ВНИМАНИЕ!</b> Не получили карты в ЛС:\n" + ", ".join(failed_pm_players) if failed_pm_players else ""

        scenario_msg = (
            f"🎬 <b>СЦЕНАРИЙ ИГРЫ</b>\n───────────────────\n"
            f"{scen['text']}\n\n"
            f"👥 <b>ПРЕТЕНДЕНТЫ НА КОНТРАКТ:</b>\n{players_list_str}\n"
            f"───────────────────\n"
            f"📩 <i>Карточки и спец-карты отправлены в ЛС.</i>{pm_warning}\n"
            f"⏳ <i>Раунд 1 начнется через 10 секунд!</i>"
        )
        await bot.send_message(chat_id, scenario_msg, parse_mode="HTML")

        await asyncio.sleep(10)
        asyncio.create_task(start_round_flow(chat_id, 1))

    except Exception as e:
        await callback.answer(f"❌ Ошибка старта: {e}", show_alert=True)

# --- КОМАНДЫ БОТА ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("⚡️ <b>ФУТБОЛЬНЫЙ БУНКЕР</b> ⚽️\n───────────────────\nДобавь бота в групповой чат и введи <code>/game</code>, чтобы начать.", parse_mode="HTML")

@dp.message(Command("rules"))
async def cmd_rules(message: types.Message):
    rules_text = (
        "📜 <b>ПРАВИЛА ИГРЫ «ФУТБОЛЬНЫЙ БУНКЕР»</b>\n───────────────────\n"
        "⚽ <b>Цель игры:</b> Доказать, что твои характеристики подходят под цель футбольного сценария, и избежать выбывания.\n\n"
        "🔹 <b>Игровой процесс:</b>\n"
        "1. В начале игры каждому участнику выдается набор из 7 характеристик и 1 разовой спец-карты в ЛС бота.\n"
        "2. В каждом раунде игроки по очереди раскрывают по 1 характеристике.\n"
        "3. После вскрытия карт проходит фаза обсуждения.\n"
        "4. В конце раунда проводится голосование — участник с наибольшим количеством голосов выбывает из игры.\n"
        "5. Игра продолжается до тех пор, пока не останется 2 победных претендента.\n\n"
        "🤖 <b>Вердикт ИИ:</b> В финале ИИ-эксперт анализирует оставшихся игроков и выносит окончательный вердикт: справилась ли команда с поставленной целью сценария!"
    )
    await message.answer(rules_text, parse_mode="HTML")

@dp.message(Command("stop"))
async def cmd_stop(message: types.Message):
    chat_id = message.chat.id
    if message.chat.type == "private":
        return await message.answer("Команду /stop нужно вводить в групповом чате игры!")

    if not await is_game_active(chat_id):
        return await message.answer("⚠️ В этом чате нет активной игры.")

    await db.set_lobby_status(chat_id, "cancelled")
    await message.answer("🛑 <b>ИГРА ПРИНУДИТЕЛЬНО ОСТАНОВЛЕНА!</b>\n───────────────────\nТекущая сессия отменена.", parse_mode="HTML")

@dp.message(Command("game"))
async def cmd_game(message: types.Message):
    if message.chat.type == "private":
        return await message.answer("Играть можно только в групповых чатах!")

    await db.create_lobby(message.chat.id, message.from_user.id)
    builder = InlineKeyboardBuilder()
    builder.button(text="⚽️ Вступить в игру", callback_data="join_game")
    builder.button(text="🚀 Начать игру (от 3 игроков)", callback_data="start_game")
    builder.adjust(1)

    await message.answer(
        f"🎮 <b>НАБОР В ФУТБОЛЬНЫЙ БУНКЕР</b>\n───────────────────\n👑 <b>Организатор:</b> {html.escape(message.from_user.first_name)}\n👥 <b>Участников:</b> 0",
        reply_markup=builder.as_markup(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "join_game")
async def join_game(callback: types.CallbackQuery):
    try:
        chat_id = callback.message.chat.id
        user = callback.from_user
        empty_pack = {"position": "-", "age": "-", "price": "-", "health": "-", "skill": "-", "inventory": "-", "secret": "-"}
        await db.add_player(chat_id, user.id, user.first_name, empty_pack)
        players = await db.get_players(chat_id)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="⚽️ Вступить в игру", callback_data="join_game")
        builder.button(text="🚀 Начать игру (от 3 игроков)", callback_data="start_game")
        builder.adjust(1)

        player_list = "\n".join([f"├ 👤 {html.escape(p[0])}" for p in players[:-1]] + [f"└ 👤 {html.escape(players[-1][0])}"])
        await callback.message.edit_text(f"🎮 <b>НАБОР В ФУТБОЛЬНЫЙ БУНКЕР</b>\n───────────────────\n👥 <b>Участники ({len(players)}):</b>\n{player_list}", reply_markup=builder.as_markup(), parse_mode="HTML")
        await callback.answer("Ты успешно вступил в игру!")
    except Exception as e:
        await callback.answer(f"❌ Ошибка входа: {e}", show_alert=True)

@dp.callback_query(F.data.startswith("reveal:"))
async def process_reveal(callback: types.CallbackQuery):
    try:
        parts = callback.data.split(":")
        trait = parts[1]
        target_chat_id = int(parts[2])
        user = callback.from_user

        if not await is_game_active(target_chat_id): return await callback.answer("❌ Игра завершена!", show_alert=True)
        
        alive_players = await db.get_alive_players(target_chat_id)
        if user.id not in {p[1] for p in alive_players}:
            return await callback.answer("❌ Выбывшие игроки не могут вскрывать карты!", show_alert=True)

        lobby = await db.get_lobby(target_chat_id)
        current_round = lobby[4]

        if await db.get_current_turn(target_chat_id) != user.id:
            return await callback.answer("⚠️ Сейчас не твой ход!", show_alert=True)

        if await db.is_trait_revealed(target_chat_id, user.id, trait):
            return await callback.answer("⚠️ Карта уже открыта!", show_alert=True)

        if await db.has_revealed_in_round(target_chat_id, user.id, current_round):
            return await callback.answer(f"⚠️ В этом раунде ты уже вскрывал карту!", show_alert=True)

        pack = await db.get_player_pack(target_chat_id, user.id)
        val = pack.get(trait, "-")
        if trait == "age" and val != "-": val = f"{val} лет"

        await db.record_reveal(target_chat_id, user.id, trait, current_round)
        image_url = CARD_IMAGES.get(trait, "")

        msg_text = f'<a href="{image_url}">&#8203;</a>🔓 <b>{html.escape(user.first_name)}</b> вскрывает <b>[{TRAIT_LABELS.get(trait, trait)}]</b>:\n└ 👉 <b>{html.escape(str(val))}</b>'
        preview_opts = LinkPreviewOptions(is_disabled=False, url=image_url, prefer_large_media=True, show_above_text=False)

        await bot.send_message(target_chat_id, msg_text, parse_mode="HTML", link_preview_options=preview_opts)
        new_markup = await build_reveal_keyboard(target_chat_id, user.id)
        await callback.message.edit_reply_markup(reply_markup=new_markup)
        await callback.answer(f"Карта {TRAIT_LABELS.get(trait, trait)} открыта!")
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)

# --- ГОЛОСОВАНИЕ И ИТОГИ ---

async def start_voting_flow(chat_id: int, round_num: int):
    if not await is_game_active(chat_id): return
    alive_players = await db.get_alive_players(chat_id)
    if len(alive_players) <= 2:
        await announce_winners_and_end(chat_id, alive_players)
        return

    await db.set_lobby_status(chat_id, "voting", round_num)
    await db.clear_votes(chat_id)
    summary_text = await build_players_summary(chat_id)

    builder = InlineKeyboardBuilder()
    for p_name, p_id in alive_players:
        builder.button(text=f"❌ {p_name}", callback_data=f"vote:{p_id}")
    builder.adjust(2)

    await bot.send_message(
        chat_id,
        f"📋 <b>ОТКРЫТЫЕ ХАРАКТЕРИСТИКИ</b>\n\n{summary_text}\n───────────────────\n🗳 <b>ГОЛОСОВАНИЕ (Раунд {round_num})</b>\n⏳ Время: 1 минута 45 секунд.",
        reply_markup=builder.as_markup(), parse_mode="HTML"
    )
    await asyncio.sleep(105)

    lobby = await db.get_lobby(chat_id)
    if lobby and lobby[0] == "voting" and lobby[4] == round_num:
        non_voters = await db.get_non_voted_alive_players(chat_id)
        for nv_id, nv_name in non_voters:
            await db.eliminate_player(chat_id, nv_id)
            await bot.send_message(chat_id, f"👞 <b>{html.escape(nv_name)}</b> исключен за AFK!", parse_mode="HTML")
        await finish_voting_flow(chat_id)

@dp.callback_query(F.data.startswith("vote:"))
async def process_vote(callback: types.CallbackQuery):
    try:
        chat_id = callback.message.chat.id
        voter = callback.from_user
        if not await is_game_active(chat_id): return await callback.answer("❌ Нет активной игры!", show_alert=True)
        
        alive_players = await db.get_alive_players(chat_id)
        alive_ids = {p[1] for p in alive_players}
        if voter.id not in alive_ids:
            return await callback.answer("❌ Выбывшие участники и зрители не могут голосовать!", show_alert=True)

        target_id = int(callback.data.split(":")[1])
        if voter.id == target_id: return await callback.answer("⚠️ За себя голосовать нельзя!", show_alert=True)
        if await db.has_user_voted(chat_id, voter.id): return await callback.answer("⚠️ Ты уже проголосовал!", show_alert=True)

        target_name = await db.get_username(chat_id, target_id)
        await db.add_vote(chat_id, voter.id, target_id)
        await callback.answer("Голос принят!")
        await bot.send_message(chat_id, f"🗳 <b>{html.escape(voter.first_name)}</b> проголосовал против <b>{html.escape(target_name)}</b>!", parse_mode="HTML")

        if await db.get_voters_count(chat_id) >= len(alive_players):
            await finish_voting_flow(chat_id)
    except Exception as e:
        await callback.answer(f"❌ Ошибка голосования: {e}", show_alert=True)

async def finish_voting_flow(chat_id: int):
    lobby = await db.get_lobby(chat_id)
    if not lobby or lobby[0] not in ("voting", "discussion"): return
    await db.set_lobby_status(chat_id, "finishing")
    
    votes_data = await db.get_votes_detailed(chat_id)
    alive = await db.get_alive_players(chat_id)

    if len(alive) <= 2:
        await announce_winners_and_end(chat_id, alive)
        return

    if not votes_data:
        asyncio.create_task(start_round_flow(chat_id, lobby[4] + 1))
        return

    max_votes = votes_data[0][2]
    top_candidates = [v for v in votes_data if v[2] == max_votes]

    if len(top_candidates) > 1:
        await db.clear_votes(chat_id)
        await bot.send_message(chat_id, f"🤝 <b>НИЧЬЯ!</b> Никто не выбывает. Переходим к Раунду {lobby[4] + 1}.", parse_mode="HTML")
        asyncio.create_task(start_round_flow(chat_id, lobby[4] + 1))
        return

    kicked_id, kicked_name = votes_data[0][0], votes_data[0][1]
    await db.eliminate_player(chat_id, kicked_id)
    await db.clear_votes(chat_id)
    
    alive_after = await db.get_alive_players(chat_id)
    await bot.send_message(chat_id, f"❌ Из команды изгнан: <b>{html.escape(kicked_name)}</b>", parse_mode="HTML")

    if len(alive_after) <= 2:
        await announce_winners_and_end(chat_id, alive_after)
    else:
        asyncio.create_task(start_round_flow(chat_id, lobby[4] + 1))

async def handle_ping(request):
    return web.Response(text="Bot Alive")

async def main():
    await db.init_db()
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
