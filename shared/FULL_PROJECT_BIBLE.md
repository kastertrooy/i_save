# INSTATG BOT — Project Bible
# Часть 1: Обзор проекта и архитектура

---

## ЧТО ДЕЛАЕТ ЭТОТ ПРОЕКТ

Сервис получает контент который пользователи отправляют в директ Instagram-аккаунта,
скачивает его и пересылает владельцу в Telegram.

### Полный сценарий:
1. Пользователь подписывается на Instagram аккаунт сервиса
2. Отправляет в директ любой контент (видео, фото, reels, stories, карусель)
3. Watcher сервис обнаруживает новое сообщение
4. Если пользователь привязан к Telegram → контент идёт в очередь на скачивание
5. Если не привязан → получает одноразовую ссылку для привязки Telegram
6. Downloader скачивает контент, сохраняет в Telegram-группу хранилище
7. Sender отправляет пользователю: видео + аудио отдельно, или фото, или альбом
8. Запись удаляется из очереди

---

## ТЕХНОЛОГИЧЕСКИЙ СТЕК

```
Язык:           Python 3.11+
Web Framework:  FastAPI
Telegram Bot:   aiogram 3.x
Browser:        Playwright (Chromium)
Загрузка медиа: yt-dlp
Аудио:          ffmpeg-python
ORM:            SQLAlchemy 2.x
Миграции:       Alembic
Планировщик:    APScheduler
Шифрование:     cryptography (AES-256)
Пароли:         passlib (bcrypt)
JWT:            PyJWT
БД основная:    PostgreSQL 15
Кэш/очереди:   Redis 7
Контейнеры:     Docker + Docker Compose
Прокси:         Nginx
ОС сервера:     Ubuntu 22.04 LTS
Frontend:       Jinja2 + TailwindCSS + Alpine.js + Chart.js
```

---

## МИКРОСЕРВИСЫ (6 сервисов)

| Сервис | Назначение | Масштабируется |
|--------|-----------|----------------|
| browser_service | Управляет браузерной сессией Instagram | Нет (1 на аккаунт) |
| instagram_watcher | Следит за директом, пишет в очередь | Да (1 на аккаунт) |
| media_downloader | Скачивает контент, сохраняет в TG группу | Да (N воркеров) |
| media_sender | Отправляет контент пользователям | Да (N воркеров) |
| telegram_bot | Регистрация, привязка, уведомления | Нет |
| admin_panel | Главный центр управления + staff | Нет |
| notification_service | Подписки, алерты, напоминания | Нет |

### Важно:
- Admin Panel — ГЛАВНЫЙ сервис, запускает все остальные
- Все сервисы запускаются через Docker из Admin Panel
- Каждый Watcher = один Instagram аккаунт
- Downloader и Sender — отдельные сервисы (не один)

---

## ПОТОК ДАННЫХ

```
Instagram Direct
    ↓
instagram_watcher (только читает директ, пишет в content_queue)
    ↓
content_queue (PostgreSQL) [status: pending]
    ↓
media_downloader (скачивает, сохраняет в TG группу хранилище)
    ↓
content_queue [status: downloaded] + media_cache (file_id)
    ↓
media_sender (отправляет пользователю)
    ↓
content_queue [DELETE] + delivery_logs
```

---

## СТАТУСЫ КОНТЕНТА В ОЧЕРЕДИ

```
no_telegram   → пользователь не привязал Telegram (ждём привязки)
pending       → готов к скачиванию
downloading   → в процессе скачивания
downloaded    → скачан, готов к отправке
sending       → в процессе отправки
done          → отправлен (сразу удаляется)
failed        → ошибка после 3 попыток
expired       → stories истекла (недоступна)
```

### Переходы статусов:
```
no_telegram → pending (после привязки Telegram)
pending → downloading (Downloader взял задачу)
downloading → downloaded (успешно скачан)
downloading → failed (3 ошибки)
downloading → expired (stories недоступна)
downloaded → sending (Sender взял задачу)
sending → DELETE (успешно отправлен)
sending → failed (3 ошибки)
```
# INSTATG BOT — Project Bible
# Часть 2: База данных (PostgreSQL)

---

## ПОЛНАЯ СХЕМА ТАБЛИЦ

### Таблица: users
```sql
CREATE TABLE users (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instagram_id          VARCHAR(100) UNIQUE NOT NULL,
    instagram_username    VARCHAR(100),
    telegram_chat_id      BIGINT UNIQUE NULL,
    telegram_username     VARCHAR(100) NULL,
    language              VARCHAR(5) DEFAULT 'ru',  -- ru, uz, en
    subscription_status   VARCHAR(20) NOT NULL DEFAULT 'no_subscription',
    -- значения: active, expired, free_trial, blocked, no_subscription
    subscription_until    TIMESTAMP NULL,
    free_trial_used       BOOLEAN DEFAULT FALSE,
    daily_limit           INT NULL,         -- NULL = берётся из system_settings
    daily_downloads_today INT DEFAULT 0,
    daily_reset_at        TIMESTAMP,
    bind_token            VARCHAR(64) NULL,  -- одноразовый UUID
    bind_token_expires_at TIMESTAMP NULL,   -- живёт 24 часа
    created_at            TIMESTAMP DEFAULT NOW(),
    updated_at            TIMESTAMP DEFAULT NOW()
);
```

### Таблица: content_queue
```sql
CREATE TABLE content_queue (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instagram_id         VARCHAR(100) NOT NULL REFERENCES users(instagram_id),
    instagram_account_id UUID NOT NULL REFERENCES instagram_accounts(id),
    url                  TEXT NOT NULL,
    content_type         VARCHAR(20) NOT NULL,
    -- значения: video, photo, reel, story, carousel
    carousel_urls        JSONB NULL,        -- массив URL для карусели
    status               VARCHAR(20) NOT NULL DEFAULT 'pending',
    -- значения: no_telegram, pending, downloading, downloaded, sending, done, failed, expired
    retry_count          INT DEFAULT 0,
    error_message        TEXT NULL,
    file_size_mb         FLOAT NULL,
    created_at           TIMESTAMP DEFAULT NOW(),
    updated_at           TIMESTAMP DEFAULT NOW()
);
-- Индексы для производительности:
CREATE INDEX idx_content_queue_status ON content_queue(status);
CREATE INDEX idx_content_queue_instagram_id ON content_queue(instagram_id);
```

