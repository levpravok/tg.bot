import asyncio
import calendar
import json
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

TOKEN = "8657842139:AAGA4ArBvv66CZsOe-ksWhW3lhB4Vhu2ySU"
ADMIN_ID = 1216617675
ADMIN_ID_STR = str(ADMIN_ID)
USERS_FILE = "users.json"
BROADCASTS_FILE = "broadcasts.json"
DATETIME_FMT = "%Y-%m-%d %H:%M"

bot = Bot(token=TOKEN)
dp = Dispatcher()


def load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


users = load_json(USERS_FILE, {})
broadcasts = load_json(BROADCASTS_FILE, [])


class BroadcastStates(StatesGroup):
    waiting_manual_students = State()
    waiting_content = State()
    waiting_datetime = State()
    waiting_repeat_type = State()
    waiting_repeat_days = State()


class StudentStates(StatesGroup):
    waiting_full_name = State()


class AdminStates(StatesGroup):
    waiting_student_new_name = State()


def admin_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📢 Новая рассылка"), KeyboardButton(text="👥 Ученики")],
            [KeyboardButton(text="📊 Активные задачи"), KeyboardButton(text="⚙️ Настройки")],
            [KeyboardButton(text="➕ Пригласить ученика")],
        ],
        resize_keyboard=True,
    )


def student_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✏️ Изменить имя")]],
        resize_keyboard=True,
    )


def broadcast_target_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👥 Всем")],
            [KeyboardButton(text="🎯 Выбрать вручную")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )


def repeat_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔁 Без повтора")],
            [KeyboardButton(text="📅 Каждый месяц")],
            [KeyboardButton(text="⏱️ Каждые N дней")],
            [KeyboardButton(text="❌ Отмена")],
        ],
        resize_keyboard=True,
    )


def datetime_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚀 Отправить сейчас")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
    )


def content_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⬅️ Назад")]],
        resize_keyboard=True,
    )


