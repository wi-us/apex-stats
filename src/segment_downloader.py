"""
Модуль для скачивания MP2T сегментов.
Поддерживает retry логику и прогресс-бар.
"""

import time
import logging
import requests
from pathlib import Path

logger = logging.getLogger(__name__)


class SegmentDownloader:
    """
    Класс для скачивания видео сегментов.
    """
    
    def __init__(self, output_dir="segments"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.downloaded_files = []
        
    def download(self, segment_urls, cookies_dict, max_retries=3, retry_delay=2):
        """
        Скачивает все сегменты.
        
        Args:
            segment_urls: List of segment info dicts
            cookies_dict: Dict с cookies для авторизации
            max_retries: Максимальное количество попыток для каждого сегмента
            retry_delay: Задержка между попытками
            
        Returns:
            List of Path objects для скачанных файлов
        """
        if not segment_urls:
            logger.warning("Нет сегментов для скачивания")
            return []
        
        logger.info("")
        logger.info("=" * 60)
        logger.info("СКАЧИВАНИЕ СЕГМЕНТОВ")
        logger.info("=" * 60)
        logger.info(f"Всего сегментов: {len(segment_urls)}")
        logger.info("")
        
        # Подготавливаем headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://www.faceit.com/',
            'Origin': 'https://www.faceit.com',
            'Accept': '*/*'
        }
        
        # Добавляем cookies
        if cookies_dict:
            cookie_str = '; '.join([f"{k}={v}" for k, v in cookies_dict.items()])
            headers['Cookie'] = cookie_str
            logger.info(f"Cookies добавлены: {len(cookies_dict)} шт.")
        
        downloaded_files = []
        failed_segments = []
        
        for i, segment_info in enumerate(segment_urls, 1):
            url = segment_info['url']
            filename = segment_info['filename']
            output_path = self.output_dir / filename
            
            # Пропускаем если уже скачан
            if output_path.exists():
                logger.debug(f"[{i:03d}] {filename} - уже существует, пропуск")
                downloaded_files.append(output_path)
                continue
            
            # Пытаемся скачать с retry
            success = False
            for attempt in range(max_retries):
                try:
                    response = requests.get(url, headers=headers, timeout=30)
                    response.raise_for_status()
                    
                    # Сохраняем
                    with open(output_path, 'wb') as f:
                        f.write(response.content)
                    
                    downloaded_files.append(output_path)
                    success = True
                    
                    # Логируем прогресс каждые 10 файлов
                    if i % 10 == 0 or i == len(segment_urls):
                        progress_pct = i / len(segment_urls) * 100
                        logger.info(f"Скачано: {i}/{len(segment_urls)} ({progress_pct:.1f}%)")
                    
                    break
                    
                except Exception as e:
                    if attempt < max_retries - 1:
                        logger.debug(f"[{i:03d}] {filename} - ошибка, retry {attempt + 1}/{max_retries}")
                        time.sleep(retry_delay)
                    else:
                        logger.error(f"[{i:03d}] {filename} - не удалось скачать после {max_retries} попыток: {e}")
                        failed_segments.append({'index': i, 'filename': filename, 'error': str(e)})
            
            if not success:
                continue
        
        logger.info("")
        logger.info(f"Успешно скачано: {len(downloaded_files)}/{len(segment_urls)}")
        
        if failed_segments:
            logger.warning(f"Не удалось скачать: {len(failed_segments)} сегментов")
            for failed in failed_segments[:5]:
                logger.warning(f"  - {failed['filename']}: {failed['error']}")
            if len(failed_segments) > 5:
                logger.warning(f"  ... и еще {len(failed_segments) - 5} сегментов")
        
        self.downloaded_files = downloaded_files
        return downloaded_files
    
    def get_downloaded_files(self):
        """
        Возвращает список скачанных файлов.
        
        Returns:
            List of Path objects
        """
        return self.downloaded_files