### Таблица: media_cache
```sql
CREATE TABLE media_cache (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    original_url            TEXT UNIQUE NOT NULL,
    content_type            VARCHAR(20),
    telegram_file_id_video  VARCHAR(200) NULL,
    telegram_file_id_audio  VARCHAR(200) NULL,
    telegram_file_id_photo  VARCHAR(200) NULL,
    storage_group_id        BIGINT NOT NULL,  -- ID Telegram группы хранилища
    file_size_mb            FLOAT,
    duration_seconds        INT NULL,
    created_at              TIMESTAMP DEFAULT NOW(),
    last_used_at            TIMESTAMP DEFAULT NOW()
);
```

### Таблица: instagram_accounts
```sql
CREATE TABLE instagram_accounts (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username               VARCHAR(100) UNIQUE NOT NULL,
    password               TEXT NOT NULL,          -- AES-256 зашифрован
    proxy_id               UUID NULL REFERENCES proxy_list(id),
    cookies                TEXT NULL,              -- AES-256 зашифрован JSON
    session_data           TEXT NULL,              -- AES-256 зашифрован JSON
    status                 VARCHAR(20) DEFAULT 'inactive',
    -- значения: active, blocked, inactive, checking
    is_primary             BOOLEAN DEFAULT FALSE,
    last_check_at          TIMESTAMP NULL,
    blocked_at             TIMESTAMP NULL,
    notify_users_on_block  BOOLEAN DEFAULT TRUE,
    assigned_watcher_id    VARCHAR(100) NULL,      -- ID Docker контейнера
    created_at             TIMESTAMP DEFAULT NOW(),
    updated_at             TIMESTAMP DEFAULT NOW()
);
```

### Таблица: proxy_list
```sql
CREATE TABLE proxy_list (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    host         VARCHAR(255) NOT NULL,
    port         INT NOT NULL,
    username     VARCHAR(100) NULL,
    password     VARCHAR(100) NULL,
    protocol     VARCHAR(10) DEFAULT 'http',   -- http, socks5
    is_active    BOOLEAN DEFAULT TRUE,
    is_working   BOOLEAN DEFAULT TRUE,
    last_check_at TIMESTAMP NULL,
    created_at   TIMESTAMP DEFAULT NOW()
);
```

### Таблица: telegram_storage_groups
```sql
CREATE TABLE telegram_storage_groups (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(100) NOT NULL,        -- понятное имя для списка
    telegram_group_id BIGINT UNIQUE NOT NULL,     -- реальный ID группы в Telegram
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT NOW()
);
```

### Таблица: staff_accounts
```sql
CREATE TABLE staff_accounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username        VARCHAR(100) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,        -- bcrypt
    role            VARCHAR(10) NOT NULL,          -- admin, staff
    telegram_chat_id BIGINT NULL,
    is_active       BOOLEAN DEFAULT TRUE,
    last_login_at   TIMESTAMP NULL,
    created_at      TIMESTAMP DEFAULT NOW()
);
```

### Таблица: brute_force_log
```sql
CREATE TABLE brute_force_log (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ip_address        VARCHAR(45) NOT NULL,
    username_attempted VARCHAR(100),
    failed_attempts   INT DEFAULT 0,
    blocked_until     TIMESTAMP NULL,
    block_duration_sec INT DEFAULT 60,
    created_at        TIMESTAMP DEFAULT NOW(),
    updated_at        TIMESTAMP DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_brute_force_ip ON brute_force_log(ip_address);
```

### Таблица: subscription_logs
```sql
CREATE TABLE subscription_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    action          VARCHAR(20) NOT NULL,
    -- значения: activated, expired, extended, blocked, granted, limited, free_trial
    granted_by      UUID NULL REFERENCES staff_accounts(id),
    period_days     INT NULL,
    daily_limit_set INT NULL,
    comment         TEXT NULL,
    created_at      TIMESTAMP DEFAULT NOW()
);
```

### Таблица: delivery_logs
```sql
CREATE TABLE delivery_logs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_queue_id  UUID NOT NULL,
    user_id           UUID NOT NULL REFERENCES users(id),
    telegram_message_id BIGINT NULL,
    delivery_type     VARCHAR(10),   -- video, audio, photo, album
    status            VARCHAR(10),   -- success, failed
    error_message     TEXT NULL,
    file_size_mb      FLOAT NULL,
    created_at        TIMESTAMP DEFAULT NOW()
);
```

### Таблица: service_instances
```sql
CREATE TABLE service_instances (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service_type          VARCHAR(20) NOT NULL,
    -- значения: watcher, downloader, sender, browser
    instance_name         VARCHAR(100) UNIQUE NOT NULL,
    instagram_account_id  UUID NULL REFERENCES instagram_accounts(id),
    storage_group_id      UUID NULL REFERENCES telegram_storage_groups(id),
    docker_container_id   VARCHAR(100) NULL,
    status                VARCHAR(20) DEFAULT 'stopped',
    -- значения: running, stopped, error, starting
    queue_start_position  VARCHAR(10) NULL,   -- beginning, middle, end
    auto_scale_enabled    BOOLEAN DEFAULT FALSE,
    scale_threshold       INT NULL,
    scale_at_time         TIME NULL,
    current_queue_size    INT DEFAULT 0,
    last_heartbeat_at     TIMESTAMP NULL,
    created_at            TIMESTAMP DEFAULT NOW()
);
```

### Таблица: worker_schedules
```sql
CREATE TABLE worker_schedules (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service_instance_id   UUID NOT NULL REFERENCES service_instances(id),
    trigger_type          VARCHAR(15) NOT NULL,   -- time, queue_size, manual
    trigger_time          TIME NULL,
    trigger_queue_size    INT NULL,
    action                VARCHAR(15) NOT NULL,   -- start, stop, scale_up, scale_down
    is_active             BOOLEAN DEFAULT TRUE,
    last_triggered_at     TIMESTAMP NULL,
    created_at            TIMESTAMP DEFAULT NOW()
);
```

