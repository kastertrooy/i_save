import logging
import logging.handlers
from pathlib import Path


def get_logger(service_name: str) -> logging.Logger:
    """
    Возвращает настроенный логгер для указанного сервиса.
    Логгер выводит в консоль (INFO+) и в файл с ротацией (DEBUG+).
    Формат: [2025-05-04 10:00:00] [SERVICE_NAME] [LEVEL] message
    """
    logger = logging.getLogger(service_name)
    if logger.hasHandlers():
        return logger  # Уже настроен

    logger.setLevel(logging.DEBUG)

    # Форматтер
    formatter = logging.Formatter(
        '[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Обработчик для консоли (INFO и выше)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Обработчик для файла с ротацией (DEBUG и выше)
    log_dir = Path('logs')
    log_dir.mkdir(exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / f'{service_name}.log',
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger