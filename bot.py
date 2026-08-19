import asyncio
import html
import os
import random
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

import database as db
import cards

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Порядок и структура карт: (код, название, индекс_значения, индекс_флага_открытия)
TRAITS_INFO = [
    ("position", "⚽️ Позиция", 0, 5),
    ("health", "🏥 Здоровье/Физуха", 1, 6),
    ("skill", "🎯 Навык", 2, 7),
    ("inventory", "🎒 Багаж", 3, 8),
    ("secret", "🤫 Секрет", 4, 9)
]

async def get_next_turn_player(chat_id: int):
    """Определяет, чей сейчас ход в текущем круге вскрытия карт."""
    lobby = await db.get_lobby(chat_id)
    if not lobby:
        return None, None, None
    
    current_round = lobby[4]
    # Теперь количество требуемых карт строго равно номеру раунда (1 раунд = 1 карта, 2 = 2 карты)
    target_count = min(5, current_round)
    
    alive_players = await db.get_alive_players(chat_id)
    if not alive_players:
        return None, None, None

    player_states = []
    for p_name, p_id in alive_players:
        rev_cnt = await db.get_player_revealed_count(chat_id, p_id)
        player_states.append((p_id, p_name, rev_cnt))

    min_rev = min(p[2] for p in player_states)

    if min_rev >= target_count:
        return None, None, target_count  # Все открыли нужные карты

    # Возвращаем первого игрока, у которого меньше всего открытых карт
    for p_id, p_name, rev_cnt in player_states:
        if rev_cnt == min_rev:
            return p_id, p_name, rev_cnt

    return None, None, None

