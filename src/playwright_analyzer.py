#!/usr/bin/env python3
"""
Анализатор FACEIT на основе Playwright для извлечения видео URL'ов.

Playwright обеспечивает:
- Лучший перехват сетевых запросов
- Автоматическое ожидание элементов  
- Более стабильную работу с SPA
- Встроенные браузеры (без WebDriver)
"""

import logging
import asyncio
import re
from typing import List, Dict, Optional, Any, Set
import json
from pathlib import Path

try:
    from playwright.async_api import async_playwright, Page, Browser, BrowserContext
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    async_playwright = None

logger = logging.getLogger(__name__)

class PlaywrightAnalysisError(Exception):
    """Исключение при ошибках Playwright анализа."""
    pass

class FaceitPlaywrightAnalyzer:
    """Анализатор FACEIT на основе Playwright."""
    
    def __init__(self, config, headless: bool = True):
        """
        Инициализация Playwright анализатора.
        
        Args:
            config: Конфигурация FACEIT
            headless: Запускать браузер в headless режиме
        """
        if not HAS_PLAYWRIGHT:
            raise PlaywrightAnalysisError(
                "Playwright не установлен. Установите: pip install playwright && playwright install"
            )
            
        self.config = config
        self.headless = headless
        
        # Коллекция для видео URL'ов
        self.video_urls: Set[str] = set()
        self.network_logs: List[Dict] = []
        
        # Отладочные переменные (инициализируются в setup_browser_context)
        self.screenshot_dir = None
        self.screenshot_counter = 0
        
        # Паттерны для поиска видео (расширенные)
        self.video_patterns = [
            r'https?://[^\\s"\']+\.m3u8(?:\?[^\\s"\']*)?',
            r'https?://edge-\d+\.facecast\.net/[^\\s"\']+\.m3u8[^\\s"\']*',
            r'https?://[^\\s"\']*facecast[^\\s"\']+\.m3u8[^\\s"\']*',
            r'https?://[^\\s"\']*\.mpd(?:\?[^\\s"\']*)?',  # DASH streams
            r'https?://[^\\s"\']*stream[^\\s"\']*\.m3u8[^\\s"\']*',
            r'https?://[^\\s"\']*video[^\\s"\']*\.m3u8[^\\s"\']*',
        ]
        
        # Селекторы для интерактивных элементов
        self.interactive_selectors = [
            # Кнопки управления видео
            'button[aria-label*="map" i]',
            'button[aria-label*="event" i]', 
            'button[title*="map" i]',
            'button[title*="event" i]',
            '[data-testid*="map" i]',
            '[data-testid*="event" i]',
            
            # Общие селекторы видео контролов
            '.video-controls button',
            '.stream-controls button', 
            '.player-controls button',
            '[class*="video"] button',
            '[class*="stream"] button',
            '[class*="player"] button',
            
            # Play/Start кнопки
            'button[aria-label*="play" i]',
            'button[aria-label*="start" i]',
            'button[title*="play" i]',
            'button[title*="start" i]',
            '.play-button',
            '.start-button',
            
            # Специфичные для FACEIT
            '[class*="match"] button',
            '[class*="broadcast"] button',
            '[class*="watch"] button',
        ]
    
    async def setup_browser_context(self) -> tuple[Browser, BrowserContext, Page]:
        """Настройка браузера Playwright."""
        logger.info("Настройка Playwright браузера...")
        
        playwright = await async_playwright().start()
        
        # Запуск браузера
        browser = await playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--disable-web-security',
                '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ]
        )
        
        # Создание папки для скриншотов
        self.screenshot_dir = Path("debug_screenshots")
        self.screenshot_dir.mkdir(exist_ok=True)
        self.screenshot_counter = 0
        
        # Создание контекста с cookie
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            ignore_https_errors=True
        )
        
        # Настройка перехвата сетевых запросов
        context.on('request', self._on_request)
        context.on('response', self._on_response) 
        
        # Создание страницы
        page = await context.new_page()
        
        logger.info("Playwright браузер готов")
        return browser, context, page
    
    async def take_screenshot(self, page: Page, name: str, description: str = ""):
        """Создание скриншота для отладки."""
        try:
            self.screenshot_counter += 1
            timestamp = asyncio.get_event_loop().time()
            filename = f"{self.screenshot_counter:02d}_{name}_{int(timestamp)}.png"
            filepath = self.screenshot_dir / filename
            
            await page.screenshot(path=str(filepath), full_page=True)
            
            logger.info(f"Скриншот сохранен: {filepath}")
            if description:
                logger.info(f"  Описание: {description}")
                
            # Сохраняем информацию о скриншоте
            info_file = filepath.with_suffix('.txt')
            with open(info_file, 'w', encoding='utf-8') as f:
                f.write(f"Скриншот: {filename}\n")
                f.write(f"Время: {timestamp}\n")
                f.write(f"URL: {page.url}\n")
                f.write(f"Заголовок: {await page.title()}\n")
                f.write(f"Описание: {description}\n")
                
        except Exception as e:
            logger.debug(f"Ошибка создания скриншота: {e}")
    
    async def _on_request(self, request):
        """Обработчик исходящих запросов."""
        url = request.url
        
        # Логируем все запросы для анализа
        self.network_logs.append({
            'type': 'request',
            'url': url,
            'method': request.method,
            'timestamp': asyncio.get_event_loop().time()
        })
        
        # Ищем видео URL'ы в запросах
        self._check_and_save_video_url(url)
    
    async def _on_response(self, response):
        """Обработчик входящих ответов."""
        url = response.url
        
        # Логируем ответы
        self.network_logs.append({
            'type': 'response', 
            'url': url,
            'status': response.status,
            'timestamp': asyncio.get_event_loop().time()
        })
        
        # Ищем видео URL'ы в ответах
        self._check_and_save_video_url(url)
        
        # Дополнительно ищем URL'ы в content-type и headers
        try:
            content_type = response.headers.get('content-type', '').lower()
            if 'application/vnd.apple.mpegurl' in content_type or 'm3u8' in content_type:
                logger.info(f"Найден HLS ответ: {url}")
                self.video_urls.add(url)
                
            # Проверяем заголовки на наличие видео URL'ов
            for header_name, header_value in response.headers.items():
                if isinstance(header_value, str):
                    self._check_and_save_video_url(header_value)
                    
        except Exception as e:
            logger.debug(f"Ошибка анализа ответа {url}: {e}")
    
    def _check_and_save_video_url(self, url: str):
        """Проверка и сохранение видео URL."""
        if not url or not isinstance(url, str):
            return
            
        for pattern in self.video_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                if url not in self.video_urls:
                    self.video_urls.add(url)
                    logger.info(f"Найден видео URL: {url}")
                break
    
    async def set_cookies_and_navigate(self, context: BrowserContext, page: Page, url: str):
        """Установка cookie и переход на страницу."""
        logger.info("Установка cookie и переход на страницу...")
        
        # Сначала переходим на главную страницу для установки cookie
        await page.goto('https://www.faceit.com', wait_until='domcontentloaded')
        
        # КРИТИЧНО: Обработка Cookie Consent на главной странице
        logger.info("=== КРИТИЧНАЯ ОБРАБОТКА COOKIE CONSENT ===")
        
        # Ждем полной загрузки страницы
        await page.wait_for_timeout(5000)
        
        # Принудительная обработка Cookie Consent
        consent_handled = await self._handle_cookie_consent(page)
        
        if not consent_handled:
            logger.warning("Cookie Consent не был обработан с первого раза, пробуем еще раз...")
            await page.wait_for_timeout(3000)
            consent_handled = await self._handle_cookie_consent(page)
            
            if not consent_handled:
                logger.warning("Cookie Consent всё ещё не обработан, но продолжаем...")
        
        logger.info(f"Cookie Consent обработка завершена: {consent_handled}")
        
        # Установка cookie с валидацией
        cookies_to_add = []
        cookies_set = 0
        cookies_failed = 0
        
        for name, value in self.config.cookies.items():
            if value and isinstance(value, str) and len(value) > 0:
                # Валидация cookie для Playwright
                try:
                    cookie_data = {
                        'name': str(name),
                        'value': str(value)[:4096],  # Ограничиваем длину
                        'domain': '.faceit.com',
                        'path': '/',
                        'secure': False,  # Для HTTP/HTTPS совместимости
                        'httpOnly': False
                    }
                    
                    # Дополнительная валидация имени cookie
                    if not name or not isinstance(name, str) or len(name) == 0:
                        continue
                        
                    # Проверяем что нет недопустимых символов
                    if any(c in name for c in [';', '=', '\n', '\r']):
                        logger.debug(f"Пропускаем cookie с недопустимыми символами: {name}")
                        continue
                        
                    cookies_to_add.append(cookie_data)
                    
                except Exception as e:
                    logger.debug(f"Ошибка подготовки cookie {name}: {e}")
                    cookies_failed += 1
                    continue
        
        # Устанавливаем cookie по одному, чтобы выявить проблемные
        for cookie in cookies_to_add:
            try:
                await context.add_cookies([cookie])
                cookies_set += 1
            except Exception as e:
                logger.warning(f"Не удалось установить cookie {cookie['name']}: {e}")
                cookies_failed += 1
        
        logger.info(f"Cookie: установлено {cookies_set}, пропущено {cookies_failed}")
        
        # Переход на целевую страницу
        logger.info(f"Переход на: {url}")
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        
        # Скриншот сразу после загрузки
        await self.take_screenshot(page, "page_loaded", "Страница загружена")
        
        # ВАЖНО: Сначала обработка Cookie Consent (до установки других cookie!)
        logger.info("Первичная обработка Cookie Consent...")
        await self._handle_cookie_consent(page)
        
        # Ждем завершения начальной загрузки (увеличенный timeout)
        try:
            await page.wait_for_load_state('networkidle', timeout=15000)
        except Exception as e:
            logger.info(f"NetworkIdle timeout (нормально для SPA): {e}")
            # Продолжаем работу, это нормально для современных SPA
            
        # Скриншот после стабилизации
        await self.take_screenshot(page, "page_stable", "Страница стабилизировалась")
        
        # Проверка на Cloudflare
        title = await page.title()
        if 'ждем' in title.lower() or 'проверка' in title.lower():
            logger.info("Обнаружена проверка Cloudflare, ожидаем...")
            await self._wait_for_cloudflare_completion(page)
    
    async def _wait_for_cloudflare_completion(self, page: Page, max_wait: int = 60):
        """Ожидание завершения проверки Cloudflare."""
        logger.info("Ожидание завершения проверки Cloudflare...")
        
        for _ in range(max_wait // 2):  # Проверяем каждые 2 секунды
            try:
                title = await page.title()
                url = page.url
                
                if 'ждем' not in title.lower() and 'проверка' not in title.lower() and '__cf_chl_tk' not in url:
                    logger.info("Проверка Cloudflare завершена")
                    await page.wait_for_load_state('networkidle', timeout=5000)
                    return True
                    
                await page.wait_for_timeout(2000)
                
            except Exception:
                await page.wait_for_timeout(2000)
                
        logger.warning("Cloudflare проверка не завершилась за отведенное время")
        return False
    
    async def interact_with_page(self, page: Page):
        """Интеллектуальное взаимодействие со страницей для активации видео."""
        logger.info("Поиск и активация видео элементов...")
        
        # Сначала ждем полной загрузки страницы
        try:
            await page.wait_for_load_state('networkidle', timeout=8000)
        except Exception:
            logger.info("NetworkIdle timeout, продолжаем работу...")
        
        # Закрываем модальные окна
        try:
            await self._close_modals(page)
            await self.take_screenshot(page, "modals_closed", "Модальные окна закрыты")
        except Exception as e:
            logger.debug(f"Ошибка закрытия модалов: {e}")
        
        # Ищем и кликаем интерактивные элементы
        clicked_elements = 0
        
        for selector in self.interactive_selectors:
            try:
                # Ищем все элементы по селектору
                elements = await page.query_selector_all(selector)
                
                for element in elements:
                    try:
                        # Проверяем видимость и возможность клика
                        if await element.is_visible() and await element.is_enabled():
                            
                            # Скроллим к элементу
                            await element.scroll_into_view_if_needed()
                            await page.wait_for_timeout(500)
                            
                            # Кликаем
                            await element.click()
                            clicked_elements += 1
                            
                            logger.info(f"Кликнули элемент: {selector}")
                            
                            # Скриншот после клика
                            await self.take_screenshot(page, f"after_click_{clicked_elements}", f"После клика на {selector}")
                            
                            # Даем время на загрузку после клика
                            await page.wait_for_timeout(3000)
                            
                            # Даем время сетевым запросам загрузиться
                            try:
                                await page.wait_for_load_state('networkidle', timeout=5000)
                            except:
                                pass  # Не критично если не дождались
                            
                            # Ограничиваем количество кликов
                            if clicked_elements >= 5:
                                break
                                
                    except Exception as e:
                        logger.debug(f"Не удалось кликнуть элемент: timeout или overlay")
                        continue
                        
                if clicked_elements >= 5:
                    break
                    
            except Exception as e:
                logger.debug(f"Ошибка поиска элементов {selector}: {e}")
                continue
        
        logger.info(f"Кликнуто элементов: {clicked_elements}")
        
        # Финальное ожидание для захвата всех запросов
        await page.wait_for_timeout(5000)
    
    async def _close_modals(self, page: Page):
        """Закрытие модальных окон и принятие cookie consent."""
        
        # Специальная обработка Cookie Consent от FACEIT
        await self._handle_cookie_consent(page)
        
        # Общие модальные окна
        modal_selectors = [
            'div[role="dialog"]',
            '[class*="modal"]',
            '[class*="Modal"]', 
            '[class*="overlay"]',
            '[class*="popup"]'
        ]
        
        for selector in modal_selectors:
            try:
                modals = await page.query_selector_all(selector)
                
                for modal in modals:
                    if await modal.is_visible():
                        # Ищем кнопку закрытия в модальном окне
                        close_buttons = await modal.query_selector_all(
                            'button[aria-label*="close" i], button[data-testid*="close"], .close, [class*="close"]'
                        )
                        
                        for btn in close_buttons:
                            if await btn.is_visible():
                                await btn.click()
                                await page.wait_for_timeout(1000)
                                logger.info("Закрыто модальное окно")
                                return
                                
            except Exception:
                continue
    
    async def _handle_cookie_consent(self, page: Page):
        """Специальная обработка Cookie Consent от FACEIT."""
        try:
            logger.info("Поиск Cookie Consent баннера...")
            
            # Селекторы на основе реального HTML от FACEIT
            consent_selectors = [
                # Точные селекторы на основе DevTools
                'button#accept-uc-accept-button',
                'button[id="accept-uc-accept-button"]',
                'button[data-action="consent"][data-action-type="accept"]',
                'button[aria-label="Accept all"]',
                'button.accept.uc-accept-button',
                
                # По тексту кнопки
                'button:has-text("Accept all")',
                'button:has-text("Accept All")', 
                'button:has-text("ACCEPT ALL")',
                
                # Usercentrics специфичные селекторы
                'button[class*="uc-accept-button"]',
                'button[id*="accept-uc"]',
                '[id*="usercentrics"] button[class*="accept"]',
                
                # По содержимому кнопки
                'button[type="button"]:has-text("Accept")',
                
                # Общие fallback селекторы
                'button[data-testid*="accept"]',
                'button[id*="accept"]',
                'button[class*="accept"]',
                
                # Общие селекторы для cookie баннеров
                '[id*="cookie"] button:has-text("Accept")',
                '[class*="cookie"] button:has-text("Accept")',
                '[class*="consent"] button:has-text("Accept")',
                
                # Дополнительные usercentrics селекторы
                '#usercentrics-cmp-ui button[class*="accept"]',
                '[data-nosnippet] button[aria-label*="Accept"]'
            ]
            
            # Дополнительно ищем по тексту содержимого страницы
            privacy_indicators = [
                "Let's talk about Privacy",
                "Privacy Policy",
                "Cookie Policy", 
                "Accept all",
                "Manage Settings"
            ]
            
            page_content = await page.content()
            has_privacy_modal = any(indicator.lower() in page_content.lower() for indicator in privacy_indicators)
            
            # Дополнительная проверка через Playwright локаторы
            has_accept_button = await page.locator('text=Accept all').count() > 0
            has_privacy_text = await page.locator('text=Privacy').count() > 0
            
            # Если один из способов нашел баннер - он есть!
            has_privacy_modal = has_privacy_modal or has_accept_button or has_privacy_text
            
            logger.debug(f"Проверка Cookie Consent: content={has_privacy_modal}, accept_btn={has_accept_button}, privacy_text={has_privacy_text}")
            
            if has_privacy_modal:
                logger.info("Обнаружен Cookie Consent баннер")
                await self.take_screenshot(page, "cookie_consent_detected", "Cookie Consent обнаружен")
                
                # НОВОЕ: Таймер на 1 минуту для ручного поиска (только в неheadless режиме)
                if not self.headless:
                    logger.info("=== РЕЖИМ ОТЛАДКИ ===")
                    logger.info("У вас есть 60 секунд чтобы вручную кликнуть 'Accept all'")
                    logger.info("Найдите кнопку с текстом 'Accept all' и кликните на неё")
                    logger.info("Если не найдете - автопоиск запустится через 60 сек")
                    
                    # Показываем полезную информацию для отладки
                    accept_elements = await page.evaluate("""
                        Array.from(document.querySelectorAll('*')).filter(el => {
                            const text = (el.textContent || '').toLowerCase();
                            return text.includes('accept') && el.offsetParent !== null;
                        }).slice(0, 5).map(el => ({
                            tag: el.tagName,
                            text: el.textContent.substring(0, 60),
                            id: el.id,
                            classes: el.className.substring(0, 50)
                        }))
                    """)
                    
                    logger.info("Найденные элементы с 'accept':")
                    for i, el in enumerate(accept_elements):
                        logger.info(f"  {i+1}. {el['tag']}: '{el['text']}' (ID: {el['id']})")
                    
                    logger.info("Ждем 60 секунд...")
                    
                    # Ждем с проверкой каждые 10 секунд
                    for i in range(6):
                        await page.wait_for_timeout(10000)
                        
                        # Проверяем исчез ли баннер
                        current_has_accept = await page.locator('text=Accept all').count() > 0
                        current_has_privacy = await page.locator('text=Privacy').count() > 0
                        
                        if not current_has_accept and not current_has_privacy:
                            logger.info("SUCCESS! Cookie Consent исчез - был принят вручную!")
                            await self.take_screenshot(page, "manual_cookie_accepted", "Cookie принят вручную")
                            return True
                            
                        remaining = (6 - i - 1) * 10
                        if remaining > 0:
                            logger.info(f"Осталось {remaining} секунд...")
                    
                    logger.info("Время вышло! Запускаем автоматический поиск...")
                
                # Пробуем разные селекторы
                for selector in consent_selectors:
                    try:
                        buttons = await page.query_selector_all(selector)
                        
                        for button in buttons:
                            if await button.is_visible() and await button.is_enabled():
                                button_text = await button.inner_text()
                                
                                if any(accept_word in button_text.lower() for accept_word in 
                                      ['accept all', 'accept', 'согласиться', 'принять']):
                                    
                                    logger.info(f"Кликаем на кнопку Cookie Consent: '{button_text}'")
                                    
                                    # Скроллим к кнопке и кликаем
                                    await button.scroll_into_view_if_needed()
                                    await page.wait_for_timeout(500)
                                    await button.click()
                                    
                                    # Ждем исчезновения баннера
                                    await page.wait_for_timeout(2000)
                                    
                                    logger.info("Cookie Consent принят!")
                                    await self.take_screenshot(page, "cookie_consent_accepted", "Cookie Consent принят")
                                    
                                    return True
                                    
                    except Exception as e:
                        logger.debug(f"Селектор {selector} не сработал: {e}")
                        continue
                        
                # Если не нашли специфичные кнопки, пробуем JavaScript
                try:
                    logger.info("Попытка принять cookie consent через JavaScript...")
                    
                    # JavaScript для принятия cookie consent (улучшенный)
                    js_code = """
                    // Специальный поиск для FACEIT Usercentrics
                    
                    // 1. Поиск по ID (самый надежный)
                    let acceptBtn = document.getElementById('accept-uc-accept-button');
                    if (acceptBtn && acceptBtn.offsetParent !== null) {
                        acceptBtn.click();
                        return 'Clicked by ID: accept-uc-accept-button';
                    }
                    
                    // 2. Поиск по data-атрибутам
                    acceptBtn = document.querySelector('button[data-action="consent"][data-action-type="accept"]');
                    if (acceptBtn && acceptBtn.offsetParent !== null) {
                        acceptBtn.click();
                        return 'Clicked by data attributes';
                    }
                    
                    // 3. Поиск по aria-label
                    acceptBtn = document.querySelector('button[aria-label="Accept all"]');
                    if (acceptBtn && acceptBtn.offsetParent !== null) {
                        acceptBtn.click();
                        return 'Clicked by aria-label';
                    }
                    
                    // 4. Поиск по классу
                    acceptBtn = document.querySelector('button.accept.uc-accept-button');
                    if (acceptBtn && acceptBtn.offsetParent !== null) {
                        acceptBtn.click();
                        return 'Clicked by class';
                    }
                    
                    // 5. Общий поиск по тексту
                    const acceptButtons = [
                        ...document.querySelectorAll('button'),
                        ...document.querySelectorAll('[role="button"]')
                    ].filter(btn => {
                        const text = (btn.textContent || btn.innerText || '').toLowerCase();
                        const ariaLabel = (btn.getAttribute('aria-label') || '').toLowerCase();
                        
                        return text.includes('accept all') || 
                               ariaLabel.includes('accept all') ||
                               text.includes('accept') && btn.id.includes('accept');
                    });
                    
                    // Кликаем на первую видимую кнопку
                    for (const btn of acceptButtons) {
                        if (btn.offsetParent !== null) {
                            btn.click();
                            return `Clicked by text search: ${btn.textContent || btn.innerText || btn.getAttribute('aria-label')}`;
                        }
                    }
                    
                    return 'No accept button found';
                    """
                    
                    result = await page.evaluate(js_code)
                    logger.info(f"JavaScript результат: {result}")
                    
                    if "Clicked:" in str(result):
                        await page.wait_for_timeout(2000)
                        logger.info("Cookie Consent принят через JavaScript!")
                        await self.take_screenshot(page, "cookie_consent_js", "Cookie Consent принят через JS")
                        return True
                        
                except Exception as e:
                    logger.debug(f"JavaScript метод не сработал: {e}")
            else:
                logger.debug("Cookie Consent баннер не обнаружен")
                
        except Exception as e:
            logger.debug(f"Ошибка обработки Cookie Consent: {e}")
            
        return False
    
    def extract_video_urls(self) -> List[Dict[str, Any]]:
        """Извлечение и сортировка найденных видео URL'ов."""
        video_list = []
        
        for url in self.video_urls:
            quality = self._extract_quality(url)
            source = self._detect_source(url)
            
            video_info = {
                'url': url,
                'quality': quality,
                'source': source,
                'type': 'hls' if '.m3u8' in url else 'dash' if '.mpd' in url else 'unknown'
            }
            
            video_list.append(video_info)
        
        # Сортируем по качеству (лучшее сначала)
        video_list.sort(key=lambda x: self._quality_priority(x['quality']), reverse=True)
        
        logger.info(f"Обработано видео URL'ов: {len(video_list)}")
        return video_list
    
    def _extract_quality(self, url: str) -> str:
        """Извлечение качества из URL."""
        qualities = ['1080p', '720p', '480p', '360p', '240p', 'source', 'best', 'high', 'medium', 'low']
        url_lower = url.lower()
        
        for quality in qualities:
            if quality in url_lower:
                return quality
                
        return 'unknown'
    
    def _detect_source(self, url: str) -> str:
        """Определение источника видео."""
        url_lower = url.lower()
        
        if 'facecast' in url_lower:
            return 'facecast'
        elif 'faceit' in url_lower:
            return 'faceit'
        elif 'cdn' in url_lower:
            return 'cdn'
        else:
            return 'unknown'
    
    def _quality_priority(self, quality: str) -> int:
        """Приоритет качества для сортировки."""
        priorities = {
            '1080p': 100, 'source': 95, 'best': 90, 'high': 85,
            '720p': 70, 'medium': 60, '480p': 50, '360p': 30, 
            'low': 20, '240p': 10, 'unknown': 0
        }
        return priorities.get(quality.lower(), 0)
    
    def save_analysis_report(self, video_urls: List[Dict], output_file: str = "playwright_analysis.json"):
        """Сохранение отчета анализа."""
        try:
            report = {
                'analyzer': 'playwright',
                'total_network_logs': len(self.network_logs),
                'unique_video_urls': len(self.video_urls),
                'processed_video_urls': len(video_urls),
                'video_urls': video_urls,
                'analysis_summary': {
                    'qualities_found': list(set(url['quality'] for url in video_urls)),
                    'sources_found': list(set(url['source'] for url in video_urls)),
                    'types_found': list(set(url['type'] for url in video_urls))
                }
            }
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
                
            logger.info(f"Отчет Playwright анализа сохранен: {output_file}")
            
        except Exception as e:
            logger.error(f"Ошибка сохранения отчета: {e}")
    
    async def analyze_faceit_video(self, match_url: str) -> Optional[str]:
        """
        Полный анализ FACEIT видео с помощью Playwright.
        
        Args:
            match_url: URL матча FACEIT
            
        Returns:
            URL лучшего качества видео или None
        """
        browser = None
        
        try:
            logger.info("=== ЗАПУСК PLAYWRIGHT АНАЛИЗА ===")
            
            # Очистка предыдущих данных
            self.video_urls.clear()
            self.network_logs.clear()
            
            # Настройка браузера
            browser, context, page = await self.setup_browser_context()
            
            # Переход на страницу с cookie
            await self.set_cookies_and_navigate(context, page, match_url)
            
            # Интерактивное взаимодействие
            try:
                await self.interact_with_page(page)
            except Exception as e:
                logger.info(f"Ошибка взаимодействия (продолжаем): {e}")
            
            # Финальное ожидание для захвата запросов
            logger.info("Финальное ожидание сетевых запросов...")
            await page.wait_for_timeout(10000)
            
            # Финальный скриншот
            await self.take_screenshot(page, "final_state", "Финальное состояние страницы")
            
            # Обработка результатов
            video_urls = self.extract_video_urls()
            
            # Проверяем найденные URL'ы
            logger.info(f"Найдено уникальных видео URL'ов: {len(self.video_urls)}")
            
            if self.video_urls:
                # Если есть сырые URL'ы, используем их
                video_list = []
                for url in self.video_urls:
                    quality = self._extract_quality(url)
                    video_list.append({
                        'url': url,
                        'quality': quality,
                        'source': self._detect_source(url),
                        'type': 'hls' if '.m3u8' in url else 'dash' if '.mpd' in url else 'unknown'
                    })
                
                # Сортируем
                video_list.sort(key=lambda x: self._quality_priority(x['quality']), reverse=True)
                
                if video_list:
                    self.save_analysis_report(video_list)
                    best_url = video_list[0]['url']
                    logger.info(f"Лучший видео URL: {video_list[0]['quality']} - {best_url}")
                    return best_url
            
            if video_urls:
                # Используем обработанные URL'ы
                self.save_analysis_report(video_urls)
                best_url = video_urls[0]['url']
                logger.info(f"Лучший видео URL: {video_urls[0]['quality']} - {best_url}")
                return best_url
            else:
                logger.warning("Playwright не нашел видео URL'ы")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка Playwright анализа: {e}")
            return None
        finally:
            if browser:
                await browser.close()
                logger.info("Playwright браузер закрыт")

def get_video_url_sync(config, match_url: str, headless: bool = True) -> Optional[str]:
    """
    Синхронная обертка для асинхронного Playwright анализатора.
    
    Args:
        config: Конфигурация FACEIT
        match_url: URL матча
        headless: Headless режим
        
    Returns:
        URL видео или None
    """
    try:
        analyzer = FaceitPlaywrightAnalyzer(config, headless)
        return asyncio.run(analyzer.analyze_faceit_video(match_url))
    except Exception as e:
        logger.error(f"Ошибка синхронной обертки Playwright: {e}")
        return None