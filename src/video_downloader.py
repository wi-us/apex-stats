"""
Модуль для скачивания видео с FACEIT используя cookie авторизацию.
"""

import logging
import re
import requests
import subprocess
from pathlib import Path
from typing import Optional, Generator, Dict, Any
from urllib.parse import urljoin, urlparse
import json

import ffmpeg

try:
    import yt_dlp
    HAS_YT_DLP = True
except ImportError:
    HAS_YT_DLP = False
    yt_dlp = None

from config.faceit_config import FaceitConfig

# Опциональный импорт браузерной автоматизации
try:
    from src.browser_automation import FaceitBrowserAutomator, BrowserAutomationError
    HAS_BROWSER_AUTOMATION = True
except ImportError:
    HAS_BROWSER_AUTOMATION = False
    FaceitBrowserAutomator = None
    BrowserAutomationError = Exception

try:
    from src.network_analyzer import FaceitNetworkAnalyzer, NetworkAnalysisError
    HAS_NETWORK_ANALYZER = True
except ImportError:
    HAS_NETWORK_ANALYZER = False
    FaceitNetworkAnalyzer = None
    NetworkAnalysisError = Exception

try:
    from src.hybrid_analyzer import FaceitHybridAnalyzer, HybridAnalysisError
    HAS_HYBRID_ANALYZER = True
except ImportError:
    HAS_HYBRID_ANALYZER = False
    FaceitHybridAnalyzer = None
    HybridAnalysisError = Exception

try:
    from src.playwright_analyzer import get_video_url_sync, PlaywrightAnalysisError
    HAS_PLAYWRIGHT_ANALYZER = True
except ImportError:
    HAS_PLAYWRIGHT_ANALYZER = False
    get_video_url_sync = None
    PlaywrightAnalysisError = Exception


logger = logging.getLogger(__name__)


class VideoDownloadError(Exception):
    """Исключение при ошибках скачивания видео."""
    pass


