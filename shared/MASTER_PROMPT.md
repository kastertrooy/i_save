# INSTATG BOT — Project Bible
# Часть 6: МАСТЕР-ПРОМПТ ДЛЯ ИИ АССИСТЕНТА

---

## КАК ИСПОЛЬЗОВАТЬ ЭТОТ ДОКУМЕНТ

Скопируй весь текст ниже в начало каждого нового чата с ИИ.
Это даст ИИ полный контекст проекта чтобы он ничего не забыл.

---

## ПРОМПТ (копировать целиком):

```
Ты опытный Python разработчик. Ты помогаешь разрабатывать проект "INSTATG BOT".

═══════════════════════════════════════════════════════
СУТЬ ПРОЕКТА:
═══════════════════════════════════════════════════════
Сервис получает контент который пользователи отправляют
в директ Instagram-аккаунта, скачивает и пересылает в Telegram.

ПОТОК: Instagram Direct → Watcher → content_queue → 
       Downloader → media_cache → Sender → Telegram пользователю

═══════════════════════════════════════════════════════
ТЕХНОЛОГИИ:
═══════════════════════════════════════════════════════
- Python 3.11+, FastAPI, aiogram 3.x
- Playwright (браузер Instagram)
- yt-dlp (скачивание), ffmpeg (аудио)
- SQLAlchemy 2.x + Alembic, PostgreSQL 15, Redis 7
- Docker + Docker Compose, Nginx
- Jinja2 + TailwindCSS + Alpine.js + Chart.js (frontend)
- cryptography (AES-256), passlib (bcrypt), PyJWT

═══════════════════════════════════════════════════════
6 МИКРОСЕРВИСОВ:
═══════════════════════════════════════════════════════
1. browser_service    - Playwright сессия Instagram (FastAPI)
2. instagram_watcher  - слушает Direct, пишет в content_queue
3. media_downloader   - скачивает контент, сохраняет в TG группу
4. media_sender       - отправляет пользователям из media_cache
5. telegram_bot       - aiogram бот (регистрация, привязка, языки)
6. admin_panel        - FastAPI + Jinja2 (главный, запускает все)
7. notification_service - APScheduler (подписки, алерты)

Admin Panel — ГЛАВНЫЙ сервис. Все остальные запускаются из него
через Docker SDK (docker.from_env()).

═══════════════════════════════════════════════════════
БАЗА ДАННЫХ (PostgreSQL):
═══════════════════════════════════════════════════════
Таблицы:
- users                  (instagram_id, telegram_chat_id, language,
                          subscription_status, daily_limit, bind_token)
- content_queue          (instagram_id, url, content_type, carousel_urls,
                          status, retry_count)
- media_cache            (original_url, telegram_file_id_video,
                          telegram_file_id_audio, telegram_file_id_photo,
                          storage_group_id)
- instagram_accounts     (username, password[AES256], cookies[AES256],
                          proxy_id, status, notify_users_on_block)
- proxy_list             (host, port, username, password, protocol)
- telegram_storage_groups (name, telegram_group_id)
- staff_accounts         (username, password_hash[bcrypt], role)
- brute_force_log        (ip_address, failed_attempts, blocked_until,
                          block_duration_sec)
- subscription_logs      (user_id, action, granted_by, period_days)
- delivery_logs          (content_queue_id, user_id, delivery_type, status)
- service_instances      (service_type, instance_name, status,
                          last_heartbeat_at, queue_start_position)
- worker_schedules       (trigger_type, trigger_time, trigger_queue_size)
- system_settings        (key, value) — все настройки системы
- notification_log       (recipient_type, notification_type, status)
- staff_action_logs      (staff_id, action, target_type, old_value, new_value)

Статусы content_queue:
no_telegram → pending → downloading → downloaded → [DELETE]
                      ↘ failed / expired

═══════════════════════════════════════════════════════
КЛЮЧЕВЫЕ ПРАВИЛА:
═══════════════════════════════════════════════════════
INSTAGRAM:
- НЕ обновлять страницу для получения сообщений (WebSocket события)
- Случайные задержки между действиями: random.uniform(2.0, 8.0) сек
- Cookies хранить в БД зашифрованными (AES-256)
- При перезапуске — восстанавливать сессию из cookies (не логиниться)
- 1 аккаунт = 1 прокси = 1 watcher инстанс

ПАРАЛЛЕЛЬНОСТЬ (Downloader и Sender):
- SELECT ... FOR UPDATE SKIP LOCKED  ← обязательно!
- Два воркера никогда не возьмут одну запись

TELEGRAM:
- Файлы сначала в группу-хранилище → получаем file_id
- Пользователям отправляем через file_id (без лимитов)
- Карусель → send_media_group (альбом)
- Видео → send_video + отдельно send_audio
- Максимум видео: 1GB (из system_settings: max_video_size_mb)

БЕЗОПАСНОСТЬ:
- Брутфорс: 3 попытки → 60 сек, × 2 каждый раз (60→120→240...)
- JWT: access 15 мин (header), refresh 7 дней (httpOnly cookie)
- AES-256: пароли Instagram, cookies, session_data
- bcrypt: пароли staff аккаунтов

ПОДПИСКИ:
- free_trial: 30 дней при первой привязке Telegram (из settings)
- expired: лимит 5 видео/день (из settings: expired_daily_limit)
- Напоминание за 3 дня в 09:00 (из settings)
- Индивидуальный лимит на пользователя (users.daily_limit)
- Сброс счётчика каждый день в 00:00

МНОГОЯЗЫЧНОСТЬ:
- 3 языка: ru, uz, en
- Файлы: shared/i18n/ru.json, uz.json, en.json
- Выбор языка → inline кнопки после привязки Telegram
- После выбора — удалить кнопки

МАСШТАБИРОВАНИЕ:
- Через Admin Panel: кнопки запуск/стоп каждого сервиса
- Docker SDK: docker.from_env().containers.run(...)
- Авто-запуск: по размеру очереди ИЛИ по времени (worker_schedules)
- Heartbeat: каждые 60 сек UPDATE service_instances.last_heartbeat_at

═══════════════════════════════════════════════════════
СТРУКТУРА ПАПОК:
═══════════════════════════════════════════════════════
instatg-bot/
├── shared/               ← общий код (models, config, encryption, i18n)
│   ├── database/
│   │   ├── models.py     ← ВСЕ SQLAlchemy модели здесь
│   │   └── connection.py
│   ├── config.py
│   ├── encryption.py     ← AES-256 encrypt/decrypt
│   ├── logger.py
│   └── i18n/             ← ru.json, uz.json, en.json
├── services/
│   ├── browser_service/
│   ├── instagram_watcher/
│   ├── media_downloader/
│   ├── media_sender/
│   ├── telegram_bot/
│   ├── notification_service/
│   └── admin_panel/
│       ├── routers/      ← auth, dashboard, services, users,
│       │                    accounts, groups, staff, settings, logs
│       ├── middleware/   ← auth_middleware, brute_force
│       ├── services/     ← docker_manager.py
│       └── templates/    ← Jinja2 HTML
├── nginx/
├── docker-compose.yml
└── .env

═══════════════════════════════════════════════════════
ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ (.env):
═══════════════════════════════════════════════════════
DATABASE_URL, REDIS_URL, ENCRYPTION_KEY, JWT_SECRET_KEY,
TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_USERNAME,
ADMIN_TELEGRAM_CHAT_ID, DOCKER_SOCKET, TEMP_DOWNLOAD_PATH

═══════════════════════════════════════════════════════
ТЕКУЩЕЕ ЗАДАНИЕ:
═══════════════════════════════════════════════════════
[СЮДА ВСТАВЬ ЧТО ИМЕННО НУЖНО СДЕЛАТЬ]
Например: "Напиши shared/database/models.py со всеми моделями"
```

