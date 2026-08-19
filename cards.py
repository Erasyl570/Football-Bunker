import random

# Список топовых клубов для сюжетов
CLUBS = [
    "Барселона",
    "Реал Мадрид",
    "Ливерпуль",
    "Манчестер Сити",
    "Бавария",
    "ПСЖ",
    "Арсенал",
    "Челси",
    "Интер",
    "Ювентус",
]

# Пулы характеристик игроков

POSITIONS = [
        "Центральный форвард (ST)", "Таргетмен (ФРВ)", "Ложная девятка (False 9)",
    "Оттянутый нападающий (CF)", "Инсайд-форвард", "Прессингующий нападающий",
    "Штрафной «лис» (Poacher)", "Атакующий завершитель"
]

HEALTH_TRAITS = [
    "Абсолютно нетравматичный",
    "Часто получает травму от любого жесткого стыка",
    "Пропускает стабильно 15 матчей за сезон из за травм",
    "Слабая физическая выносливость",
    "Вернулся из отпуска с +8 кг лишнего веса",
    "Частый недосып и вялость",
    "Минимальный процент жира, филигранная выносливость",
    "Боится идти в стыки на искусственном газоне",
    "Восстанавливается после любых нагрузок за 24 часа",
    "Соблюдает жесткую диету, спит по 9 часов, принимает ледяные ванны"
]

SKILLS = [
    "Пушечный удар",
    "Мастер изолированного дриблинга 1 на 1",
    "Видение поля на высочайшем уровне",
    "100% реализация пенальти",
    "Слишком часто симулирует",
    "Умеет бить с обеих ног хорошо",
    "Мастер в исполнении стандартов",
    "Деревянный прием мяча",
    "Воздушная доминация",
    "Плохая реализация моментов",
    "Умение вбрасывать длинные ауты",
    "Абсолютный лидер на поле и вне поля",
]

INVENTORIES = [
    "Золотые бутсы от персонального спонсора",
    "Персональный массажист и личный фитнес-тренер",
    "Диплом спортивного аналитика и планшет с подробным разбором соперников",
    "Ингалятор, эластичные бинты и заживляющие спреи",
    "Вип-подписка на спортивную аналитику и персональный SMM-менеджер",
    "Счастливая капитанская повязка, приносящая удачу",
    "Банка энергетика и стимуляторы перед матчем",
    "Портативный массажный пистолет и изотоники",
    "Защитная маска на лицо",
    "Набор профессиональных видеокамер для снятия влогов на YouTube"
]

SECRETS = [
    "Секретно болеет за главнейшего заклятого врага вашего клуба",
    "Был пойман на нарушении режима в ночном клубе",
    "Имеет тайный предварительный контракт с клубом из Саудовской Аравии",
    "В детстве 7 лет занимался балетом, благодаря чему имеет невероятную гибкость",
    "Принимал запрещенный жиросжигатель перед началом предсезонных сборов",
    "На самом деле ему на 3 года больше, чем написано в паспорте (переписанный)",
    "Был забанен на 6 месяцев за ставки на матчи собственной лиги",
    "Сын вице-президента клуба, попал в основную команду по блату",
    "Задрот компьютерных игр",
    "Ни разу в жизни не смотрел полный футбольный матч от начала до конца"
]

def generate_game_packs(num_players: int) -> list:
    """
    Генерирует уникальный набор карт (пак) для каждого игрока.
    """
    positions = random.sample(POSITIONS, k=min(num_players, len(POSITIONS)))
    if num_players > len(POSITIONS):
        positions += random.choices(POSITIONS, k=num_players - len(POSITIONS))

    healths = random.sample(HEALTH_TRAITS, k=min(num_players, len(HEALTH_TRAITS)))
    if num_players > len(HEALTH_TRAITS):
        healths += random.choices(HEALTH_TRAITS, k=num_players - len(HEALTH_TRAITS))

    skills = random.sample(SKILLS, k=min(num_players, len(SKILLS)))
    if num_players > len(SKILLS):
        skills += random.choices(SKILLS, k=num_players - len(SKILLS))

    inventories = random.sample(INVENTORIES, k=min(num_players, len(INVENTORIES)))
    if num_players > len(INVENTORIES):
        inventories += random.choices(INVENTORIES, k=num_players - len(INVENTORIES))

    secrets = random.sample(SECRETS, k=min(num_players, len(SECRETS)))
    if num_players > len(SECRETS):
        secrets += random.choices(SECRETS, k=num_players - len(SECRETS))

    random.shuffle(positions)
    random.shuffle(healths)
    random.shuffle(skills)
    random.shuffle(inventories)
    random.shuffle(secrets)

    packs = []
    for i in range(num_players):
        pack = {
            "position": positions[i],
            "health": healths[i],
            "skill": skills[i],
            "inventory": inventories[i],
            "secret": secrets[i]
        }
        packs.append(pack)

    return packs

def generate_scenario(players_count: int) -> dict:
    """
    Генерирует случайный сюжет игры для клуба с фиксированным числом победителей (2 человека).
    """
    club = random.choice(CLUBS)
    
    # Всегда строго 2 победителя
    winners_needed = 2 
    
    scenarios = [
        f"📉 <b>КРИЗИС АТАКИ В {club.upper()}!</b>\nРуководство в ярости от плохой реализации. Клуб срочно ищет <b>{winners_needed} игроков</b> в основу, остальные идут на трансфер.",
        f"🦠 <b>ЭПИДЕМИЯ В {club.upper()}!</b>\nПоловина состава выпала перед важнейшим матчем сезона. Остаться в команде смогут только <b>{winners_needed}</b> самых стойких и физически готовых игроков.",
        f"🔥 <b>МЕГА-ПЕРЕСТРОЙКА В {club.upper()}!</b>\nНовый главный тренер полностью меняет тактику. Контракт с клубом получат только <b>{winners_needed}</b> лучших претендентов!"
    ]
    
    return {
        "club": club,
        "winners_needed": winners_needed,
        "text": random.choice(scenarios)
    }