class VideoDownloader:
    """Класс для скачивания видео с FACEIT."""
    
    def __init__(self, config: FaceitConfig):
        """
        Инициализация загрузчика видео.
        
        Args:
            config: Конфигурация FACEIT
        """
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(config.headers)
        self.session.cookies.update(config.cookies)
        
        # Проверка авторизации
        if not config.validate_cookies():
            logger.warning("Не все необходимые cookie установлены. Возможны проблемы с авторизацией.")
            
    def _get_video_manifest_url(self, watch_url: str) -> Optional[str]:
        """
        Получить URL манифеста видео из страницы FACEIT.
        
        Args:
            watch_url: URL страницы просмотра матча
            
        Returns:
            URL манифеста видео или None при ошибке
        """
        try:
            logger.info(f"Запрос страницы: {watch_url}")
            response = self.session.get(watch_url, timeout=30)
            response.raise_for_status()
            
            # Поиск URL видео в HTML или JavaScript
            # FACEIT часто использует JavaScript для загрузки видео
            content = response.text
            
            # Паттерны для поиска URL видео
            patterns = [
                r'"videoUrl":\s*"([^"]+)"',
                r'"streamUrl":\s*"([^"]+)"', 
                r'"manifestUrl":\s*"([^"]+)"',
                r'videoSrc:\s*["\']([^"\']+)["\']',
                r'src:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
                r'https://[^"\s]*\.m3u8[^"\s]*'
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                for match in matches:
                    if match.endswith('.m3u8') or 'stream' in match.lower():
                        # Очистка escape-символов
                        clean_url = match.replace('\\', '')
                        logger.info(f"Найден потенциальный URL видео: {clean_url}")
                        return clean_url
                        
            # Поиск дополнительных API запросов в JavaScript
            api_patterns = [
                r'/api/[^"\s]*video[^"\s]*',
                r'/api/[^"\s]*stream[^"\s]*',
                r'/api/[^"\s]*watch[^"\s]*'
            ]
            
            for pattern in api_patterns:
                matches = re.findall(pattern, content)
                for match in matches:
                    api_url = urljoin(self.config.base_url, match)
                    try:
                        api_response = self.session.get(api_url, timeout=15)
                        if api_response.status_code == 200:
                            api_data = api_response.json()
                            if 'videoUrl' in api_data or 'streamUrl' in api_data:
                                return api_data.get('videoUrl') or api_data.get('streamUrl')
                    except:
                        continue
                        
            logger.error("URL видео не найден на странице")
            return None
            
        except Exception as e:
            logger.error(f"Ошибка получения URL видео: {e}")
            return None
            
    def _get_video_url_with_browser(self, watch_url: str) -> Optional[str]:
        """
        Получить URL видео используя браузерную автоматизацию.
        
        Args:
            watch_url: URL страницы просмотра матча
            
        Returns:
            URL видео или None при ошибке
        """
        if not HAS_BROWSER_AUTOMATION:
            logger.warning("Браузерная автоматизация не доступна. Установите: pip install -r requirements-browser.txt")
            return None
            
        try:
            logger.info("Использование браузерной автоматизации для получения видео URL")
            
            # Создание автоматизатора
            browser_automator = FaceitBrowserAutomator(
                config=self.config,
                headless=True  # Headless режим по умолчанию
            )
            
            # Получение URL через браузер
            video_url = browser_automator.get_video_stream_url(watch_url)
            
            if video_url:
                logger.info(f"Получен URL через браузер: {video_url}")
                return video_url
            else:
                logger.error("Браузерная автоматизация не смогла получить URL видео")
                return None
                
        except BrowserAutomationError as e:
            logger.error(f"Ошибка браузерной автоматизации: {e}")
            return None
        except Exception as e:
            logger.error(f"Неожиданная ошибка в браузерной автоматизации: {e}")
            return None
    
    def _get_video_url_with_network_analyzer(self, watch_url: str) -> Optional[str]:
        """
        Получить URL видео используя анализатор сетевых запросов.
        
        Args:
            watch_url: URL страницы для просмотра
            
        Returns:
            URL лучшего качества видео или None
        """
        if not HAS_NETWORK_ANALYZER:
            logger.error("Сетевой анализатор не доступен. Установите: pip install selenium webdriver-manager")
            return None
            
        try:
            logger.info("Использование анализатора сетевых запросов для получения видео URL")
            
            network_analyzer = FaceitNetworkAnalyzer(self.config, headless=True)
            video_urls = network_analyzer.analyze_faceit_video(watch_url, wait_time=30)
            
            if not video_urls:
                logger.error("Видео URL'ы не найдены в сетевых запросах")
                return None
                
            # Возвращаем URL лучшего качества (первый в отсортированном списке)
            best_video = video_urls[0]
            logger.info(f"Выбран лучший видео URL: {best_video['quality']} - {best_video['url']}")
            
            return best_video['url']
            
        except NetworkAnalysisError as e:
            logger.error(f"Ошибка анализа сетевых запросов: {e}")
            return None
        except Exception as e:
            logger.error(f"Неожиданная ошибка сетевого анализатора: {e}")
            return None

    def _get_video_url_with_hybrid_analyzer(self, watch_url: str) -> Optional[str]:
        """
        Получить URL видео используя гибридный анализатор (рекомендуется).
        
        Args:
            watch_url: URL страницы для просмотра
            
        Returns:
            URL лучшего качества видео или None
        """
        if not HAS_HYBRID_ANALYZER:
            logger.error("Гибридный анализатор не доступен. Установите: pip install selenium webdriver-manager")
            return None
            
        try:
            logger.info("Использование гибридного анализатора для получения видео URL")
            
            hybrid_analyzer = FaceitHybridAnalyzer(self.config, headless=True)
            video_url = hybrid_analyzer.get_best_video_url(watch_url)
            
            if video_url:
                logger.info(f"Гибридный анализатор получил URL: {video_url}")
                return video_url
            else:
                logger.error("Гибридный анализатор не смог получить видео URL")
                return None
                
        except HybridAnalysisError as e:
            logger.error(f"Ошибка гибридного анализа: {e}")
            return None
        except Exception as e:
            logger.error(f"Неожиданная ошибка гибридного анализатора: {e}")
            return None

    def _get_video_url_with_playwright(self, watch_url: str) -> Optional[str]:
        """
        Получить URL видео используя Playwright анализатор (наиболее современный).
        
        Args:
            watch_url: URL страницы для просмотра
            
        Returns:
            URL лучшего качества видео или None
        """
        if not HAS_PLAYWRIGHT_ANALYZER:
            logger.error("Playwright анализатор не доступен. Установите: pip install playwright && playwright install")
            return None
            
        try:
            logger.info("Использование Playwright анализатора для получения видео URL")
            
            video_url = get_video_url_sync(self.config, watch_url, headless=True)
            
            if video_url:
                logger.info(f"Playwright получил URL: {video_url}")
                return video_url
            else:
                logger.error("Playwright анализатор не смог получить видео URL")
                return None
                
        except PlaywrightAnalysisError as e:
            logger.error(f"Ошибка Playwright анализа: {e}")
            return None
        except Exception as e:
            logger.error(f"Неожиданная ошибка Playwright анализатора: {e}")
            return None
            
    def _download_with_requests(self, video_url: str, output_path: Path) -> bool:
        """
        Скачать видео используя requests (для MP4 и других прямых ссылок).
        
        Args:
            video_url: URL видео
            output_path: Путь для сохранения
            
        Returns:
            True при успехе, False при ошибке
        """
        try:
            logger.info(f"Скачивание через requests: {video_url}")
            
            with self.session.get(video_url, stream=True, timeout=30) as response:
                response.raise_for_status()
                
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                
                with open(output_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=self.config.chunk_size):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            
                            if total_size > 0:
                                progress = (downloaded / total_size) * 100
                                if downloaded % (1024 * 1024) == 0:  # Лог каждый MB
                                    logger.info(f"Скачано: {progress:.1f}%")
                                    
            logger.info(f"Видео успешно скачано: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка скачивания через requests: {e}")
            return False
            
    def _download_with_ffmpeg(self, video_url: str, output_path: Path) -> bool:
        """
        Скачать видео используя ffmpeg (для HLS/m3u8 потоков).
        
        Args:
            video_url: URL видео (обычно .m3u8)
            output_path: Путь для сохранения
            
        Returns:
            True при успехе, False при ошибке
        """
        try:
            logger.info(f"Скачивание через ffmpeg: {video_url}")
            
            # Подготовка заголовков для ffmpeg
            headers_str = '\\r\\n'.join([f"{k}: {v}" for k, v in self.config.headers.items()])
            cookies_str = '; '.join([f"{k}={v}" for k, v in self.config.cookies.items() if v])
            
            # Команда ffmpeg с авторизацией
            cmd = [
                'ffmpeg',
                '-headers', headers_str,
                '-cookies', cookies_str,
                '-i', video_url,
                '-c', 'copy',  # Копирование без перекодировки
                '-bsf:a', 'aac_adtstoasc',  # Фикс для AAC аудио
                '-y',  # Перезапись файла
                str(output_path)
            ]
            
            logger.debug(f"Команда ffmpeg: {' '.join(cmd[:6])}... [остальные параметры скрыты]")
            
            # Запуск ffmpeg
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Мониторинг прогресса
            while True:
                output = process.stderr.readline()
                if output == '' and process.poll() is not None:
                    break
                    
                if output and 'time=' in output:
                    # Извлечение времени для отображения прогресса
                    time_match = re.search(r'time=(\d+:\d+:\d+)', output)
                    if time_match:
                        logger.info(f"Обработано: {time_match.group(1)}")
                        
            # Проверка результата
            return_code = process.poll()
            if return_code == 0:
                logger.info(f"Видео успешно скачано через ffmpeg: {output_path}")
                return True
            else:
                stderr = process.stderr.read()
                logger.error(f"Ошибка ffmpeg (код {return_code}): {stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка скачивания через ffmpeg: {e}")
            return False

    def _download_with_ytdlp(self, video_url: str, output_path: Path) -> bool:
        """
        Скачать видео используя yt-dlp (для HLS/DASH потоков).
        
        Args:
            video_url: URL видео (HLS/DASH поток)
            output_path: Путь для сохранения
            
        Returns:
            True если скачивание успешно
        """
        if not HAS_YT_DLP:
            logger.error("yt-dlp не установлен. Установите: pip install yt-dlp")
            return False
            
        try:
            logger.info(f"Скачиваем через yt-dlp: {video_url}")
            
            # Убираем расширение из пути, yt-dlp сам определит формат
            output_template = str(output_path.with_suffix('')) + '.%(ext)s'
            
            # Настройки yt-dlp
            ydl_opts = {
                'outtmpl': output_template,
                'format': 'best[ext=mp4]/best',  # Предпочитаем mp4
                'no_warnings': False,
                'extractaudio': False,
                'audioformat': 'mp4',
                'embed_subs': False,
                'writesubtitles': False,
                'writeautomaticsub': False,
                'ignoreerrors': False,
            }
            
            # Скачивание
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
            
            # Проверяем результат (yt-dlp может изменить расширение)
            possible_files = [
                output_path,
                output_path.with_suffix('.mp4'),
                output_path.with_suffix('.mkv'),
                output_path.with_suffix('.webm')
            ]
            
            for file_path in possible_files:
                if file_path.exists() and file_path.stat().st_size > 1024:  # Больше 1KB
                    logger.info(f"Видео успешно скачано через yt-dlp: {file_path}")
                    
                    # Переименовываем в нужное имя если требуется
                    if file_path != output_path:
                        try:
                            if output_path.exists():
                                output_path.unlink()
                            file_path.rename(output_path)
                            logger.info(f"Файл переименован в: {output_path}")
                        except Exception as e:
                            logger.warning(f"Не удалось переименовать файл: {e}")
                    
                    return True
            
            logger.error("yt-dlp: файл не найден после скачивания")
            return False
            
        except Exception as e:
            logger.error(f"Ошибка yt-dlp: {e}")
            return False
            
    def download_video(self, url: str, map_number: int = 1, pov: Optional[str] = None, 
                      force_browser: bool = False) -> Optional[Path]:
        """
        Скачать видео по URL FACEIT.
        
        Args:
            url: URL страницы матча FACEIT
            map_number: Номер карты
            pov: POV константа (опциональная)
            force_browser: Принудительно использовать браузер
            
        Returns:
            Путь к скачанному файлу или None при ошибке
        """
        try:
            # Парсинг URL
            url_components = self.config.parse_match_url(url)
            if not url_components:
                raise VideoDownloadError(f"Некорректный URL FACEIT: {url}")
                
            video_url = None
            
            # Если принудительно не используем браузер, пробуем стандартный метод
            if not force_browser:
                try:
                    # Построение URL без pov по умолчанию
                    watch_url = self.config.get_match_url_without_pov(
                        url_components['match_id'],
                        url_components['tournament_name'],
                        map_number
                    )
                    
                    logger.info(f"Попытка загрузки через HTTP: {watch_url}")
                    video_url = self._get_video_manifest_url(watch_url)
                    
                except Exception as e:
                    logger.warning(f"HTTP метод не сработал: {e}")
                    if "403" in str(e) or "Forbidden" in str(e):
                        logger.info("Обнаружена ошибка 403, переключаемся на браузерную автоматизацию")
                    else:
                        logger.info("Переключаемся на браузерную автоматизацию как fallback")
                        
            # Если URL не получен через HTTP или принудительно используем браузер
            if not video_url:
                try:
                    watch_url = self.config.get_match_url_without_pov(
                        url_components['match_id'],
                        url_components['tournament_name'],
                        map_number
                    )
                    
                    # Сначала пробуем Playwright анализатор (наиболее современный)
                    logger.info("Попытка получения URL через Playwright анализатор...")
                    video_url = self._get_video_url_with_playwright(watch_url)
                    
                    # Если Playwright не сработал, пробуем гибридный анализатор 
                    if not video_url:
                        logger.warning("Playwright не дал результата, пробуем гибридный анализатор...")
                        video_url = self._get_video_url_with_hybrid_analyzer(watch_url)
                    
                    # Если и гибридный не сработал, пробуем простой сетевой анализатор  
                    if not video_url:
                        logger.warning("Гибридный анализатор не дал результата, пробуем простой сетевой анализ...")
                        video_url = self._get_video_url_with_network_analyzer(watch_url)
                    
                    # Последний вариант - старая браузерная автоматизация
                    if not video_url:
                        logger.warning("Пробуем старую браузерную автоматизацию как последний fallback...")
                        video_url = self._get_video_url_with_browser(watch_url)
                        
                except Exception as e:
                    logger.error(f"Браузерные методы не сработали: {e}")
                    
            if not video_url:
                raise VideoDownloadError("Не удалось получить URL видео ни одним из методов")
                
            # Определение имени файла
            pov_suffix = f"_{pov}" if pov else ""
            filename = f"{url_components['match_id']}_map{map_number}{pov_suffix}.mp4"
            output_path = Path("data/videos") / filename
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Попытка скачивания
            success = False
            
            if '.m3u8' in video_url or 'manifest' in video_url.lower():
                # HLS поток - используем yt-dlp как первый выбор, затем ffmpeg
                logger.info(f"Обнаружен HLS поток (.m3u8). HAS_YT_DLP={HAS_YT_DLP}")
                if HAS_YT_DLP:
                    logger.info("Используем yt-dlp для HLS потока...")
                    success = self._download_with_ytdlp(video_url, output_path)
                    if not success:
                        logger.info("yt-dlp не сработал, пробуем ffmpeg...")
                        success = self._download_with_ffmpeg(video_url, output_path)
                else:
                    logger.warning("yt-dlp не доступен, используем ffmpeg для HLS...")
                    success = self._download_with_ffmpeg(video_url, output_path)
            else:
                # Прямая ссылка - используем requests
                logger.info("Обнаружена прямая ссылка, используем requests")
                success = self._download_with_requests(video_url, output_path)
                
            if not success:
                # Fallback - пробуем другие методы
                logger.info("Пробуем альтернативные методы скачивания...")
                if '.m3u8' in video_url:
                    if not HAS_YT_DLP:  # Только если yt-dlp не пробовали
                        success = self._download_with_ytdlp(video_url, output_path)
                    if not success:
                        success = self._download_with_requests(video_url, output_path)
                else:
                    success = self._download_with_ytdlp(video_url, output_path)
                    if not success:
                        success = self._download_with_ffmpeg(video_url, output_path)
                    
            if success:
                return output_path
            else:
                raise VideoDownloadError("Все методы скачивания неуспешны")
                
        except Exception as e:
            logger.error(f"Ошибка скачивания видео: {e}")
            raise VideoDownloadError(f"Не удалось скачать видео: {e}")
            
    def get_stream(self, url: str, map_number: int = 1, pov: Optional[str] = None,
                  force_browser: bool = False) -> Generator[bytes, None, None]:
        """
        Получить поток видео для обработки в реальном времени.
        
        Args:
            url: URL страницы матча FACEIT
            map_number: Номер карты
            pov: POV константа (опциональная)
            force_browser: Принудительно использовать браузер
            
        Yields:
            Блоки видеоданных
        """
        try:
            # Парсинг URL
            url_components = self.config.parse_match_url(url)
            if not url_components:
                raise VideoDownloadError(f"Некорректный URL FACEIT: {url}")
                
            video_url = None
            
            # Получение URL видео (аналогично download_video)
            if not force_browser:
                try:
                    watch_url = self.config.get_match_url_without_pov(
                        url_components['match_id'],
                        url_components['tournament_name'],
                        map_number
                    )
                    video_url = self._get_video_manifest_url(watch_url)
                except Exception as e:
                    logger.warning(f"HTTP метод для потока не сработал: {e}")
                    
            # Fallback к браузеру
            if not video_url:
                try:
                    watch_url = self.config.get_match_url_without_pov(
                        url_components['match_id'],
                        url_components['tournament_name'],
                        map_number
                    )
                    
                    # Сначала пробуем Playwright анализатор
                    logger.info("Попытка получения URL потока через Playwright анализатор...")
                    video_url = self._get_video_url_with_playwright(watch_url)
                    
                    # Fallback к гибридному анализатору
                    if not video_url:
                        logger.warning("Пробуем гибридный анализатор для потока...")
                        video_url = self._get_video_url_with_hybrid_analyzer(watch_url)
                    
                    # Fallback к простому сетевому анализатору
                    if not video_url:
                        logger.warning("Пробуем простой сетевой анализ для потока...")
                        video_url = self._get_video_url_with_network_analyzer(watch_url)
                    
                    # Последний fallback к старой браузерной автоматизации
                    if not video_url:
                        logger.warning("Пробуем старую браузерную автоматизацию для потока...")
                        video_url = self._get_video_url_with_browser(watch_url)
                        
                except Exception as e:
                    logger.error(f"Браузерные методы для потока не сработали: {e}")
                    
            if not video_url:
                raise VideoDownloadError("Не удалось получить URL видео для потока")
                
            logger.info(f"Создание потока для: {video_url}")
            
            # Поточное получение данных
            with self.session.get(video_url, stream=True, timeout=30) as response:
                response.raise_for_status()
                
                for chunk in response.iter_content(chunk_size=self.config.chunk_size):
                    if chunk:
                        yield chunk
                        
        except Exception as e:
            logger.error(f"Ошибка создания потока: {e}")
            raise VideoDownloadError(f"Не удалось создать поток: {e}")