### Таблица: system_settings
```sql
CREATE TABLE system_settings (
    key           VARCHAR(100) PRIMARY KEY,
    value         TEXT NOT NULL,
    description   TEXT,
    updated_by    UUID NULL REFERENCES staff_accounts(id),
    updated_at    TIMESTAMP DEFAULT NOW()
);

-- Начальные значения:
INSERT INTO system_settings VALUES
('free_trial_days',         '30',      'Дней бесплатного пробного периода'),
('expired_daily_limit',     '5',       'Лимит скачиваний в день без подписки'),
('queue_alert_threshold',   '100',     'Порог очереди для алерта'),
('auto_scale_threshold',    '100',     'Порог для авто-запуска доп воркера'),
('max_video_size_mb',       '1000',    'Максимальный размер видео (МБ)'),
('subscription_remind_days','3',       'За сколько дней напоминать об истечении'),
('admin_telegram_chat_id',  '',        'Chat ID администратора для алертов'),
('new_account_notify',      'true',    'Уведомлять пользователей о смене аккаунта');
```

### Таблица: notification_log
```sql
CREATE TABLE notification_log (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recipient_type    VARCHAR(10),   -- user, admin, staff
    recipient_id      UUID,
    notification_type VARCHAR(30),
    -- subscription_expiry, content_failed, account_blocked,
    -- system_alert, bind_link, story_expired
    message           TEXT,
    status            VARCHAR(10),   -- sent, failed, pending
    created_at        TIMESTAMP DEFAULT NOW()
);
```

### Таблица: staff_action_logs
```sql
CREATE TABLE staff_action_logs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    staff_id    UUID NOT NULL REFERENCES staff_accounts(id),
    action      TEXT NOT NULL,          -- описание действия
    target_type VARCHAR(20),            -- user, account, service, setting
    target_id   VARCHAR(100),           -- ID объекта
    old_value   TEXT NULL,
    new_value   TEXT NULL,
    created_at  TIMESTAMP DEFAULT NOW()
);
```

---

## ШИФРОВАНИЕ

- Пароли Instagram (поле password) → AES-256-CBC, ключ из .env
- Cookies/session (поля cookies, session_data) → AES-256-CBC
- Пароли staff (поле password_hash) → bcrypt (не AES, bcrypt)
- JWT секрет → в .env переменной JWT_SECRET_KEY
# INSTATG BOT — Project Bible
# Часть 3: Логика каждого сервиса

---

## SERVICE 1: browser_service

### Назначение:
Управляет браузерной сессией Instagram через Playwright.
Один экземпляр = один Instagram аккаунт.

### Запуск:
1. Получить instagram_account_id из переменной окружения
2. Загрузить cookies из БД (поле cookies, расшифровать AES-256)
3. Запустить Playwright Chromium
4. Применить прокси если proxy_id задан в аккаунте
5. Применить cookies → открыть instagram.com
6. Проверить валидность сессии (ищем элемент директа)
7. Если сессия валидна → сохранить статус ready
8. Если нет → выполнить логин (username + password)
9. Сохранить новые cookies в БД (зашифровать)
10. Обновить session_data

### ВАЖНО — НЕ обновлять страницу:
Watcher слушает WebSocket события Instagram которые приходят сами.
Никаких F5, location.reload() или перехода по URL для получения сообщений.

### Health Check (каждые 60 сек):
- Проверить что браузер живой (page.is_closed())
- Проверить что мы залогинены в Instagram
- Обновить last_heartbeat_at в service_instances
- Сохранить актуальные cookies в БД
- Если 3 проверки подряд провалились → рестарт сервиса
- Если Instagram показал блокировку аккаунта:
  * UPDATE instagram_accounts SET status='blocked', blocked_at=NOW()
  * Уведомить админа в Telegram
  * Если notify_users_on_block=true → уведомить всех активных пользователей

### API эндпоинты (FastAPI):
```
GET  /health          → статус сервиса
GET  /session/status  → статус браузерной сессии
POST /session/save    → сохранить текущие cookies
POST /restart         → перезапустить браузер
```

### Переменные окружения:
```
INSTAGRAM_ACCOUNT_ID=uuid
ENCRYPTION_KEY=...
DATABASE_URL=...
```

---

## SERVICE 2: instagram_watcher

### Назначение:
Следит за Instagram Direct, парсит новые сообщения,
записывает контент в content_queue. ТОЛЬКО запись в БД, больше ничего.

### Важные правила:
- НЕ скачивает контент
- НЕ отправляет в Telegram
- НЕ обновляет страницу для получения сообщений
- Слушает события которые Instagram отправляет через WebSocket/XHR
- Имитирует человеческое поведение (случайные задержки 2-8 сек)

### Запуск (через Admin Panel):
- Получает instagram_account_id (из списка или ввод)
- Подключается к browser_service этого аккаунта

### Основной цикл (событийная модель, не polling):
```
Новое событие в директе:
  1. Извлечь instagram_id отправителя
  2. Определить тип контента
  3. Проверить пользователя в БД

  [Пользователь НЕ найден]:
  - INSERT users (instagram_id, instagram_username)
  - Генерировать bind_token = UUID4
  - bind_token_expires_at = NOW() + 24 часа
  - Отправить в директ Instagram:
    "Привет! Чтобы получать контент, привяжи Telegram:
     t.me/{BOT_USERNAME}?start={bind_token}"
  - INSERT content_queue (status='no_telegram')

  [Пользователь найден, telegram_chat_id IS NULL]:
  - Проверить bind_token — если нет или истёк:
    * Сгенерировать новый bind_token
    * Отправить ссылку в директ
  - INSERT content_queue (status='no_telegram')

  [Пользователь найден, telegram_chat_id NOT NULL]:
  - Проверить subscription_status:
    * active / free_trial → INSERT content_queue (status='pending')
    * expired → проверить daily_downloads_today vs лимит
      - меньше лимита → INSERT content_queue (status='pending')
      - больше лимита → отправить в директ "лимит исчерпан"
    * blocked → игнорировать

  [Парсинг типа контента]:
  - Одиночное видео → content_type='video'
  - Одиночное фото  → content_type='photo'
  - Reels           → content_type='reel'
  - Stories видео   → content_type='story'
  - Stories фото    → content_type='story'
  - Карусель        → content_type='carousel', carousel_urls=[url1, url2, ...]
  - Видео >1GB      → пропустить, уведомить "файл слишком большой"
```

### Формат записи в content_queue:
```python
{
  "instagram_id": "12345678",
  "instagram_account_id": "uuid-аккаунта",
  "url": "https://instagram.com/...",
  "content_type": "video",
  "carousel_urls": None,  # или список URL для карусели
  "status": "pending"
}
```

---

## SERVICE 3: media_downloader

