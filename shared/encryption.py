from cryptography.fernet import Fernet, InvalidToken
from shared.config import settings


def encrypt(text: str) -> str:
    """
    Шифрует текст с помощью AES-256 (Fernet).
    Возвращает зашифрованный текст в base64.
    """
    f = Fernet(settings.encryption_key.encode())
    encrypted = f.encrypt(text.encode())
    return encrypted.decode()


def decrypt(encrypted_text: str) -> str:
    """
    Расшифровывает текст с помощью AES-256 (Fernet).
    Возвращает исходный текст.
    Вызывает ValueError при неверном ключе или повреждённых данных.
    """
    try:
        f = Fernet(settings.encryption_key.encode())
        decrypted = f.decrypt(encrypted_text.encode())
        return decrypted.decode()
    except InvalidToken:
        raise ValueError("Invalid encryption key or corrupted data")