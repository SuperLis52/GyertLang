import os
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
# 🔑 КЛЮЧИ БЕРУТСЯ ИЗ НАСТРОЕК RENDER (ENVIRONMENT):
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

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
            "prompt": "You are GyertLang, a friendly Chinese language teacher. Chat with the user in Chinese. Keep responses very short (1-2 sentences max), simple, and support the user. Ask easy questions to keep the chat going."
        }
    else:
        return {
            "voice": "en-US-EmmaMultilingualNeural",
            "prompt": "You are GyertLang, a friendly English language companion. Talk to the user like a real human being in English. Keep your responses exceptionally short (1-2 sentences max). Ask a quick question."
        }

async def get_llm_response(chat_id: int, user_text: str) -> str:
    if chat_id not in user_data or "lang" not in user_data[chat_id]:
        user_data[chat_id] = {"lang": "en", "voice_mode": "text", "history": []}
    
    if "history" not in user_data[chat_id] or not isinstance(user_data[chat_id]["history"], list):
        user_data[chat_id]["history"] = []
        
    settings = get_lang_settings(user_data[chat_id]["lang"])
    
    if not user_data[chat_id]["history"] or user_data[chat_id]["history"][0].get("role") != "system":
        user_data[chat_id]["history"].insert(0, {"role": "system", "content": settings["prompt"]})
    
    user_data[chat_id]["history"].append({"role": "user", "content": str(user_text)})
    
    if len(user_data[chat_id]["history"]) > 11:
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

async def generate_voice_reply(chat_id: int, text_to_speak: str, output_path: str):
    lang = user_data.get(chat_id, {}).get("lang", "en")
    tts_lang = "zh-CN" if lang == "zh" else "en"

    loop = asyncio.get_event_loop()
    def _save_tts():
        tts = gTTS(text=text_to_speak, lang=tts_lang)
        tts.save(output_path)

    await loop.run_in_executor(None, _save_tts)
def get_combined_keyboard(voice_mode="text"):
    builder = InlineKeyboardBuilder()
    builder.button(text="🇬🇧 English (Английский)", callback_data="set_lang_en")
    builder.button(text="🇨🇳 🪐 (Китайский)", callback_data="set_lang_zh")
    
    if voice_mode == "voice":
        builder.button(text="🟢 Всегда голосом (ВКЛ)", callback_data="set_mode_text")
    else:
        builder.button(text="✍️ Только текст (ВКЛ)", callback_data="set_mode_voice")
        
    builder.adjust(2, 1)
    return builder.as_markup()

def get_translate_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="👁 Перевести на русский", callback_data="translate_msg")
    return builder.as_markup()

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    chat_id = message.chat.id
    if chat_id not in user_data:
        user_data[chat_id] = {"lang": "en", "voice_mode": "text", "history": []}
        
    await state.set_state(BotStates.choosing_language)
    await message.answer(
        "👋 Привет! Я твой AI-собеседник GyertLang.\n\n"
        "1️⃣ Нажми на флаг, чтобы выбрать изучаемый язык.\n"
        "2️⃣ Нажми на нижнюю кнопку, чтобы переключить режим ответов (Голос / Текст).",
        reply_markup=get_combined_keyboard(user_data[chat_id]["voice_mode"])
    )

@dp.callback_query(F.data.startswith("set_lang_"))
async def process_lang_choice(callback_query: types.CallbackQuery, state: FSMContext):
    lang = callback_query.data.split("_")[-1]
    chat_id = callback_query.message.chat.id
    
    if chat_id not in user_data:
        user_data[chat_id] = {"lang": "en", "voice_mode": "text", "history": []}
        
    user_data[chat_id]["lang"] = lang
    user_data[chat_id]["history"] = []
    await state.set_state(BotStates.chatting)
    
    lang_name = "Английский" if lang == "en" else "Китайский"
    mode_name = "Голосовой 🎙" if user_data[chat_id]["voice_mode"] == "voice" else "Текстовый ✍️"
    
    await callback_query.answer()
    await bot.send_message(
        chat_id=chat_id,
        text=f"🚀 Настройки сохранены!\n"
             f"Язык: *{lang_name}*\n"
             f"Режим ответов бота: *{mode_name}*\n\n"
             f"Напиши или наговори мне что-нибудь, и начнем! Для настроек введи /start",
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("set_mode_"))
async def process_mode_choice(callback_query: types.CallbackQuery):
    mode = callback_query.data.split("_")[-1]
    chat_id = callback_query.message.chat.id
    
    if chat_id not in user_data:
        user_data[chat_id] = {"lang": "en", "voice_mode": "text", "history": []}
        
    user_data[chat_id]["voice_mode"] = mode
    await callback_query.answer("Режим изменен!")
    await callback_query.message.edit_reply_markup(reply_markup=get_combined_keyboard(mode))

async def respond_to_user(message: types.Message, text_content: str):
    chat_id = message.chat.id
    mode = user_data.get(chat_id, {}).get("voice_mode", "text")
    
    ai_response = await get_llm_response(chat_id, text_content)
    await message.answer(f"🤖 *GyertLang:* {ai_response}", parse_mode="Markdown", reply_markup=get_translate_keyboard())
    
    if mode == "voice" or message.voice is not None:
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

@dp.message(BotStates.chatting, F.text)
async def handle_text(message: types.Message):
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        await respond_to_user(message, message.text)
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
        russian_translation = response.choices.message.content
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
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