### Назначение:
Берёт записи из content_queue (status='pending'),
скачивает контент, отправляет в Telegram группу-хранилище,
записывает file_id в media_cache, меняет статус на 'downloaded'.

### Запуск (через Admin Panel):
- Выбрать позицию начала: beginning / middle / end
- Выбрать Telegram группу хранилище из списка telegram_storage_groups

### Параллельная безопасность:
Использовать PostgreSQL механизм:
```sql
SELECT * FROM content_queue
WHERE status = 'pending'
ORDER BY created_at ASC   -- или DESC для 'end', OFFSET для 'middle'
LIMIT 5
FOR UPDATE SKIP LOCKED;   -- два воркера не возьмут одну запись
```

### Основной цикл (каждые 15 сек):
```
1. Взять до 5 записей (FOR UPDATE SKIP LOCKED)
2. Для каждой записи:

   [ПРОВЕРКА КЭША]:
   SELECT * FROM media_cache WHERE original_url = url
   Если найден → пропустить скачивание, взять file_id → сразу 'downloaded'

   [ПРОВЕРКА STORIES]:
   Если content_type = 'story':
   - Проверить доступность URL
   - Если 404/недоступна → status='expired'
   - Уведомить пользователя: "Не успели скачать сторис, извините"
   - Следующая запись

   [СКАЧИВАНИЕ]:
   UPDATE status='downloading'
   
   Если content_type = 'carousel':
   - Скачать каждый URL из carousel_urls по отдельности
   
   Иначе:
   - Скачать URL через yt-dlp
   - Команда: yt-dlp -o "temp/{id}.%(ext)s" {url}
   - Если видео/reel/story(видео) → извлечь аудио:
     ffmpeg -i video.mp4 -vn -acodec mp3 audio.mp3

   [ОТПРАВКА В ХРАНИЛИЩЕ]:
   Bot.send_video(chat_id=storage_group_telegram_id, video=file)
   → получить file_id от Telegram
   
   Bot.send_audio(chat_id=storage_group_telegram_id, audio=file)
   → получить file_id
   
   INSERT INTO media_cache:
   - original_url
   - telegram_file_id_video
   - telegram_file_id_audio
   - storage_group_id
   - file_size_mb
   
   [ЗАВЕРШЕНИЕ]:
   UPDATE content_queue SET status='downloaded'
   Удалить локальные файлы (os.remove)
   
   [ОШИБКА]:
   retry_count += 1
   Если retry_count >= 3 → status='failed', уведомить админа
   Иначе → status='pending' (вернуть в очередь)
```

---

## SERVICE 4: media_sender

### Назначение:
Берёт записи из content_queue (status='downloaded'),
отправляет пользователям в Telegram, удаляет записи.

### Параллельная безопасность:
Та же схема FOR UPDATE SKIP LOCKED что и у Downloader.

### Основной цикл (каждые 10 сек):
```
1. Взять до 10 записей (status='downloaded', FOR UPDATE SKIP LOCKED)
2. Для каждой записи:

   [ПРОВЕРКА ПОЛЬЗОВАТЕЛЯ]:
   Получить telegram_chat_id пользователя
   Проверить subscription_status:
   - blocked → DELETE запись, не отправлять
   - expired → проверить daily_downloads_today vs лимит
     * >= лимита → уведомить "лимит исчерпан", не отправлять
     * < лимита → отправляем
   - active / free_trial → отправляем

   [ПОЛУЧЕНИЕ FILE_ID]:
   SELECT * FROM media_cache WHERE original_url = url
   Взять нужные file_id

   [ОТПРАВКА ПО ТИПУ КОНТЕНТА]:
   
   video / reel:
   - Bot.send_video(chat_id, video=file_id_video)
   - Bot.send_audio(chat_id, audio=file_id_audio)  ← отдельным сообщением
   
   photo:
   - Bot.send_photo(chat_id, photo=file_id_photo)
   
   story (видео):
   - Bot.send_video(chat_id, video=file_id_video)
   - Bot.send_audio(chat_id, audio=file_id_audio)
   
   story (фото):
   - Bot.send_photo(chat_id, photo=file_id_photo)
   
   carousel:
   - Bot.send_media_group(chat_id, media=[...])  ← альбом одним блоком
     Каждый элемент: InputMediaVideo или InputMediaPhoto

   [ПОСЛЕ УСПЕШНОЙ ОТПРАВКИ]:
   INSERT delivery_logs (status='success')
   UPDATE users SET daily_downloads_today += 1
   DELETE FROM content_queue WHERE id = ?

   [ОШИБКА]:
   retry_count += 1
   INSERT delivery_logs (status='failed', error_message=...)
   Если retry_count >= 3 → status='failed'
```

---

## SERVICE 5: telegram_bot

### Команды:

**/start** (без параметра):
```
→ "Привет! Подпишись на наш Instagram @{account}
   и отправь любой контент в директ чтобы получить его здесь"
```

**/start {bind_token}** (привязка аккаунта):
```
1. SELECT user WHERE bind_token=? AND bind_token_expires_at > NOW()
2. Если не найден → "Ссылка недействительна или истекла"
3. Если telegram_chat_id уже заполнен → "Аккаунт уже привязан"
4. UPDATE users SET
   telegram_chat_id = message.from_user.id,
   telegram_username = message.from_user.username,
   bind_token = NULL,
   bind_token_expires_at = NULL
5. Если free_trial_used = FALSE:
   UPDATE users SET
   subscription_status = 'free_trial',
   subscription_until = NOW() + interval '{free_trial_days} days',
   free_trial_used = TRUE
   INSERT subscription_logs (action='free_trial')
6. Показать inline кнопки выбора языка:
   [🇷🇺 Русский]  [🇺🇿 O'zbekcha]  [🇬🇧 English]
7. После нажатия кнопки:
   UPDATE users SET language = выбранный
   Удалить сообщение с кнопками
   Отправить приветствие на выбранном языке
8. UPDATE content_queue SET status='pending'
   WHERE instagram_id = user.instagram_id AND status='no_telegram'
```

**/status**:
```
→ Статус подписки, дней осталось, лимит сегодня
   На языке пользователя (user.language)
```

**/help**:
```
→ Инструкция на языке пользователя
```

### Тексты на трёх языках (i18n):
Все сообщения бота хранятся в файлах:
- shared/i18n/ru.json
- shared/i18n/uz.json
- shared/i18n/en.json

