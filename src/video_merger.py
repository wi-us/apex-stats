"""
Модуль для склейки MP2T сегментов в один MP4 файл через ffmpeg.
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class VideoMerger:
    """
    Класс для склейки видео сегментов через ffmpeg.
    """
    
    def __init__(self, segments_dir="segments"):
        self.segments_dir = Path(segments_dir)
        
    def merge(self, segment_files, output_file, timeout=300):
        """
        Склеивает .ts сегменты в один .mp4 файл.
        
        Args:
            segment_files: List of Path objects с путями к сегментам
            output_file: Путь к выходному .mp4 файлу
            timeout: Таймаут выполнения ffmpeg в секундах
            
        Returns:
            True если успешно
        """
        if not segment_files:
            logger.error("Нет сегментов для склейки")
            return False
        
        logger.info("")
        logger.info("=" * 60)
        logger.info("СКЛЕЙКА СЕГМЕНТОВ В MP4")
        logger.info("=" * 60)
        logger.info(f"Сегментов: {len(segment_files)}")
        logger.info(f"Выходной файл: {output_file}")
        logger.info("")
        
        # Создаем filelist.txt для ffmpeg
        filelist_path = self.segments_dir / "filelist.txt"
        
        try:
            with open(filelist_path, 'w', encoding='utf-8') as f:
                for segment_file in sorted(segment_files, key=lambda x: x.name):
                    # ffmpeg требует формат: file 'path'
                    f.write(f"file '{segment_file.absolute()}'\n")
            
            logger.info(f"Создан список файлов: {filelist_path}")
            
        except Exception as e:
            logger.error(f"Ошибка создания filelist.txt: {e}")
            return False
        
        # Запускаем ffmpeg
        cmd = [
            'ffmpeg',
            '-f', 'concat',      # Формат concat для склейки
            '-safe', '0',        # Разрешить абсолютные пути
            '-i', str(filelist_path),
            '-c', 'copy',        # Копировать без перекодирования
            '-y',                # Перезаписать если существует
            output_file
        ]
        
        logger.info("Запуск ffmpeg...")
        logger.debug(f"Команда: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            if result.returncode == 0:
                # Проверяем результат
                output_path = Path(output_file)
                if output_path.exists():
                    size_mb = output_path.stat().st_size / (1024 * 1024)
                    
                    logger.info("")
                    logger.info("=" * 60)
                    logger.info("[УСПЕХ] Видео создано!")
                    logger.info("=" * 60)
                    logger.info(f"Файл: {output_file}")
                    logger.info(f"Размер: {size_mb:.2f} МБ")
                    logger.info(f"Сегментов: {len(segment_files)}")
                    logger.info("")
                    logger.info(f"Для просмотра: python play_video.py {output_file}")
                    logger.info("=" * 60)
                    
                    return True
                else:
                    logger.error("Файл не создан, хотя ffmpeg завершился успешно")
                    return False
            else:
                logger.error(f"ffmpeg завершился с ошибкой: код {result.returncode}")
                logger.error(f"stderr: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error(f"ffmpeg превысил таймаут ({timeout}с)")
            return False
            
        except FileNotFoundError:
            logger.error("ffmpeg не найден в системе")
            logger.error("")
            logger.error("Установите ffmpeg:")
            logger.error("  1. Скачайте с https://ffmpeg.org/download.html")
            logger.error("  2. Добавьте в PATH")
            logger.error("  3. Проверьте: ffmpeg -version")
            return False
            
        except Exception as e:
            logger.error(f"Ошибка запуска ffmpeg: {e}")
            return False