def manual_students_keyboard(pool: list[dict], selected_ids: set[str]):
    rows = []
    for student in pool:
        uid = student["uid"]
        label = student["label"]
        marker = "✅ " if uid in selected_ids else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}{label}"[:64],
                    callback_data=f"pick:{uid}",
                )
            ]
        )

    rows.append([InlineKeyboardButton(text="✅ Выбрать", callback_data="pick_done")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="pick_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_repeat_info(task: dict):
    repeat_type = task.get("repeat", {}).get("type", "none")
    if repeat_type == "monthly":
        return "каждый месяц"
    if repeat_type == "every_n_days":
        return f"каждые {task.get('repeat', {}).get('days')} дн."
    return "без повтора"


def active_tasks_keyboard(active_tasks_list: list[dict]):
    rows = []
    for task in active_tasks_list:
        task_id = task.get("id")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"👁 Сообщение ID {task_id}",
                    callback_data=f"view_task:{task_id}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 Удалить ID {task_id}",
                    callback_data=f"delete_task:{task_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_students_keyboard(students: list[tuple[str, dict]]):
    rows = []
    for uid, data in students:
        label = format_student_label(data, uid)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"✏️ Изменить: {label}"[:64],
                    callback_data=f"settings_edit:{uid}",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 Удалить: {label}"[:64],
                    callback_data=f"settings_delete:{uid}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_active_tasks_text(active_tasks_list: list[dict]):
    lines = ["📊 Активные задачи:\n"]
    for task in active_tasks_list:
        target_usernames = []
        for uid in task.get("target_ids", []):
            user_data = users.get(str(uid), {})
            username = user_data.get("username") if isinstance(user_data, dict) else None
            full_name = user_data.get("full_name") if isinstance(user_data, dict) else None
            username_text = f"@{username}" if username else f"id:{uid}"
            if full_name:
                target_usernames.append(f"{username_text} ({full_name})")
            else:
                target_usernames.append(username_text)

        targets_text = ", ".join(target_usernames) if target_usernames else "нет получателей"
        lines.append(
            f"ID {task.get('id')}: {task.get('next_send_at')} | "
            f"получатели: {targets_text} | {format_repeat_info(task)}"
        )
    return "\n".join(lines)


def get_students():
    items = []
    for uid, data in users.items():
        if str(uid) == ADMIN_ID_STR:
            continue
        items.append((str(uid), data if isinstance(data, dict) else {}))
    return items


def format_student_label(user_data: dict, uid: str):
    username = user_data.get("username")
    full_name = user_data.get("full_name")
    username_text = f"@{username}" if username else f"id:{uid}"
    if full_name:
        return f"{username_text} ({full_name})"
    return username_text


def normalize_full_name(text: str):
    parts = [part for part in (text or "").strip().split() if part]
    if len(parts) < 2:
        return None
    return " ".join(parts)


def parse_datetime(text: str):
    return datetime.strptime(text.strip(), DATETIME_FMT)


def sanitize_broadcasts_after_user_delete(deleted_uid: str):
    changed = False
    for task in broadcasts:
        target_ids = [str(uid) for uid in task.get("target_ids", [])]
        if deleted_uid in target_ids:
            updated_ids = [uid for uid in target_ids if uid != deleted_uid]
            task["target_ids"] = updated_ids
            if not updated_ids:
                task["active"] = False
            changed = True
    if changed:
        save_json(BROADCASTS_FILE, broadcasts)


def add_one_month(dt: datetime):
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def build_broadcast_preview(data: dict):
    count = len(data["target_ids"])
    repeat = data.get("repeat_type", "none")
    repeat_text = "без повтора"
    if repeat == "monthly":
        repeat_text = "каждый месяц"
    elif repeat == "every_n_days":
        repeat_text = f"каждые {data.get('repeat_days')} дней"

    content_type = data.get("content_type")
    content_text = data.get("text") or data.get("caption") or "(без текста)"

    return (
        f"✅ Рассылка сохранена.\\n"
        f"Получатели: {count}\\n"
        f"Дата/время: {data['send_at']}\\n"
        f"Повтор: {repeat_text}\\n"
        f"Тип: {content_type}\\n"
        f"Текст: {content_text}"
    )


async def finish_broadcast_creation(message: types.Message, state: FSMContext):
    state_data = await state.get_data()

    new_id = max([item.get("id", 0) for item in broadcasts], default=0) + 1
    task = {
        "id": new_id,
        "created_at": datetime.now().strftime(DATETIME_FMT),
        "active": True,
        "target_ids": state_data["target_ids"],
        "content": {
            "type": state_data["content_type"],
            "text": state_data.get("text"),
            "photo_file_id": state_data.get("photo_file_id"),
            "caption": state_data.get("caption"),
        },
        "next_send_at": state_data["send_at"],
        "repeat": {
            "type": state_data.get("repeat_type", "none"),
            "days": state_data.get("repeat_days"),
        },
    }

    broadcasts.append(task)
    save_json(BROADCASTS_FILE, broadcasts)

    await message.answer(build_broadcast_preview(state_data), reply_markup=admin_keyboard())
    await state.clear()


@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        await state.clear()
        await message.answer("Добро пожаловать в панель администратора.", reply_markup=admin_keyboard())
        return

    user_id = str(message.from_user.id)
    existing = users.get(user_id, {})
    if not isinstance(existing, dict):
        existing = {}

    users[user_id] = {
        "username": message.from_user.username,
        "full_name": existing.get("full_name"),
    }
    save_json(USERS_FILE, users)

    if not users[user_id].get("full_name"):
        await state.set_state(StudentStates.waiting_full_name)
        await message.answer(
            "Пожалуйста, введите фамилию и имя (например: Иванов Иван).",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    await state.clear()
    await message.answer("Ваш профиль активен.", reply_markup=student_keyboard())


@dp.message(F.text == "✏️ Изменить имя")
async def request_name_change(message: types.Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        return
    await state.set_state(StudentStates.waiting_full_name)
    await message.answer(
        "Пожалуйста, введите новую фамилию и имя (например: Иванов Иван).",
        reply_markup=ReplyKeyboardRemove(),
    )


@dp.message(StudentStates.waiting_full_name)
async def save_student_name(message: types.Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        return

    full_name = normalize_full_name(message.text or "")
    if not full_name:
        await message.answer("Пожалуйста, введите минимум два слова: Фамилия Имя.")
        return

    user_id = str(message.from_user.id)
    existing = users.get(user_id, {})
    if not isinstance(existing, dict):
        existing = {}
    is_new_registration = not existing.get("full_name")

    users[user_id] = {
        "username": message.from_user.username,
        "full_name": full_name,
    }
    save_json(USERS_FILE, users)

    await state.clear()
    await message.answer("Имя сохранено.", reply_markup=student_keyboard())

    if is_new_registration:
        username = message.from_user.username or "без username"
        try:
            await bot.send_message(
                ADMIN_ID,
                "Новый ученик зарегистрирован:\n"
                f"ID: {message.from_user.id}\n"
                f"Username: @{username}\n"
                f"Имя: {full_name}",
            )
        except Exception:
            pass


@dp.message(F.text == "📢 Новая рассылка")
async def new_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    await message.answer("Пожалуйста, выберите получателей рассылки.", reply_markup=broadcast_target_keyboard())


@dp.message(F.text == "⬅️ Назад")
async def back_to_menu(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    await message.answer("Вы вернулись в главное меню.", reply_markup=admin_keyboard())


@dp.message(F.text == "👥 Всем")
async def send_all_message(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    students = get_students()
    if not students:
        await message.answer("Сейчас нет учеников для рассылки.", reply_markup=admin_keyboard())
        return

    target_ids = [uid for uid, _ in students]
    await state.update_data(target_ids=target_ids)
    await state.set_state(BroadcastStates.waiting_content)
    await message.answer(
        "Пожалуйста, отправьте контент рассылки: текст или фото с подписью.\n"
        "Если хотите вернуться к выбору получателей, нажмите «⬅️ Назад».",
        reply_markup=content_keyboard(),
    )


@dp.message(F.text == "🎯 Выбрать вручную")
async def send_select_message(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    students = get_students()
    if not students:
        await message.answer("Список учеников пока пуст.", reply_markup=admin_keyboard())
        return

    pool = []
    for uid, data in students:
        pool.append({"uid": uid, "label": format_student_label(data, uid)})

    await state.update_data(student_pool=pool, selected_ids=[])
    await state.set_state(BroadcastStates.waiting_manual_students)
    await message.answer(
        "Пожалуйста, отметьте учеников кнопками и нажмите «✅ Выбрать».",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        "Список учеников:",
        reply_markup=manual_students_keyboard(pool, set()),
    )


@dp.callback_query(BroadcastStates.waiting_manual_students, F.data.startswith("pick:"))
async def manual_pick_toggle(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return

    data = await state.get_data()
    pool = data.get("student_pool", [])
    selected_ids = set(data.get("selected_ids", []))
    uid = callback.data.split(":", 1)[1]
    valid_ids = {item["uid"] for item in pool}
    if uid not in valid_ids:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    if uid in selected_ids:
        selected_ids.remove(uid)
    else:
        selected_ids.add(uid)

    await state.update_data(selected_ids=sorted(selected_ids))
    await callback.message.edit_reply_markup(
        reply_markup=manual_students_keyboard(pool, selected_ids)
    )
    await callback.answer()


@dp.callback_query(BroadcastStates.waiting_manual_students, F.data == "pick_done")
async def manual_pick_done(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return

    data = await state.get_data()
    selected_ids = data.get("selected_ids", [])
    if not selected_ids:
        await callback.answer("Пожалуйста, выберите хотя бы одного ученика.", show_alert=True)
        return

    await state.update_data(target_ids=selected_ids)
    await state.set_state(BroadcastStates.waiting_content)
    await callback.message.answer(
        "Теперь, пожалуйста, отправьте текст или фото с подписью для рассылки.\n"
        "Если хотите вернуться к выбору получателей, нажмите «⬅️ Назад».",
        reply_markup=content_keyboard(),
    )
    await callback.answer("Получатели выбраны.")


@dp.callback_query(BroadcastStates.waiting_manual_students, F.data == "pick_back")
async def manual_pick_back(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return

    await state.clear()
    await callback.message.answer("Пожалуйста, выберите получателей рассылки.", reply_markup=broadcast_target_keyboard())
    await callback.answer("Возврат выполнен.")


@dp.message(BroadcastStates.waiting_manual_students)
async def manual_pick_text_fallback(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Пожалуйста, используйте кнопки ниже для выбора учеников.")


@dp.message(BroadcastStates.waiting_content)
async def handle_content(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    if (message.text or "").strip() == "⬅️ Назад":
        await state.clear()
        await message.answer("Пожалуйста, выберите получателей рассылки.", reply_markup=broadcast_target_keyboard())
        return

    if message.photo:
        await state.update_data(
            content_type="photo",
            photo_file_id=message.photo[-1].file_id,
            caption=message.caption,
            text=None,
        )
    elif message.text:
        await state.update_data(
            content_type="text",
            text=message.text,
            photo_file_id=None,
            caption=None,
        )
    else:
        await message.answer("Пожалуйста, отправьте текст или фото с подписью.")
        return

    await state.set_state(BroadcastStates.waiting_datetime)
    await message.answer(
        "Пожалуйста, введите дату и время отправки в формате: YYYY-MM-DD HH:MM\\n"
        "Пример: 2026-03-01 18:30\\n"
        "Или нажмите «🚀 Отправить сейчас».",
        reply_markup=datetime_keyboard(),
    )


@dp.message(BroadcastStates.waiting_datetime)
async def handle_datetime(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    if (message.text or "").strip() == "⬅️ Назад":
        await state.set_state(BroadcastStates.waiting_content)
        await message.answer(
            "Вы вернулись к шагу контента. Пожалуйста, отправьте новый текст или фото с подписью.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if (message.text or "").strip() == "🚀 Отправить сейчас":
        await state.update_data(send_at=datetime.now().strftime(DATETIME_FMT))
        await state.set_state(BroadcastStates.waiting_repeat_type)
        await message.answer("Пожалуйста, выберите режим повтора:", reply_markup=repeat_keyboard())
        return

    try:
        dt = parse_datetime(message.text or "")
        if dt < datetime.now():
            await message.answer("Эта дата уже в прошлом. Пожалуйста, введите будущую дату и время.")
            return

        await state.update_data(send_at=dt.strftime(DATETIME_FMT))
        await state.set_state(BroadcastStates.waiting_repeat_type)
        await message.answer("Пожалуйста, выберите режим повтора:", reply_markup=repeat_keyboard())
    except ValueError:
        await message.answer("Неверный формат. Пожалуйста, используйте: YYYY-MM-DD HH:MM")


@dp.message(BroadcastStates.waiting_repeat_type, F.text == "🔁 Без повтора")
async def repeat_none(message: types.Message, state: FSMContext):
    await state.update_data(repeat_type="none", repeat_days=None)
    await finish_broadcast_creation(message, state)


@dp.message(BroadcastStates.waiting_repeat_type, F.text == "📅 Каждый месяц")
async def repeat_monthly(message: types.Message, state: FSMContext):
    await state.update_data(repeat_type="monthly", repeat_days=None)
    await finish_broadcast_creation(message, state)


@dp.message(BroadcastStates.waiting_repeat_type, F.text == "⏱️ Каждые N дней")
async def repeat_n_days(message: types.Message, state: FSMContext):
    await state.set_state(BroadcastStates.waiting_repeat_days)
    await message.answer("Пожалуйста, введите количество дней (целое число, например 7):")


@dp.message(BroadcastStates.waiting_repeat_type, F.text == "❌ Отмена")
async def repeat_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Создание рассылки отменено.", reply_markup=admin_keyboard())


@dp.message(BroadcastStates.waiting_repeat_type)
async def repeat_invalid(message: types.Message):
    await message.answer("Пожалуйста, выберите вариант кнопкой: без повтора, каждый месяц или каждые N дней.")


@dp.message(BroadcastStates.waiting_repeat_days)
async def handle_repeat_days(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        days = int((message.text or "").strip())
        if days < 1:
            raise ValueError

        await state.update_data(repeat_type="every_n_days", repeat_days=days)
        await finish_broadcast_creation(message, state)
    except ValueError:
        await message.answer("Пожалуйста, укажите положительное целое число, например: 7")


@dp.message(F.text == "👥 Ученики")
@dp.message(Command("students"))
async def show_students(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    students = get_students()
    if not students:
        await message.answer("Список учеников пока пуст.")
        return

    text = "📋 Ученики:\n\n"
    for i, (uid, data) in enumerate(students, start=1):
        text += f"{i}. {format_student_label(data, uid)}\n"

    await message.answer(text)


@dp.message(F.text == "📊 Активные задачи")
async def active_tasks(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    active = [task for task in broadcasts if task.get("active")]
    if not active:
        await message.answer("Сейчас активных задач нет.")
        return

    await message.answer(
        build_active_tasks_text(active),
        reply_markup=active_tasks_keyboard(active),
    )


@dp.message(F.text == "➕ Пригласить ученика")
async def invite_student(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    me = await bot.get_me()
    invite_link = f"https://t.me/{me.username}?start=student"
    await message.answer(
        "Пожалуйста, отправьте ученику эту ссылку, чтобы он зашел в бота и зарегистрировался:\n"
        f"{invite_link}"
    )


@dp.message(F.text == "⚙️ Настройки")
async def settings_menu(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    await state.clear()
    students = get_students()
    if not students:
        await message.answer("Список участников пока пуст.")
        return

    text_lines = ["⚙️ Участники:\n"]
    for idx, (uid, data) in enumerate(students, start=1):
        text_lines.append(f"{idx}. {format_student_label(data, uid)}")

    await message.answer(
        "\n".join(text_lines),
        reply_markup=settings_students_keyboard(students),
    )


@dp.callback_query(F.data == "settings_back")
async def settings_back(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return
    await state.clear()
    await callback.message.answer("Вы вернулись в главное меню.", reply_markup=admin_keyboard())
    await callback.answer("Возврат выполнен.")


@dp.callback_query(F.data.startswith("settings_edit:"))
async def settings_edit_student(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return

    uid = callback.data.split(":", 1)[1]
    if uid not in users:
        await callback.answer("Ученик не найден.", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_student_new_name)
    await state.update_data(edit_student_uid=uid)
    await callback.message.answer(
        "Пожалуйста, введите новое Фамилия Имя для выбранного ученика.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await callback.answer("Введите новое имя.")


@dp.callback_query(F.data.startswith("settings_delete:"))
async def settings_delete_student(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return

    uid = callback.data.split(":", 1)[1]
    if uid not in users:
        await callback.answer("Ученик не найден.", show_alert=True)
        return

    if str(uid) == ADMIN_ID_STR:
        await callback.answer("Администратора удалять нельзя.", show_alert=True)
        return

    users.pop(uid, None)
    save_json(USERS_FILE, users)
    sanitize_broadcasts_after_user_delete(uid)

    students = get_students()
    if not students:
        await callback.message.edit_text("Список участников пуст.")
        await callback.answer("Ученик удален.")
        return

    text_lines = ["⚙️ Участники:\n"]
    for idx, (student_uid, data) in enumerate(students, start=1):
        text_lines.append(f"{idx}. {format_student_label(data, student_uid)}")

    await callback.message.edit_text(
        "\n".join(text_lines),
        reply_markup=settings_students_keyboard(students),
    )
    await callback.answer("Ученик удален.")


@dp.callback_query(F.data.startswith("delete_task:"))
async def delete_active_task(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return

    raw_id = callback.data.split(":", 1)[1]
    try:
        task_id = int(raw_id)
    except ValueError:
        await callback.answer("Неверный ID.", show_alert=True)
        return

    deleted = False
    for task in broadcasts:
        if task.get("id") == task_id and task.get("active"):
            task["active"] = False
            deleted = True
            break

    if not deleted:
        await callback.answer("Задача уже удалена или не найдена.", show_alert=True)
        return

    save_json(BROADCASTS_FILE, broadcasts)
    active = [task for task in broadcasts if task.get("active")]
    if not active:
        await callback.message.edit_text("Сейчас активных задач нет.")
        await callback.answer("Задача удалена.")
        return

    await callback.message.edit_text(
        build_active_tasks_text(active),
        reply_markup=active_tasks_keyboard(active),
    )
    await callback.answer("Задача удалена.")


@dp.callback_query(F.data.startswith("view_task:"))
async def view_task_message(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return

    raw_id = callback.data.split(":", 1)[1]
    try:
        task_id = int(raw_id)
    except ValueError:
        await callback.answer("Неверный ID.", show_alert=True)
        return

    task = next((item for item in broadcasts if item.get("id") == task_id and item.get("active")), None)
    if not task:
        await callback.answer("Задача не найдена.", show_alert=True)
        return

    content = task.get("content", {})
    content_type = content.get("type")
    caption_or_text = content.get("text") or content.get("caption") or "(без текста)"
    header = f"ID {task_id} | {task.get('next_send_at')} | {format_repeat_info(task)}"

    try:
        if content_type == "photo" and content.get("photo_file_id"):
            await callback.message.answer_photo(
                photo=content.get("photo_file_id"),
                caption=f"{header}\n\n{caption_or_text}",
            )
        else:
            await callback.message.answer(f"{header}\n\n{caption_or_text}")
        await callback.answer("Сообщение показано.")
    except Exception:
        await callback.answer("Не удалось показать сообщение.", show_alert=True)


@dp.message(AdminStates.waiting_student_new_name)
async def settings_save_student_name(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    full_name = normalize_full_name(message.text or "")
    if not full_name:
        await message.answer("Пожалуйста, введите минимум два слова: Фамилия Имя.")
        return

    data = await state.get_data()
    uid = data.get("edit_student_uid")
    if not uid or uid not in users:
        await state.clear()
        await message.answer("Ученик не найден.", reply_markup=admin_keyboard())
        return

    user_data = users.get(uid, {})
    if not isinstance(user_data, dict):
        user_data = {}
    user_data["full_name"] = full_name
    users[uid] = user_data
    save_json(USERS_FILE, users)

    await state.clear()
    students = get_students()
    if not students:
        await message.answer("Список участников пуст.", reply_markup=admin_keyboard())
        return

    text_lines = ["⚙️ Участники:\n"]
    for idx, (student_uid, student_data) in enumerate(students, start=1):
        text_lines.append(f"{idx}. {format_student_label(student_data, student_uid)}")

    await message.answer(
        "Имя ученика обновлено.\n\n" + "\n".join(text_lines),
        reply_markup=settings_students_keyboard(students),
    )


@dp.message()
async def student_fallback(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        return
    await message.answer("Сейчас доступна только кнопка «✏️ Изменить имя».", reply_markup=student_keyboard())


async def send_broadcast(task: dict):
    content = task.get("content", {})
    target_ids = task.get("target_ids", [])

    for uid in target_ids:
        try:
            if content.get("type") == "photo":
                await bot.send_photo(
                    chat_id=int(uid),
                    photo=content.get("photo_file_id"),
                    caption=content.get("caption") or "",
                )
            else:
                await bot.send_message(chat_id=int(uid), text=content.get("text") or "")
        except Exception:
            continue


def update_next_send(task: dict, sent_at: datetime):
    repeat = task.get("repeat", {})
    repeat_type = repeat.get("type", "none")

    if repeat_type == "monthly":
        task["next_send_at"] = add_one_month(sent_at).strftime(DATETIME_FMT)
        return

    if repeat_type == "every_n_days":
        days = repeat.get("days") or 1
        task["next_send_at"] = (sent_at + timedelta(days=days)).strftime(DATETIME_FMT)
        return

    task["active"] = False


async def check_scheduled_broadcasts():
    while True:
        now = datetime.now()
        changed = False

        for task in broadcasts:
            if not task.get("active"):
                continue

            next_send_raw = task.get("next_send_at")
            if not next_send_raw:
                continue

            try:
                next_send = parse_datetime(next_send_raw)
            except ValueError:
                task["active"] = False
                changed = True
                continue

            if now >= next_send:
                await send_broadcast(task)
                task["last_sent_at"] = now.strftime(DATETIME_FMT)
                update_next_send(task, next_send)
                changed = True

        if changed:
            save_json(BROADCASTS_FILE, broadcasts)

        await asyncio.sleep(20)


async def main():
    asyncio.create_task(check_scheduled_broadcasts())
    reconnect_delay = 5
    while True:
        try:
            print("Бот запущен...")
            await dp.start_polling(bot)
            break
        except TelegramNetworkError as err:
            print(f"Проблема сети: {err}. Повтор через {reconnect_delay} сек.")
            await asyncio.sleep(reconnect_delay)
        except Exception as err:
            print(f"Непредвиденная ошибка: {err}. Повтор через {reconnect_delay} сек.")
            await asyncio.sleep(reconnect_delay)


if __name__ == "__main__":
    asyncio.run(main())
