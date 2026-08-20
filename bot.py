import asyncio
import html
import os
import random
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import LinkPreviewOptions
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

import database as db
import cards

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Словарь ссылок на картинки карточек
CARD_IMAGES = {
    "position": "https://i.ibb.co/rRQQn3Jk/E17684-E2-56-F4-4-D66-8727-A0411741-F102.png", 
    "age": "https://i.ibb.co/SXVZG1m8/E13-D392-F-B727-4782-BE64-4-B3-FF6460-AE0.png",
    "price": "https://i.ibb.co/gbtmrfbt/2-E013-C9-A-F562-435-D-8445-057199144-D69.png",
    "health": "https://i.ibb.co/RktLxj4k/BE52-B7-D1-DE73-4-E85-9641-0242-E4-FA816-F.png",
    "skill": "https://i.ibb.co/jPN42k2b/0-F0274-A2-6320-4-EE8-9-F2-F-9-CB9-E2-C35209.png",
    "inventory": "https://i.ibb.co/qL8JH60p/5-A3-AD710-D4-C9-443-B-AD86-14029-ED7-D237.png",
    "secret": "https://i.ibb.co/LzJ1zstj/7-DC7-EFD5-B294-4-EC8-BE3-D-EDF77-BFD93-DF.png"
}

def get_rank(wins: int) -> str:
    if wins >= 20: return "🌟 Легенда Трансферов"
    if wins >= 10: return "💼 Главный Скаут"
    if wins >= 5:  return "⚽ Игрок Основы"
    if wins >= 1:  return "👟 Перспективный Новичок"
    return "📋 Агент на испытательном"

async def is_game_active(chat_id: int) -> bool:
    lobby = await db.get_lobby(chat_id)
    return lobby is not None and lobby[0] not in ("ended", "cancelled")

async def build_reveal_keyboard(chat_id: int, user_id: int):
    builder = InlineKeyboardBuilder()
    traits = [
        ("position", "📢 Позицию"),
        ("age", "📢 Возраст"),
        ("price", "📢 Цену"),
        ("health", "📢 Здоровье"),
        ("skill", "📢 Навык"),
        ("inventory", "📢 Багаж"),
        ("secret", "📢 Секрет")
    ]
    
    for trait_key, trait_label in traits:
        if not await db.is_trait_revealed(chat_id, user_id, trait_key):
            builder.button(text=trait_label, callback_data=f"reveal_{trait_key}_{chat_id}")
            
    builder.adjust(2)
    return builder.as_markup()

async def build_players_summary(chat_id: int) -> str:
    alive_players = await db.get_alive_players(chat_id)
    if not alive_players:
        return ""

    trait_structure = [
        ("position", "💼", "Позиция"),
        ("age", "👤", "Возраст"),
        ("price", "💰", "Цена"),
        ("health", "❤️", "Здоровье"),
        ("skill", "🎯", "Навык"),
        ("inventory", "🎒", "Багаж"),
        ("secret", "🔍", "Секрет")
    ]

    players_blocks = []
    for idx, (p_name, p_id) in enumerate(alive_players, 1):
        pack = await db.get_player_pack(chat_id, p_id) or {}
        player_lines = [f"<b>{idx}. {html.escape(p_name)}</b>"]

        for trait_key, emoji, title in trait_structure:
            is_revealed = await db.is_trait_revealed(chat_id, p_id, trait_key)
            if is_revealed:
                val = pack.get(trait_key, "-")
                if trait_key == "age" and val != "-":
                    val = f"{val} лет"
                player_lines.append(f"{emoji} {title}: <b>{html.escape(str(val))}</b>")
            else:
                player_lines.append(f"{emoji} {title}: ❌")

        players_blocks.append("\n".join(player_lines))

    return "\n\n".join(players_blocks)

