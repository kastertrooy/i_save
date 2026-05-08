from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    instagram_id = Column(String, nullable=False)
    telegram_chat_id = Column(Integer, nullable=True)
    language = Column(String(2), nullable=False)  # ru, uz, en
    subscription_status = Column(String, nullable=False)
    daily_limit = Column(Integer, nullable=False)
    bind_token = Column(String, nullable=True)
    bind_token_expires_at = Column(DateTime, nullable=True)
    free_trial_used = Column(Boolean, default=False)
    subscription_until = Column(DateTime, nullable=True)
    telegram_username = Column(String, nullable=True)
    daily_downloads_today = Column(Integer, default=0)
    daily_downloads_updated_at = Column(DateTime, nullable=True)


class ContentQueue(Base):
    __tablename__ = 'content_queue'

    id = Column(Integer, primary_key=True)
    instagram_id = Column(String, nullable=False)
    url = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    carousel_urls = Column(JSON, nullable=True)  # list of strings
    status = Column(String, nullable=False)
    retry_count = Column(Integer, default=0)


class MediaCache(Base):
    __tablename__ = 'media_cache'

    id = Column(Integer, primary_key=True)
    original_url = Column(String, nullable=False)
    telegram_file_id_video = Column(String, nullable=True)
    telegram_file_id_audio = Column(String, nullable=True)
    telegram_file_id_photo = Column(String, nullable=True)
    storage_group_id = Column(Integer, ForeignKey('telegram_storage_groups.id'), nullable=False)


class InstagramAccount(Base):
    __tablename__ = 'instagram_accounts'

    id = Column(Integer, primary_key=True)
    username = Column(String, nullable=False)
    password = Column(String, nullable=False)  # encrypted AES-256
    cookies = Column(Text, nullable=True)  # encrypted AES-256
    session_data = Column(Text, nullable=True)  # encrypted AES-256
    proxy_id = Column(Integer, ForeignKey('proxy_list.id'), nullable=True)
    status = Column(String, nullable=False)
    notify_users_on_block = Column(Boolean, default=False)
    is_primary = Column(Boolean, default=False)


class Proxy(Base):
    __tablename__ = 'proxy_list'

    id = Column(Integer, primary_key=True)
    host = Column(String, nullable=False)
    port = Column(Integer, nullable=False)
    username = Column(String, nullable=True)
    password = Column(String, nullable=True)
    protocol = Column(String, nullable=False)
    is_working = Column(Boolean, default=False)


class TelegramStorageGroup(Base):
    __tablename__ = 'telegram_storage_groups'

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    telegram_group_id = Column(Integer, nullable=False)


class StaffAccount(Base):
    __tablename__ = 'staff_accounts'

    id = Column(Integer, primary_key=True)
    username = Column(String, nullable=False, unique=True)
    password_hash = Column(String, nullable=False)  # bcrypt
    role = Column(String, nullable=False)


class BruteForceLog(Base):
    __tablename__ = 'brute_force_log'

    id = Column(Integer, primary_key=True)
    ip_address = Column(String, nullable=False)
    failed_attempts = Column(Integer, default=0)
    blocked_until = Column(DateTime, nullable=True)
    block_duration_sec = Column(Integer, nullable=True)


class SubscriptionLog(Base):
    __tablename__ = 'subscription_logs'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    action = Column(String, nullable=False)
    granted_by = Column(String, nullable=True)
    period_days = Column(Integer, nullable=True)


class DeliveryLog(Base):
    __tablename__ = 'delivery_logs'

    id = Column(Integer, primary_key=True)
    content_queue_id = Column(Integer, ForeignKey('content_queue.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    delivery_type = Column(String, nullable=False)
    status = Column(String, nullable=False)


class ServiceInstance(Base):
    __tablename__ = 'service_instances'

    id = Column(Integer, primary_key=True)
    service_type = Column(String, nullable=False)
    instance_name = Column(String, nullable=False)
    status = Column(String, nullable=False)
    container_id = Column(String(100), nullable=True)
    last_heartbeat_at = Column(DateTime, nullable=True)
    queue_start_position = Column(Integer, nullable=True)


class WorkerSchedule(Base):
    __tablename__ = 'worker_schedules'

    id = Column(Integer, primary_key=True)
    trigger_type = Column(String, nullable=False)
    trigger_time = Column(String, nullable=True)
    trigger_queue_size = Column(Integer, nullable=True)


class SystemSetting(Base):
    __tablename__ = 'system_settings'

    id = Column(Integer, primary_key=True)
    key = Column(String, nullable=False, unique=True)
    value = Column(String, nullable=False)


class NotificationLog(Base):
    __tablename__ = 'notification_log'

    id = Column(Integer, primary_key=True)
    recipient_type = Column(String, nullable=False)
    notification_type = Column(String, nullable=False)
    status = Column(String, nullable=False)


class StaffActionLog(Base):
    __tablename__ = 'staff_action_logs'

    id = Column(Integer, primary_key=True)
    staff_id = Column(Integer, ForeignKey('staff_accounts.id'), nullable=False)
    action = Column(String, nullable=False)
    target_type = Column(String, nullable=True)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)