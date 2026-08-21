import os
import sys
import asyncio
import re
from datetime import datetime
from telethon import TelegramClient
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    DocumentAttributeFilename,
    DocumentAttributeAudio,
    DocumentAttributeVideo
)
from telethon.errors import FloodWaitError, UserDeactivatedBanError
from tqdm import tqdm

# Исправление кодировки для Windows консоли
if sys.platform == 'win32':
    import codecs
    try:
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'replace')
        sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'replace')
        os.system('chcp 65001 >nul')
    except Exception:
        pass

# Конфигурация API
api_id = "12345678"
api_hash = 'your_api_hash'
phone = '+1234567890'

# Создание клиента
client = TelegramClient('session_name', api_id, api_hash)


def sanitize_filename(name: str) -> str:
    """Удаляет недопустимые символы из имени файла/папки для Windows/Linux."""
    if not name:
        return "unnamed"
    cleaned = re.sub(r'[\\/*?:"<>|]', '_', name)
    return cleaned.strip('. ')


def format_size(size_bytes: int) -> str:
    """Форматирует размер в байтах в человекочитаемый вид (KB, MB, GB)."""
    if not size_bytes or size_bytes < 0:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def normalize_channel_input(channel_input: str) -> str:
    """Преобразует различные форматы ссылок Telegram в нормальный вид."""
    if not channel_input:
        return ""
    channel_input = channel_input.strip()
    if channel_input.startswith('https://') or channel_input.startswith('http://'):
        channel_input = channel_input.split('://', 1)[1]
    if channel_input.startswith('t.me/'):
        channel_input = channel_input.split('t.me/', 1)[1]
    if channel_input.startswith('+') or channel_input.startswith('joinchat/'):
        return channel_input
    if channel_input.startswith('@'):
        return channel_input
    return '@' + channel_input


def classify_message(msg) -> dict:
    """
    Определяет категорию медиа и метаданные сообщения:
    Возвращает словарь:
    {
        'type': 'photo' | 'video' | 'round_video' | 'audio' | 'voice' | 'document' | 'text' | 'other',
        'category': 'photos' | 'videos' | 'audio' | 'voice' | 'documents' | 'text',
        'filename': 'имя_файла',
        'size': размер_в_байтах
    }
    """
    if not msg:
        return {'type': 'empty', 'category': 'empty', 'filename': '', 'size': 0}

    if not msg.media:
        return {'type': 'text', 'category': 'text', 'filename': '', 'size': 0}

    # 1. Фотография
    if isinstance(msg.media, MessageMediaPhoto) or getattr(msg, 'photo', None):
        size = getattr(msg.file, 'size', 0) or 0
        return {
            'type': 'photo',
            'category': 'photos',
            'filename': f"photo_{msg.id}.jpg",
            'size': size
        }

    # 2. Документы (видео, аудио, голосовые, файлы)
    if isinstance(msg.media, MessageMediaDocument) or getattr(msg, 'document', None):
        doc = msg.document
        size = getattr(doc, 'size', 0) or getattr(msg.file, 'size', 0) or 0
        mime = (getattr(doc, 'mime_type', '') or '').lower()

        is_voice = False
        is_audio = False
        is_video = False
        is_round = False
        original_name = None

        for attr in getattr(doc, 'attributes', []):
            if isinstance(attr, DocumentAttributeAudio):
                if getattr(attr, 'voice', False):
                    is_voice = True
                else:
                    is_audio = True
            elif isinstance(attr, DocumentAttributeVideo):
                if getattr(attr, 'round_message', False):
                    is_round = True
                else:
                    is_video = True
            elif isinstance(attr, DocumentAttributeFilename):
                original_name = attr.file_name

        if is_voice or mime.startswith('audio/ogg'):
            filename = original_name or f"voice_{msg.id}.ogg"
            return {'type': 'voice', 'category': 'voice', 'filename': filename, 'size': size}

        if is_audio or mime.startswith('audio/'):
            filename = original_name or f"audio_{msg.id}.mp3"
            return {'type': 'audio', 'category': 'audio', 'filename': filename, 'size': size}

        if is_round:
            filename = original_name or f"round_video_{msg.id}.mp4"
            return {'type': 'round_video', 'category': 'videos', 'filename': filename, 'size': size}

        if is_video or mime.startswith('video/'):
            filename = original_name or f"video_{msg.id}.mp4"
            return {'type': 'video', 'category': 'videos', 'filename': filename, 'size': size}

        # Обычный документ / архив / файл
        filename = original_name or f"doc_{msg.id}.bin"
        return {'type': 'document', 'category': 'documents', 'filename': filename, 'size': size}

    return {'type': 'other', 'category': 'documents', 'filename': f"media_{msg.id}", 'size': 0}