Ключи: welcome, bind_success, bind_invalid, status_active,
status_expired, status_trial, story_expired, limit_reached,
account_changed, subscription_expiry_remind, subscription_expired

---

## SERVICE 6: notification_service

### Расписание (APScheduler):

**Каждый день в 09:00** — напоминания о подписке:
```
SELECT users WHERE subscription_until BETWEEN NOW() AND NOW() + 3 days
AND subscription_status IN ('active', 'free_trial')
→ Отправить напоминание на языке пользователя
→ INSERT notification_log
```

**Каждый час** — проверка истёкших подписок:
```
SELECT users WHERE subscription_until < NOW()
AND subscription_status IN ('active', 'free_trial')
→ UPDATE subscription_status = 'expired'
→ Уведомить пользователя
→ INSERT subscription_logs (action='expired')
```

**Каждый день в 00:00** — сброс дневных счётчиков:
```
UPDATE users SET daily_downloads_today = 0
```

**Каждые 2 минуты** — проверка сервисов:
```
SELECT service_instances WHERE status='running'
AND last_heartbeat_at < NOW() - interval '5 minutes'
→ Уведомить админа: "Сервис {name} не отвечает"
→ UPDATE status='error'
```

**Real-time** — проверка очереди:
```
SELECT COUNT(*) FROM content_queue WHERE status='pending'
Если > queue_alert_threshold:
→ Уведомить админа: "Очередь: {count} записей"
```

### Уведомления админу (Telegram):
Все алерты идут в admin_telegram_chat_id из system_settings.
Типы:
- 🔴 Аккаунт Instagram заблокирован
- 🔴 Сервис не отвечает (heartbeat)
- 🟡 Очередь переполнена (> порога)
- 🔴 Ошибка скачивания (retry_count >= 3)

---

## SERVICE 7: admin_panel

### Аутентификация:
- JWT токены: access (15 мин), refresh (7 дней)
- Хранить refresh token в httpOnly cookie

### Защита от брутфорса:
```python
Попытка 1,2: ничего не делаем
Попытка 3:   block_duration = 60 сек
Попытка 4:   block_duration = 120 сек
Попытка 5:   block_duration = 240 сек
Попытка N:   block_duration = 60 * (2 ** (N-3)) сек

Показать на UI таймер: "Следующая попытка через {X} секунд"
Таймер обновляется каждую секунду (JavaScript)
```

### Роли:
- **admin**: полный доступ ко всем разделам
- **staff**: доступ только к разделам Пользователи и Подписки

### Разделы Admin Panel:

**Дашборд** (главная):
- Метрики: очередь, скачано сегодня, активные пользователи, МБ скачано, новые пользователи
- Статус каждого сервиса с кнопками управления
- Графики (Chart.js): по дням/неделям/месяцам
- Последние логи ошибок (10 записей)
- Статус очереди по каждому статусу

**Сервисы**:
- Список всех запущенных и остановленных сервисов
- Запуск Watcher: выбрать аккаунт из списка / ввести логин+пароль
- Запуск Downloader: выбрать позицию (beginning/middle/end) + группу хранилища
- Запуск Sender
- Настройка авто-масштабирования: триггер по очереди или по времени
- Добавить/удалить воркер

**Instagram аккаунты**:
- Список аккаунтов (статус, прокси, последняя активность)
- Добавить аккаунт (логин, пароль, прокси)
- Редактировать аккаунт
- Назначить прокси
- Переключить notify_users_on_block
- Удалить аккаунт

**Telegram группы** (хранилища):
- Список групп (имя, ID, активность)
- Добавить группу (имя + telegram_group_id)
- Удалить группу

**Прокси**:
- Список прокси (host:port, статус, привязан к аккаунту)
- Добавить прокси (host, port, user, pass, protocol)
- Проверить работоспособность прокси
- Удалить прокси

**Пользователи** (admin + staff):
- Поиск по instagram_id / telegram / username
- Таблица всех пользователей с фильтрами по статусу
- Карточка пользователя:
  * Статус подписки
  * История (subscription_logs)
  * Сегодня скачано / лимит
  * Индивидуальный дневной лимит
- Дать подписку одному пользователю (N дней)
- Дать подписку ВСЕМ пользователям (N дней)
- Дать подписку списку пользователей
- Заблокировать / разблокировать
- Изменить индивидуальный лимит (daily_limit)

**Персонал** (только admin):
- Список staff аккаунтов
- Добавить staff (username, password, role)
- Деактивировать staff
- Лог действий (staff_action_logs)

**Настройки** (только admin):
- Все ключи из system_settings редактируются в UI
- free_trial_days
- expired_daily_limit
- queue_alert_threshold
- max_video_size_mb
- subscription_remind_days
- admin_telegram_chat_id
- new_account_notify

**Логи ошибок**:
- Таблица с фильтрами: сервис, уровень, дата
- Детальный просмотр ошибки
# INSTATG BOT — Project Bible
# Часть 4: Структура файлов и Docker

---

## ПОЛНАЯ СТРУКТУРА ПРОЕКТА