async def prompt_next_player(chat_id: int):
    """Отправляет в чат объявление, чей сейчас ход."""
    p_id, p_name, rev_cnt = await get_next_turn_player(chat_id)
    lobby = await db.get_lobby(chat_id)
    current_round = lobby[4]
    target_count = min(5, current_round)

    if p_id is None:
        # --- 1 РАУНД: Без голосования сразу идем во 2-й раунд ---
        if current_round == 1:
            await db.set_lobby_status(chat_id, "round2", 2)
            await bot.send_message(
                chat_id,
                "🎉 <b>Все игроки открыли по 1 карте!</b>\n\n"
                "В 1-м раунде выбывания нет! Сразу переходим к <b>Раунду 2</b>.\n"
                "Открываем еще по 1 карте на выбор!",
                parse_mode="HTML"
            )
            await prompt_next_player(chat_id)
            return

        # --- 2+ РАУНДЫ: 90 секунд обсуждения и голосование ---
        if lobby[0] not in ["discussion", "voting"]:
            await db.set_lobby_status(chat_id, "discussion")
            await bot.send_message(
                chat_id,
                f"🎉 <b>Все игроки открыли по {target_count} х-ки!</b>\n\n"
                f"💬 <b>ОБСУЖДЕНИЕ (90 СЕКУНД)!</b>\n"
                f"У вас есть 1.5 минуты, чтобы обсудить кандидатов и решить, кто меньше всего подходит команде.\n\n"
                f"⏳ <i>Голосование начнется автоматически через 90 секунд...</i>",
                parse_mode="HTML"
            )
            await asyncio.sleep(90)
            check_lobby = await db.get_lobby(chat_id)
            if check_lobby and check_lobby[0] == "discussion":
                await start_voting_flow(chat_id, current_round)
        return

    bot_info = await bot.get_me()
    builder = InlineKeyboardBuilder()
    builder.button(text="🎴 Выбрать карту в ЛС", url=f"https://t.me/{bot_info.username}?start=reveal_{chat_id}")

    safe_name = html.escape(p_name)
    card_num = rev_cnt + 1

    await bot.send_message(
        chat_id,
        f"🎲 <b>Ход игрока <a href='tg://user?id={p_id}'>{safe_name}</a>!</b>\n"
        f"Выбери и вскрой карту №{card_num} на свой выбор.\n"
        f"Перейди в ЛС с ботом и нажми кнопку ниже!",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    args = command.args
    if args and args.startswith("reveal_"):
        try:
            chat_id = int(args.split("_")[1])
            await show_trait_menu_pm(message.from_user.id, chat_id, message)
            return
        except ValueError:
            pass

    await message.answer("⚽️ <b>Футбольный Бункер запущен!</b>\nДобавь бота в групповой чат и напиши <code>/game</code> для старта.", parse_mode="HTML")

async def show_trait_menu_pm(user_id: int, chat_id: int, message_obj: types.Message):
    card = await db.get_player_card(chat_id, user_id)
    if not card or card[10] == 0:
        return await message_obj.answer("❌ Ты изгнан из команды и не можешь совершать действия!")

    lobby = await db.get_lobby(chat_id)
    if not lobby:
        return await message_obj.answer("⚠️ Игра не найдена. Создайте новую игру через /game.")

    current_round = lobby[4]
    target_count = min(5, current_round)
    revealed_count = await db.get_player_revealed_count(chat_id, user_id)

    card_text_lines = ["🎴 <b>ТВОЯ КАРТОЧКА ИГРОКА:</b>\n"]
    builder = InlineKeyboardBuilder()

    for trait_code, title, val_idx, rev_idx in TRAITS_INFO:
        val = html.escape(str(card[val_idx]))
        is_rev = card[rev_idx]
        if is_rev:
            card_text_lines.append(f"{title}: <b>{val}</b> 🔓")
        else:
            card_text_lines.append(f"{title}: <i>[Скрыто 🔒]</i>")
            builder.button(text=f"🔓 {title}", callback_data=f"pmrev_{chat_id}_{trait_code}")

    card_status = "\n".join(card_text_lines)

    if revealed_count >= target_count:
        return await message_obj.answer(
            f"{card_status}\n\n"
            f"✅ <b>Ты открыл карту для Раунда {current_round}! ({revealed_count}/{target_count})</b>\n"
            f"Возвращайся в общий чат и жди очереди других участников.", 
            parse_mode="HTML"
        )

    builder.adjust(1)
    await message_obj.answer(
        f"{card_status}\n\n"
        f"🎯 <b>Раунд {current_round} ({revealed_count}/{target_count} открыто):</b>\n"
        f"Выбери, какую карту ты хочешь вскрыть в общий чат:", 
        reply_markup=builder.as_markup(), 
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("pmrev_"))
async def process_pm_reveal(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("_")
        chat_id = int(parts[1])
        trait_code = parts[2]
        user = callback.from_user

        turn_p_id, turn_p_name, _ = await get_next_turn_player(chat_id)
        if user.id != turn_p_id:
            safe_current = html.escape(turn_p_name) if turn_p_name else "другой игрок"
            return await callback.answer(f"⏳ Сейчас ход игрока {safe_current}! Подожди своей очереди.", show_alert=True)

        card = await db.get_player_card(chat_id, user.id)
        if not card or card[10] == 0:
            return await callback.answer("❌ Ты выбыл из игры!", show_alert=True)

        lobby = await db.get_lobby(chat_id)
        current_round = lobby[4]
        target_count = min(5, current_round)
        revealed_count = await db.get_player_revealed_count(chat_id, user.id)

        if revealed_count >= target_count:
            return await callback.answer(f"Ты уже открыл достаточно карт ({target_count})!", show_alert=True)

        await db.reveal_trait(chat_id, user.id, trait_code)

        val_map = {
            "position": (card[0], "⚽️ Позицию"),
            "health": (card[1], "🏥 Здоровье/Физуху"),
            "skill": (card[2], "🎯 Навык"),
            "inventory": (card[3], "🎒 Багаж"),
            "secret": (card[4], "🤫 Секрет")
        }
        val, title = val_map[trait_code]
        safe_user_name = html.escape(user.first_name)
        safe_val = html.escape(str(val))

        await bot.send_message(
            chat_id, 
            f"📢 <b>{safe_user_name}</b> вскрывает свой параметр <b>{title}</b>:\n👉 <i>{safe_val}</i>", 
            parse_mode="HTML"
        )

        await callback.message.edit_text(
            f"✅ <b>{title}</b> успешно отправлена в чат!\nВозвращайся в группу.", 
            parse_mode="HTML"
        )

        await prompt_next_player(chat_id)

    except Exception as e:
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)

@dp.message(Command("game"))
async def cmd_game(message: types.Message):
    if message.chat.type == "private":
        return await message.answer("Играть можно только в групповых чатах!")

    await db.create_lobby(message.chat.id, message.from_user.id)
    bot_info = await bot.get_me()
    safe_host_name = html.escape(message.from_user.first_name)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="⚽️ Вступить в игру", callback_data="join_game")
    builder.button(text="📩 Открыть ЛС с ботом", url=f"https://t.me/{bot_info.username}?start=game")
    builder.button(text="🚀 Начать игру (от 3 игроков)", callback_data="start_game")
    builder.adjust(1)

    await message.answer(
        f"🎮 <b>Идет сбор в Футбольный Бункер!</b>\n\n"
        f"Организатор: <a href='tg://user?id={message.from_user.id}'>{safe_host_name}</a>\n"
        f"Участников: 0\n\n"
        f"<i>Минимум для старта: 3 человека.</i>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "join_game")
async def join_game(callback: types.CallbackQuery):
    try:
        chat_id = callback.message.chat.id
        user = callback.from_user
        
        empty_pack = {
            "position": "Ожидает старта", "health": "Ожидает старта",
            "skill": "Ожидает старта", "inventory": "Ожидает старта", "secret": "Ожидает старта"
        }
        await db.add_player(chat_id, user.id, user.first_name, empty_pack)
        players = await db.get_players(chat_id)
        bot_info = await bot.get_me()
        
        builder = InlineKeyboardBuilder()
        builder.button(text="⚽️ Вступить в игру", callback_data="join_game")
        builder.button(text="📩 Открыть ЛС с ботом", url=f"https://t.me/{bot_info.username}?start=game")
        builder.button(text="🚀 Начать игру (от 3 игроков)", callback_data="start_game")
        builder.adjust(1)

        player_list = "\n".join([f"• {html.escape(p[0])}" for p in players])
        await callback.message.edit_text(
            f"🎮 <b>Идет сбор в Футбольный Бункер!</b>\n\nУчастники ({len(players)}):\n{player_list}",
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
        for idx, (p_name, p_id, _) in enumerate(players):
            await db.add_player(chat_id, p_id, p_name, packs[idx])

        scen = cards.generate_scenario(num_players)
        # Фиксируем ровно 2 победителя
        await db.update_lobby_scenario(chat_id, scen["text"], 2)
        
        for idx, (p_name, p_id, _) in enumerate(players):
            try:
                pack = packs[idx]
                text_card = (
                    f"🎴 <b>ТВОЯ КАРТОЧКА ИГРОКА СФОРМИРОВАНА!</b>\n\n"
                    f"⚽️ <b>Позиция:</b> {html.escape(pack['position'])}\n"
                    f"🏥 <b>Здоровье:</b> {html.escape(pack['health'])}\n"
                    f"🎯 <b>Навык:</b> {html.escape(pack['skill'])}\n"
                    f"🎒 <b>Багаж:</b> {html.escape(pack['inventory'])}\n"
                    f"🤫 <b>Секрет:</b> {html.escape(pack['secret'])}\n\n"
                    f"<i>Жди своего хода в группе и выбирай, какую карту вскрывать!</i>"
                )
                await bot.send_message(p_id, text_card, parse_mode="HTML")
            except Exception:
                pass

        await callback.message.edit_text(
            f"{scen['text']}\n\n"
            f"🔥 <b>РАУНД 1: Вскрытие карт по очереди!</b>\n"
            f"Каждому игроку нужно открыть по <b>1 характеристике</b>.\n"
            f"<i>(В 1-м раунде голосования не будет — просто знакомимся с составом!)</i>",
            parse_mode="HTML"
        )

        await prompt_next_player(chat_id)

    except Exception as e:
        await callback.answer(f"❌ Ошибка старта: {e}", show_alert=True)

async def start_voting_flow(chat_id: int, round_num: int):
    await db.set_lobby_status(chat_id, "voting")
    await db.clear_votes(chat_id)
    
    alive_players = await db.get_alive_players(chat_id)
    builder = InlineKeyboardBuilder()
    for p_name, p_id in alive_players:
        builder.button(text=f"❌ {p_name}", callback_data=f"vote_{p_id}")
    builder.adjust(2)

    await bot.send_message(
        chat_id,
        f"🗳 <b>ОБСУЖДЕНИЕ ОКОНЧЕНО! НАЧИНАЕТСЯ ГОЛОСОВАНИЕ (Раунд {round_num})!</b>\n\n"
        f"Голосуйте кнопками ниже, кого выгнать из команды:\n"
        f"<i>Проголосовали: 0/{len(alive_players)}</i>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("vote_"))
async def process_vote(callback: types.CallbackQuery):
    try:
        chat_id = callback.message.chat.id
        target_id = int(callback.data.split("_")[1])
        voter = callback.from_user
        
        card = await db.get_player_card(chat_id, voter.id)
        if not card or card[10] == 0:
            return await callback.answer("❌ Ты изгнан и не можешь голосовать!", show_alert=True)

        if voter.id == target_id:
            return await callback.answer("⚠️ За себя голосовать нельзя!", show_alert=True)

        if await db.has_user_voted(chat_id, voter.id):
            return await callback.answer("⚠️ Ты уже отдал свой голос в этом раунде!", show_alert=True)

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
        alive_players = await db.get_alive_players(chat_id)

        if voters_count >= len(alive_players):
            await bot.send_message(chat_id, "🎉 <b>Все игроки проголосовали! Подводим итоги...</b>", parse_mode="HTML")
            await finish_voting_flow(chat_id)
    except Exception as e:
        await callback.answer(f"❌ Ошибка голосования: {e}", show_alert=True)

async def finish_voting_flow(chat_id: int):
    lobby = await db.get_lobby(chat_id)
    votes_data = await db.get_votes_detailed(chat_id)
    
    if not votes_data:
        return

    results_lines = [f"• <b>{html.escape(t_name)}</b>: {count} гол." for _, t_name, count in votes_data]
    results_text = "\n".join(results_lines)

    max_votes = votes_data[0][2]
    top_candidates = [v for v in votes_data if v[2] == max_votes]

    tie_note = ""
    if len(top_candidates) > 1:
        chosen = random.choice(top_candidates)
        kicked_id, kicked_name = chosen[0], chosen[1]
        tie_note = f"\n\n⚽️ <i>Равное количество голосов! В серии пенальти роковой промах совершил <b>{html.escape(kicked_name)}</b>!</i>"
    else:
        kicked_id, kicked_name = votes_data[0][0], votes_data[0][1]

    await db.eliminate_player(chat_id, kicked_id)
    await db.clear_votes(chat_id)
    
    alive = await db.get_alive_players(chat_id)
    winners_needed = 2  # Строго 2 победителя

    safe_kicked_name = html.escape(kicked_name)

    if len(alive) <= winners_needed:
        winners_str = "\n".join([f"🏆 <b>{html.escape(p[0])}</b>" for p in alive])
        await bot.send_message(
            chat_id,
            f"📊 <b>ИТОГИ ГОЛОСОВАНИЯ:</b>\n{results_text}{tie_note}\n\n"
            f"❌ Из команды изгнан: <b>{safe_kicked_name}</b>\n\n"
            f"🎉 <b>ИГРА ОКОНЧЕНА!</b>\n\n"
            f"Контракт с клубом получают 2 лучших игрока:\n{winners_str}",
            parse_mode="HTML"
        )
        await db.set_lobby_status(chat_id, "ended")
    else:
        next_round = lobby[4] + 1
        await db.set_lobby_status(chat_id, f"round{next_round}", next_round)

        await bot.send_message(
            chat_id,
            f"📊 <b>ИТОГИ ГОЛОСОВАНИЯ:</b>\n{results_text}{tie_note}\n\n"
            f"❌ Из команды изгнан: <b>{safe_kicked_name}</b>\n\n"
            f"🔥 <b>РАУНД {next_round}!</b>\n"
            f"Осталось претендентов: {len(alive)}. Нужно оставить 2 победителей.\n"
            f"Начинаем открывать по еще одной карте!",
            parse_mode="HTML"
        )
        await prompt_next_player(chat_id)

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
