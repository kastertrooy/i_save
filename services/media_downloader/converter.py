import subprocess
from pathlib import Path
from typing import List

from shared.logger import get_logger

logger = get_logger('media_downloader')


def extract_audio(video_path: str) -> str:
    """
    Извлекает аудио из видео через ffmpeg и сохраняет как {video_path}_audio.mp3.
    """
    video_file = Path(video_path)
    if not video_file.exists():
        raise FileNotFoundError(f'Video file not found: {video_path}')

    audio_file = video_file.with_name(f'{video_file.name}_audio.mp3')
    command = [
        'ffmpeg',
        '-y',
        '-i',
        str(video_file),
        '-vn',
        '-acodec',
        'libmp3lame',
        '-q:a',
        '2',
        str(audio_file),
    ]

    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode != 0:
        logger.error('FFmpeg conversion failed for %s: %s', video_path, process.stderr)
        raise RuntimeError(f'FFmpeg conversion failed: {process.stderr}')

    logger.info('Extracted audio to %s', audio_file)
    return str(audio_file)


def cleanup_files(paths: List[str]) -> None:
    """
    Удаляет временные файлы после отправки.
    """
    for path in paths:
        try:
            file_path = Path(path)
            if file_path.exists():
                file_path.unlink()
                logger.info('Deleted temporary file %s', file_path)
        except Exception as exc:
            logger.warning('Failed to remove temporary file %s: %s', path, exc)