```
instatg-bot/
│
├── .env                          ← секреты (не в git!)
├── .env.example                  ← шаблон для .env
├── .gitignore
├── docker-compose.yml            ← основной
├── docker-compose.dev.yml        ← для разработки
├── Makefile                      ← удобные команды
│
├── shared/                       ← общий код для ВСЕХ сервисов
│   ├── __init__.py
│   ├── config.py                 ← загрузка всех .env переменных
│   ├── encryption.py             ← AES-256 шифрование/дешифрование
│   ├── logger.py                 ← единый логгер для всех сервисов
│   ├── redis_client.py           ← подключение к Redis
│   ├── database/
│   │   ├── __init__.py
│   │   ├── connection.py         ← SQLAlchemy engine + session
│   │   ├── models.py             ← ВСЕ модели SQLAlchemy
│   │   └── migrations/           ← Alembic миграции
│   │       ├── alembic.ini
│   │       ├── env.py
│   │       └── versions/
│   │           └── 001_initial.py
│   └── i18n/                     ← переводы для Telegram бота
│       ├── ru.json
│       ├── uz.json
│       └── en.json
│
├── services/
│   │
│   ├── browser_service/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── main.py               ← FastAPI приложение + эндпоинты
│   │   ├── browser_manager.py    ← Playwright: запуск, сессии
│   │   ├── session_manager.py    ← cookies: загрузка/сохранение
│   │   └── proxy_manager.py      ← применение прокси к браузеру
│   │
│   ├── instagram_watcher/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── main.py               ← точка входа, цикл событий
│   │   ├── watcher.py            ← основной цикл слежки за директом
│   │   ├── content_parser.py     ← определение типа контента, парсинг URL
│   │   ├── direct_reader.py      ← чтение событий Instagram Direct
│   │   └── token_generator.py    ← генерация одноразовых bind_token
│   │
│   ├── media_downloader/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── main.py               ← точка входа, рабочий цикл
│   │   ├── worker.py             ← основной цикл (FOR UPDATE SKIP LOCKED)
│   │   ├── downloader.py         ← скачивание через yt-dlp
│   │   ├── converter.py          ← извлечение аудио через ffmpeg
│   │   └── storage_uploader.py   ← отправка в Telegram группу хранилище
│   │
│   ├── media_sender/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── main.py               ← точка входа, рабочий цикл
│   │   ├── worker.py             ← основной цикл (FOR UPDATE SKIP LOCKED)
│   │   └── sender.py             ← отправка пользователям по типу контента
│   │
│   ├── telegram_bot/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── main.py               ← инициализация aiogram
│   │   ├── handlers/
│   │   │   ├── __init__.py
│   │   │   ├── start.py          ← /start и /start {token}
│   │   │   ├── status.py         ← /status
│   │   │   ├── help.py           ← /help
│   │   │   └── language.py       ← callback выбора языка
│   │   ├── keyboards.py          ← inline клавиатуры
│   │   └── i18n_helper.py        ← загрузка текстов по языку
│   │
│   ├── notification_service/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── main.py               ← точка входа + APScheduler
│   │   ├── scheduler.py          ← все расписания задач
│   │   ├── notifier.py           ← отправка уведомлений в Telegram
│   │   └── admin_alerts.py       ← алерты администратору
│   │
│   └── admin_panel/
│       ├── Dockerfile
│       ├── requirements.txt
│       ├── main.py               ← FastAPI приложение
│       ├── routers/
│       │   ├── __init__.py
│       │   ├── auth.py           ← логин, логаут, refresh token
│       │   ├── dashboard.py      ← метрики, статистика
│       │   ├── services.py       ← управление Docker сервисами
│       │   ├── users.py          ← управление пользователями
│       │   ├── accounts.py       ← Instagram аккаунты + прокси
│       │   ├── groups.py         ← Telegram группы хранилища
│       │   ├── staff.py          ← персонал (только admin)
│       │   ├── settings.py       ← system_settings
│       │   └── logs.py           ← логи ошибок
│       ├── middleware/
│       │   ├── auth_middleware.py  ← проверка JWT
│       │   └── brute_force.py      ← защита от брутфорса
│       ├── services/
│       │   └── docker_manager.py   ← запуск/стоп Docker контейнеров
│       ├── static/
│       │   ├── css/
│       │   │   └── main.css
│       │   └── js/
│       │       ├── main.js
│       │       └── charts.js
│       └── templates/
│           ├── base.html
│           ├── login.html
│           ├── dashboard.html
│           ├── services.html
│           ├── users.html
│           ├── user_detail.html
│           ├── accounts.html
│           ├── groups.html
│           ├── proxies.html
│           ├── staff.html
│           ├── settings.html
│           └── logs.html
│
├── nginx/
│   ├── Dockerfile
│   └── nginx.conf
│
└── temp/                         ← временные файлы (в .gitignore)
    └── downloads/                ← скачанные файлы до отправки
```

---

## ФАЙЛ .env (все переменные)

```env
# База данных
DATABASE_URL=postgresql+asyncpg://postgres:password@postgres:5432/instatgbot
POSTGRES_USER=postgres
POSTGRES_PASSWORD=yourpassword
POSTGRES_DB=instatgbot

# Redis
REDIS_URL=redis://redis:6379/0

# Безопасность
ENCRYPTION_KEY=your-32-byte-aes-key-here-base64==
JWT_SECRET_KEY=your-jwt-secret-key-minimum-32-chars
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

# Telegram Bot
TELEGRAM_BOT_TOKEN=your-bot-token-from-botfather
TELEGRAM_BOT_USERNAME=your_bot_username

# Admin Telegram (для алертов)
ADMIN_TELEGRAM_CHAT_ID=your-admin-chat-id

# Docker (для управления контейнерами из admin panel)
DOCKER_SOCKET=/var/run/docker.sock

# Пути
TEMP_DOWNLOAD_PATH=/app/temp/downloads
```

---

## docker-compose.yml

```yaml
version: '3.9'

networks:
  instatg_network:
    driver: bridge

volumes:
  postgres_data:
  redis_data:
  temp_downloads:

services:

  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - instatg_network
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
    networks:
      - instatg_network
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 3

  admin_panel:
    build: ./services/admin_panel
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
      - JWT_SECRET_KEY=${JWT_SECRET_KEY}
      - ENCRYPTION_KEY=${ENCRYPTION_KEY}
      - DOCKER_HOST=unix:///var/run/docker.sock
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./shared:/app/shared
    networks:
      - instatg_network
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  telegram_bot:
    build: ./services/telegram_bot
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - ENCRYPTION_KEY=${ENCRYPTION_KEY}
    volumes:
      - ./shared:/app/shared
    networks:
      - instatg_network
    depends_on:
      - postgres
      - redis
    restart: unless-stopped

  notification_service:
    build: ./services/notification_service
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - ADMIN_TELEGRAM_CHAT_ID=${ADMIN_TELEGRAM_CHAT_ID}
    volumes:
      - ./shared:/app/shared
    networks:
      - instatg_network
    depends_on:
      - postgres
      - redis
    restart: unless-stopped

  nginx:
    build: ./nginx
    ports:
      - "80:80"
    networks:
      - instatg_network
    depends_on:
      - admin_panel
    restart: unless-stopped

  # Динамические сервисы запускаются через Admin Panel:
  # - browser_service_* (по одному на аккаунт)
  # - instagram_watcher_* (по одному на аккаунт)
  # - media_downloader_* (один или несколько воркеров)
  # - media_sender_* (один или несколько воркеров)
```

---

## ЗАПУСК ДИНАМИЧЕСКИХ СЕРВИСОВ

Admin Panel запускает динамические сервисы через Docker SDK:

