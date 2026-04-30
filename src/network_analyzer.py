#!/usr/bin/env python3
"""
Анализатор сетевых запросов для извлечения видео URL'ов с FACEIT.

Этот модуль использует браузер для перехвата сетевых запросов 
и извлечения прямых ссылок на HLS стримы (.m3u8 файлы).
"""

import logging
import time
import re
from typing import List, Dict, Optional, Any
from pathlib import Path
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

class NetworkAnalysisError(Exception):
    """Исключение при ошибках анализа сетевых запросов."""
    pass

class FaceitNetworkAnalyzer:
    """Анализатор сетевых запросов FACEIT для извлечения видео URL'ов."""
    
    def __init__(self, config, headless: bool = True):
        """
        Инициализация анализатора.
        
        Args:
            config: Конфигурация FACEIT
            headless: Запускать браузер в headless режиме
        """
        if not HAS_SELENIUM:
            raise NetworkAnalysisError(
                "Selenium не установлен. "
                "Установите: pip install selenium webdriver-manager"
            )
            
        self.config = config
        self.headless = headless
        self.driver = None
        self.network_logs = []
        
        # Паттерны для поиска видео URL'ов
        self.video_patterns = [
            r'https?://[^\\s"\']+\.m3u8[^\\s"\']*',  # HLS стримы
            r'https?://edge-\d+\.facecast\.net/[^\\s"\']+\.m3u8[^\\s"\']*',  # Facecast
            r'https?://[^\\s"\']*facecast[^\\s"\']+\.m3u8[^\\s"\']*',  # Все Facecast стримы
        ]
        
        # Ключевые слова для фильтрации качества
        self.quality_keywords = ['1080p', '720p', '480p', '360p', '240p', 'source', 'best']
        
    def setup_browser(self) -> bool:
        """
        Настройка браузера с включенным логированием сети.
        
        Returns:
            True если настройка успешна
        """
        try:
            logger.info("Настройка браузера для анализа сетевых запросов...")
            
            # Настройка Chrome опций
            chrome_options = Options()
            
            if self.headless:
                chrome_options.add_argument('--headless=new')
                
            # Основные опции для стабильной работы
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage') 
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920,1080')
            
            # Обход детекции автоматизации
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)
            
            # User agent
            chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            
            # КРИТИЧЕСКИ ВАЖНО: Включаем логирование сетевых запросов
            chrome_options.add_argument('--enable-logging')
            chrome_options.add_argument('--log-level=0')
            chrome_options.set_capability('goog:loggingPrefs', {
                'performance': 'ALL',
                'browser': 'ALL'
            })
            
            # Создание WebDriver
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            
            # Включаем Performance logging для перехвата сетевых запросов  
            self.driver.execute_cdp_cmd('Network.enable', {})
            self.driver.execute_cdp_cmd('Runtime.enable', {})
            
            # Скрываем признаки автоматизации
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            logger.info("Браузер успешно настроен")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка настройки браузера: {e}")
            return False
    
    def set_cookies(self, url: str) -> bool:
        """
        Установка cookie для авторизации на FACEIT.
        
        Args:
            url: URL для установки cookie
            
        Returns:
            True если cookie установлены
        """
        if not self.driver:
            return False
            
        try:
            logger.info("Установка cookie...")
            
            # Переходим на главную страницу FACEIT для установки cookie
            self.driver.get("https://www.faceit.com")
            time.sleep(2)
            
            # Установка cookie из конфигурации
            cookies_set = 0
            for name, value in self.config.cookies.items():
                if value:  # Только непустые cookie
                    try:
                        self.driver.add_cookie({
                            'name': name,
                            'value': value,
                            'domain': '.faceit.com',
                            'path': '/'
                        })
                        cookies_set += 1
                    except Exception as e:
                        logger.warning(f"Не удалось установить cookie {name}: {e}")
                        
            logger.info(f"Cookie установлены: {cookies_set} из {len(self.config.cookies)}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка установки cookie: {e}")
            return False
    
    def navigate_and_capture(self, url: str, wait_time: int = 30) -> List[Dict[str, Any]]:
        """
        Переход на страницу и захват сетевых запросов.
        
        Args:
            url: URL страницы для анализа
            wait_time: Время ожидания загрузки (секунды)
            
        Returns:
            Список захваченных сетевых запросов
        """
        if not self.driver:
            raise NetworkAnalysisError("Браузер не настроен")
            
        try:
            logger.info(f"Переход на страницу: {url}")
            
            # Очистка предыдущих логов
            self.network_logs.clear()
            
            # Переход на целевую страницу
            self.driver.get(url)
            
            # Ожидание загрузки основных элементов
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                logger.info("Основная страница загружена")
            except TimeoutException:
                logger.warning("Таймаут загрузки основной страницы")
            
            # Даем время на загрузку всех ресурсов и стримов
            logger.info(f"Ожидание загрузки ресурсов ({wait_time} секунд)...")
            
            # Периодически получаем логи во время ожидания
            for i in range(wait_time):
                time.sleep(1)
                
                # Получаем логи производительности (сетевые запросы)
                logs = self.driver.get_log('performance')
                for log in logs:
                    self.network_logs.append(log)
                    
                # Показываем прогресс каждые 5 секунд
                if (i + 1) % 5 == 0:
                    logger.info(f"  Прогресс: {i + 1}/{wait_time}с, захвачено запросов: {len(self.network_logs)}")
            
            logger.info(f"Захвачено сетевых запросов: {len(self.network_logs)}")
            return self.network_logs
            
        except Exception as e:
            logger.error(f"Ошибка навигации и захвата: {e}")
            raise NetworkAnalysisError(f"Не удалось захватить сетевые запросы: {e}")
    
    def extract_video_urls(self, network_logs: Optional[List[Dict]] = None) -> List[Dict[str, Any]]:
        """
        Извлечение видео URL'ов из сетевых логов.
        
        Args:
            network_logs: Логи сетевых запросов (если не указаны, используются внутренние)
            
        Returns:
            Список найденных видео URL'ов с метаданными
        """
        if network_logs is None:
            network_logs = self.network_logs
            
        video_urls = []
        processed_urls = set()  # Избегаем дубликатов
        
        logger.info("Анализ сетевых запросов для поиска видео...")
        
        for log_entry in network_logs:
            try:
                message = json.loads(log_entry['message'])
                
                # Ищем сетевые запросы
                if message['message']['method'] in ['Network.responseReceived', 'Network.requestWillBeSent']:
                    
                    # Получаем URL из запроса
                    if 'request' in message['message']['params']:
                        url = message['message']['params']['request']['url']
                    elif 'response' in message['message']['params']:
                        url = message['message']['params']['response']['url']
                    else:
                        continue
                    
                    # Проверяем URL на соответствие видео паттернам
                    for pattern in self.video_patterns:
                        if re.search(pattern, url, re.IGNORECASE):
                            if url not in processed_urls:
                                processed_urls.add(url)
                                
                                # Определяем качество из URL
                                quality = self._extract_quality(url)
                                
                                video_info = {
                                    'url': url,
                                    'quality': quality,
                                    'type': 'hls',  # HLS стрим
                                    'source': 'facecast' if 'facecast' in url else 'unknown',
                                    'timestamp': log_entry.get('timestamp', 0)
                                }
                                
                                video_urls.append(video_info)
                                logger.info(f"Найден видео URL: {quality} - {url}")
                                
            except (json.JSONDecodeError, KeyError) as e:
                # Пропускаем невалидные записи
                continue
                
        # Сортируем по качеству (лучшее качество сначала)
        video_urls.sort(key=lambda x: self._quality_priority(x['quality']), reverse=True)
        
        logger.info(f"Найдено уникальных видео URL'ов: {len(video_urls)}")
        return video_urls
    
    def _extract_quality(self, url: str) -> str:
        """Извлечение качества видео из URL."""
        for quality in self.quality_keywords:
            if quality in url.lower():
                return quality
        return 'unknown'
    
    def _quality_priority(self, quality: str) -> int:
        """Приоритет качества для сортировки."""
        priorities = {
            '1080p': 100,
            'source': 95,
            'best': 90, 
            '720p': 70,
            '480p': 50,
            '360p': 30,
            '240p': 10,
            'unknown': 0
        }
        return priorities.get(quality.lower(), 0)
    
    def save_analysis_report(self, video_urls: List[Dict], output_file: str = "network_analysis.json"):
        """
        Сохранение отчета анализа в файл.
        
        Args:
            video_urls: Найденные видео URL'ы
            output_file: Имя файла для сохранения
        """
        try:
            report = {
                'timestamp': time.time(),
                'total_network_logs': len(self.network_logs),
                'found_video_urls': len(video_urls),
                'video_urls': video_urls,
                'analysis_summary': {
                    'qualities_found': list(set(url['quality'] for url in video_urls)),
                    'sources_found': list(set(url['source'] for url in video_urls))
                }
            }
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
                
            logger.info(f"Отчет анализа сохранен: {output_file}")
            
        except Exception as e:
            logger.error(f"Ошибка сохранения отчета: {e}")
    
    def analyze_faceit_video(self, match_url: str, wait_time: int = 30) -> List[Dict[str, Any]]:
        """
        Полный анализ видео FACEIT матча.
        
        Args:
            match_url: URL матча на FACEIT
            wait_time: Время ожидания загрузки ресурсов
            
        Returns:
            Список найденных видео URL'ов
        """
        try:
            logger.info("=== АНАЛИЗ СЕТЕВЫХ ЗАПРОСОВ FACEIT ===")
            
            # 1. Настройка браузера
            if not self.setup_browser():
                raise NetworkAnalysisError("Не удалось настроить браузер")
            
            # 2. Установка cookie
            if not self.set_cookies(match_url):
                logger.warning("Не удалось установить все cookie, продолжаем...")
            
            # 3. Навигация и захват сетевых запросов
            network_logs = self.navigate_and_capture(match_url, wait_time)
            
            # 4. Извлечение видео URL'ов
            video_urls = self.extract_video_urls(network_logs)
            
            # 5. Сохранение отчета
            if video_urls:
                self.save_analysis_report(video_urls)
                
            logger.info("=== АНАЛИЗ ЗАВЕРШЕН ===")
            return video_urls
            
        except Exception as e:
            logger.error(f"Ошибка анализа FACEIT видео: {e}")
            raise NetworkAnalysisError(f"Анализ не удался: {e}")
        finally:
            self.cleanup()
    
    def cleanup(self):
        """Очистка ресурсов браузера."""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Браузер закрыт")
            except Exception:
                pass
            finally:
                self.driver = None