async def check_account_status() -> bool:
    """Проверяет статус аккаунта и выполняет авторизацию при необходимости."""
    try:
        if not client.is_connected():
            await client.connect()
        if not await client.is_user_authorized():
            await client.start(phone)
        return True
    except UserDeactivatedBanError:
        print("\n[!] ОШИБКА: Аккаунт заблокирован (Banned).")
        return False
    except FloodWaitError as e:
        print(f"\n[!] Лимит запросов. Подождите {e.seconds} секунд.")
        return False
    except Exception as e:
        print(f"\n[!] Ошибка авторизации: {e}")
        return False


async def get_channel_entity(channel_input: str):
    """Получает объект канала по username или ссылке."""
    norm_input = normalize_channel_input(channel_input)
    if not norm_input:
        print("\n[!] Введено пустое имя канала.")
        return None

    try:
        # Проверка приватных ссылок-приглашений (+ или joinchat/)
        if norm_input.startswith('+') or norm_input.startswith('joinchat/'):
            hash_val = norm_input.replace('joinchat/', '').replace('+', '')
            try:
                from telethon.tl.functions.messages import CheckChatInviteRequest
                invite_res = await client(CheckChatInviteRequest(hash_val))
                return invite_res.chat
            except Exception:
                pass

        entity = await client.get_entity(norm_input)
        return entity
    except ValueError:
        print(f"\n[!] Канал '{channel_input}' не найден. Убедитесь, что username указан верно.")
        return None
    except Exception as e:
        print(f"\n[!] Ошибка при поиске канала '{channel_input}': {e}")
        return None


async def download_single_media(msg, target_path: str, semaphore: asyncio.Semaphore, progress_bar: tqdm, stats: dict):
    """Скачивает один медиафайл с проверкой на существование и семафором."""
    async with semaphore:
        try:
            # Пропуск уже ранее скачанных файлов
            expected_size = stats.get('expected_size', 0)
            if os.path.exists(target_path):
                current_size = os.path.getsize(target_path)
                if current_size > 0 and (expected_size == 0 or current_size == expected_size):
                    stats['skipped'] += 1
                    progress_bar.set_postfix_str(f"Пропущен (уже есть): {os.path.basename(target_path)[:20]}")
                    progress_bar.update(1)
                    return

            # Загрузка
            await client.download_media(msg, file=target_path)
            stats['downloaded'] += 1
            progress_bar.set_postfix_str(f"Скачан: {os.path.basename(target_path)[:20]}")
            progress_bar.update(1)
        except FloodWaitError as e:
            stats['failed'] += 1
            progress_bar.write(f"\n[!] FloodWait: пауза {e.seconds} сек...")
            await asyncio.sleep(e.seconds + 1)
            try:
                await client.download_media(msg, file=target_path)
                stats['downloaded'] += 1
                stats['failed'] -= 1
            except Exception as retry_err:
                progress_bar.write(f"[!] Ошибка при повторе ID {msg.id}: {retry_err}")
            progress_bar.update(1)
        except Exception as e:
            stats['failed'] += 1
            progress_bar.write(f"[!] Ошибка загрузки ID {msg.id}: {e}")
            progress_bar.update(1)


async def download_media_from_channel(channel_input: str, selected_categories=None, limit_messages=None, base_download_dir='downloads'):
    """
    Скачивает выбранные типы медиа (фото, видео, музыка, голосовые, документы).
    Сортирует их по отдельным папкам и отображает прогресс-бар tqdm.
    """
    if not await check_account_status():
        return

    entity = await get_channel_entity(channel_input)
    if not entity:
        return

    channel_title = getattr(entity, 'title', None) or getattr(entity, 'username', 'channel')
    safe_channel_name = sanitize_filename(channel_title)
    channel_dir = os.path.join(base_download_dir, safe_channel_name)

    print(f"\n[*] Сканирование сообщений в канале: {channel_title} ...")

    # Сбор сообщений с подходящими типами медиа
    media_tasks_info = []
    total_scanned = 0

    async for msg in client.iter_messages(entity, limit=limit_messages):
        total_scanned += 1
        info = classify_message(msg)
        category = info['category']

        if category == 'text' or category == 'empty':
            continue

        # Фильтрация по выбранным категориям
        if selected_categories and category not in selected_categories:
            continue

        # Формирование имени и пути
        category_dir = os.path.join(channel_dir, category)
        os.makedirs(category_dir, exist_ok=True)

        clean_file_name = sanitize_filename(info['filename'])
        target_path = os.path.join(category_dir, f"{msg.id}_{clean_file_name}")

        media_tasks_info.append({
            'msg': msg,
            'target_path': target_path,
            'size': info['size'],
            'category': category
        })

    if not media_tasks_info:
        print(f"[!] Не найдено медиафайлов для скачивания (просканировано сообщений: {total_scanned}).")
        return

    print(f"[+] Найдено {len(media_tasks_info)} медиафайлов для загрузки.")
    print(f"[+] Папка сохранения: {os.path.abspath(channel_dir)}")

    semaphore = asyncio.Semaphore(5)
    stats = {'downloaded': 0, 'skipped': 0, 'failed': 0}

    with tqdm(total=len(media_tasks_info), desc="Загрузка файлов", unit="файл") as pbar:
        tasks = []
        for item in media_tasks_info:
            task = download_single_media(
                msg=item['msg'],
                target_path=item['target_path'],
                semaphore=semaphore,
                progress_bar=pbar,
                stats={**stats, 'expected_size': item['size']}
            )
            tasks.append(task)

        # Выполняем параллельную загрузку
        await asyncio.gather(*tasks)

    print("\n" + "=" * 50)
    print(f"📊 ИТОГИ ЗАГРУЗКИ:")
    print(f"   - Скачано новых файлов: {stats['downloaded']}")
    print(f"   - Пропущено (уже скачаны): {stats['skipped']}")
    print(f"   - Ошибок: {stats['failed']}")
    print(f"   - Всего обработано: {len(media_tasks_info)}")
    print(f"   - Файлы сохранены в: {os.path.abspath(channel_dir)}")
    print("=" * 50)