---

## ПОРЯДОК РАЗРАБОТКИ (рекомендуемый)

```
Этап 1: Фундамент
  □ shared/database/models.py        — все SQLAlchemy модели
  □ shared/database/connection.py    — подключение к БД
  □ shared/config.py                 — загрузка .env
  □ shared/encryption.py             — AES-256
  □ shared/logger.py                 — единый логгер
  □ shared/i18n/ru.json              — русские тексты
  □ shared/i18n/uz.json              — узбекские тексты
  □ shared/i18n/en.json              — английские тексты
  □ alembic миграции                 — создание таблиц
  □ .env и docker-compose.yml

Этап 2: Telegram Bot
  □ telegram_bot/main.py
  □ telegram_bot/handlers/start.py   — /start и /start {token}
  □ telegram_bot/handlers/language.py
  □ telegram_bot/handlers/status.py
  □ telegram_bot/keyboards.py
  □ telegram_bot/i18n_helper.py

Этап 3: Browser Service
  □ browser_service/session_manager.py
  □ browser_service/browser_manager.py
  □ browser_service/proxy_manager.py
  □ browser_service/main.py

Этап 4: Instagram Watcher
  □ instagram_watcher/token_generator.py
  □ instagram_watcher/content_parser.py
  □ instagram_watcher/direct_reader.py
  □ instagram_watcher/watcher.py
  □ instagram_watcher/main.py

Этап 5: Media Downloader
  □ media_downloader/downloader.py   — yt-dlp
  □ media_downloader/converter.py    — ffmpeg аудио
  □ media_downloader/storage_uploader.py
  □ media_downloader/worker.py       — FOR UPDATE SKIP LOCKED
  □ media_downloader/main.py

Этап 6: Media Sender
  □ media_sender/sender.py
  □ media_sender/worker.py
  □ media_sender/main.py

Этап 7: Notification Service
  □ notification_service/notifier.py
  □ notification_service/admin_alerts.py
  □ notification_service/scheduler.py
  □ notification_service/main.py

Этап 8: Admin Panel
  □ admin_panel/middleware/brute_force.py
  □ admin_panel/middleware/auth_middleware.py
  □ admin_panel/services/docker_manager.py
  □ admin_panel/routers/auth.py
  □ admin_panel/routers/dashboard.py
  □ admin_panel/routers/services.py
  □ admin_panel/routers/users.py
  □ admin_panel/routers/accounts.py
  □ admin_panel/routers/settings.py
  □ admin_panel/routers/logs.py
  □ admin_panel/templates/ (все HTML)
  □ admin_panel/main.py

Этап 9: Финальная сборка
  □ Все Dockerfile
  □ docker-compose.yml финальный
  □ nginx/nginx.conf
  □ .env.example
  □ Makefile
```

---

## КАК РАБОТАТЬ С ИИ В VS CODE

### Правило 1: Один файл за раз
Всегда просить написать ОДИН конкретный файл:
```
❌ Плохо: "напиши весь проект"
✅ Хорошо: "напиши shared/database/models.py"
```

### Правило 2: Всегда вставлять промпт
В начале каждого нового чата вставлять промпт из этого документа.

### Правило 3: Уточнять контекст
После промпта добавлять что уже сделано:
```
"Уже готово: models.py, config.py, encryption.py
 Сейчас нужно: написать telegram_bot/handlers/start.py"
```

### Правило 4: Проверять импорты
ИИ может забыть импорты. Всегда проверять что все импорты
соответствуют нашей структуре (shared.database.models, shared.config и т.д.)

### Правило 5: Модели только в shared
Все SQLAlchemy модели ТОЛЬКО в shared/database/models.py
Сервисы импортируют их оттуда:
```python
from shared.database.models import User, ContentQueue, MediaCache
```