```python
# docker_manager.py — пример запуска Watcher
import docker

client = docker.from_env()

def start_watcher(account_id: str, instance_name: str):
    container = client.containers.run(
        image="instatg-watcher:latest",
        name=f"watcher_{instance_name}",
        environment={
            "DATABASE_URL": settings.DATABASE_URL,
            "REDIS_URL": settings.REDIS_URL,
            "INSTAGRAM_ACCOUNT_ID": account_id,
            "ENCRYPTION_KEY": settings.ENCRYPTION_KEY,
        },
        network="instatg_network",
        volumes={"./shared": {"bind": "/app/shared", "mode": "ro"}},
        detach=True,
        restart_policy={"Name": "unless-stopped"}
    )
    return container.id

def stop_container(container_id: str):
    container = client.containers.get(container_id)
    container.stop()
    container.remove()
```
# INSTATG BOT — Project Bible
# Часть 5: Безопасность, правила кода и важные детали

---

## БЕЗОПАСНОСТЬ

### Брутфорс защита (admin_panel/middleware/brute_force.py)

```python
# Логика блокировки по IP:
# Попытка 1,2 → ничего
# Попытка 3   → block_duration = 60 сек
# Попытка 4   → block_duration = 120 сек
# Попытка 5   → block_duration = 240 сек
# Попытка N   → block_duration = 60 * (2 ** (N-3)) сек

# В таблице brute_force_log:
# - ip_address (UNIQUE индекс)
# - failed_attempts (счётчик)
# - blocked_until (timestamp)
# - block_duration_sec (текущая длительность блока)

# При каждом запросе на /login:
# 1. Найти запись по IP
# 2. Если blocked_until > NOW() → вернуть 429 с оставшимися секундами
# 3. Если логин провален → failed_attempts += 1
#    Если failed_attempts >= 3 → вычислить block_duration, установить blocked_until
# 4. Если логин успешен → удалить запись по IP

# Ответ при блокировке:
# {"detail": "Too many attempts", "retry_after_seconds": 120}

# На фронтенде (login.html):
# Показать таймер: "Следующая попытка через {X} сек"
# Обновлять каждую секунду через setInterval
# Кнопка "Войти" задизейблена пока таймер > 0
```

### Шифрование паролей Instagram (shared/encryption.py)

```python
# Использовать: from cryptography.fernet import Fernet
# ENCRYPTION_KEY из .env (32 байта, base64)
# 
# def encrypt(text: str) -> str  - шифрует, возвращает строку
# def decrypt(text: str) -> str  - дешифрует, возвращает строку
#
# Применять для полей:
# - instagram_accounts.password
# - instagram_accounts.cookies
# - instagram_accounts.session_data
```

### JWT токены (admin_panel/routers/auth.py)

```python
# Access token: 15 минут, в заголовке Authorization: Bearer {token}
# Refresh token: 7 дней, в httpOnly cookie
# 
# При каждом запросе к защищённым эндпоинтам:
# - Проверить access token
# - Если истёк → использовать refresh token для обновления
# - Если оба истекли → редирект на /login
#
# Payload JWT:
# {"sub": staff_id, "role": "admin"/"staff", "exp": timestamp}
```

### Одноразовые токены привязки (instagram_watcher/token_generator.py)

```python
# bind_token = str(uuid.uuid4())  - случайный UUID
# bind_token_expires_at = datetime.now() + timedelta(hours=24)
# 
# После использования:
# UPDATE users SET bind_token=NULL, bind_token_expires_at=NULL
#
# Проверка при /start {token}:
# WHERE bind_token = token AND bind_token_expires_at > NOW()
# Если не найден → "Ссылка недействительна или истекла. 
#                   Отправьте контент в директ снова."
```

### Сетевая изоляция Docker

```
Снаружи доступен ТОЛЬКО порт 80 (Nginx)
Nginx проксирует только на admin_panel:8000

PostgreSQL: доступен только внутри Docker network
Redis:      доступен только внутри Docker network
Сервисы:    общаются только через Docker network instatg_network
```

---

## ПАРАЛЛЕЛЬНАЯ БЕЗОПАСНОСТЬ (FOR UPDATE SKIP LOCKED)

### Применять в: media_downloader/worker.py и media_sender/worker.py

```sql
-- Правильный способ взять задачи без конфликта между воркерами:
SELECT * FROM content_queue
WHERE status = 'pending'
ORDER BY created_at ASC
LIMIT 5
FOR UPDATE SKIP LOCKED;

-- SKIP LOCKED означает: если запись заблокирована другим воркером,
-- просто пропустить её и взять следующую.
-- Два воркера НИКОГДА не возьмут одну и ту же запись.
```

### В SQLAlchemy 2.x:
```python
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

stmt = (
    select(ContentQueue)
    .where(ContentQueue.status == 'pending')
    .order_by(ContentQueue.created_at.asc())
    .limit(5)
    .with_for_update(skip_locked=True)
)
```

---

## INSTAGRAM — ВАЖНЫЕ ПРАВИЛА

### НЕ делать (чтобы не забанили аккаунт):

```
❌ НЕ обновлять страницу для получения сообщений
❌ НЕ делать запросы слишком часто (< 2 сек между действиями)
❌ НЕ использовать официальный Instagram API (нарушение ToS)
❌ НЕ запускать несколько браузеров на одном аккаунте
❌ НЕ менять IP во время активной сессии
```

### ДЕЛАТЬ:

```
✅ Слушать WebSocket/XHR события (они приходят сами)
✅ Случайные задержки между действиями: random.uniform(2.0, 8.0) сек
✅ Сохранять cookies в БД после каждого действия
✅ Использовать прокси (1 аккаунт = 1 прокси)
✅ Имитировать движение мыши (Playwright mouse.move)
✅ При перезапуске — восстанавливать сессию из cookies
✅ НЕ логиниться заново если cookies живые
```

### Определение блокировки аккаунта:
```python
# Признаки блокировки в браузере:
# - URL содержит /challenge/
# - URL содержит /accounts/login/
# - Появился элемент с текстом "suspicious login"
# - HTTP статус 401 на API запросах
#
# При обнаружении:
# UPDATE instagram_accounts SET status='blocked'
# Уведомить админа
```

---

## TELEGRAM — ВАЖНЫЕ ПРАВИЛА

### Ограничения Telegram Bot API:

