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

# Порядок вскрытия карт: (код, название_в_чате, название_для_кнопки, индекс_в_card)
TRAITS_ORDER = [
    ("position", "⚽️ Позиция", "⚽️ Вскрыть Позицию", 0),
    ("health", "🏥 Здоровье/Физуха", "🏥 Вскрыть Здоровье", 1),
    ("skill", "🎯 Навык", "🎯 Вскрыть Навык", 2),
    ("inventory", "🎒 Багаж", "🎒 Вскрыть Багаж", 3),
    ("secret", "🤫 Секрет", "🤫 Вскрыть Секрет", 4)
]

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
    target_count = min(5, current_round + 1)
    revealed_count = await db.get_player_revealed_count(chat_id, user_id)

    card_text_lines = ["🎴 <b>ТВОЯ КАРТОЧКА ИГРОКА:</b>\n"]
    for trait_code, title, _, idx in TRAITS_ORDER:
        val = html.escape(str(card[idx]))
        is_rev = card[idx + 5]
        if is_rev:
            card_text_lines.append(f"{title}: <b>{val}</b> 🔓")
        else:
            card_text_lines.append(f"{title}: <i>[Скрыто 🔒]</i>")

    card_status = "\n".join(card_text_lines)

    if revealed_count >= target_count:
        return await message_obj.answer(
            f"{card_status}\n\n"
            f"✅ <b>Ты открыл нужное количество карт ({revealed_count}/{target_count}) для Раунда {current_round}!</b>\n"
            f"Возвращайся в общий чат и жди остальных участников.", 
            parse_mode="HTML"
        )

    next_trait = TRAITS_ORDER[revealed_count]
    next_code, next_title, next_btn_text, _ = next_trait

    builder = InlineKeyboardBuilder()
    builder.button(text=f"🔓 {next_btn_text}", callback_data=f"pmrev_{chat_id}_{next_code}")

    await message_obj.answer(
        f"{card_status}\n\n"
        f"🎯 <b>Раунд {current_round} ({revealed_count}/{target_count} открыто):</b>\n"
        f"Следующая карта для вскрытия в общий чат: <b>{next_title}</b>", 
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

        card = await db.get_player_card(chat_id, user.id)
        if not card or card[10] == 0:
            return await callback.answer("❌ Ты выбыл из игры!", show_alert=True)

        lobby = await db.get_lobby(chat_id)
        current_round = lobby[4]
        target_count = min(5, current_round + 1)
        revealed_count = await db.get_player_revealed_count(chat_id, user.id)

        if revealed_count >= target_count:
            return await callback.answer(f"Ты уже открыл достаточно карт ({target_count})!", show_alert=True)

        await db.reveal_trait(chat_id, user.id, trait_code)
        new_revealed_count = revealed_count + 1

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

        if new_revealed_count < target_count:
            await callback.message.edit_text(
                f"✅ <b>{title}</b> отправлена в чат!\n\n"
                f"⚠️ В этом раунде тебе нужно открыть еще 1 карту.", 
                parse_mode="HTML"
            )
            await show_trait_menu_pm(user.id, chat_id, callback.message)
        else:
            await callback.message.edit_text(
                f"✅ <b>{title}</b> отправлена в чат!\n\n"
                f"🎉 Все карты на Раунд {current_round} открыты ({new_revealed_count}/{target_count})! Возвращайся в общий чат.", 
                parse_mode="HTML"
            )

        if await db.are_all_revealed_for_round(chat_id, current_round):
            curr_lobby = await db.get_lobby(chat_id)
            if curr_lobby[0] not in ["discussion", "voting"]:
                await db.set_lobby_status(chat_id, "discussion")
                
                req_cards = "2 характеристики" if current_round == 1 else f"{target_count} характеристики"
                await bot.send_message(
                    chat_id,
                    f"🎉 <b>Все живые игроки открыли по {req_cards}!</b>\n\n"
                    f"💬 <b>ОБСУЖДЕНИЕ (1 МИНУТА)!</b>\n"
                    f"У вас есть 60 секунд, чтобы обсудить кандидатов и решить, кто меньше всего подходит команде.\n\n"
                    f"⏳ <i>Голосование начнется автоматически через 1 минуту...</i>",
                    parse_mode="HTML"
                )
                
                await asyncio.sleep(60)
                
                check_lobby = await db.get_lobby(chat_id)
                if check_lobby and check_lobby[0] == "discussion":
                    await start_voting_flow(chat_id, current_round)
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
        
        dummy_pack = {
            "position": "Определится при старте",
            "health": "Определится при старте",
            "skill": "Определится при старте",
            "inventory": "Определится при старте",
            "secret": "Определится при старте"
        }
        await db.add_player(chat_id, user.id, user.first_name, dummy_pack)
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
        await db.update_lobby_scenario(chat_id, scen["text"], scen["winners_needed"])
        
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
                    f"<i>Вскрывай характеристики по порядку через меню бота!</i>"
                )
                await bot.send_message(p_id, text_card, parse_mode="HTML")
            except Exception:
                pass

        bot_info = await bot.get_me()
        builder = InlineKeyboardBuilder()
        builder.button(text="🎴 Вскрыть карты в ЛС", url=f"https://t.me/{bot_info.username}?start=reveal_{chat_id}")
        builder.adjust(1)

        await callback.message.edit_text(
            f"{scen['text']}\n\n"
            f"🔥 <b>РАУНД 1: Вскрытие карт</b>\n"
            f"Каждому игроку нужно последовательно вскрыть <b>2 характеристики</b> (Позицию и Здоровье)!\n"
            f"Нажмите кнопку ниже, чтобы перейти в ЛС и открыть первую карту.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
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
    winners_needed = lobby[3]
    bot_info = await bot.get_me()

    safe_kicked_name = html.escape(kicked_name)

    if len(alive) <= winners_needed:
        winners_str = "\n".join([f"🏆 <b>{html.escape(p[0])}</b>" for p in alive])
        await bot.send_message(
            chat_id,
            f"📊 <b>ИТОГИ ГОЛОСОВАНИЯ:</b>\n{results_text}{tie_note}\n\n"
            f"❌ Из команды изгнан: <b>{safe_kicked_name}</b>\n\n"
            f"🎉 <b>ИГРА ОКОНЧЕНА!</b>\n\n"
            f"Контракт с клубом получают лучшие игроки:\n{winners_str}",
            parse_mode="HTML"
        )
        await db.set_lobby_status(chat_id, "ended")
    else:
        next_round = lobby[4] + 1
        await db.set_lobby_status(chat_id, f"round{next_round}", next_round)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="🎴 Вскрыть карту в ЛС", url=f"https://t.me/{bot_info.username}?start=reveal_{chat_id}")
        builder.adjust(1)

        await bot.send_message(
            chat_id,
            f"📊 <b>ИТОГИ ГОЛОСОВАНИЯ:</b>\n{results_text}{tie_note}\n\n"
            f"❌ Из команды изгнан: <b>{safe_kicked_name}</b>\n\n"
            f"🔥 <b>РАУНД {next_round}!</b>\n"
            f"Осталось претендентов: {len(alive)}. Нужно оставить: {winners_needed}.\n"
            f"Перейдите в ЛС бота для вскрытия следующей карты!",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )

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
