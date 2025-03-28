import asyncio
import os
from aiogram import Bot, Dispatcher, F
from dotenv import load_dotenv
from datetime import datetime, timedelta
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BufferedInputFile, InputFile, URLInputFile, FSInputFile
from aiogram.filters.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from babel.dates import format_date
import numpy as np
import pandas as pd
from matplotlib.table import Table
import re
import html
import aiohttp
import tempfile
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import io
from logger import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from database import User

load_dotenv()

bot = Bot(os.getenv('TOKEN'))
dp = Dispatcher()
scheduler = AsyncIOScheduler()

class AuthStates(StatesGroup):
    login = State()
    password = State()

class DiaryDateStates(StatesGroup):
    waiting_for_date = State()

def remove_unpaired_asterisks(text):
    asterisk_count = text.count("*")
    if asterisk_count % 2 != 0:
        text = text.replace("*", "", 1) if asterisk_count > 0 else text
        return remove_unpaired_asterisks(text)
    return text

def markdown_protect(text):
    text = str(text)
    text = text.replace("\r\n", "\n")
    text = remove_unpaired_asterisks(text)
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!\\-])", r"\\\1", text)


@dp.message(default_state, F.text == '/start')
async def start(message: Message, state: FSMContext):
    await state.clear()
    user = User.get_or_none(id=message.from_user.id)
    if user and user.login and user.password:
        logging.info(f"{message.from_user.id} | /start")
        markup = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text='📖 Дневник'), KeyboardButton(text='📅 Расписание')],
        [KeyboardButton(text='📊 Успеваемость'), KeyboardButton(text='❌ Пропущенные уроки')],
        [KeyboardButton(text='👤 Профиль')]
    ], resize_keyboard=True)
        await message.answer(f"👋 Привет, {user.FIO}! Вы уже авторизованы.", reply_markup=markup)
    else:
        logging.info(f"{message.from_user.id} | Авторизация")
        markup = ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text='Авторизация')]
        ], resize_keyboard=True, one_time_keyboard=True)
        await message.answer("👋 Привет! Чтобы начать пользоваться ботом, необходимо авторизоваться.", reply_markup=markup)