```
Максимальный размер файла через бота: 50 МБ для send_video
Максимальный размер через file_id:    нет ограничений (уже на серверах TG)
Максимум в медиа-группе (альбом):     10 элементов

Поэтому:
1. Сначала загружаем файл в группу-хранилище → получаем file_id
2. Пользователям отправляем через file_id (без ограничений размера)
3. Видео >1GB — не скачиваем (system_settings: max_video_size_mb)
```

### Медиа-группа (карусель):
```python
from aiogram.types import InputMediaVideo, InputMediaPhoto

media = []
for item in carousel_items:
    if item.type == 'video':
        media.append(InputMediaVideo(media=file_id_video))
    else:
        media.append(InputMediaPhoto(media=file_id_photo))

await bot.send_media_group(chat_id=user.telegram_chat_id, media=media)
```

### Структура telegram_storage_groups:
```
Бот должен быть АДМИНИСТРАТОРОМ в группе хранилище.
Проверять это при добавлении группы в список.
```

---

## ЛОКАЛИЗАЦИЯ (i18n)

### Формат файлов shared/i18n/{язык}.json:

```json
{
  "welcome": "Привет! Подпишитесь на @{account} в Instagram...",
  "bind_success": "✅ Telegram успешно привязан!\nПробный период: {days} дней",
  "bind_invalid": "❌ Ссылка недействительна или истекла.\nОтправьте контент в директ снова.",
  "language_select": "Выберите язык / Tilni tanlang / Choose language:",
  "status_active": "✅ Подписка активна до {date}",
  "status_trial": "🎁 Пробный период до {date} ({days} дней осталось)",
  "status_expired": "⏰ Подписка истекла. Лимит сегодня: {used}/{limit}",
  "status_blocked": "🚫 Аккаунт заблокирован. Обратитесь к администратору.",
  "story_expired": "😔 К сожалению, Stories от @{username} уже недоступны (истекли 24ч). Извините!",
  "limit_reached": "⚠️ Дневной лимит исчерпан ({limit} файлов). Подождите до завтра.",
  "subscription_remind": "⏰ Подписка истекает через {days} дней ({date}). Обратитесь к администратору.",
  "subscription_expired": "📭 Ваша подписка истекла. Теперь лимит: {limit} файлов в день.",
  "account_changed": "📢 Наш Instagram аккаунт изменён! Подпишитесь: @{new_account}",
  "file_too_large": "⚠️ Файл слишком большой (>1GB). Попробуйте другой контент.",
  "help": "ℹ️ Как пользоваться:\n1. Подпишитесь на @{account}\n2. Отправьте любой контент в директ\n3. Получите его здесь в Telegram"
}
```

### Использование:
```python
# i18n_helper.py
import json, os

_translations = {}

def get_text(key: str, language: str, **kwargs) -> str:
    if language not in _translations:
        path = f"/app/shared/i18n/{language}.json"
        if not os.path.exists(path):
            language = 'ru'  # fallback
        with open(f"/app/shared/i18n/{language}.json") as f:
            _translations[language] = json.load(f)
    
    text = _translations[language].get(key, _translations['ru'].get(key, key))
    return text.format(**kwargs)
```

---

## СИСТЕМА МЕТРИК ДЛЯ ДАШБОРДА

### Эндпоинты admin_panel/routers/dashboard.py:

```python
GET /api/metrics/summary
→ {
    queue_pending: int,
    queue_no_telegram: int,
    queue_failed: int,
    downloaded_today: int,
    mb_downloaded_today: float,
    active_users: int,
    new_users_today: int,
    new_users_week: int,
    new_users_month: int
  }

GET /api/metrics/chart?period=7d|30d|90d
→ {
    labels: ["Пн", "Вт", ...],
    downloads: [34, 67, ...],
    new_users: [3, 8, ...]
  }

GET /api/services/status
→ [
    {
      id: uuid,
      name: "watcher_main",
      type: "watcher",
      status: "running",
      last_heartbeat: "2025-05-04T10:00:00",
      is_alive: true  // last_heartbeat < 5 мин назад
    },
    ...
  ]
```

---

## ВАЖНЫЕ ДЕТАЛИ РЕАЛИЗАЦИИ

### 1. Heartbeat сервисов:
Каждый сервис раз в 60 секунд:
```python
UPDATE service_instances
SET last_heartbeat_at = NOW(), current_queue_size = {count}
WHERE instance_name = {self.instance_name}
```

### 2. Позиция начала скачивания (Downloader):
```python
if position == 'beginning':
    ORDER BY created_at ASC
elif position == 'end':
    ORDER BY created_at DESC
elif position == 'middle':
    total = COUNT(*)
    OFFSET total // 2
    ORDER BY created_at ASC
```

### 3. Проверка прокси:
```python
# При добавлении прокси в список:
import requests
response = requests.get(
    'https://api.ipify.org?format=json',
    proxies={'http': proxy_url, 'https': proxy_url},
    timeout=10
)
# Если ответ получен → прокси рабочий
```

### 4. Сохранение cookies Playwright:
```python
# После логина или периодически:
cookies = await context.cookies()
cookies_json = json.dumps(cookies)
encrypted = encrypt(cookies_json)
# UPDATE instagram_accounts SET cookies = encrypted

# При загрузке:
encrypted = account.cookies
cookies_json = decrypt(encrypted)
cookies = json.loads(cookies_json)
await context.add_cookies(cookies)
```

### 5. Уведомление всех пользователей о смене аккаунта:
```python
# Только если notify_users_on_block = TRUE в instagram_accounts
users = SELECT * FROM users 
        WHERE telegram_chat_id IS NOT NULL
        AND subscription_status NOT IN ('blocked')

for user in users:
    text = get_text('account_changed', user.language, 
                    new_account=new_account.username)
    await bot.send_message(user.telegram_chat_id, text)
    # Задержка между отправками: asyncio.sleep(0.05)
    # Иначе Telegram заблокирует за спам
```

### 6. Логирование:
```python
# shared/logger.py
# Единый формат для всех сервисов:
# [2025-05-04 10:00:00] [SERVICE_NAME] [LEVEL] message

import logging
def get_logger(service_name: str) -> logging.Logger:
    logger = logging.getLogger(service_name)
    # Настроить formatter, handler
    return logger
```

### 7. Staff action logs:
```python
# При любом действии staff/admin над пользователем:
INSERT INTO staff_action_logs (
    staff_id = current_user.id,
    action = "Изменена подписка",
    target_type = "user",
    target_id = user.id,
    old_value = "expired",
    new_value = "active (30 дней)"
)
```
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
