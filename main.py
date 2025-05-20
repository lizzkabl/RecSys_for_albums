import asyncio

from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import conn, cursor, UserProfile, _df as df

# ---------- Настройка Бота ----------
TOKEN = "8049310715:AAFGmVpSIiBsJ4zmLeEK5sC5e6P4Y-mf5bE"
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Храним профили пользователей в памяти
user_profiles: dict[int, UserProfile] = {}

# Клавиатуры
rating_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=str(i)) for i in range(1, 6)],
        [KeyboardButton(text="Пропустить")]
    ],
    resize_keyboard=True
)
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Получить рекомендацию")],
        [KeyboardButton(text="Сбросить оценки")],
        [KeyboardButton(text="Выход")]
    ],
    resize_keyboard=True
)

# Состояния
class Form(StatesGroup):
    RATE = State()
    MAIN = State()

# ---------- Хендлеры ----------
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    uid = message.from_user.id
    uname = message.from_user.username or f"user{uid}"

    # Создаем профиль пользователя и регистрируем в БД
    profile = UserProfile(uid, uname)
    user_profiles[uid] = profile

    await message.answer(
        "🎵 Добро пожаловать! Оцените альбомы, чтобы начать:",
        reply_markup=rating_kb
    )

    # Предлагаем первый альбом
    next_album = df.sample(1)['album_name'].values[0]
    await message.answer(f"Оцените: {next_album}")
    await state.set_state(Form.RATE)
    await state.update_data(current_album=next_album)

@dp.message(Form.RATE)
async def process_rating(message: Message, state: FSMContext):
    uid = message.from_user.id
    data = await state.get_data()
    album = data.get('current_album')
    text = message.text

    if text.isdigit() and 1 <= int(text) <= 5:
        profile = user_profiles.get(uid)
        if profile:
            profile.add_rating(album, int(text))
        await message.answer(
            f"Оценка {text} сохранена!",
            reply_markup=main_kb
        )
        await state.set_state(Form.MAIN)
    else:
        await message.answer(
            "Используйте кнопки для оценки.",
            reply_markup=rating_kb
        )

@dp.message(Form.MAIN)
async def main_menu(message: Message, state: FSMContext):
    uid = message.from_user.id
    text = message.text
    profile = user_profiles.get(uid)

    if text == "Получить рекомендацию":
        rec = profile.get_recommendation() if profile else None
        if rec:
            await message.answer(
                f"🎧 Рекомендую: {rec}\nОцените его:",
                reply_markup=rating_kb
            )
            await state.set_state(Form.RATE)
            await state.update_data(current_album=rec)
        else:
            await message.answer(
                "Недостаточно данных для рекомендации. Оцените больше альбомов.",
                reply_markup=rating_kb
            )
            next_album = df.sample(1)['album_name'].values[0]
            await message.answer(f"Оцените: {next_album}")
            await state.set_state(Form.RATE)
            await state.update_data(current_album=next_album)

    elif text == "Сбросить оценки":
        cursor.execute("DELETE FROM ratings WHERE user_id = %s", (uid,))
        conn.commit()
        user_profiles[uid] = UserProfile(uid, message.from_user.username or f"user{uid}")
        await message.answer(
            "🔄 Оценки сброшены. Начните заново:",
            reply_markup=rating_kb
        )
        next_album = df.sample(1)['album_name'].values[0]
        await message.answer(f"Оцените: {next_album}")
        await state.set_state(Form.RATE)
        await state.update_data(current_album=next_album)

    elif text == "Выход":
        await message.answer(
            "До встречи! 👋",
            reply_markup=types.ReplyKeyboardRemove()
        )
        await state.clear()

# ---------- Запуск ----------
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
