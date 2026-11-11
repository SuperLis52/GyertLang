import os
import json
import sqlite3
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from groq import Groq
from gtts import gTTS
from aiohttp import web

# ==========================================
# 🔑 КЛЮЧИ ИЗ НАСТРОЕК RENDER:
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

CURRENT_MODEL = "qwen/qwen3.8-27b"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
groq_client = Groq(api_key=GROQ_API_KEY)

# ==========================================
# 💾 ПОСТОЯННАЯ ПАМЯТЬ (SQLite):
# ==========================================
conn = sqlite3.connect("gyertlang.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        chat_id INTEGER PRIMARY KEY,
        lang TEXT DEFAULT 'en',
        voice_mode TEXT DEFAULT 'text',
        history TEXT DEFAULT '[]',
        last_user_msg TEXT DEFAULT ''
    )
""")
conn.commit()

def get_user(chat_id: int) -> dict:
    cursor.execute("SELECT lang, voice_mode, history, last_user_msg FROM users WHERE chat_id = ?", (chat_id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO users (chat_id) VALUES (?)", (chat_id,))
        conn.commit()
        return {"lang": "en", "voice_mode": "text", "history": [], "last_user_msg": ""}
    
    lang, voice_mode, history_str, last_user_msg = row
    return {
        "lang": lang,
        "voice_mode": voice_mode,
        "history": json.loads(history_str),
        "last_user_msg": last_user_msg
    }

def save_user(chat_id: int, data: dict):
    cursor.execute("""
        UPDATE users 
        SET lang = ?, voice_mode = ?, history = ?, last_user_msg = ? 
        WHERE chat_id = ?
    """, (data.get("lang"), data.get("voice_mode"), json.dumps(data.get("history")), data.get("last_user_msg"), chat_id))
    conn.commit()

# ==========================================
# 🌍 НАСТРОЙКИ 4 ЯЗЫКОВ:
# ==========================================
LANG_CONFIG = {
    "en": {
        "name": "Английский",
        "tts": "en",
        "prompt": "You are GyertLang, a friendly English companion. Talk to the user naturally in English. Keep responses short (1-3 sentences) and ask a follow-up question."
    },
    "zh": {
        "name": "Китайский",
        "tts": "zh-CN",
        "prompt": "You are GyertLang, a friendly Chinese companion. Chat with the user in Chinese. Keep responses very short (1-2 sentences), simple, and ask an easy question."
    },
    "fr": {
        "name": "Французский",
        "tts": "fr",
        "prompt": "You are GyertLang, a friendly French companion. Talk to the user naturally in French. Keep responses short (1-3 sentences) and ask a follow-up question."
    },
    "es": {
        "name": "Испанский",
        "tts": "es",
        "prompt": "You are GyertLang, a friendly Spanish companion. Talk to the user naturally in Spanish. Keep responses short (1-3 sentences) and ask a follow-up question."
    }
}

class BotStates(StatesGroup):
    choosing_language = State()
    chatting = State()

async def get_llm_response(chat_id: int, user_text: str) -> str:
    user = get_user(chat_id)
    lang_info = LANG_CONFIG.get(user.get("lang"), LANG_CONFIG.get("en"))
    history = user.get("history")

    if not history or next(iter(history)).get("role") != "system":
        history.insert(0, {"role": "system", "content": lang_info.get("prompt")})
    else:
        first_item = next(iter(history))
        first_item["content"] = lang_info.get("prompt")

    history.append({"role": "user", "content": str(user_text)})

    if len(history) > 15:
        sys_msg = history.pop(0)
        history = [sys_msg] + history[-14:]

    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: groq_client.chat.completions.create(
            model=CURRENT_MODEL,
            messages=history,
            temperature=0.7,
        )
    )

    ai_text = next(iter(response.choices)).message.content
    history.append({"role": "assistant", "content": ai_text})
    
    user["history"] = history
    user["last_user_msg"] = str(user_text)
    save_user(chat_id, user)
    
    return ai_text

async def transcribe_audio(file_path: str) -> str:
    loop = asyncio.get_event_loop()
    with open(file_path, "rb") as audio_file:
        transcription = await loop.run_in_executor(
            None,
            lambda: groq_client.audio.transcriptions.create(
                file=audio_file,
                model="whisper-large-v3",
                response_format="text"
            )
        )
    return transcription

async def generate_voice_reply(chat_id: int, text_to_speak: str, output_path: str):
    user = get_user(chat_id)
    tts_lang = LANG_CONFIG.get(user.get("lang"), LANG_CONFIG.get("en")).get("tts")
    
    loop = asyncio.get_event_loop()
    def _save_tts():
        tts = gTTS(text=text_to_speak, lang=tts_lang)
        tts.save(output_path)
    await loop.run_in_executor(None, _save_tts)
def get_combined_keyboard(voice_mode="text"):
    builder = InlineKeyboardBuilder()
    builder.button(text="🇬🇧 English", callback_data="set_lang_en")
    builder.button(text="🇨🇳 Китайский", callback_data="set_lang_zh")
    builder.button(text="🇫🇷 Французский", callback_data="set_lang_fr")
    builder.button(text="🇪🇸 Испанский", callback_data="set_lang_es")
    
    if voice_mode == "voice":
        builder.button(text="🟢 Всегда голосом (ВКЛ)", callback_data="set_mode_text")
    else:
        builder.button(text="✍️ Только текст (ВКЛ)", callback_data="set_mode_voice")
        
    builder.adjust(2, 2, 1)
    return builder.as_markup()

def get_action_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="👁 Перевод", callback_data="translate_msg")
    builder.button(text="📝 Разбор ошибок", callback_data="check_errors")
    builder.adjust(2)
    return builder.as_markup()

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    user = get_user(message.chat.id)
    await state.set_state(BotStates.choosing_language)
    await message.answer(
        "👋 Привет! Я твой AI-репетитор GyertLang.\n\n"
        "1️⃣ Выбери язык для практики (память диалога сохраняется).\n"
        "2️⃣ Выбери формат моих ответов (Голос / Текст).",
        reply_markup=get_combined_keyboard(user.get("voice_mode"))
    )

@dp.callback_query(F.data.startswith("set_lang_"))
async def process_lang_choice(callback_query: types.CallbackQuery, state: FSMContext):
    lang = callback_query.data.split("_").pop()
    chat_id = callback_query.message.chat.id
    user = get_user(chat_id)
    
    if user.get("lang") != lang:
        user["history"] = []
        
    user["lang"] = lang
    save_user(chat_id, user)
    await state.set_state(BotStates.chatting)
    
    lang_name = LANG_CONFIG.get(lang).get("name")
    mode_name = "Голосовой 🎙" if user.get("voice_mode") == "voice" else "Текстовый ✍️"
    
    await callback_query.answer()
    await bot.send_message(
        chat_id=chat_id,
        text=f"🚀 Готово к общению!\n"
             f"Язык: *{lang_name}*\n"
             f"Режим ответов: *{mode_name}*\n\n"
             f"Пиши или наговаривай голосовухи. Под каждым моим ответом теперь есть перевод и разбор твоих ошибок! Меню настроек — /start",
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("set_mode_"))
async def process_mode_choice(callback_query: types.CallbackQuery):
    mode = callback_query.data.split("_").pop()
    chat_id = callback_query.message.chat.id
    user = get_user(chat_id)
    
    user["voice_mode"] = mode
    save_user(chat_id, user)
    
    await callback_query.answer("Режим изменен!")
    await callback_query.message.edit_reply_markup(reply_markup=get_combined_keyboard(mode))

async def respond_to_user(message: types.Message, text_content: str):
    chat_id = message.chat.id
    user = get_user(chat_id)
    
    ai_response = await get_llm_response(chat_id, text_content)
    await message.answer(
        f"🤖 *GyertLang:* {ai_response}",
        parse_mode="Markdown",
        reply_markup=get_action_keyboard()
    )
    
    if user.get("voice_mode") == "voice" or message.voice is not None:
        try:
            await message.bot.send_chat_action(chat_id=chat_id, action="record_voice")
            voice_output = f"reply_{chat_id}.mp3"
            
            await generate_voice_reply(chat_id, ai_response, voice_output)
            
            voice_file = types.FSInputFile(voice_output)
            await message.answer_audio(audio=voice_file, title="GyertLang Voice", performer="AI")
            
            if os.path.exists(voice_output):
                os.remove(voice_output)
        except Exception as audio_error:
            await message.answer(f"⚠️ Ошибка озвучки:\n`{str(audio_error)}`", parse_mode="Markdown")

@dp.message(F.text)
async def handle_text(message: types.Message, state: FSMContext):
    await state.set_state(BotStates.chatting)
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        await respond_to_user(message, message.text)
    except Exception as e:
        await message.answer(f"⚠️ Ошибка ИИ:\n`{str(e)}`", parse_mode="Markdown")

@dp.message(F.voice)
async def handle_voice(message: types.Message, state: FSMContext):
    await state.set_state(BotStates.chatting)
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    file_id = message.voice.file_id
    file_on_disk = f"{file_id}.ogg"
    
    try:
        await bot.download(file=file_id, destination=file_on_disk)
        user_text = await transcribe_audio(file_on_disk)
        await respond_to_user(message, user_text)
    except Exception as e:
        await message.answer(f"⚠️ Ошибка обработки звука:\n`{str(e)}`", parse_mode="Markdown")
    finally:
        if os.path.exists(file_on_disk):
            os.remove(file_on_disk)

@dp.callback_query(F.data == "translate_msg")
async def process_translation(callback_query: types.CallbackQuery):
    original_text = callback_query.message.text or callback_query.message.caption
    if "GyertLang:" in original_text:
        original_text = original_text.split("GyertLang:").pop().strip()

    await callback_query.answer("Перевожу...")
    try:
        translation_prompt = f"Translate into Russian perfectly and naturally. Return ONLY the translation:\n\n{original_text}"
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: groq_client.chat.completions.create(
                model=CURRENT_MODEL,
                messages=[{"role": "user", "content": translation_prompt}],
                temperature=0.3,
            )
        )
        russian_translation = next(iter(response.choices)).message.content
        await bot.send_message(
            chat_id=callback_query.message.chat.id,
            text=f"🔰 *Перевод:* {russian_translation}",
            parse_mode="Markdown",
            reply_to_message_id=callback_query.message.message_id
        )
    except Exception as e:
        await bot.send_message(
            chat_id=callback_query.message.chat.id,
            text=f"⚠️ Ошибка перевода:\n`{str(e)}`",
            parse_mode="Markdown"
        )

@dp.callback_query(F.data == "check_errors")
async def process_error_check(callback_query: types.CallbackQuery):
    chat_id = callback_query.message.chat.id
    user = get_user(chat_id)
    last_msg = user.get("last_user_msg", "")
    lang_name = LANG_CONFIG.get(user.get("lang"), LANG_CONFIG.get("en")).get("name")

    if not last_msg:
        await callback_query.answer("Нет сообщения для разбора!", show_alert=True)
        return

    await callback_query.answer("Проверяю грамматику...")
    try:
        check_prompt = (
            f"Ты дружелюбный репетитор ({lang_name} язык). Ученик написал или сказал: «{last_msg}».\n"
            f"1. Если фраза звучит абсолютно правильно и естественно для носителя, похвали его коротко на русском.\n"
            f"2. Если есть грамматические, лексические ошибки или фраза звучит неестественно, напиши правильный вариант и коротко объясни на русском, в чём ошибка. Не используй сложные лингвистические термины."
        )
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: groq_client.chat.completions.create(
                model=CURRENT_MODEL,
                messages=[{"role": "user", "content": check_prompt}],
                temperature=0.4,
            )
        )
        feedback = next(iter(response.choices)).message.content
        await bot.send_message(
            chat_id=chat_id,
            text=f"📝 *Разбор фразы:* «{last_msg}»\n\n{feedback}",
            parse_mode="Markdown"
        )
    except Exception as e:
        await bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Ошибка при разборе:\n`{str(e)}`",
            parse_mode="Markdown"
        )

async def handle_web(request):
    return web.Response(text="GyertLang Is Running Live!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_web)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(start_web_server())
    print("Бот GyertLang 2.0 с памятью и 4 языками запущен!")
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
