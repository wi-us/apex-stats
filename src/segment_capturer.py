"""
Модуль для перехвата MP2T сегментов из Network через CDP.
Фильтрует запросы по критериям: URL содержит 'index_' и Content-Type: video/MP2T.
"""

import time
import json
import logging
from pathlib import Path
from collections import deque

logger = logging.getLogger(__name__)


class SegmentCapturer:
    """
    Класс для перехвата видео сегментов из Performance logs браузера.
    """
    
    def __init__(self, output_dir="segments"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.segments = []
        
    def capture(self, driver, duration_seconds, progress_interval=10):
        """
        Перехватывает MP2T сегменты из Network logs через CDP.
        
        Args:
            driver: Selenium WebDriver
            duration_seconds: Длительность перехвата в секундах
            progress_interval: Интервал логирования прогресса
            
        Returns:
            List of segment info dicts
        """
        logger.info("")
        logger.info("=" * 60)
        logger.info("НАЧАЛО ПЕРЕХВАТА MP2T СЕГМЕНТОВ")
        logger.info("=" * 60)
        logger.info(f"Длительность: {duration_seconds}с")
        logger.info("")
        
        # Включаем Network через CDP
        try:
            driver.execute_cdp_cmd('Network.enable', {})
            logger.info("[OK] Network monitoring включен через CDP")
            logger.info("")
        except Exception as e:
            logger.error(f"Ошибка включения Network: {e}")
            logger.error("Перезапустите Chrome с параметром --enable-logging")
            return []
        
        segment_urls = []
        seen_urls = set()
        start_time = time.time()
        last_log_time = start_time
        request_map = {}  # Для связи requestId с URL
        
        while time.time() - start_time < duration_seconds:
            try:
                # Получаем Performance logs
                try:
                    logs = driver.get_log('performance')
                except Exception as e:
                    # Performance logs недоступны - используем polling через CDP
                    logger.warning(f"Performance logs недоступны: {e}")
                    logger.info("Используйте мониторинг Network в DevTools вручную")
                    time.sleep(1)
                    continue
                
                for entry in logs:
                    try:
                        message = json.loads(entry['message'])['message']
                        method = message.get('method', '')
                        
                        # Ищем Network.responseReceived
                        if method == 'Network.responseReceived':
                            params = message.get('params', {})
                            response = params.get('response', {})
                            url = response.get('url', '')
                            mime_type = response.get('mimeType', '')
                            headers = response.get('headers', {})
                            content_type = headers.get('Content-Type', headers.get('content-type', ''))
                            
                            # Фильтруем по критериям:
                            # 1. URL содержит "index_"
                            # 2. Content-Type содержит "video/MP2T" или "video/mp2t"
                            if self._is_mp2t_segment(url, content_type, mime_type):
                                if url not in seen_urls:
                                    seen_urls.add(url)
                                    
                                    filename = self._extract_filename(url)
                                    
                                    segment_info = {
                                        'url': url,
                                        'filename': filename,
                                        'content_type': content_type,
                                        'mime_type': mime_type,
                                        'timestamp': time.time() - start_time
                                    }
                                    
                                    segment_urls.append(segment_info)
                                    logger.info(f"[{len(segment_urls):03d}] {filename}")
                    
                    except Exception as e:
                        continue
                
                # Логируем прогресс
                if time.time() - last_log_time >= progress_interval:
                    elapsed = int(time.time() - start_time)
                    logger.info(f">>> Прогресс: {elapsed}/{duration_seconds}с, сегментов: {len(segment_urls)}")
                    last_log_time = time.time()
                
                time.sleep(0.5)
                
            except Exception as e:
                logger.warning(f"Ошибка чтения logs: {e}")
                time.sleep(2)
        
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"ПЕРЕХВАТ ЗАВЕРШЕН: {len(segment_urls)} сегментов")
        logger.info("=" * 60)
        
        # Сохраняем список сегментов
        if segment_urls:
            self._save_segments_list(segment_urls)
        
        self.segments = segment_urls
        return segment_urls
    
    def _is_mp2t_segment(self, url, content_type, mime_type):
        """
        Проверяет является ли запрос MP2T сегментом.
        
        Args:
            url: URL запроса
            content_type: Content-Type из headers
            mime_type: MIME type из response
            
        Returns:
            True если это MP2T сегмент
        """
        # Проверяем наличие "index_" в URL
        if 'index_' not in url:
            return False
        
        # Проверяем Content-Type
        content_lower = content_type.lower()
        mime_lower = mime_type.lower()
        
        if 'video/mp2t' in content_lower or 'video/mp2t' in mime_lower:
            return True
        
        # Дополнительная проверка по расширению
        if url.endswith('.ts') or '.ts?' in url:
            return True
        
        return False
    
    def _extract_filename(self, url):
        """
        Извлекает имя файла из URL.
        
        Args:
            url: URL сегмента
            
        Returns:
            Имя файла
        """
        try:
            # Берем последнюю часть URL до параметров
            filename = url.split('/')[-1].split('?')[0]
            
            # Если нет расширения .ts, добавляем
            if not filename.endswith('.ts'):
                filename += '.ts'
            
            return filename
        except:
            return f"segment_{int(time.time())}.ts"
    
    def _save_segments_list(self, segment_urls):
        """
        Сохраняет список сегментов в JSON файл.
        
        Args:
            segment_urls: List of segment info dicts
        """
        try:
            segments_json = self.output_dir / "segments_list.json"
            
            with open(segments_json, 'w', encoding='utf-8') as f:
                json.dump(segment_urls, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Список сегментов сохранен: {segments_json}")
            
        except Exception as e:
            logger.warning(f"Не удалось сохранить список сегментов: {e}")
    
    def get_segments(self):
        """
        Возвращает список перехваченных сегментов.
        
        Returns:
            List of segment info dicts
        """
        return self.segments
