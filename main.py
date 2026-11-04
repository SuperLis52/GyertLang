import os
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from groq import Groq
import edge_tts

# ==========================================
# 🔑 ВСТАВЬТЕ ВАШИ ТОКЕНЫ В КАВЫЧКИ НИЖЕ:
# ==========================================
BOT_TOKEN = "8717348381:AAHZT3SYY4ZbERM5HpObMSzGaaQRlOWe2cg"
GROQ_API_KEY = "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"

# Инициализация клиентов
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
groq_client = Groq(api_key=GROQ_API_KEY)

# Хранилище истории сообщений в памяти (для бесплатного старта)
# В продакшене лучше использовать Базу Данных
user_history = {}

# Системный промпт, задающий роль ИИ
SYSTEM_PROMPT = (
    "You are GyertLang, a friendly and supportive language learning companion. "
    "Talk to the user like a real human being. Keep the conversation flowing naturally, "
    "ask follow-up questions, and keep your responses relatively short (2-4 sentences max) "
    "so it feels like a real chat. Use a warm, encouraging tone."
)

# Функция для генерации ответа от ИИ (Groq Llama 3)
async def get_llm_response(chat_id: int, user_text: str) -> str:
    if chat_id not in user_history:
        user_history[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    user_history[chat_id].append({"role": "user", "content": user_text})
    
    # Ограничиваем историю последних 10 сообщений, чтобы не перегружать контекст
    if len(user_history[chat_id]) > 11:
        user_history[chat_id] = [user_history[chat_id][0]] + user_history[chat_id][-10:]

    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None, 
        lambda: groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=user_history[chat_id],
            temperature=0.7,
        )
    )
    
    ai_text = response.choices[0].message.content
    user_history[chat_id].append({"role": "assistant", "content": ai_text})
    return ai_text

# Функция для распознавания речи (Groq Whisper)
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

# Функция для озвучки текста (Edge-TTS - голос Microsoft)
async def text_to_speech(text: str, output_path: str):
    # Используем качественный английский голос "en-US-EmmaMultilingualNeural"
    # Вы можете заменить его на любой другой из библиотеки edge-tts
    communicate = edge_tts.Communicate(text, "en-US-EmmaMultilingualNeural")
    await communicate.save(output_path)

# Кнопка для перевода под сообщением
def get_translate_keyboard(text_to_translate: str):
    builder = InlineKeyboardBuilder()
    # Текст сообщения обрезаем для callback_data, так как там лимит 64 байта
    # Но для надежности мы будем переводить последнее сообщение из истории или само сообщение
    builder.button(text="👁 Перевести на русский", callback_data="translate_msg")
    return builder.as_markup()

# Обработчик команды /start
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_history[message.chat.id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    await message.answer(
        "Привет! Я бот GyertLang. Давай пообщаемся! "
        "Ты можешь писать мне текстовые сообщения или отправлять голосовые. "
        "Я буду отвечать тебе как реальный человек, а под моими сообщениями будет кнопка перевода."
    )

# Обработчик текстовых сообщений
@dp.message(F.text)
async def handle_text(message: types.Message):
    # Показываем статус "печатает"
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    ai_response = await get_llm_response(message.chat.id, message.text)
    
    await message.answer(ai_response, reply_markup=get_translate_keyboard(ai_response))

# Обработчик голосовых сообщений
@dp.message(F.voice)
async def handle_voice(message: types.Message):
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    # Скачиваем голосовое сообщение
    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    file_on_disk = f"{file_id}.ogg"
    await bot.download_file(file.file_path, destination=file_on_disk)
    
    try:
        # 1. Распознаем речь (STT)
        user_text = await transcribe_audio(file_on_disk)
        
        # 2. Генерируем текстовый ответ от ИИ
        ai_response = await get_llm_response(message.chat.id, user_text)
        
        # 3. Генерируем аудио из текста ответа (TTS)
        voice_output = f"reply_{file_id}.mp3"
        await text_to_speech(ai_response, voice_output)
        
        # Отправляем текстовый ответ с кнопкой перевода
        await message.answer(f"🤖 *GyertLang:* {ai_response}", parse_mode="Markdown", reply_markup=get_translate_keyboard(ai_response))
        
        # Отправляем голосовой ответ
        await message.bot.send_chat_action(chat_id=message.chat.id, action="record_voice")
        voice_file = types.FSInputFile(voice_output)
        await message.answer_voice(voice_file)
        
        # Удаляем временные файлы
        if os.path.exists(voice_output):
            os.remove(voice_output)
            
    except Exception as e:
        await message.answer(f"Произошла ошибка при обработке голоса. Попробуй еще раз.")
        print(f"Error: {e}")
        
    finally:
        if os.path.exists(file_on_disk):
            os.remove(file_on_disk)

# Обработчик нажатия на кнопку "Перевести"
@dp.callback_query(F.data == "translate_msg")
async def process_translation(callback_query: types.CallbackQuery):
    # Получаем исходный текст сообщения, на котором нажали кнопку
    original_text = callback_query.message.text or callback_query.message.caption
    if "GyertLang:" in original_text:
        original_text = original_text.split("GyertLang:")[-1].strip()

    await callback_query.answer("Перевожу...")

    # Быстрый запрос к ИИ исключительно для перевода этой фразы
    loop = asyncio.get_event_loop()
    translation_prompt = f"Translate the following text into Russian perfectly and naturally. Return ONLY the translation, nothing else:\n\n{original_text}"
    
    response = await loop.run_in_executor(
        None, 
        lambda: groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[{"role": "user", "content": translation_prompt}],
            temperature=0.3,
        )
    )
    russian_translation = response.choices[0].message.content

    # Отправляем всплывающее окно с переводом, либо сообщение
    await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text=f"🔰 *Перевод:* {russian_translation}",
        parse_mode="Markdown",
        reply_to_message_id=callback_query.message.message_id
    )

# Главная функция запуска
async def main():
    print("Бот GyertLang успешно запущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
