#!/usr/bin/env python3
"""
Гибридный анализатор FACEIT, объединяющий:
- Анализ сетевых запросов
- Интеллектуальную автоматизацию браузера
- Обход Cloudflare
"""

import logging
import time
import re
from typing import List, Dict, Optional, Any
import json

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from webdriver_manager.chrome import ChromeDriverManager
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False
    webdriver = None

logger = logging.getLogger(__name__)

class HybridAnalysisError(Exception):
    """Исключение при ошибках гибридного анализа."""
    pass

class FaceitHybridAnalyzer:
    """Гибридный анализатор FACEIT с интеллектуальной автоматизацией."""
    
    def __init__(self, config, headless: bool = True):
        """
        Инициализация гибридного анализатора.
        
        Args:
            config: Конфигурация FACEIT
            headless: Запускать браузер в headless режиме
        """
        if not HAS_SELENIUM:
            raise HybridAnalysisError(
                "Selenium не установлен. "
                "Установите: pip install selenium webdriver-manager"
            )
            
        self.config = config
        self.headless = headless
        self.driver = None
        self.network_logs = []
        
        # Паттерны для поиска видео URL'ов (расширенные)
        self.video_patterns = [
            r'https?://[^\\s"\']+\.m3u8[^\\s"\']*',
            r'https?://edge-\d+\.facecast\.net/[^\\s"\']+\.m3u8[^\\s"\']*',
            r'https?://[^\\s"\']*facecast[^\\s"\']+\.m3u8[^\\s"\']*',
            r'https?://[^\\s"\']*\.m3u8\?[^\\s"\']*',
            r'https?://[^\\s"\']*stream[^\\s"\']*\.m3u8[^\\s"\']*',
        ]
        
        # Селекторы для поиска элементов управления
        self.control_selectors = {
            # Кнопки видео управления
            'video_buttons': [
                'button[aria-label*="Map" i]',
                'button[aria-label*="Event" i]', 
                'button[title*="Map" i]',
                'button[title*="Event" i]',
                '[data-testid*="map" i]',
                '[data-testid*="event" i]',
                'button:contains("Map")',
                'button:contains("Event")',
                '.video-controls button',
                '.stream-controls button',
                '.player-controls button'
            ],
            
            # Контейнеры видео
            'video_containers': [
                'video',
                'iframe[src*="stream"]',
                'iframe[src*="video"]', 
                '[class*="video"]',
                '[class*="stream"]',
                '[class*="player"]'
            ],
            
            # Модальные окна для закрытия
            'modal_selectors': [
                'div[role="dialog"]',
                '[class*="modal"]',
                '[class*="Modal"]',
                '[class*="overlay"]',
                '[class*="popup"]'
            ]
        }
        
    def setup_browser_with_network_capture(self) -> bool:
        """Настройка браузера с перехватом сетевых запросов."""
        try:
            logger.info("Настройка гибридного анализатора...")
            
            chrome_options = Options()
            
            if self.headless:
                chrome_options.add_argument('--headless=new')
                
            # Основные опции
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage') 
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920,1080')
            
            # Обход детекции
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)
            
            # User agent
            chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            
            # ВАЖНО: Логирование сетевых запросов
            chrome_options.set_capability('goog:loggingPrefs', {
                'performance': 'ALL',
                'browser': 'ALL'
            })
            
            # Создание драйвера
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            
            # Включение Network API
            self.driver.execute_cdp_cmd('Network.enable', {})
            self.driver.execute_cdp_cmd('Runtime.enable', {})
            
            # Скрытие автоматизации
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            logger.info("Гибридный анализатор готов")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка настройки гибридного анализатора: {e}")
            return False
    
    def set_cookies_and_navigate(self, url: str) -> bool:
        """Установка cookie и переход на страницу."""
        try:
            logger.info("Установка cookie и переход на страницу...")
            
            # Установка cookie
            self.driver.get("https://www.faceit.com")
            time.sleep(2)
            
            cookies_set = 0
            for name, value in self.config.cookies.items():
                if value:
                    try:
                        self.driver.add_cookie({
                            'name': name,
                            'value': value,
                            'domain': '.faceit.com',
                            'path': '/'
                        })
                        cookies_set += 1
                    except Exception:
                        pass
                        
            logger.info(f"Cookie установлены: {cookies_set}")
            
            # Переход на целевую страницу
            logger.info(f"Переход на: {url}")
            self.driver.get(url)
            
            # Ожидание загрузки
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            # Проверка на Cloudflare
            title = self.driver.title.lower()
            if "ждем" in title or "проверка" in title:
                logger.info("Обнаружена проверка Cloudflare, ожидаем...")
                return self._wait_for_cloudflare_completion()
            
            logger.info("Страница успешно загружена")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка навигации: {e}")
            return False
    
    def _wait_for_cloudflare_completion(self, max_wait: int = 60) -> bool:
        """Ожидание завершения проверки Cloudflare."""
        logger.info("Ожидание завершения проверки Cloudflare...")
        
        start_time = time.time()
        while time.time() - start_time < max_wait:
            try:
                title = self.driver.title.lower()
                current_url = self.driver.current_url
                
                # Если заголовок изменился и нет challenge параметров - проверка завершена
                if "ждем" not in title and "проверка" not in title and "__cf_chl_tk" not in current_url:
                    logger.info("Проверка Cloudflare завершена")
                    return True
                    
                time.sleep(2)
                
            except Exception:
                time.sleep(2)
                
        logger.warning("Проверка Cloudflare не завершилась за отведенное время")
        return False
    
    def analyze_page_and_activate_video(self) -> bool:
        """Анализ страницы и активация видео при необходимости."""
        try:
            logger.info("Анализ страницы для активации видео...")
            
            # Ищем видео контейнеры
            video_found = False
            for selector in self.control_selectors['video_containers']:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        logger.info(f"Найдены видео элементы: {len(elements)} ({selector})")
                        video_found = True
                        break
                except:
                    continue
            
            if not video_found:
                logger.info("Видео элементы не найдены, ищем кнопки управления...")
                
                # Ищем и кликаем кнопки управления
                for selector in self.control_selectors['video_buttons']:
                    try:
                        if selector.startswith('button:contains'):
                            # XPath для :contains
                            text = selector.split('("')[1].rstrip('")')
                            xpath = f"//button[contains(text(), '{text}')]"
                            elements = self.driver.find_elements(By.XPATH, xpath)
                        else:
                            elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        
                        for element in elements:
                            if element.is_displayed() and element.is_enabled():
                                logger.info(f"Кликаем элемент: {selector}")
                                self.driver.execute_script("arguments[0].scrollIntoView(true);", element)
                                time.sleep(1)
                                element.click()
                                time.sleep(3)  # Ожидание загрузки видео
                                return True
                                
                    except Exception as e:
                        logger.debug(f"Элемент {selector} не найден или не кликабелен: {e}")
                        continue
            
            # Закрываем модальные окна, если есть
            self._close_modal_dialogs()
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка анализа страницы: {e}")
            return False
    
    def _close_modal_dialogs(self):
        """Закрытие модальных окон."""
        for selector in self.control_selectors['modal_selectors']:
            try:
                modals = self.driver.find_elements(By.CSS_SELECTOR, selector)
                for modal in modals:
                    if modal.is_displayed():
                        # Ищем кнопку закрытия
                        close_buttons = modal.find_elements(By.CSS_SELECTOR, 
                            'button[aria-label*="close" i], button[data-testid*="close"], .close, [class*="close"]')
                        for btn in close_buttons:
                            if btn.is_displayed():
                                btn.click()
                                time.sleep(1)
                                logger.info("Закрыто модальное окно")
                                return
            except:
                continue
    
    def capture_network_requests(self, duration: int = 20) -> List[Dict]:
        """Захват сетевых запросов с возможностью взаимодействия."""
        logger.info(f"Захват сетевых запросов ({duration} секунд)...")
        
        self.network_logs.clear()
        start_time = time.time()
        
        while time.time() - start_time < duration:
            # Получаем новые логи
            logs = self.driver.get_log('performance')
            for log in logs:
                self.network_logs.append(log)
            
            # Каждые 5 секунд пробуем взаимодействовать со страницей  
            if int(time.time() - start_time) % 5 == 0:
                self.analyze_page_and_activate_video()
                
            time.sleep(1)
        
        logger.info(f"Захвачено запросов: {len(self.network_logs)}")
        return self.network_logs
    
    def extract_video_urls_from_logs(self) -> List[Dict[str, Any]]:
        """Извлечение видео URL'ов из логов."""
        video_urls = []
        processed_urls = set()
        
        logger.info("Поиск видео URL'ов в сетевых запросах...")
        
        for log_entry in self.network_logs:
            try:
                message = json.loads(log_entry['message'])
                
                if message['message']['method'] in ['Network.responseReceived', 'Network.requestWillBeSent']:
                    
                    # Извлекаем URL
                    url = None
                    if 'request' in message['message']['params']:
                        url = message['message']['params']['request']['url']
                    elif 'response' in message['message']['params']:
                        url = message['message']['params']['response']['url']
                    
                    if not url:
                        continue
                    
                    # Проверяем по паттернам
                    for pattern in self.video_patterns:
                        if re.search(pattern, url, re.IGNORECASE):
                            if url not in processed_urls:
                                processed_urls.add(url)
                                
                                quality = self._extract_quality_from_url(url)
                                
                                video_info = {
                                    'url': url,
                                    'quality': quality,
                                    'type': 'hls',
                                    'source': self._detect_source(url),
                                    'timestamp': log_entry.get('timestamp', 0)
                                }
                                
                                video_urls.append(video_info)
                                logger.info(f"Найден видео URL: {quality} - {url[:100]}...")
                                
            except (json.JSONDecodeError, KeyError):
                continue
                
        # Сортировка по качеству
        video_urls.sort(key=lambda x: self._quality_priority(x['quality']), reverse=True)
        
        logger.info(f"Найдено уникальных видео URL'ов: {len(video_urls)}")
        return video_urls
    
    def _extract_quality_from_url(self, url: str) -> str:
        """Извлечение качества из URL."""
        qualities = ['1080p', '720p', '480p', '360p', '240p', 'source', 'best']
        for quality in qualities:
            if quality in url.lower():
                return quality
        return 'unknown'
    
    def _detect_source(self, url: str) -> str:
        """Определение источника видео."""
        if 'facecast' in url:
            return 'facecast'
        elif 'faceit' in url:
            return 'faceit'
        else:
            return 'unknown'
    
    def _quality_priority(self, quality: str) -> int:
        """Приоритет качества."""
        priorities = {
            '1080p': 100, 'source': 95, 'best': 90,
            '720p': 70, '480p': 50, '360p': 30, 
            '240p': 10, 'unknown': 0
        }
        return priorities.get(quality.lower(), 0)
    
    def get_best_video_url(self, match_url: str) -> Optional[str]:
        """
        Получение лучшего видео URL через гибридный анализ.
        
        Args:
            match_url: URL матча FACEIT
            
        Returns:
            URL лучшего качества или None
        """
        try:
            logger.info("=== ЗАПУСК ГИБРИДНОГО АНАЛИЗА ===")
            
            # 1. Настройка браузера
            if not self.setup_browser_with_network_capture():
                raise HybridAnalysisError("Не удалось настроить браузер")
            
            # 2. Переход на страницу с cookie
            if not self.set_cookies_and_navigate(match_url):
                raise HybridAnalysisError("Не удалось загрузить страницу")
            
            # 3. Захват запросов с интерактивностью 
            self.capture_network_requests(duration=25)
            
            # 4. Извлечение видео URL'ов
            video_urls = self.extract_video_urls_from_logs()
            
            if video_urls:
                best_url = video_urls[0]['url']
                logger.info(f"Выбран лучший URL: {video_urls[0]['quality']} - {best_url}")
                return best_url
            else:
                logger.warning("Видео URL'ы не найдены")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка гибридного анализа: {e}")
            return None
        finally:
            self.cleanup()
    
    def cleanup(self):
        """Очистка ресурсов."""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Браузер закрыт")
            except Exception:
                pass
            finally:
                self.driver = None