async def sort_and_display_messages(channel_input: str, sort_by='date_desc', limit=50, search_query=None):
    """
    Считывает, сортирует и отображает сообщения канала в консоли.
    Поддерживает сортировку по дате (новые/старые), по типу, по размеру, а также поиск по тексту.
    """
    if not await check_account_status():
        return

    entity = await get_channel_entity(channel_input)
    if not entity:
        return

    channel_title = getattr(entity, 'title', None) or getattr(entity, 'username', 'channel')
    print(f"\n[*] Получение сообщений из '{channel_title}' (лимит: {limit or 'все'})...")

    messages_data = []
    async for msg in client.iter_messages(entity, limit=limit):
        info = classify_message(msg)
        text_preview = (msg.message or "").strip()
        
        # Если включен поиск по тексту
        if search_query:
            if search_query.lower() not in text_preview.lower():
                continue

        messages_data.append({
            'id': msg.id,
            'date': msg.date,
            'type': info['type'],
            'category': info['category'],
            'filename': info['filename'],
            'size': info['size'],
            'text': text_preview
        })

    if not messages_data:
        print("[!] Сообщений не найдено (или ничего не подошло под условия поиска).")
        return

    # Применение сортировки
    if sort_by == 'date_asc':
        messages_data.sort(key=lambda m: m['date'] or datetime.min)
    elif sort_by == 'date_desc':
        messages_data.sort(key=lambda m: m['date'] or datetime.min, reverse=True)
    elif sort_by == 'type':
        messages_data.sort(key=lambda m: (m['category'], m['date'] or datetime.min))
    elif sort_by == 'size':
        messages_data.sort(key=lambda m: m['size'], reverse=True)

    print("\n" + "=" * 90)
    print(f"📋 СПИСОК СООБЩЕНИЙ КАНАЛА: {channel_title} (Найдено: {len(messages_data)})")
    print("=" * 90)

    for item in messages_data:
        date_str = item['date'].strftime('%Y-%m-%d %H:%M:%S') if item['date'] else 'Нет даты'
        type_str = f"[{item['type'].upper()}]"
        
        media_info = ""
        if item['size'] > 0 or item['filename']:
            media_info = f" | Файл: {item['filename']} ({format_size(item['size'])})"
        
        print(f"ID: {item['id']:<7} | {date_str} | {type_str:<14}{media_info}")
        if item['text']:
            # Превью текста до 120 символов, убираем переносы строк для компактности
            clean_text = ' '.join(item['text'].split())
            if len(clean_text) > 120:
                clean_text = clean_text[:117] + "..."
            print(f"   Текст: {clean_text}")
        print("-" * 90)

    print(f"\nВсего отображено сообщений: {len(messages_data)}")


async def show_channel_info(channel_input: str):
    """Показывает подробную информацию о канале/чате."""
    if not await check_account_status():
        return

    entity = await get_channel_entity(channel_input)
    if not entity:
        return

    print("\n" + "=" * 50)
    print("📊 ИНФОРМАЦИЯ О КАНАЛЕ:")
    print(f"   Название:     {getattr(entity, 'title', 'Без названия')}")
    print(f"   ID:           {entity.id}")
    print(f"   Username:     @{entity.username}" if getattr(entity, 'username', None) else "   Username:     (нет публичного username)")
    print(f"   Приватный:    {'Нет (Публичный)' if getattr(entity, 'username', None) else 'Да'}")
    if hasattr(entity, 'participants_count') and entity.participants_count:
        print(f"   Участников:   {entity.participants_count}")
    print("=" * 50)


