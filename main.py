#!/usr/bin/env python3
"""
Apex Legends Stats Parser - Main Entry Point

Приложение для автоматизированного анализа видео трансляций Apex Legends турниров с FACEIT,
использующее OpenCV для отслеживания позиций команд на карте.

LEGACY NOTICE:
Этот root CLI оставлен для обратной совместимости.
Новый активный pipeline находится в services/* + apps/* (см. docs/ARCHITECTURE_TARGET.md).
"""

import argparse
import logging
import sys
import json
from pathlib import Path

from src.video_downloader import VideoDownloader
from src.video_processor import VideoProcessor
from src.data_exporter import DataExporter
from config.faceit_config import FaceitConfig
from config.opencv_config import OpenCVConfig


def setup_logging(verbose=False):
    """Настройка системы логирования."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('apex_parser.log')
        ]
    )


def main():
    """Главная функция приложения."""
    parser = argparse.ArgumentParser(
        description='Apex Legends Stats Parser - анализ турнирных видео'
    )
    
    parser.add_argument(
        'url',
        help='URL FACEIT матча для анализа'
    )
    
    parser.add_argument(
        '--map',
        type=int,
        default=1,
        help='Номер карты (по умолчанию: 1)'
    )
    
    parser.add_argument(
        '--pov',
        help='POV константа (опциональная)'
    )
    
    parser.add_argument(
        '--output',
        '-o',
        help='Путь для сохранения результата (по умолчанию: data/outputs/)'
    )
    
    parser.add_argument(
        '--stream',
        action='store_true',
        help='Обрабатывать видео в реальном времени (без скачивания)'
    )
    
    parser.add_argument(
        '--verbose',
        '-v',
        action='store_true',
        help='Подробный вывод логов'
    )
    
    parser.add_argument(
        '--segment',
        action='store_true',
        help='Включить автоматическую сегментацию на отдельные игры'
    )
    
    parser.add_argument(
        '--game-id',
        type=int,
        help='ID конкретной игры для обработки (требует --segment)'
    )
    
    parser.add_argument(
        '--all-games',
        action='store_true',
        help='Обработать все найденные игры (требует --segment)'
    )
    
    parser.add_argument(
        '--use-browser',
        action='store_true',
        help='Принудительно использовать старую браузерную автоматизацию (устарело)'
    )
    parser.add_argument(
        '--method',
        choices=['auto', 'http', 'playwright-analyzer', 'hybrid-analyzer', 'network-analyzer', 'browser-automation'],
        default='auto',
        help='Метод получения видео URL (по умолчанию: auto)'
    )
    
    # Аргументы для FACEIT авторизации
    parser.add_argument(
        '--auth',
        action='store_true',
        help='🔐 Запустить авторизацию FACEIT перед анализом (требует --faceit-email и --faceit-password)'
    )
    
    parser.add_argument(
        '--faceit-email',
        help='📧 Email для авторизации FACEIT (только с --auth)'
    )
    
    parser.add_argument(
        '--faceit-password', 
        help='🔒 Пароль для авторизации FACEIT (только с --auth)'
    )
    
    parser.add_argument(
        '--auth-headless',
        action='store_true',
        help='🤖 Запуск авторизации в headless режиме (по умолчанию: False для отладки)'
    )
    
    parser.add_argument(
        '--auth-only',
        action='store_true',
        help='🔑 Только авторизация, не запускать анализ видео (браузер остается открытым)'
    )
    
    args = parser.parse_args()
    
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)
    logger.warning(
        "Legacy CLI mode: main.py/src flow is frozen. Prefer services/analysis and apps/api workflows."
    )
    
    try:
        # Загрузка конфигураций
        faceit_config = FaceitConfig()
        
        # Обработка авторизации FACEIT (если запрошена)
        if args.auth:
            if not args.faceit_email or not args.faceit_password:
                logger.error("[ERROR] Для --auth требуются --faceit-email и --faceit-password")
                return 1
            
            logger.info("[AUTH] Запуск автоматической авторизации FACEIT...")
            
            try:
                from faceit_authenticator import FaceitAuthenticator
                
                authenticator = FaceitAuthenticator(
                    email=args.faceit_email,
                    password=args.faceit_password,
                    headless=args.auth_headless,
                    keep_browser_open=args.auth_only
                )
                
                logger.info("[BROWSER] Инициализация браузера для авторизации...")
                logger.info("[CLOUDFLARE] Обход Cloudflare...")
                if not args.auth_headless:
                    logger.info("[2FA] Если потребуется 2FA - введите код в браузере")
                
                session_data = authenticator.run()
                
                if session_data and 'cookies' in session_data:
                    # Обновляем конфигурацию fresh cookies
                    fresh_cookies = {cookie['name']: cookie['value'] for cookie in session_data['cookies']}
                    faceit_config.set_cookies_from_dict(fresh_cookies)
                    
                    logger.info("[SUCCESS] Авторизация успешна!")
                    logger.info(f"[COOKIES] Получено fresh cookies: {len(fresh_cookies)}")
                    
                    # Показываем критичные cookies
                    critical_cookies = ['__Host-AuthSession', '__Host-FaceitGatewayAuthorization', 
                                      '__Secure-FaceitBrowserId', '__cf_bm', 'cf_clearance']
                    found_critical = sum(1 for name in critical_cookies if name in fresh_cookies)
                    logger.info(f"[CRITICAL] Критичных cookies: {found_critical}/{len(critical_cookies)}")
                    
                    # Остановка выполнения при --auth-only
                    if args.auth_only:
                        logger.info("[STOP] Авторизация завершена. Браузер оставлен открытым для отслеживания.")
                        logger.info("[INFO] Cookies автоматически сохранены в faceit_session_cookies.json")
                        logger.info("[INFO] Для анализа видео запустите без --auth-only:")
                        logger.info(f"[INFO] python main.py --method playwright-analyzer \"{args.url}\"")
                        return 0
                    
                else:
                    logger.error("[FAIL] Авторизация не удалась!")
                    logger.error("Возможные причины:")
                    logger.error("  • Неверные логин/пароль")
                    logger.error("  • Проблемы с 2FA")
                    logger.error("  • Блокировка FACEIT")
                    logger.error("  • Ошибка Cloudflare обхода")
                    return 1
                    
            except ImportError:
                logger.error("[ERROR] FaceitAuthenticator не найден!")
                logger.error("Убедитесь что faceit_authenticator.py находится в папке проекта")
                return 1
            except Exception as e:
                logger.error(f"[ERROR] Ошибка авторизации: {e}")
                return 1
        
        # Загрузка cookie из различных источников (если авторизация не использовалась)
        if not args.auth:
            cookies_loaded = False
            
            # Пытаемся загрузить из config_local.py
            try:
                from config_local import FACEIT_COOKIES
                faceit_config.set_cookies_from_dict(FACEIT_COOKIES)
                logger.info(f"Загружено cookie из config_local.py: {len([k for k, v in faceit_config.cookies.items() if v])}")
                cookies_loaded = True
            except ImportError:
                logger.info("Файл config_local.py не найден. Пробуем загрузить из сохраненного файла...")
            except Exception as e:
                logger.warning(f"Ошибка загрузки cookie из config_local.py: {e}")
            
            # Если config_local.py не загрузился, пытаемся загрузить из файла cookies
            if not cookies_loaded:
                try:
                    import json
                    import os
                    cookies_file = "faceit_session_cookies.json"
                    
                    if os.path.exists(cookies_file):
                        with open(cookies_file, "r", encoding="utf-8") as f:
                            cookies_data = json.load(f)
                        
                        if 'cookies' in cookies_data:
                            # Конвертируем cookies из формата Selenium в dict
                            cookies_dict = {cookie['name']: cookie['value'] for cookie in cookies_data['cookies']}
                            faceit_config.set_cookies_from_dict(cookies_dict)
                            logger.info(f"Загружено cookie из {cookies_file}: {len(cookies_dict)}")
                            cookies_loaded = True
                    else:
                        logger.info(f"Файл {cookies_file} не найден")
                        
                except Exception as e:
                    logger.warning(f"Ошибка загрузки cookie из файла: {e}")
            
            if not cookies_loaded:
                logger.warning("Cookie не загружены из всех источников.")
                logger.info("[TIP] Используйте --auth для автоматической авторизации:")
                logger.info(f"[TIP] python main.py --auth --auth-only --faceit-email YOUR_EMAIL --faceit-password YOUR_PASS \"{args.url}\"")
                
        else:
            logger.info("[INFO] Используются fresh cookies из авторизации")
            
        opencv_config = OpenCVConfig()
        
        logger.info("Запуск анализа видео Apex Legends")
        logger.info(f"URL: {args.url}")
        logger.info(f"Карта: {args.map}, POV: {args.pov}")
        
        # Информация о методе извлечения видео
        if args.use_browser:
            logger.info("Метод: Принудительная браузерная автоматизация (устарело)")
        elif args.method == 'browser-automation':
            logger.info("Метод: Старая браузерная автоматизация")
        elif args.method == 'playwright-analyzer':
            logger.info("Метод: Playwright анализатор (наиболее современный)")
        elif args.method == 'hybrid-analyzer':
            logger.info("Метод: Гибридный анализатор")
        elif args.method == 'network-analyzer':
            logger.info("Метод: Простой анализатор сетевых запросов")
        elif args.method == 'http':
            logger.info("Метод: Только HTTP запросы")
        else:
            logger.info("Метод: Автоматический (HTTP → Playwright → Гибридный → Сетевой → Браузерная автоматизация)")
        
        # Инициализация компонентов
        downloader = VideoDownloader(faceit_config)
        processor = VideoProcessor(opencv_config)
        exporter = DataExporter()
        
        if args.stream:
            # Потоковая обработка
            logger.info("Запуск потоковой обработки")
            
            # Определяем метод извлечения видео
            force_browser = args.use_browser or args.method == 'browser-automation'
            
            video_stream = downloader.get_stream(
                args.url, 
                args.map, 
                args.pov,
                force_browser=force_browser
            )
            tracking_data = processor.process_stream(video_stream)
            
            # Экспорт результатов потока
            output_path = args.output or f"data/outputs/{Path(args.url).stem}_stream_results.json"
            exporter.export_data(tracking_data, output_path, url=args.url)
            
        else:
            # Скачивание и обработка файла
            logger.info("Скачивание видео...")
            
            # Определяем метод извлечения видео
            force_browser = args.use_browser or args.method == 'browser-automation'
            
            video_path = downloader.download_video(
                args.url, 
                args.map, 
                args.pov,
                force_browser=force_browser
            )
            logger.info(f"Видео сохранено: {video_path}")
            
            if args.segment:
                # Обработка с сегментацией игр
                logger.info("Обработка видео с автоматической сегментацией игр...")
                
                process_all = args.all_games or (args.game_id is None)
                specific_game = args.game_id
                
                results = processor.process_video_with_segmentation(
                    video_path, 
                    process_all_games=process_all,
                    specific_game_id=specific_game
                )
                
                # Экспорт результатов с сегментацией
                base_name = f"{Path(args.url).stem}_segmented"
                if specific_game:
                    base_name += f"_game{specific_game}"
                    
                output_path = args.output or f"data/outputs/{base_name}.json"
                
                # Экспорт полных результатов с сегментацией
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump(results, f, indent=2, ensure_ascii=False)
                    
                logger.info(f"Результаты сегментированной обработки сохранены: {output_path}")
                
                # Дополнительный экспорт каждой игры отдельно
                for game_id, game_data in results["games_processed"].items():
                    if "error" not in game_data and game_data.get("tracking_results"):
                        game_output = f"data/outputs/{Path(args.url).stem}_game{game_id}.json"
                        exporter.export_data(
                            game_data["tracking_results"], 
                            game_output,
                            url=args.url,
                            game_id=int(game_id)
                        )
                        logger.info(f"Игра {game_id} сохранена отдельно: {game_output}")
                        
            else:
                # Обычная обработка всего видео
                logger.info("Обработка видео...")
                tracking_data = processor.process_video(video_path)
                
                # Экспорт результатов
                output_path = args.output or f"data/outputs/{Path(args.url).stem}_results.json"
                exporter.export_data(tracking_data, output_path, url=args.url)
        
        logger.info(f"Анализ завершен. Результаты сохранены: {output_path}")
        
    except Exception as e:
        logger.error(f"Ошибка выполнения: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()