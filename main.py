import os
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from groq import Groq
import edge_tts
from aiohttp import web

# ==========================================
# 🔑 ВАШИ ТОКЕНЫ:
# ==========================================
BOT_TOKEN = "8717348381:AAHZT3SYY4ZbERM5HpObMSzGaaQRlOWe2cg"
GROQ_API_KEY = "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"

CURRENT_MODEL = "qwen/qwen3.8-27b"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
groq_client = Groq(api_key=GROQ_API_KEY)

user_data = {}

class BotStates(StatesGroup):
    choosing_language = State()
    chatting = State()

def get_lang_settings(lang_code: str):
    if lang_code == "zh":
        return {
            "voice": "zh-CN-XiaoxiaoNeural",
            "prompt": "You are GyertLang, a friendly Chinese language teacher. Chat with the user in Chinese. Keep responses very short (1-3 sentences), simple, and support the user. Ask easy questions to keep the chat going."
        }
    else:
        return {
            "voice": "en-US-EmmaMultilingualNeural",
            "prompt": "You are GyertLang, a friendly English language companion. Talk to the user like a real human being in English. Keep your responses short (2-3 sentences max) and ask follow-up questions. Use a warm tone."
        }

async def get_llm_response(chat_id: int, user_text: str) -> str:
    # 🛡 Защита от очистки памяти: создаем структуру, если она исчезла
    if chat_id not in user_data or "lang" not in user_data[chat_id]:
        user_data[chat_id] = {"lang": "en", "history": []}
    
    if "history" not in user_data[chat_id] or not isinstance(user_data[chat_id]["history"], list):
        user_data[chat_id]["history"] = []
        
    settings = get_lang_settings(user_data[chat_id]["lang"])
    
    # Гарантируем, что первым сообщением всегда идет объект с правильной ролью system
    if not user_data[chat_id]["history"] or user_data[chat_id]["history"][0].get("role") != "system":
        user_data[chat_id]["history"].insert(0, {"role": "system", "content": settings["prompt"]})
    
    # Добавляем сообщение пользователя строго в виде объекта
    user_data[chat_id]["history"].append({"role": "user", "content": str(user_text)})
    
    # Ограничиваем глубину контекста диалога
    if len(user_data[chat_id]["history"]) > 11:
        # Сохраняем системный промпт на 0-м месте, берем последние 10 сообщений
        user_data[chat_id]["history"] = [user_data[chat_id]["history"][0]] + user_data[chat_id]["history"][-10:]

    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None, 
        lambda: groq_client.chat.completions.create(
            model=CURRENT_MODEL,
            messages=user_data[chat_id]["history"],
            temperature=0.7,
        )
    )
    
    ai_text = response.choices[0].message.content
    user_data[chat_id]["history"].append({"role": "assistant", "content": ai_text})
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

def get_language_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🇬🇧 English (Английский)", callback_data="set_lang_en")
    builder.button(text="🇨🇳 🪐 (Китайский)", callback_data="set_lang_zh")
    builder.adjust(1)
    return builder.as_markup()

def get_translate_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="👁 Перевести на русский", callback_data="translate_msg")
    return builder.as_markup()

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.set_state(BotStates.choosing_language)
    await message.answer(
        "👋 Привет! Я твой AI-собеседник GyertLang.\n"
        "Выбери язык, который ты хочешь изучать и практиковать:",
        reply_markup=get_language_keyboard()
    )

@dp.callback_query(F.data.startswith("set_lang_"))
async def process_lang_choice(callback_query: types.CallbackQuery, state: FSMContext):
    lang = callback_query.data.split("_")[-1]
    chat_id = callback_query.message.chat.id
    
    user_data[chat_id] = {"lang": lang, "history": []}
    await state.set_state(BotStates.chatting)
    
    lang_name = "Английский" if lang == "en" else "Китайский"
    await callback_query.answer()
    await bot.send_message(
        chat_id=chat_id,
        text=f"🚀 Отлично! Выбран язык: *{lang_name}*.\n\n"
             f"Отправь мне текстовое или голосовое сообщение на этом языке, и мы начнем общение! "
             f"Если захочешь сменить язык, просто набери /start",
        parse_mode="Markdown"
    )

@dp.message(BotStates.chatting, F.text)
async def handle_text(message: types.Message):
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        ai_response = await get_llm_response(message.chat.id, message.text)
        await message.answer(ai_response, reply_markup=get_translate_keyboard())
    except Exception as e:
        await message.answer(f"⚠️ Ошибка ИИ:\n`{str(e)}`", parse_mode="Markdown")

@dp.message(BotStates.chatting, F.voice)
async def handle_voice(message: types.Message):
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    file_id = message.voice.file_id
    file_on_disk = f"{file_id}.ogg"
    
    try:
        await bot.download(file=file_id, destination=file_on_disk)
        
        user_text = await transcribe_audio(file_on_disk)
        ai_response = await get_llm_response(message.chat.id, user_text)
        
        await message.answer(f"🤖 *GyertLang:* {ai_response}", parse_mode="Markdown", reply_markup=get_translate_keyboard())
        
        try:
            await message.bot.send_chat_action(chat_id=message.chat.id, action="record_voice")
            voice_output = f"reply_{file_id}.mp3"
            
            # На всякий случай проверяем существование данных перед озвучкой
            lang_code = user_data.get(message.chat.id, {}).get("lang", "en")
            settings = get_lang_settings(lang_code)
            
            communicate = edge_tts.Communicate(ai_response, settings["voice"])
            await communicate.save(voice_output)
            
            voice_file = types.FSInputFile(voice_output)
            await message.answer_voice(voice_file)
            
            if os.path.exists(voice_output):
                os.remove(voice_output)
        except Exception as audio_error:
            print(f"Audio error: {audio_error}")
            
    except Exception as e:
        await message.answer(f"⚠️ Ошибка обработки звука:\n`{str(e)}`", parse_mode="Markdown")
    finally:
        if os.path.exists(file_on_disk):
            os.remove(file_on_disk)

@dp.callback_query(F.data == "translate_msg")
async def process_translation(callback_query: types.CallbackQuery):
    original_text = callback_query.message.text or callback_query.message.caption
    if "GyertLang:" in original_text:
        original_text = original_text.split("GyertLang:")[-1].strip()

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
        russian_translation = response.choices[0].message.content
        await bot.send_message(
            chat_id=callback_query.message.chat.id,
            text=f"🔰 *Перевод:* {russian_translation}",
            parse_mode="Markdown",
            reply_to_message_id=callback_query.message.message_id
        )
    except Exception as e:
        print(f"Translation error: {e}")

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
    print("Бот GyertLang перезапущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