async def auto_reveal_single_player(chat_id: int, user_id: int, user_name: str, current_round: int):
    unrevealed = await db.get_unrevealed_traits(chat_id, user_id)
    if unrevealed:
        chosen_trait = random.choice(unrevealed)
        pack = await db.get_player_pack(chat_id, user_id)
        
        trait_names = {
            "position": "Позиция", "age": "Возраст", "price": "Трансферная цена",
            "health": "Здоровье", "skill": "Навык", "inventory": "Багаж", "secret": "Секрет"
        }
        
        val = pack.get(chosen_trait, "-")
        if chosen_trait == "age" and val != "-":
            val = f"{val} лет"
        
        await db.record_reveal(chat_id, user_id, chosen_trait, current_round)
        
        safe_name = html.escape(user_name)
        safe_val = html.escape(str(val))
        trait_title = trait_names.get(chosen_trait, chosen_trait)
        image_url = CARD_IMAGES.get(chosen_trait, "")
        
        msg_text = f'<a href="{image_url}">&#8203;</a>⏱ <b>{safe_name}</b> не успел открыть карту! Бот автоматически вскрыл [<b>{trait_title}</b>]:\n👉 <b>{safe_val}</b>'
        preview_opts = LinkPreviewOptions(
            is_disabled=False,
            url=image_url,
            prefer_large_media=True,
            show_above_text=False
        )
        
        await bot.send_message(
            chat_id,
            msg_text,
            parse_mode="HTML",
            link_preview_options=preview_opts
        )

async def announce_winners_and_end(chat_id: int, alive_players: list):
    await db.set_lobby_status(chat_id, "ended")
    
    # Обновление статистики игроков
    all_players = await db.get_players(chat_id)
    alive_ids = {p[1] for p in alive_players}
    
    for p_name, p_id, _ in all_players:
        is_win = (p_id in alive_ids) and (len(alive_players) > 0)
        await db.update_user_stats(p_id, p_name, is_win)

    if not alive_players:
        await bot.send_message(chat_id, "❌ Все игроки выбыли из игры! Победителей нет.", parse_mode="HTML")
    else:
        winners_str = "\n".join([f"🏆 <b>{html.escape(p[0])}</b>" for p in alive_players])
        await bot.send_message(
            chat_id,
            f"🎉 <b>ИГРА ОКОНЧЕНА!</b>\n\n"
            f"Оставшиеся игроки получают контракт с клубом:\n{winners_str}",
            parse_mode="HTML"
        )