@dp.message(default_state, F.text == 'Авторизация')
async def start_auth(message: Message, state: FSMContext):
    await message.answer("🔑 Введите ваш логин:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(AuthStates.login)



@dp.message(AuthStates.login)
async def get_login(message: Message, state: FSMContext):
    await state.update_data(login=message.text)
    await message.reply('🔒 Введите ваш пароль:')
    await state.set_state(AuthStates.password)
    logging.debug(f"{message.from_user.id} | Приняли логин")





@logging.catch()
@dp.message(AuthStates.password)
async def get_password(message: Message, state: FSMContext):
    user_data = await state.get_data()
    login = user_data['login']
    password = message.text
    logging.debug(f"{message.from_user.id} | Приняли пароль")

    try:
        async with aiohttp.ClientSession() as session:
            user: User = User.create(id=message.from_user.id)
            await user.credentials(login, password, session)
            print(await user.get_new_grades(session))
            markup = ReplyKeyboardMarkup(keyboard=[
                [KeyboardButton(text='📖 Дневник'), KeyboardButton(text='📅 Расписание')],
                [KeyboardButton(text='📊 Успеваемость'), KeyboardButton(text='❌ Пропущенные уроки')],
                [KeyboardButton(text='👤 Профиль')]
            ], resize_keyboard=True)

            await message.reply(f'✅ Авторизация успешна, {user.FIO}!', reply_markup=markup)
            await message.delete()
            logging.success(f"{message.from_user.id} | {user.FIO} | Авторизация успешна")
            await state.clear()

    except Exception as e:
        await message.reply(f'❌ Ошибка авторизации: {e}. Попробуйте еще раз. /start')
        logging.error(f"{message.from_user.id} | {e} Ошибка авторизации")
        user.delete_instance()
        await state.clear()

@dp.message(F.text == '📖 Дневник')
async def diary(message: Message, state: FSMContext):
    user: User = User.get_or_none(id=message.from_user.id)
    if user:
        today = datetime.now()
        keyboard = []
        for i in range(-2, 7):
            date = today + timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")
            day_name = date.strftime("%A")
            button_text = f"{date.strftime('%d.%m.%Y')} ({format_date(date, 'EEEE', locale='ru_RU').title()})"
            keyboard.append([InlineKeyboardButton(text=button_text, callback_data=f"diary_date:{date_str}")])

        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await state.update_data(original_message_id=message.message_id+1)
        
        await message.reply("Выберите дату:", reply_markup=markup)
        await state.set_state(DiaryDateStates.waiting_for_date)
        logging.debug(f'{message.from_user.id} | {user.FIO} | вывод дат дневника')
    else:
        await message.reply("Сначала необходимо авторизоваться. /start")


@dp.callback_query(DiaryDateStates.waiting_for_date, F.data.startswith('diary_date:'))
async def process_diary_date(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    user = User.get_or_none(id=callback_query.from_user.id)
    if user:
        date_str = callback_query.data.split(":")[1]
        async with aiohttp.ClientSession() as session:
            await user._check_token_expire(session)
            diary = await user._fetch_data('http://api-mobile.nz.ua/v1/schedule/diary', [date_str], session)
        data = await state.get_data()
        original_message_id = data.get('original_message_id')
        if diary and diary.get('dates'):
            date_data = diary['dates'][0]
            date_str_display = html.escape(date_data['date'])
            calls = date_data['calls']

            diary_html = f"📅 <b>Дневник за {date_str_display}:</b>\n\n"

            for call in calls:
                call_number = call['call_number']
                subjects = call['subjects']

                for subject in subjects:
                    subject_name = html.escape(subject['subject_name'])
                    teacher_name = html.escape(subject['teacher']['name']) if subject.get('teacher') else "—" 
                    lessons = subject['lesson']
                    hometask = subject['hometask']
                    diary_html += f"<b>{call_number}. {subject_name} ({teacher_name}):</b>\n"


                    for lesson in lessons:
                        lesson_type = html.escape(lesson['type'])
                        mark = html.escape(lesson['mark']) if lesson['mark'] else "—"
                        comment = html.escape(lesson['comment']) if lesson['comment'] else ""
                        diary_html += f"    - {lesson_type}: {mark} {comment}\n"

                    if any(hometask):
                        diary_html += "    - <b>Домашнее задание:</b>\n"
                        for task in hometask:
                            escaped_task = html.escape(task)
                            diary_html += f"      - {escaped_task}\n"
                    else:
                        diary_html += "     - Домашнее задание отсутствует\n"

            await bot.edit_message_text(text=diary_html, chat_id=callback_query.message.chat.id,
                message_id=original_message_id,
                parse_mode="HTML", disable_web_page_preview=True
            )
            logging.success(f'{callback_query.message.from_user.id} | {user.FIO} | Вывод дневника за {date_str}')


        else:
                await bot.edit_message_text(
                text="На выбранную дату нет записей в дневнике.",
                chat_id=callback_query.message.chat.id,
                message_id=original_message_id,

            )

        await state.clear()

@dp.message(F.text == '📅 Расписание')
async def timetable(message: Message):
    user: User = User.get_or_none(id=message.from_user.id)
    if user:
        today = datetime.now()
        start_of_week = today - timedelta(days=today.weekday())
        end_of_week = start_of_week + timedelta(days=6)
        dates = [start_of_week.strftime("%Y-%m-%d"), end_of_week.strftime("%Y-%m-%d")]
        async with aiohttp.ClientSession() as session:
            await user._check_token_expire(session)
            diary_data = await user._fetch_data('http://api-mobile.nz.ua/v1/schedule/timetable', dates, session)

        if diary_data and diary_data.get('dates'):
            timetable_html = "📅 <b>Расписание на неделю:</b> ✨\n\n"

            for day_data in diary_data['dates']:
                date_str_display = day_data['date']
                day_name = format_date(datetime.strptime(date_str_display, '%Y-%m-%d'), 'EEEE', locale='ru_RU').title()
                timetable_html += f"<b>{day_name} ({date_str_display}):</b> 🗓️\n"

                if day_data.get('calls'):
                    for call in day_data['calls']:
                        call_number = call['call_number']
                        subjects = call['subjects']
                        timetable_html += f"<i>{call_number}.</i> "

                        for subject in subjects:
                            subject_name = html.escape(subject['subject_name'])
                            teacher_name = html.escape(subject['teacher']['name']) if subject.get('teacher') else "—"
                            timetable_html += f"{subject_name} ({teacher_name}) \n"

                else:
                    timetable_html += "Нет уроков 🎉\n"

            await message.reply(timetable_html, parse_mode="HTML")
            logging.success(f'{message.from_user.id} | {user.FIO} | Вывод рассписания')
        else:
            await message.reply("Расписание на эту неделю не найдено. 😔")

    else:
        await message.reply("Сначала необходимо авторизоваться. /start")


@dp.message(F.text == '📊 Успеваемость')
async def student_performance(message: Message):
    user: User = User.get_or_none(id=message.from_user.id)
    if user:
        today = datetime.now()
        start_date = today.replace(day=1).strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")
        try:
            async with aiohttp.ClientSession() as session:
                    await user._check_token_expire(session)
                    performance_data = await user._fetch_data(
                        'http://api-mobile.nz.ua/v1/schedule/student-performance',
                        [start_date, end_date], session
                    )

            if performance_data and performance_data.get('subjects'):
                performance_html = "📊 <b>Успеваемость за текущий месяц:</b>\n\n"

                for subject in performance_data['subjects']:
                    subject_name = html.escape(subject['subject_name'])
                    marks = subject.get('marks', [])
                    if marks:
                        try:
                            avg_grade = round(sum(int(mark) for mark in marks) / len(marks), 2)
                            performance_html += f"<b>{subject_name}:</b> {avg_grade}\n"
                        except (ValueError, TypeError):
                            performance_html += f"<b>{subject_name}:</b> Невозможно вычислить средний балл (некорректные оценки)\n"
                    else:
                        performance_html += f"<b>{subject_name}:</b> Нет оценок\n"

                if performance_data.get('missed'):
                    missed_days = performance_data['missed'].get('days', 0)
                    missed_lessons = performance_data['missed'].get('lessons', 0)
                    performance_html += f"\n<b>Пропущено дней:</b> {missed_days}\n"
                    performance_html += f"<b>Пропущено уроков:</b> {missed_lessons}\n"
                image = user.generate_image()

                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                    temp_file.write(image)
                    temp_file_name = temp_file.name
                logging.debug(temp_file_name)
                photo = FSInputFile(temp_file_name, filename='performance_img.png')
                await message.answer_photo(photo, caption=performance_html, parse_mode="HTML")

                os.remove(temp_file_name)

                logging.success(f'{message.from_user.id} | {user.FIO} | Вывод успеваемости')

            else:
                await message.reply("Данные об успеваемости за этот месяц пока недоступны.")

        except Exception as e:
            logging.exception(f"An unexpected error occurred: {e}")
            await message.reply(f"Произошла непредвиденная ошибка: {e}")

    else:
        await message.reply("Сначала необходимо авторизоваться. /start")
marks2emoji = {
    1: "💩",
    2: "💅",
    3: "☠️",
    4: "✨",
    5: "🤡",
    6: "💞",
    7: "😅",
    8: "🥳",
    9: "🥹",
    10: "🔥",
    11: "🥵",
    12: "😎",
}

# ...

async def background_task():
    async with aiohttp.ClientSession() as session:
        while True:
            time_all = datetime.now()
            users = list(User.select().where(User.token_expired != None))
            
            for user in users:
                try:
                    time_user = datetime.now()
                    
                    marks = await user.get_new_grades(session)
                    logging.success(marks)
                    if not marks or not marks['new_grades']:
                        continue
                    
                    for grade in marks['new_grades']:
                        emoji = marks2emoji[int(grade['mark'])]
                        message = (
                            f"{emoji} *Новая оценка!* {emoji}\n\n"
                            f"*Оценка:* {grade['mark']}\n"
                            f"*Предмет:* {grade['subject']} ({grade['lesson_type']})\n"
                            f"*Дата:* {grade['lesson_date']}\n"
                        )
                        
                        if grade['comment']:
                            message += f"*Комментарий:* {markdown_protect(grade['comment'])}\n"
                        
                        try:
                            await bot.send_message(user.id, message, parse_mode="Markdown")
                            logging.info(f"{user.FIO} Новая оценка {grade['mark']} | {grade['subject']}")
                        except Exception as e:
                            logging.error(f"Ошибка отправки сообщения пользователю {user.FIO}: {e}")
                    
                    if marks['updated_grades']:
                        for grade_data in marks['updated_grades']:
                            new_grade = grade_data['new']
                            old_grade = grade_data['old']
                            
                            emoji = marks2emoji[int(new_grade['mark'])]
                            message = (
                                f"{emoji} *Изменение оценки!* {emoji}\n\n"
                                f"*Предмет:* {new_grade['subject']} ({new_grade['lesson_type']})\n"
                                f"*Дата:* {new_grade['lesson_date']}\n"
                                f"*Оценка:* {old_grade['mark']} -> {new_grade['mark']}\n"
                            )
                            
                            if old_grade['comment']:
                                message += f"*Старый комментарий:* {markdown_protect(old_grade['comment'])}\n"
                            if new_grade['comment']:
                                message += f"*Новый комментарий:* {markdown_protect(new_grade['comment'])}\n"
                            
                            try:
                                await bot.send_message(user.id, message, parse_mode="Markdown")
                                logging.info(f"{user.FIO} Изменение оценки {old_grade['mark']} -> {new_grade['mark']} | {new_grade['subject']}")
                            except Exception as e:
                                logging.error(f"Ошибка отправки сообщения пользователю {user.FIO}: {e}")
                
                except Exception as e:
                    logging.exception(f"Ошибка в цикле: {e}. Продолжаем работу")
                finally:
                    logging.debug(f"	{user.FIO} - ({datetime.now() - time_user}) {marks if marks else None}")
            
            logging.info(f'Время цикла: {datetime.now() - time_all}')
            await asyncio.sleep(60)


@dp.message(F.text == '❌ Пропущенные уроки')
async def missed_lessons(message: Message):
    user: User = User.get_or_none(id=message.from_user.id)
    if user:
        today = datetime.now()
        start_of_month = today.replace(day=1)
        end_of_month = (today.replace(month=today.month + 1, day=1) - timedelta(days=1))
        dates = [start_of_month.strftime("%Y-%m-%d"), end_of_month.strftime("%Y-%m-%d")]

        try:
            async with aiohttp.ClientSession() as session:
                missed_lessons_data = await user._fetch_data('http://api-mobile.nz.ua/v1/schedule/missed-lessons', dates, session)

            if missed_lessons_data and missed_lessons_data.get('missed_lessons'):
                missed_lessons_html = "❌ <b>Пропущенные уроки за месяц:</b>\n\n"

                for lesson in missed_lessons_data['missed_lessons']:
                    lesson_date = lesson['lesson_date']
                    lesson_number = lesson['lesson_number']
                    subject_name = html.escape(lesson['subject'])
                    missed_lessons_html += f"<b>Дата:</b> {lesson_date}, <b>Урок:</b> {lesson_number}, <b>Предмет:</b> {subject_name}\n"
                
                await message.reply(missed_lessons_html, parse_mode="HTML")
                logging.success(f'{message.from_user.id} | {user.FIO} | Вывод пропущенных уроков')

            else:
                await message.reply("В этом месяце пропущенных уроков нет. 🎉")

        except Exception as e:
            logging.error(f"{message.from_user.id} | {user.FIO} | Ошибка при получении пропущенных уроков: {e}")
            await message.reply(f"Произошла ошибка при получении пропущенных уроков: {e}")


    else:
        await message.reply("Сначала необходимо авторизоваться. /start")


@dp.message(F.text == '👤 Профиль')
async def profile(message: Message):
    user: User = User.get_or_none(id=message.from_user.id)
    if user:
        profile_info = (
            f"👤 *Ваш профиль:*\n\n"
            f"*ФИО:* {markdown_protect(user.FIO)}\n"
            f"*Логин:* {markdown_protect(user.login)}\n"
            f"*ID студента:* {user.student_id}\n"
            f"*Срок действия токена:* {datetime.fromtimestamp(user.token_expired).strftime('%Y-%m-%d %H:%M:%S') if user.token_expired else 'Неизвестно'}\n"
        )

        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✖️ Выйти из аккаунта", callback_data="logout")]
        ])

        topin = await message.reply(profile_info, parse_mode="Markdown", reply_markup=markup)
        logging.success(f'{message.from_user.id} | {user.FIO} | Вывод профиля')

    else:
        await message.reply("Сначала необходимо авторизоваться. /start")


