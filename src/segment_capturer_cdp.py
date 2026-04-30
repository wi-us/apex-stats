"""
Модуль для перехвата MP2T сегментов через CDP Network events.
Использует прямой мониторинг Network.responseReceived events.
"""

import time
import json
import logging
from pathlib import Path
from threading import Thread, Event as ThreadEvent
from queue import Queue

logger = logging.getLogger(__name__)


class SegmentCapturerCDP:
    """
    Класс для перехвата видео сегментов через CDP Network monitoring.
    """
    
    def __init__(self, output_dir="segments"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.segments = []
        self.seen_urls = set()
        self.stop_event = ThreadEvent()
        self.segment_queue = Queue()
        
    def capture(self, driver, duration_seconds, progress_interval=10):
        """
        Перехватывает MP2T сегменты через мониторинг Network.
        
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
            logger.info("[OK] Network monitoring включен")
            logger.info("")
        except Exception as e:
            logger.error(f"Ошибка включения Network: {e}")
            return []
        
        # Используем JavaScript для мониторинга fetch/XHR
        logger.info("Установка JavaScript перехватчика...")
        
        try:
            driver.execute_script("""
                window.__mp2t_segments = window.__mp2t_segments || [];
                
                // Перехватываем fetch
                const originalFetch = window.fetch;
                window.fetch = function(...args) {
                    const url = args[0];
                    
                    return originalFetch.apply(this, args).then(response => {
                        const contentType = response.headers.get('content-type') || '';
                        
                        if (url.includes('index_') && 
                            (contentType.includes('video/MP2T') || contentType.includes('video/mp2t'))) {
                            window.__mp2t_segments.push({
                                url: url,
                                contentType: contentType,
                                timestamp: Date.now()
                            });
                        }
                        
                        return response;
                    });
                };
                
                // Перехватываем XMLHttpRequest
                const originalOpen = XMLHttpRequest.prototype.open;
                const originalSend = XMLHttpRequest.prototype.send;
                
                XMLHttpRequest.prototype.open = function(method, url) {
                    this._url = url;
                    return originalOpen.apply(this, arguments);
                };
                
                XMLHttpRequest.prototype.send = function() {
                    this.addEventListener('load', function() {
                        const contentType = this.getResponseHeader('content-type') || '';
                        const url = this._url;
                        
                        if (url && url.includes('index_') && 
                            (contentType.includes('video/MP2T') || contentType.includes('video/mp2t'))) {
                            window.__mp2t_segments.push({
                                url: url,
                                contentType: contentType,
                                timestamp: Date.now()
                            });
                        }
                    });
                    
                    return originalSend.apply(this, arguments);
                };
                
                console.log('[MP2T Capturer] JavaScript interceptor installed');
            """)
            
            logger.info("[OK] JavaScript перехватчик установлен")
            logger.info("")
            
        except Exception as e:
            logger.error(f"Ошибка установки перехватчика: {e}")
            return []
        
        # Мониторинг
        segment_urls = []
        start_time = time.time()
        last_log_time = start_time
        last_count = 0
        
        logger.info("Мониторинг сегментов...")
        logger.info("")
        
        while time.time() - start_time < duration_seconds:
            try:
                # Получаем захваченные сегменты
                captured = driver.execute_script("return window.__mp2t_segments || [];")
                
                # Добавляем новые
                for seg in captured:
                    url = seg.get('url', '')
                    if url and url not in self.seen_urls:
                        self.seen_urls.add(url)
                        
                        filename = self._extract_filename(url)
                        
                        segment_info = {
                            'url': url,
                            'filename': filename,
                            'content_type': seg.get('contentType', ''),
                            'timestamp': seg.get('timestamp', 0)
                        }
                        
                        segment_urls.append(segment_info)
                        self.segments.append(segment_info)
                
                # Логирование прогресса
                current_time = time.time()
                elapsed = int(current_time - start_time)
                
                if current_time - last_log_time >= progress_interval:
                    new_count = len(segment_urls) - last_count
                    logger.info(f"[{elapsed:03d}s] Захвачено: {len(segment_urls)} сегментов (+{new_count} новых)")
                    last_log_time = current_time
                    last_count = len(segment_urls)
                
                time.sleep(0.5)
                
            except Exception as e:
                logger.warning(f"Ошибка чтения сегментов: {e}")
                time.sleep(1)
        
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"ПЕРЕХВАТ ЗАВЕРШЕН: {len(segment_urls)} сегментов")
        logger.info("=" * 60)
        logger.info("")
        
        # Сохраняем список
        if segment_urls:
            self._save_segments_list(segment_urls)
        
        return segment_urls
    
    def _extract_filename(self, url):
        """Извлекает имя файла из URL."""
        try:
            # Ищем index_NNNN.ts в URL
            if 'index_' in url:
                parts = url.split('/')
                for part in reversed(parts):
                    if 'index_' in part and '.ts' in part:
                        # Убираем query parameters
                        return part.split('?')[0]
            
            # Fallback - последняя часть URL
            filename = url.split('/')[-1].split('?')[0]
            if not filename.endswith('.ts'):
                filename += '.ts'
            return filename
        except:
            return f"segment_{int(time.time())}.ts"
    
    def _save_segments_list(self, segment_urls):
        """Сохраняет список сегментов в JSON файл."""
        try:
            segments_json = self.output_dir / "segments_list.json"
            
            with open(segments_json, 'w', encoding='utf-8') as f:
                json.dump(segment_urls, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Список сегментов сохранен: {segments_json}")
            
        except Exception as e:
            logger.warning(f"Не удалось сохранить список сегментов: {e}")
    
    def get_segments(self):
        """Возвращает список перехваченных сегментов."""
        return self.segments