async def start_round_flow(chat_id: int, current_round: int):
    if not await is_game_active(chat_id):
        return

    alive_players = await db.get_alive_players(chat_id)
    if len(alive_players) <= 2:
        await announce_winners_and_end(chat_id, alive_players)
        return

    all_players = await db.get_players(chat_id)
    total_count = len(all_players)
    bot_info = await bot.get_me()

    # --- ЭТАП 1: ПООЧЕРЕДНОЕ ВСКРЫТИЕ КАРТ ---
    await db.set_lobby_status(chat_id, "reveal_phase", current_round)
    
    await bot.send_message(
        chat_id,
        f"📢 <b>РАУНД {current_round}: ПООЧЕРЕДНОЕ ВСКРЫТИЕ КАРТ!</b>\n\n"
        f"Игроки вытягивают по 1 карте строго по очереди.",
        parse_mode="HTML"
    )

    for p_name, p_id in alive_players:
        if not await is_game_active(chat_id):
            return

        await db.set_current_turn(chat_id, p_id)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="📩 Открыть карту в ЛС", url=f"https://t.me/{bot_info.username}")
        
        safe_p_name = html.escape(p_name)
        await bot.send_message(
            chat_id,
            f"🎲 <b>ОЧЕРЕДЬ ИГРОКА: {safe_p_name}</b>\n\n"
            f"👉 <a href='tg://user?id={p_id}'><b>{safe_p_name}</b></a>, перейди в ЛС и открой 1 карту!\n"
            f"⏳ <i>У тебя 25 секунд...</i>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )

        for _ in range(25):
            if not await is_game_active(chat_id):
                return
            if await db.has_revealed_in_round(chat_id, p_id, current_round):
                break
            await asyncio.sleep(1)

        if not await is_game_active(chat_id):
            return

        if not await db.has_revealed_in_round(chat_id, p_id, current_round):
            await auto_reveal_single_player(chat_id, p_id, p_name, current_round)

        await asyncio.sleep(2)

    if not await is_game_active(chat_id):
        return

    await db.set_current_turn(chat_id, 0)

    # --- ЭТАП 2: ФАЗА ОБСУЖДЕНИЯ ---
    await db.set_lobby_status(chat_id, "discussion", current_round)

    has_voting = not (total_count in (3, 4) and current_round < 3)
    discussion_time = 60 if has_voting else 30

    if not has_voting:
        discussion_msg = (
            f"💬 <b>РАУНД {current_round}: ОБСУЖДЕНИЕ (30 СЕКУНД)!</b>\n\n"
            f"Первое голосование откроется <b>в Раунде 3</b>.\n"
            f"Обсуждайте открытые карты!\n\n"
            f"⏳ <i>Раунд {current_round + 1} начнется через 30 секунд...</i>"
        )
    elif total_count in (3, 4) and current_round == 3:
        discussion_msg = (
            f"💬 <b>РАУНД 3: ОБСУЖДЕНИЕ (60 СЕКУНД)!</b>\n\n"
            f"Готовьтесь к первому голосованию на вылет!\n\n"
            f"⏳ <i>Голосование начнется через 60 секунд...</i>"
        )
    else:
        discussion_msg = (
            f"💬 <b>РАУНД {current_round}: ОБСУЖДЕНИЕ (60 СЕКУНД)!</b>\n\n"
            f"Обсуждайте карты и решите, кто покидает команду.\n\n"
            f"⏳ <i>Голосование начнется через 60 секунд...</i>"
        )

    await bot.send_message(chat_id, discussion_msg, parse_mode="HTML")
    await asyncio.sleep(discussion_time)

    if not await is_game_active(chat_id):
        return

    check_lobby = await db.get_lobby(chat_id)
    if check_lobby and check_lobby[0] == "discussion":
        if not has_voting:
            await start_round_flow(chat_id, current_round + 1)
        else:
            await start_voting_flow(chat_id, current_round)

# --- КОМАНДЫ БОТА ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer("⚽️ <b>Футбольный Бункер запущен!</b>\nДобавь бота в групповой чат и напиши <code>/game</code> для старта.", parse_mode="HTML")

@dp.message(Command("rules"))
async def cmd_rules(message: types.Message):
    rules_text = (
        "📜 <b>ПРАВИЛА ИГРЫ «ТРАНСФЕРНЫЙ РЫНОК»</b>\n\n"
        "⚽ <b>Концепция:</b>\n"
        "Клуб попал в кризисную ситуацию и ищет усиление. Вы — кандидаты на трансфер. "
        "Вам выдаются случайные характеристики: позиция, возраст, цена, здоровье, навыки, багаж и тайные секреты.\n\n"
        "🎯 <b>Цель игры:</b>\n"
        "Путем убеждения и тактических споров доказать другим игрокам, "
        "что именно ваш персонаж идеален для подписания контракта, и избегать выбывания.\n\n"
        "🔄 <b>Ход игры:</b>\n"
        "1. <b>Получение пака:</b> Каждому в ЛС приходит скрытая карточка игрока.\n"
        "2. <b>Раунды и открытие карт:</b> В каждый раунд открывается по 1 характеристике.\n"
        "3. <b>Голосование:</b> В конце раунда чат выбирает, кто выбывает из просмотра.\n"
        "4. <b>Финал:</b> Оставшиеся 2 игрока подписывают контракт и побеждают!"
    )
    await message.answer(rules_text, parse_mode="HTML")

@dp.message(Command("profile"))
async def cmd_profile(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.full_name
    
    games, wins = await db.get_user_profile(user_id, username)
    winrate = round((wins / games * 100), 1) if games > 0 else 0.0
    rank = get_rank(wins)
    
    profile_text = (
        f"🎴 <b>ПРОФИЛЬ ИГРОКА:</b> {html.escape(username)}\n"
        f"🏅 <b>Статус:</b> {rank}\n"
        f"───────────────────\n"
        f"📊 <b>Статистика карьеры:</b>\n"
        f"├ 🎮 Всего игр: <b>{games}</b>\n"
        f"├ 🏆 Подписано контрактов: <b>{wins}</b>\n"
        f"└ 📈 Процент успешности: <b>{winrate}%</b>"
    )
    await message.answer(profile_text, parse_mode="HTML")

@dp.message(Command("stopgame", "stop"))
async def cmd_stopgame(message: types.Message):
    if message.chat.type == "private":
        return await message.answer("Остановить игру можно только в группах!")

    chat_id = message.chat.id
    if not await is_game_active(chat_id):
        return await message.answer("⚠️ В этом чате нет активной игры.")

    await db.set_lobby_status(chat_id, "ended")
    await message.answer("🛑 <b>Игра принудительно остановлена!</b>", parse_mode="HTML")

@dp.message(Command("game"))
async def cmd_game(message: types.Message):
    if message.chat.type == "private":
        return await message.answer("Играть можно только в групповых чатах!")

    await db.create_lobby(message.chat.id, message.from_user.id)
    safe_host_name = html.escape(message.from_user.first_name)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="⚽️ Вступить в игру", callback_data="join_game")
    builder.button(text="🚀 Начать игру (от 3 игроков)", callback_data="start_game")
    builder.adjust(1)

    await message.answer(
        f"🎮 <b>Идет сбор в Футбольный Бункер!</b>\n\n"
        f"Организатор: <a href='tg://user?id={message.from_user.id}'>{safe_host_name}</a>\n"
        f"Участников: 0\n\n"
        f"<i>Минимум для старта: 3 человека. Запустите бота в ЛС!</i>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "join_game")
async def join_game(callback: types.CallbackQuery):
    try:
        chat_id = callback.message.chat.id
        user = callback.from_user
        
        empty_pack = {
            "position": "-", "age": "-", "price": "-", "health": "-",
            "skill": "-", "inventory": "-", "secret": "-"
        }
        await db.add_player(chat_id, user.id, user.first_name, empty_pack)
        players = await db.get_players(chat_id)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="⚽️ Вступить в игру", callback_data="join_game")
        builder.button(text="🚀 Начать игру (от 3 игроков)", callback_data="start_game")
        builder.adjust(1)

        player_list = "\n".join([f"• {html.escape(p[0])}" for p in players])
        await callback.message.edit_text(
            f"🎮 <b>Идет набор игроков в Футбольный Бункер!</b>\n\nУчастники ({len(players)}):\n{player_list}",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        await callback.answer("Ты успешно вступил в игру!")
    except Exception as e:
        await callback.answer(f"❌ Ошибка входа: {e}", show_alert=True)

@dp.callback_query(F.data == "start_game")
async def start_game(callback: types.CallbackQuery):
    try:
        chat_id = callback.message.chat.id
        players = await db.get_players(chat_id)
        num_players = len(players)
        
        if num_players < 3:
            return await callback.answer(f"⚠️ Сейчас участников: {num_players}. Нужно минимум 3 человека!", show_alert=True)

        await callback.answer("Игра начинается!")
        
        packs = cards.generate_game_packs(num_players)
        scen = cards.generate_scenario(num_players)
        await db.update_lobby_scenario(chat_id, scen["text"], 1)

        bot_username = (await bot.get_me()).username
        failed_pm_players = []

        for idx, (p_name, p_id, _) in enumerate(players):
            pack = packs[idx]
            await db.add_player(chat_id, p_id, p_name, pack)
            
            pm_card_text = (
                f"📋 <b>ТВОЯ КАРТОЧКА ИГРОКА:</b>\n\n"
                f"• Позиция: <b>{html.escape(pack['position'])}</b>\n"
                f"• Возраст: <b>{pack['age']} лет</b>\n"
                f"• Трансферная цена: <b>{pack['price']}</b>\n"
                f"• Здоровье: {html.escape(pack['health'])}\n"
                f"• Навык: {html.escape(pack['skill'])}\n"
                f"• Багаж: {html.escape(pack['inventory'])}\n"
                f"• Секрет: <i>{html.escape(pack['secret'])}</i>\n\n"
                f"👇 <i>Нажимай кнопки ниже, когда настанет ТВОЯ очередь:</i>"
            )
            
            markup = await build_reveal_keyboard(chat_id, p_id)
            
            try:
                await bot.send_message(p_id, pm_card_text, reply_markup=markup, parse_mode="HTML")
            except Exception:
                failed_pm_players.append(f"<a href='tg://user?id={p_id}'>{html.escape(p_name)}</a>")

        players_list_str = "\n".join([f"• {html.escape(p[0])}" for p in players])
        
        pm_warning = ""
        if failed_pm_players:
            pm_warning = f"\n\n⚠️ <b>ВНИМАНИЕ!</b> Не получили карты в ЛС:\n" + ", ".join(failed_pm_players) + f"\nНажмите 👉 @{bot_username} и нажмите Start!"

        scenario_msg = (
            f"🎬 <b>СЦЕНАРИЙ ИГРЫ:</b>\n\n"
            f"{scen['text']}\n\n"
            f"👥 <b>ПРЕТЕНДЕНТЫ НА КОНТРАКТ:</b>\n{players_list_str}\n\n"
            f"📩 <b>Карточки и кнопки отправлены каждому в ЛС!</b>{pm_warning}\n\n"
            f"🔥 <i>Через 10 секунд начнется Раунд 1!</i>"
        )
        await bot.send_message(chat_id, scenario_msg, parse_mode="HTML")

        await asyncio.sleep(10)
        await start_round_flow(chat_id, 1)

    except Exception as e:
        await callback.answer(f"❌ Ошибка старта: {e}", show_alert=True)

@dp.callback_query(F.data.startswith("reveal_"))
async def process_reveal(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("_")
        trait = parts[1]
        target_chat_id = int(parts[2])
        user = callback.from_user

        if not await is_game_active(target_chat_id):
            return await callback.answer("❌ Игра завершена или не существует!", show_alert=True)

        lobby = await db.get_lobby(target_chat_id)
        current_round = lobby[4]

        current_turn_id = await db.get_current_turn(target_chat_id)
        if current_turn_id != user.id:
            turn_username = await db.get_username(target_chat_id, current_turn_id)
            safe_turn_user = html.escape(turn_username)
            return await callback.answer(f"⚠️ Сейчас очередь игрока {safe_turn_user}! Дождись своего хода.", show_alert=True)

        if await db.is_trait_revealed(target_chat_id, user.id, trait):
            return await callback.answer("⚠️ Ты уже вскрыл эту характеристику!", show_alert=True)

        if await db.has_revealed_in_round(target_chat_id, user.id, current_round):
            return await callback.answer(f"⚠️ В Раунде {current_round} ты уже открыл 1 карту!", show_alert=True)

        pack = await db.get_player_pack(target_chat_id, user.id)
        if not pack:
            return await callback.answer("❌ Ошибка: ты не найден в этой игре!", show_alert=True)

        trait_titles = {
            "position": ("Позиция", pack.get("position", "-")),
            "age": ("Возраст", f"{pack.get('age', '-')} лет"),
            "price": ("Трансферная цена", pack.get("price", "-")),
            "health": ("Здоровье", pack.get("health", "-")),
            "skill": ("Навык", pack.get("skill", "-")),
            "inventory": ("Багаж", pack.get("inventory", "-")),
            "secret": ("Секрет", pack.get("secret", "-"))
        }

        if trait not in trait_titles:
            return await callback.answer("❌ Неизвестная характеристика!")

        title, value = trait_titles[trait]
        await db.record_reveal(target_chat_id, user.id, trait, current_round)

        safe_name = html.escape(user.first_name)
        safe_val = html.escape(str(value))
        image_url = CARD_IMAGES.get(trait, "")

        msg_text = f'<a href="{image_url}">&#8203;</a>🔓 <b>{safe_name}</b> открывает карту [<b>{title}</b>]:\n👉 <b>{safe_val}</b>'
        preview_opts = LinkPreviewOptions(
            is_disabled=False,
            url=image_url,
            prefer_large_media=True,
            show_above_text=False
        )

        await bot.send_message(
            target_chat_id,
            msg_text,
            parse_mode="HTML",
            link_preview_options=preview_opts
        )
        
        new_markup = await build_reveal_keyboard(target_chat_id, user.id)
        await callback.message.edit_reply_markup(reply_markup=new_markup)
        await callback.answer(f"Ты открыл карту {title} в общем чате!")

    except Exception as e:
        await callback.answer(f"❌ Ошибка отправки: {e}", show_alert=True)

async def start_voting_flow(chat_id: int, round_num: int):
    if not await is_game_active(chat_id):
        return

    alive_players = await db.get_alive_players(chat_id)
    if len(alive_players) <= 2:
        await announce_winners_and_end(chat_id, alive_players)
        return

    await db.set_lobby_status(chat_id, "voting", round_num)
    await db.clear_votes(chat_id)

    summary_text = await build_players_summary(chat_id)

    builder = InlineKeyboardBuilder()
    for p_name, p_id in alive_players:
        builder.button(text=f"❌ {p_name}", callback_data=f"vote_{p_id}")
    builder.adjust(2)

    voting_msg = (
        f"📋 <b>ОТКРЫТЫЕ ХАРАКТЕРИСТИКИ ИГРОКОВ:</b>\n\n"
        f"{summary_text}\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🗳 <b>НАЧИНАЕТСЯ ГОЛОСОВАНИЕ (Раунд {round_num})!</b>\n\n"
        f"Голосуйте кнопками ниже! У вас есть <b>60 секунд</b>.\n"
        f"<i>Те, кто не проголосуют вовремя, будут кикнуты за AFK!</i>\n\n"
        f"<i>Проголосовали: 0/{len(alive_players)}</i>"
    )

    await bot.send_message(
        chat_id,
        voting_msg,
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

    await asyncio.sleep(60)

    lobby = await db.get_lobby(chat_id)
    if lobby and lobby[0] == "voting" and lobby[4] == round_num:
        non_voters = await db.get_non_voted_alive_players(chat_id)
        for nv_id, nv_name in non_voters:
            await db.eliminate_player(chat_id, nv_id)
            await bot.send_message(
                chat_id,
                f"👞 <b>{html.escape(nv_name)}</b> исключен из игры за AFK в голосовании!",
                parse_mode="HTML"
            )

        await finish_voting_flow(chat_id)

@dp.callback_query(F.data.startswith("vote_"))
async def process_vote(callback: types.CallbackQuery):
    try:
        chat_id = callback.message.chat.id
        voter = callback.from_user

        if not await is_game_active(chat_id):
            return await callback.answer("❌ В этом чате нет активной игры!", show_alert=True)

        lobby = await db.get_lobby(chat_id)
        if not lobby or lobby[0] != "voting":
            return await callback.answer("⚠️ Сейчас не фаза голосования!", show_alert=True)

        alive_players = await db.get_alive_players(chat_id)
        alive_ids = [p[1] for p in alive_players]

        if voter.id not in alive_ids:
            return await callback.answer("❌ Ты не участвуешь в этой игре или уже выбыл!", show_alert=True)

        target_id = int(callback.data.split("_")[1])

        if voter.id == target_id:
            return await callback.answer("⚠️ За себя голосовать нельзя!", show_alert=True)

        if await db.has_user_voted(chat_id, voter.id):
            return await callback.answer("⚠️ Ты уже отдал свой голос!", show_alert=True)

        target_name = await db.get_username(chat_id, target_id)
        await db.add_vote(chat_id, voter.id, target_id)
        await callback.answer("Твой голос принят!")

        safe_voter = html.escape(voter.first_name)
        safe_target = html.escape(target_name)

        await bot.send_message(
            chat_id, 
            f"🗳 <b>{safe_voter}</b> проголосовал(а) против <b>{safe_target}</b>!", 
            parse_mode="HTML"
        )

        voters_count = await db.get_voters_count(chat_id)

        if voters_count >= len(alive_players):
            await bot.send_message(chat_id, "🎉 <b>Все живые игроки проголосовали! Подводим итоги...</b>", parse_mode="HTML")
            await finish_voting_flow(chat_id)
    except Exception as e:
        await callback.answer(f"❌ Ошибка голосования: {e}", show_alert=True)

async def finish_voting_flow(chat_id: int):
    lobby = await db.get_lobby(chat_id)
    if not lobby or lobby[0] not in ("voting", "discussion"):
        return
    
    await db.set_lobby_status(chat_id, "finishing")
    votes_data = await db.get_votes_detailed(chat_id)
    alive = await db.get_alive_players(chat_id)

    if len(alive) <= 2:
        await announce_winners_and_end(chat_id, alive)
        return

    if not votes_data:
        next_round = lobby[4] + 1
        await bot.send_message(chat_id, f"⚠️ Голосов не было. Переходим к Раунду {next_round}!", parse_mode="HTML")
        await start_round_flow(chat_id, next_round)
        return

    results_lines = [f"• <b>{html.escape(t_name)}</b>: {count} гол." for _, t_name, count in votes_data]
    results_text = "\n".join(results_lines)

    max_votes = votes_data[0][2]
    top_candidates = [v for v in votes_data if v[2] == max_votes]

    if len(top_candidates) > 1:
        await db.clear_votes(chat_id)
        
        if len(alive) <= 2:
            await bot.send_message(
                chat_id,
                f"📊 <b>ИТОГИ ГОЛОСОВАНИЯ:</b>\n{results_text}\n\n"
                f"🤝 <b>НИЧЬЯ!</b> Оставшиеся претенденты делят контракт между собой.",
                parse_mode="HTML"
            )
            await announce_winners_and_end(chat_id, alive)
            return

        next_round = lobby[4] + 1
        await bot.send_message(
            chat_id,
            f"📊 <b>ИТОГИ ГОЛОСОВАНИЯ:</b>\n{results_text}\n\n"
            f"🤝 <b>НИЧЬЯ!</b> Никто не выбывает.\n"
            f"Переходим к Раунду {next_round}.",
            parse_mode="HTML"
        )
        await start_round_flow(chat_id, next_round)
        return

    kicked_id, kicked_name = votes_data[0][0], votes_data[0][1]

    await db.eliminate_player(chat_id, kicked_id)
    await db.clear_votes(chat_id)
    
    alive_after = await db.get_alive_players(chat_id)
    safe_kicked_name = html.escape(kicked_name)

    if len(alive_after) <= 2:
        await bot.send_message(
            chat_id,
            f"📊 <b>ИТОГИ ГОЛОСОВАНИЯ:</b>\n{results_text}\n\n"
            f"❌ Из команды изгнан: <b>{safe_kicked_name}</b>",
            parse_mode="HTML"
        )
        await announce_winners_and_end(chat_id, alive_after)
    else:
        next_round = lobby[4] + 1
        await bot.send_message(
            chat_id,
            f"📊 <b>ИТОГИ ГОЛОСОВАНИЯ:</b>\n{results_text}\n\n"
            f"❌ Из команды изгнан: <b>{safe_kicked_name}</b>\n\n"
            f"Переходим к Раунду {next_round}. Осталось игроков: {len(alive_after)}.",
            parse_mode="HTML"
        )
        await start_round_flow(chat_id, next_round)

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