@dp.callback_query(F.data == 'logout')
async def logout(callback_query: CallbackQuery):
    user: User = User.get_or_none(id=callback_query.from_user.id)
    if user:
        user.delete_instance()

        await callback_query.message.answer("Вы успешно вышли из аккаунта. /start", reply_markup=ReplyKeyboardRemove())
        await callback_query.answer()
        logging.info(f'{callback_query.from_user.id} | Выход из аккаунта')
    else:
        await callback_query.answer("Вы не авторизованы.")


async def send_tomorrow_homework(user: User, session, callback_query: CallbackQuery = None):
    message_id = callback_query.message.message_id if callback_query else None

    tomorrow = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        diary = await user._fetch_data("http://api-mobile.nz.ua/v1/schedule/diary", [tomorrow], session)

        if diary and diary.get('dates'):
            date_data = diary['dates'][0]
            calls = date_data['calls']

            homework_message = f"📅 <b>Домашнее задание на {tomorrow}:</b>\n\n"

            for call in calls:
                for subject in call['subjects']:
                    if subject['hometask']:
                        homework_message += f"<b>{call['call_number']}. {html.escape(subject['subject_name'])}:</b>\n"
                        for task in subject['hometask']:
                            homework_message += f"• {html.escape(task)}\n"

            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить данные", callback_data=f"refresh_homework:{tomorrow}")]
            ])

            try:
                if message_id:
                    await bot.edit_message_text(
                        homework_message, chat_id=user.id, message_id=message_id,
                        parse_mode="HTML", reply_markup=markup
                    )
                    logging.info(f'{user.id} | {user.FIO} | Домашнее задание на завтра обновлено')

                else:
                    await bot.send_message(
                        user.id, homework_message, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True
                    )
                    logging.info(f'{user.id} | {user.FIO} | Домашнее задание на завтра отправлено')

            except Exception as e:
                if "message is not modified" in str(e):
                    if callback_query: 
                       await bot.answer_callback_query(callback_query.id, "Данные актуальны")
                    logging.info(f'{user.id} | {user.FIO} | Данные по домашнему заданию актуальны')
                else:
                    raise e

        else:
            not_found_message = f"На завтра ({tomorrow}) домашнее задание не найдено."
            if message_id:
                await bot.edit_message_text(not_found_message, chat_id=user.id, message_id=message_id)
            else:
                await bot.send_message(user.id, not_found_message)

    except Exception as e:
        logging.exception(f"Ошибка при отправке домашнего задания: {e}")
        await bot.send_message(user.id, "Произошла ошибка при получении домашнего задания.")



async def scheduled_homework_task():
    for user in User.select().where(User.token_expired != None):
        async with aiohttp.ClientSession() as session:
            await send_tomorrow_homework(user, session)




@dp.callback_query(F.data.startswith('refresh_homework:'))
async def refresh_homework(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    user = User.get_or_none(id=user_id)
    if user:
        await send_tomorrow_homework(user, callback_query=callback_query)
    else:
        await bot.send_message(user_id, "Пользователь не найден. Попробуйте авторизоваться снова. /start")
async def main():
    asyncio.create_task(background_task())
    

    scheduler.add_job(scheduled_homework_task, 'cron', hour=11, minute=0)
    scheduler.start()
    await dp.start_polling(bot)

    
if __name__ == '__main__':
    asyncio.run(main())