async def main_menu():
    """Главное интерактивное меню приложения (работает в непрерывном цикле)."""
    if not await check_account_status():
        print("\n[!] Не удалось авторизоваться в Telegram. Проверьте настройки.")
        return

    current_channel = None

    print("\n" + "=" * 60)
    print("🚀 TELEGRAM MEDIA & MESSAGE MANAGER")
    print("=" * 60)

    while True:
        # Если канал еще не задан, запрашиваем
        if not current_channel:
            print("\nВведите канал для работы (например: @durov, t.me/durov или ссылку):")
            c_input = input("Канал > ").strip()
            if not c_input:
                continue
            entity = await get_channel_entity(c_input)
            if entity:
                current_channel = c_input
                title = getattr(entity, 'title', c_input)
                print(f"[✓] Выбран канал: {title}")
            else:
                continue

        print("\n" + "-" * 50)
        print(f"📌 Текущий канал: {current_channel}")
        print("-" * 50)
        print("1. 📥 Скачать медиа (фото, видео, аудио, документы и др.)")
        print("2. 📋 Просмотреть и отсортировать сообщения")
        print("3. 🔍 Поиск сообщений по ключевому слову")
        print("4. ℹ️ Информация о канале")
        print("5. 🔄 Сменить рабочий канал")
        print("0. 🚪 Выход из программы")
        print("-" * 50)

        choice = input("Выберите действие [0-5]: ").strip()

        if choice == '1':
            print("\n--- НАСТРОЙКА ЗАГРУЗКИ МЕДИА ---")
            print("Какие типы файлов скачивать?")
            print("  1 - 📦 Все типы медиа (фото, видео, музыка, голосовые, документы)")
            print("  2 - 🖼️  Только Фотографии (photos)")
            print("  3 - 🎥 Только Видео (videos)")
            print("  4 - 🎵 Только Музыка / Аудио (audio)")
            print("  5 - 🎙️  Только Голосовые сообщения (voice)")
            print("  6 - 📄 Только Документы и файлы (documents)")
            type_choice = input("Выберите тип [1-6, по умолчанию 1]: ").strip() or '1'

            cat_map = {
                '1': None,  # Все
                '2': ['photos'],
                '3': ['videos'],
                '4': ['audio'],
                '5': ['voice'],
                '6': ['documents']
            }
            selected_cats = cat_map.get(type_choice, None)

            limit_input = input("Лимит сообщений для проверки (Enter - без лимита / все посты): ").strip()
            limit = int(limit_input) if limit_input.isdigit() else None

            await download_media_from_channel(
                channel_input=current_channel,
                selected_categories=selected_cats,
                limit_messages=limit
            )

        elif choice == '2':
            print("\n--- ПРОСМОТР И СОРТИРОВКА СООБЩЕНИЙ ---")
            print("Выберите вариант сортировки:")
            print("  1 - ⏳ По дате (сначала новые)")
            print("  2 - ⌛ По дате (сначала старые)")
            print("  3 - 📁 По типу медиа (фото -> видео -> музыка -> файлы -> текст)")
            print("  4 - 💾 По размеру файла (сначала самые большие)")
            sort_choice = input("Сортировка [1-4, по умолчанию 1]: ").strip() or '1'

            sort_map = {
                '1': 'date_desc',
                '2': 'date_asc',
                '3': 'type',
                '4': 'size'
            }
            sort_by = sort_map.get(sort_choice, 'date_desc')

            limit_input = input("Количество сообщений для просмотра (по умолчанию 50, 'all' для всех): ").strip()
            if limit_input.lower() == 'all':
                limit = None
            elif limit_input.isdigit():
                limit = int(limit_input)
            else:
                limit = 50

            await sort_and_display_messages(
                channel_input=current_channel,
                sort_by=sort_by,
                limit=limit
            )

        elif choice == '3':
            search_query = input("\nВведите слово или фразу для поиска: ").strip()
            if search_query:
                limit_input = input("Лимит проверяемых сообщений (по умолчанию 200, 'all' для всех): ").strip()
                limit = None if limit_input.lower() == 'all' else (int(limit_input) if limit_input.isdigit() else 200)
                await sort_and_display_messages(
                    channel_input=current_channel,
                    sort_by='date_desc',
                    limit=limit,
                    search_query=search_query
                )
            else:
                print("[!] Пустой поисковый запрос.")

        elif choice == '4':
            await show_channel_info(current_channel)

        elif choice == '5':
            current_channel = None

        elif choice == '0':
            print("\nЗавершение работы. До свидания!")
            break
        else:
            print("[!] Неверный выбор. Введите число от 0 до 5.")


if __name__ == '__main__':
    try:
        asyncio.run(main_menu())
    except KeyboardInterrupt:
        print("\n\n[!] Программа прервана пользователем.")
    except Exception as e:
        print(f"\n[!] Непредвиденная ошибка: {e}")