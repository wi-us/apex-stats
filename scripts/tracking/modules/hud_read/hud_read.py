"""hud_read — поверхностный проход по VOD: OCR/кропы по зонам /admin/zones.

См. README.md рядом. Цель — увидеть, какие зоны надо подвинуть,
и собрать таймлайн HUD (game/map/teams/players/ring + по командам).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np
from tqdm import tqdm

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None  # type: ignore


MODULE_DIR = Path(__file__).resolve().parent
TRACKING_DIR = MODULE_DIR.parents[1]

# Поля, где не нужен OCR — сохраняем кроп и считаем dHash.
IMAGE_NAMES = {"logo", "hero 1", "hero 2", "hero 3"}

# Поля, состоящие только из цифр.
DIGIT_NAMES = {"pts", "game number", "number of teams alive", "number of players alive"}

# Зоны, которые НЕ должны OCR-иться (используются только как геометрия).
# Например, minimap.camera roi нужна ring_locator/track_teams; OCR по ней
# даёт длинные «стихи» названий команд внутри миникарты и сильно засоряет
# отчёт + замедляет проход.
OCR_SKIP_ZONES: set[tuple[str, str]] = {
    ("minimap", "camera roi"),
}

# Поля, которые в матче не меняются — достаточно зафиксировать первые стабильные значения.
# Для команд "name"/"logo" и для HUD "map name"/"game number".
STATIC_TEAM_NAMES = {"name", "logo"}
STATIC_HUD_NAMES = {"map name", "game number"}

# Известные карты Apex — для snap-фикса OCR-ошибок (G↔C, O↔0 и т.п.).
KNOWN_MAPS = [
    "STORM POINT", "WORLDS EDGE", "KINGS CANYON",
    "OLYMPUS", "BROKEN MOON", "E-DISTRICT",
]

# Словарь известных тегов команд для конкретного матча.
# Заполняется из configs/teams.<match-id>.json через --teams-vocab.
# Используется как snap-цель для OCR (Левенштейн, max_dist зависит от длины).
KNOWN_TEAMS: list[str] = []

RE_MATCH = re.compile(r"MATCH\s+(\d+)", re.I)
RE_TEAMS = re.compile(r"(\d+)\s*TEAMS?", re.I)
RE_PLAYERS = re.compile(r"(\d+)\s*PLAYERS?", re.I)
RE_RING = re.compile(r"RING\s*(\d+).*?(CLOSING|COUNTDOWN|CLOSED|OPEN)", re.I)
# Раздельные «половинки» для случаев, когда OCR-плашки путает символы
# (CLOS1NG, OOUNTDOWN и т.п.) и единый regex не срабатывает.
RE_RING_NUM = re.compile(r"R[I1L]NG\s*([1-9])", re.I)
# В Apex HUD во время COUNTDOWN буквального слова "COUNTDOWN" нет —
# отображается "RING N IN M:SS". Детектим таймер MM:SS / M:SS.
RE_RING_TIMER = re.compile(r"\b\d{1,2}\s*[:.]\s*\d{2}\b")
RING_STATES = ("CLOSING", "COUNTDOWN", "CLOSED", "OPEN")
RE_INT = re.compile(r"-?\d+")
RE_ELIM = re.compile(r"ELIMIN", re.I)
_OCR_ERRORS_SEEN: set[str] = set()

# Эталоны цифр для template-matching fallback (грузим лениво из --digit-templates).
_DIGIT_TPL: dict[str, np.ndarray] = {}
_DIGIT_TPL_LOADED = False


def load_digit_templates(path: Optional[Path]) -> None:
    """Загружает эталоны 0.png..9.png. Бинаризует к {0,255} и инвертирует
    при необходимости, чтобы цифра была чёрной на белом."""
    global _DIGIT_TPL_LOADED
    if _DIGIT_TPL_LOADED:
        return
    _DIGIT_TPL_LOADED = True
    if not path or not path.is_dir():
        return
    for d in "0123456789":
        p = path / f"{d}.png"
        if not p.exists():
            continue
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if img is None or img.size == 0:
            continue
        _, b = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        # хотим digit=чёрный, bg=белый
        if b.mean() < 127:
            b = cv2.bitwise_not(b)
        _DIGIT_TPL[d] = b
    if _DIGIT_TPL:
        print(f"[hud_read] digit-templates loaded: {sorted(_DIGIT_TPL.keys())}", file=sys.stderr)


def ocr_pts_templates(crop: np.ndarray, min_score: float = 0.55) -> str:
    """Fallback: распознать pts через template matching цифр.
    Возвращает строку цифр слева-направо или ''."""
    if not _DIGIT_TPL or crop.size == 0:
        return ""
    h, w = crop.shape[:2]
    # Нормализуем к высоте эталонов.
    tpl_h = next(iter(_DIGIT_TPL.values())).shape[0]
    if h <= 0:
        return ""
    scale = tpl_h / h
    if scale <= 0 or scale > 20:
        return ""
    nh, nw = tpl_h, max(1, int(round(w * scale)))
    big = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY) if big.ndim == 3 else big
    _, b = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if b.mean() < 127:
        b = cv2.bitwise_not(b)
    # Скан слева-направо: для каждого x ищем лучшую цифру.
    hits: list[tuple[int, str, float]] = []  # (x, digit, score)
    for d, tpl in _DIGIT_TPL.items():
        th, tw = tpl.shape[:2]
        if b.shape[1] < tw or b.shape[0] < th:
            continue
        res = cv2.matchTemplate(b, tpl, cv2.TM_CCOEFF_NORMED)
        # все пики >= min_score
        ys, xs = np.where(res >= min_score)
        for y, x in zip(ys, xs):
            hits.append((int(x), d, float(res[y, x])))
    if not hits:
        return ""
    # NMS по x: схлопываем близкие пики (< tpl_w*0.5), оставляя лучший.
    hits.sort(key=lambda t: (t[0], -t[2]))
    tw0 = next(iter(_DIGIT_TPL.values())).shape[1]
    merged: list[tuple[int, str, float]] = []
    for x, d, s in hits:
        if merged and x - merged[-1][0] < tw0 * 0.5:
            if s > merged[-1][2]:
                merged[-1] = (x, d, s)
            continue
        merged.append((x, d, s))
    merged.sort(key=lambda t: t[0])
    return "".join(d for _, d, _ in merged)


# Глобальные кеши, работают на протяжении всего прогона.
# Кеш OCR по (tag, name, dhash(crop)) → последнее распарсенное value.
_OCR_CACHE: dict[tuple[str, str, str], Any] = {}
_OCR_CACHE_HITS = 0
_OCR_CACHE_MISS = 0
# Калибровка: после первой удачной комбинации (psm_idx, prep_idx)
# для зоны фиксируем «победителя» и больше не перебираем варианты.
_OCR_CALIB: dict[tuple[str, str], tuple[int, int]] = {}


# ── helpers ──────────────────────────────────────────────────────────
def resolve_zones_path(arg: Path | None) -> Path:
    if arg is not None:
        return arg
    candidates = [
        MODULE_DIR / "configs" / "zones.vod.json",
        MODULE_DIR / "configs" / "zones.vod2.json",
        TRACKING_DIR / "configs" / "zones.vod.json",
        TRACKING_DIR / "configs" / "zones.vod2.json",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise SystemExit(
        "[hud_read] zones JSON не найден. Положи экспорт из /admin/zones в\n"
        f"  {candidates[0]}\nили передай --zones явно."
    )


def load_zones(path: Path) -> tuple[list[dict[str, Any]], tuple[int, int]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    base = data.get("base") or [1920, 1080]
    zones = data.get("zones") or []
    return zones, (int(base[0]), int(base[1]))


def dhash(img: np.ndarray, hash_size: int = 8) -> str:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    resized = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
    diff = resized[:, 1:] > resized[:, :-1]
    bits = 0
    for b in diff.flatten():
        bits = (bits << 1) | int(b)
    return f"{bits:0{hash_size * hash_size // 4}x}"


def preprocess_for_ocr(crop: np.ndarray) -> np.ndarray:
    if crop.size == 0:
        return crop
    h, w = crop.shape[:2]
    # Тянем до ~64px высоты строки — мелкие цифры pts вроде "11" иначе склеиваются.
    scale = max(2, int(round(64 / max(1, h))))
    big = cv2.resize(crop, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY) if big.ndim == 3 else big
    # HUD Apex бывает и белый-на-тёмном, и тёмно-красный на светлом
    # (например, "20 TEAMS"/"60 PLAYERS"). Возвращаем ОБА варианта —
    # OCR прогонит и выберет лучший.
    _, th1 = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th2 = cv2.bitwise_not(th1)
    pad = lambda im: cv2.copyMakeBorder(im, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=255)
    return pad(th1), pad(th2)


def preprocess_for_ocr_strong(crop: np.ndarray) -> tuple[np.ndarray, ...]:
    """Агрессивный фолбэк для крошечных цифр (team_20.pts и т.п.):
    апскейл x6, лёгкий blur + Otsu, инверсия, дополнительно adaptive threshold.
    Возвращает несколько вариантов препроцесса для прогона через tesseract."""
    if crop.size == 0:
        return ()
    h, w = crop.shape[:2]
    scale = 6
    big = cv2.resize(crop, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY) if big.ndim == 3 else big
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, ot = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ot_inv = cv2.bitwise_not(ot)
    ad = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY, 25, 8)
    pad = lambda im: cv2.copyMakeBorder(im, 12, 12, 12, 12,
                                        cv2.BORDER_CONSTANT, value=255)
    return pad(ot), pad(ot_inv), pad(ad)


def ocr(crop: np.ndarray, lang: str, digits_only: bool, alnum_only: bool = False,
        calib_key: Optional[tuple[str, str]] = None) -> str:
    if pytesseract is None or crop.size == 0:
        return ""
    preps = preprocess_for_ocr(crop)
    if not isinstance(preps, tuple):
        preps = (preps,)
    whitelist = ""
    if digits_only:
        whitelist = "0123456789"
    elif alnum_only:
        # Не добавляем пробелы/апострофы в whitelist: pytesseract передаёт config
        # через парсер аргументов, и такие символы на Windows дают "No closing quotation".
        whitelist = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    # Пробуем несколько PSM и берём непустой/самый длинный результат.
    # Если для этой зоны уже зафиксирована «победившая» комбинация —
    # используем только её (экономия x3–x6 на вызовах tesseract).
    psms = (7, 8, 6) if not digits_only else (8, 7, 10)
    locked = _OCR_CALIB.get(calib_key) if calib_key is not None else None
    if locked is not None:
        psm_i, prep_i = locked
        try:
            prep = preps[prep_i]
            psm = psms[psm_i]
            cfg = f"--psm {psm} --oem 1"
            if whitelist:
                cfg += f" -c tessedit_char_whitelist={whitelist}"
            locked_txt = pytesseract.image_to_string(prep, lang=lang, config=cfg).strip()
        except Exception as e:  # pragma: no cover
            msg = str(e)
            if msg not in _OCR_ERRORS_SEEN:
                _OCR_ERRORS_SEEN.add(msg)
                print(f"[hud_read] tesseract error: {msg}", file=sys.stderr)
            return ""
        if locked_txt or not digits_only:
            return locked_txt
        # digits_only + пусто → проваливаемся в strong-фолбэк ниже
        best = ""
    else:
        best = ""
    best_combo: Optional[tuple[int, int]] = None
    for prep_i, prep in enumerate(preps):
        if locked is not None:
            break  # уже пробовали залоченную комбу — сразу к strong-фолбэку
        for psm_i, psm in enumerate(psms):
            cfg = f"--psm {psm} --oem 1"
            if whitelist:
                cfg += f" -c tessedit_char_whitelist={whitelist}"
            try:
                txt = pytesseract.image_to_string(prep, lang=lang, config=cfg).strip()
            except Exception as e:  # pragma: no cover
                msg = str(e)
                if msg not in _OCR_ERRORS_SEEN:
                    _OCR_ERRORS_SEEN.add(msg)
                    print(f"[hud_read] tesseract error: {msg}", file=sys.stderr)
                return ""
            if len(txt) > len(best):
                best = txt
                best_combo = (psm_i, prep_i)
    if calib_key is not None and best and best_combo is not None:
        _OCR_CALIB[calib_key] = best_combo
    # Фолбэк для крошечных цифр (например, team_20.pts): если основной
    # проход вернул пустоту для digits_only — пробуем сильный препроцесс
    # (×6 апскейл + Otsu/adaptive) с psm=10/8/13. Калибровку не трогаем.
    if digits_only and not best:
        strong = preprocess_for_ocr_strong(crop)
        wl = whitelist or "0123456789"
        for psm in (10, 8, 13):
            cfg = f"--psm {psm} --oem 1 -c tessedit_char_whitelist={wl}"
            for prep in strong:
                try:
                    txt = pytesseract.image_to_string(prep, lang=lang, config=cfg).strip()
                except Exception:
                    txt = ""
                if txt:
                    return txt
    return best


def snap_to_known(text: str, vocab: list[str], max_dist: int = 2) -> str | None:
    """Возвращает ближайшее слово из vocab по расстоянию Левенштейна, либо None."""
    if not text:
        return None
    t = re.sub(r"[^A-Z0-9' -]", "", text.upper()).strip()
    if not t:
        return None
    if t in vocab:
        return t
    # маленькая реализация расстояния (vocab короткий)
    def lev(a: str, b: str) -> int:
        if a == b:
            return 0
        if not a:
            return len(b)
        if not b:
            return len(a)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cur.append(min(cur[-1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb)))
            prev = cur
        return prev[-1]
    # Расширяем кандидата через таблицу типовых OCR-конфьюжнов
    # (буква↔цифра, похожие глифы). Каждый вариант пробуем как t.
    variants = _ocr_confusion_variants(t)
    best, best_d = None, max_dist + 1
    for cand in variants:
        if cand in vocab:
            return cand
        for w in vocab:
            d = lev(cand, w)
            if d < best_d:
                best, best_d = w, d
    return best


# Типовые ошибки Tesseract на HUD-шрифте Apex. Двунаправленные пары.
_OCR_CONFUSIONS: dict[str, str] = {
    "0": "O", "O": "0",
    "1": "I", "I": "1",
    "5": "S", "S": "5",
    "8": "B", "B": "8",
    "2": "Z", "Z": "2",
    "6": "G", "G": "6",
    "D": "O",  # DINO часто читается как CINO/OINO — D→O редко, D→C чаще, но C→D добавим обратно
    "C": "D",
}


def _ocr_confusion_variants(text: str, max_swaps: int = 2) -> list[str]:
    """Возвращает text + все варианты с ≤max_swaps заменами по таблице конфьюжнов.
    Длинные строки не раздуваем — режем до 32 вариантов."""
    out: list[str] = [text]
    seen = {text}
    frontier = [text]
    for _ in range(max_swaps):
        next_frontier: list[str] = []
        for s in frontier:
            for i, ch in enumerate(s):
                rep = _OCR_CONFUSIONS.get(ch)
                if rep is None:
                    continue
                v = s[:i] + rep + s[i + 1:]
                if v not in seen:
                    seen.add(v)
                    out.append(v)
                    next_frontier.append(v)
                    if len(out) >= 32:
                        return out
        frontier = next_frontier
    return out


def parse_field(tag: str, name: str, text: str) -> Any:
    if name == "game number":
        m = RE_MATCH.search(text) or RE_INT.search(text)
        return int(m.group(1) if m.re is RE_MATCH else m.group(0)) if m else None
    if name == "number of teams alive":
        m = RE_TEAMS.search(text) or RE_INT.search(text)
        return int(m.group(1) if m.re is RE_TEAMS else m.group(0)) if m else None
    if name == "number of players alive":
        m = RE_PLAYERS.search(text) or RE_INT.search(text)
        return int(m.group(1) if m.re is RE_PLAYERS else m.group(0)) if m else None
    if name == "ring status":
        m = RE_RING.search(text)
        if not m:
            return None
        return {"ring": int(m.group(1)), "state": m.group(2).upper()}
    if name == "pts":
        m = RE_INT.search(text)
        if not m:
            return None
        try:
            v = int(m.group(0))
        except ValueError:
            return None
        # Apex score: реально 0..~120 в матче. Берём 0..199 как мягкий sanity-фильтр —
        # отсекаем явный OCR-мусор, но не теряем валидные значения.
        if v < 0 or v > 199:
            return None
        return v
    if name == "eliminated":
        return bool(RE_ELIM.search(text))
    if name == "map name":
        cleaned = re.sub(r"[^A-Z' -]", "", text.upper()).strip()
        snapped = snap_to_known(cleaned, KNOWN_MAPS, max_dist=3)
        return snapped or (cleaned or None)
    if name == "name":  # team tag, e.g. "TSM"
        cleaned = re.sub(r"[^A-Z0-9]", "", text.upper()).strip()
        if not cleaned:
            return None
        # Apex теги: 2..5 символов. Всё остальное — почти всегда OCR-мусор.
        if not (2 <= len(cleaned) <= 5):
            return None
        if KNOWN_TEAMS:
            # len==2 → max 1 (иначе "EE"→"TL"); len>=3 → max 2 (нужно для "820"→"S2", "JDDG"→"JDG").
            max_dist = 1 if len(cleaned) <= 2 else 2
            snapped = snap_to_known(cleaned, KNOWN_TEAMS, max_dist=max_dist)
            # Если словарь задан — отвергаем чтения, не попавшие в snap.
            # Так HH (3 голоса, snap=None) проиграет TL (2 голоса, snap=TL)
            # при голосовании в static_state.
            return snapped
        return cleaned
    return text or None


def team_slot(tag: str) -> int | None:
    m = re.match(r"team[_ ]?(\d+)", tag, re.I)
    return int(m.group(1)) if m else None


# ── overlay ──────────────────────────────────────────────────────────
def draw_overlay(frame: np.ndarray, zones_scaled: list[dict], values: dict[str, Any]) -> np.ndarray:
    out = frame.copy()
    for z in zones_scaled:
        x, y, w, h = z["x"], z["y"], z["w"], z["h"]
        color = (0, 200, 255) if z["tag"] == "hud" else (80, 220, 120)
        cv2.rectangle(out, (x, y), (x + w, y + h), color, 1)
        label = f"{z['tag']}/{z['name']}"
        v = values.get(z["id"])
        if v is not None:
            label += f" = {v}"
        cv2.putText(out, label, (x, max(12, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA)
    return out


# ── scout (reverse) ──────────────────────────────────────────────────
def read_frame(cap: cv2.VideoCapture, f: int) -> np.ndarray | None:
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, f))
    ok, frame = cap.read()
    return frame if ok else None


def is_eliminated_at(cap: cv2.VideoCapture, f: int, zone: dict, lang: str) -> bool | None:
    """True/False — определён ли elim в кадре, None — кадр не прочитался."""
    frame = read_frame(cap, f)
    if frame is None:
        return None
    x, y, w, h = zone["x"], zone["y"], zone["w"], zone["h"]
    crop = frame[y:y + h, x:x + w]
    if crop.size == 0:
        return None
    txt = ocr(crop, lang, digits_only=False, alnum_only=True,
              calib_key=(zone["tag"], zone["name"]))
    return bool(RE_ELIM.search(txt))


def read_ring_at(cap: cv2.VideoCapture, f: int, zone: dict,
                 lang: str) -> dict | None:
    """Возвращает {'ring':int,'state':str} или None если кадр/ocr пуст."""
    frame = read_frame(cap, f)
    if frame is None:
        return None
    x, y, w, h = zone["x"], zone["y"], zone["w"], zone["h"]
    crop = frame[y:y + h, x:x + w]
    if crop.size == 0:
        return None
    # Никаких цветовых масок: читаем плашку как обычный текст.
    # Готовим несколько grayscale-вариантов (Otsu + инверсия + adaptive),
    # на upscale ×3, и прогоняем каждый через tesseract с whitelist,
    # включающим ':' и '.', иначе таймер "M:SS" никогда не распарсится
    # (а без него состояние COUNTDOWN не определяется).
    txt = _ocr_ring_text(crop, lang)
    if not txt:
        return None
    up = txt.upper()
    # Сначала номер кольца (CLOSING/CLOSED/COUNTDOWN все начинаются с "RING N").
    mn = RE_RING_NUM.search(up)
    if not mn:
        return None
    ring_n = int(mn.group(1))
    # Приоритет состояний: CLOSED > CLOSING > COUNTDOWN (по таймеру/слову).
    # 1) явные ключевые слова в OCR-выхлопе
    state: str | None = None
    if "CLOSED" in up:
        state = "CLOSED"
    elif "COUNTDOWN" in up or "COUNT" in up or "NTDOWN" in up:
        state = "COUNTDOWN"
    elif "CLOSING" in up or "CLOS" in up:  # OCR любит ломать "CLOSING" → "CLOS1NG"/"CLOSNG"
        # snap: проверим, что это именно CLOSING, а не CLOSED
        snapped = snap_to_known(up, ["CLOSING", "CLOSED"], max_dist=2)
        state = snapped or "CLOSING"
    # 2) если ничего из слов — но виден таймер MM:SS → это COUNTDOWN ("RING N IN M:SS")
    if state is None and RE_RING_TIMER.search(up):
        state = "COUNTDOWN"
    # 3) последний шанс — fuzzy по словарю
    if state is None:
        state = snap_to_known(up, list(RING_STATES), max_dist=2)
    if state is None:
        return None
    return {"ring": ring_n, "state": state}


def _ocr_ring_text(crop: np.ndarray, lang: str) -> str:
    """Чисто-текстовый OCR для плашки кольца. Возвращает лучший вариант,
    в котором видно ``RING N`` (если такого нет — самый длинный)."""
    if pytesseract is None or crop.size == 0:
        return ""
    h0, w0 = crop.shape[:2]
    scale = max(2, int(round(96 / max(1, h0))))  # ~96px высоты строки
    big = cv2.resize(crop, (w0 * scale, h0 * scale),
                     interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY) if big.ndim == 3 else big
    # Лёгкое сглаживание против JPEG-шума плашки.
    gray = cv2.bilateralFilter(gray, 5, 40, 40)

    variants: list[np.ndarray] = []
    # 1) Otsu прямой (тёмный текст на светлом)
    _, v1 = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(v1)
    # 2) Otsu инвертированный (светлый текст на тёмном)
    variants.append(cv2.bitwise_not(v1))
    # 3) Adaptive mean — устойчив к неоднородному фону игры под плашкой
    v3 = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY, 31, 10)
    variants.append(v3)
    variants.append(cv2.bitwise_not(v3))

    pad = lambda im: cv2.copyMakeBorder(im, 12, 12, 12, 12,
                                        cv2.BORDER_CONSTANT, value=255)
    # ВАЖНО: в whitelist обязательно ':' и '.' для таймера M:SS,
    # иначе COUNTDOWN не распознать.
    whitelist = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:."
    cfg_base = f"--oem 1 -c tessedit_char_whitelist={whitelist}"
    best = ""
    for v in variants:
        prep = pad(v)
        for psm in (7, 6, 11):
            cfg = f"--psm {psm} {cfg_base}"
            try:
                t = pytesseract.image_to_string(prep, lang=lang,
                                                config=cfg).strip()
            except Exception as e:  # pragma: no cover
                msg = str(e)
                if msg not in _OCR_ERRORS_SEEN:
                    _OCR_ERRORS_SEEN.add(msg)
                    print(f"[hud_read] tesseract error: {msg}",
                          file=sys.stderr)
                return ""
            up = t.upper()
            # Идеальный исход — видно номер кольца, возвращаем сразу.
            if RE_RING_NUM.search(up) and (
                RE_RING_TIMER.search(up)
                or "CLOSING" in up or "CLOSED" in up or "COUNT" in up
            ):
                return t
            if len(t) > len(best):
                best = t
    return best


def _ring_state_key(rs: dict | None) -> tuple | None:
    if not rs:
        return None
    return (rs["ring"], rs["state"])


def scout_rings(cap: cv2.VideoCapture, zones_scaled: list[dict],
                start_f: int, end_f: int, fps: float,
                scout_step: int, lang: str,
                refine_budget: int = 10,
                refine_linear: int = 4) -> dict[str, Any]:
    """Coarse forward-pass + find-then-rollback для каждого ring.

    Алгоритм: проходим вперёд крупным шагом, фиксируем все samples;
    затем для каждого ring N, у которого видели CLOSING, откатываемся
    назад чтобы найти точный t_closing_start[N] (граница ≠CLOSING(N) →
    CLOSING(N)) и t_countdown_start[N] (граница ≠COUNTDOWN(N) →
    COUNTDOWN(N)). Не требует знать тайминги колец заранее.
    """
    ring_zone = next(
        (z for z in zones_scaled if z["tag"] == "hud" and z["name"] == "ring status"),
        None,
    )
    if ring_zone is None:
        print("[hud_read][ring-scout] зона 'ring status' не найдена")
        return {"transitions": [], "phases": []}

    return _scout_rings_with_zone(cap, ring_zone, start_f, end_f, fps,
                                  scout_step, lang, refine_budget, refine_linear)


def save_ring_debug_screenshots(cap: cv2.VideoCapture, zones_scaled: list[dict],
                                start_f: int, end_f: int, fps: float,
                                step_sec: float, out_dir: Path,
                                lang: str) -> int:
    """Каждые step_sec секунд сохраняет PNG с кропом зоны 'ring status'
    + аннотацией распарсенного состояния. Полезно для глазной проверки
    того, что зона выровнена и таймер/CLOSING читается."""
    if step_sec <= 0:
        return 0
    ring_zone = next(
        (z for z in zones_scaled if z["tag"] == "hud" and z["name"] == "ring status"),
        None,
    )
    if ring_zone is None:
        print("[hud_read][ring-debug] зона 'ring status' не найдена")
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    step_f = max(1, int(round(step_sec * fps)))
    f = max(0, start_f)
    saved = 0
    pbar = tqdm(total=max(1, (end_f - f) // step_f + 1),
                unit="shot", desc="ring-debug", dynamic_ncols=True)
    while f < end_f:
        frame = read_frame(cap, f)
        if frame is None:
            f += step_f
            pbar.update(1)
            continue
        x, y, w, h = ring_zone["x"], ring_zone["y"], ring_zone["w"], ring_zone["h"]
        crop = frame[y:y + h, x:x + w]
        rs = read_ring_at(cap, f, ring_zone, lang) if crop.size else None
        # Аннотация: t, state — пишем над кропом.
        label_state = (f"R{rs['ring']} {rs['state']}" if rs else "—")
        t_sec = f / fps
        label = f"f={f} t={t_sec:.1f}s  {label_state}"
        # Увеличиваем кроп x2 для читаемости, добавляем верхнюю полосу под текст.
        if crop.size:
            big = cv2.resize(crop, (max(1, crop.shape[1] * 2),
                                    max(1, crop.shape[0] * 2)),
                             interpolation=cv2.INTER_NEAREST)
        else:
            big = np.zeros((40, 200, 3), dtype=np.uint8)
        bar = np.zeros((28, big.shape[1], 3), dtype=np.uint8)
        cv2.putText(bar, label, (6, 20), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (0, 220, 255), 1, cv2.LINE_AA)
        out_img = np.vstack([bar, big])
        mm = int(t_sec // 60)
        ss = int(t_sec - mm * 60)
        fname = f"ring_t{mm:03d}m{ss:02d}s_f{f:07d}.png"
        cv2.imwrite(str(out_dir / fname), out_img)
        saved += 1
        pbar.update(1)
        f += step_f
    pbar.close()
    print(f"[hud_read][ring-debug] saved {saved} screenshots → {out_dir}")
    return saved


def _build_runs(samples: list[tuple[int, dict | None]],
                gap_tolerance: int = 1) -> list[dict]:
    """Из coarse-сэмплов строим непрерывные runs одного (ring,state).
    Одиночные «выбросы» (≠ текущему run) длиной ≤ gap_tolerance
    игнорируются — это либо None-кадр, либо OCR-ошибка.
    Возвращает list[{ring, state, f_start, f_end, n_samples, samples:[f,...]}].
    """
    runs: list[dict] = []
    cur: dict | None = None
    pending_gap = 0  # сколько подряд сэмплов «не наш» внутри run
    for f, rs in samples:
        key = _ring_state_key(rs)
        if cur is None:
            if key is None:
                continue
            cur = {
                "ring": key[0], "state": key[1],
                "f_start": f, "f_end": f, "n_samples": 1,
                "samples": [f],
            }
            pending_gap = 0
            continue
        cur_key = (cur["ring"], cur["state"])
        if key == cur_key:
            cur["f_end"] = f
            cur["n_samples"] += 1
            cur["samples"].append(f)
            pending_gap = 0
        else:
            pending_gap += 1
            if pending_gap > gap_tolerance:
                runs.append(cur)
                if key is None:
                    cur = None
                else:
                    cur = {
                        "ring": key[0], "state": key[1],
                        "f_start": f, "f_end": f, "n_samples": 1,
                        "samples": [f],
                    }
                pending_gap = 0
    if cur is not None:
        runs.append(cur)
    return runs


def _enforce_monotonic(phases: list[dict]) -> tuple[list[dict], list[dict]]:
    """Фильтр: кольца закрываются строго по порядку.
    Возвращает (clean_phases, rejected_phases).
    """
    if not phases:
        return phases, []
    by_ring = {p["ring"]: p for p in phases}
    rings_sorted = sorted(by_ring)
    rejected: list[dict] = []
    # 1) внутри одной фазы: countdown ≤ closing.
    for n in rings_sorted:
        p = by_ring[n]
        tc = p.get("t_countdown_start")
        tcl = p.get("t_closing_start")
        if tc is not None and tcl is not None and tc > tcl + 1.0:
            p["t_countdown_start"] = None
            p["countdown_start_f"] = None
            p.setdefault("confidence", {})["COUNTDOWN"] = "rejected_monotonic"
    # 2) межфазная монотонность: t_countdown_start[N+1] > t_closing_start[N].
    last_anchor: float | None = None
    for n in rings_sorted:
        p = by_ring[n]
        anchor = p.get("t_closing_start") or p.get("t_countdown_start")
        if anchor is None:
            continue
        if last_anchor is not None and anchor < last_anchor:
            # эта фаза «вернулась в прошлое» — фантом.
            rejected.append(p)
            continue
        last_anchor = max(filter(None, [
            p.get("t_closing_start"), p.get("t_countdown_start"),
            last_anchor,
        ]))
    rejected_rings = {r["ring"] for r in rejected}
    clean = [p for p in phases if p["ring"] not in rejected_rings]
    return clean, rejected


def _scout_rings_with_zone(cap, ring_zone, start_f, end_f, fps,
                           scout_step, lang, refine_budget, refine_linear):
    """Внутренняя реализация scout_rings — вынесена, чтобы не дублировать поиск зоны."""

    print(f"[hud_read][ring-scout] coarse pass: step={scout_step} "
          f"frames ({scout_step/fps:.1f}s)")

    # Coarse: собираем samples
    samples: list[tuple[int, dict | None]] = []
    f = max(0, start_f)
    total_iters = max(1, (end_f - f) // scout_step + 1)
    pbar = tqdm(total=total_iters, unit="f", desc="ring-scout", dynamic_ncols=True)
    while f < end_f:
        rs = read_ring_at(cap, f, ring_zone, lang)
        samples.append((f, rs))
        if rs:
            tqdm.write(f"ring f{f:>7} t={f/fps:6.1f}s  R{rs['ring']} {rs['state']}")
        pbar.update(1)
        f += scout_step
    pbar.close()

    # Строим runs — устойчивые отрезки одного (ring,state).
    # Это автоматически отбрасывает одиночные OCR-выбросы
    # (например, "RING 4" → "RING 5" в одном кадре посреди CLOSING(4)).
    runs = _build_runs(samples, gap_tolerance=1)
    # earliest run для каждого (ring,state) — самый ранний и длиной >= 2.
    earliest_runs: dict[tuple[int, str], dict] = {}
    singles: dict[tuple[int, str], int] = {}  # fallback на одиночки
    rejected_singles: list[tuple[int, int, str]] = []  # (f, ring, state)
    for r in runs:
        key = (r["ring"], r["state"])
        if r["n_samples"] >= 2:
            if key not in earliest_runs or r["f_start"] < earliest_runs[key]["f_start"]:
                earliest_runs[key] = r
        else:
            if key not in singles or r["f_start"] < singles[key]:
                singles[key] = r["f_start"]
            rejected_singles.append((r["f_start"], r["ring"], r["state"]))
    for f_out, ring_out, st_out in rejected_singles:
        # Если для этого ключа есть нормальный run — single был шумом.
        if (ring_out, st_out) in earliest_runs:
            tqdm.write(f"[ring-scout] outlier dropped: f={f_out} "
                       f"t={f_out/fps:.1f}s R{ring_out} {st_out}")

    def _rollback_start(target_ring: int, target_state: str,
                        f_hi_known: int) -> tuple[int | None, str]:
        """Найти кадр f, где впервые наблюдается (target_ring, target_state).
        Stage 1: идём назад шагами по 50 кадров до первого кадра, где
        state != target — это примерная нижняя граница.
        Stage 2: уточняем границу шагами по 4 кадра (вперёд от f_lo).
        Stage 3: линейный доводчик до кадровой точности.
        Возвращает (f_start, confidence)."""
        target = (target_ring, target_state)
        # Stage 1 — крупный обратный скан шагом 50 кадров.
        coarse_back_step = 50
        f_hi = f_hi_known
        f_lo: int | None = None
        cur = f_hi - coarse_back_step
        max_back_iters = max(1, (f_hi_known // coarse_back_step) + 2)
        iters = 0
        while cur >= 0 and iters < max_back_iters:
            rs = read_ring_at(cap, cur, ring_zone, lang)
            k = _ring_state_key(rs)
            if k == target:
                f_hi = cur
            else:
                f_lo = cur
                break
            cur -= coarse_back_step
            iters += 1
        if f_lo is None:
            # Упёрлись в t=0, значит фаза началась с самого начала видео.
            return (f_hi, "boundary")
        # Stage 2 — уточнение шагами по 4 кадра вперёд от f_lo до f_hi.
        # Окно после Stage 1 ≤ 50 кадров, шаг 4 даёт точность ~0.07s @60fps.
        confidence = "high"
        last_unknown = False
        fine_step = 4
        cur_fwd = f_lo + fine_step
        while cur_fwd < f_hi:
            rs = read_ring_at(cap, cur_fwd, ring_zone, lang)
            k = _ring_state_key(rs)
            if k == target:
                f_hi = cur_fwd
                break
            elif k is None:
                last_unknown = True
                # пустой кадр — продолжаем, не сдвигая f_lo агрессивно
            else:
                f_lo = cur_fwd
            cur_fwd += fine_step
        # Stage 3 — линейный доводчик до кадровой точности.
        linear_used = 0
        while f_hi - f_lo > 1 and linear_used < refine_linear:
            cur2 = f_lo + 1
            rs = read_ring_at(cap, cur2, ring_zone, lang)
            k = _ring_state_key(rs)
            if k is None:
                f_lo = cur2
                last_unknown = True
            elif k == target:
                f_hi = cur2
            else:
                f_lo = cur2
            linear_used += 1
        if last_unknown:
            confidence = "medium"
        return (f_hi, confidence)

    def _verify_phase(target_ring: int, target_state: str,
                      f_start: int) -> bool:
        """Sanity-check: после rollback хотя бы один из 3 кадров вперёд
        в окне [f_start, f_start + scout_step//2] должен подтвердить state."""
        window = max(2, scout_step // 2)
        probes = [f_start, f_start + window // 4, f_start + window // 2]
        for fp in probes:
            if fp >= end_f:
                continue
            rs = read_ring_at(cap, fp, ring_zone, lang)
            if rs and rs["ring"] == target_ring and rs["state"] == target_state:
                return True
        return False

    # Только ring-ы, у которых есть хоть один устойчивый run.
    rings_to_resolve = sorted({k[0] for k in earliest_runs.keys()})
    print(f"[hud_read][ring-scout] coarse found rings: {rings_to_resolve} "
          f"(via {len(earliest_runs)} runs, {len(singles)} singles ignored) "
          f"(rollback budget binary≤{refine_budget} + linear≤{refine_linear})")

    phases_map: dict[int, dict] = {}
    transitions: list[dict] = []
    for ring_n in rings_to_resolve:
        ph = phases_map.setdefault(ring_n, {
            "ring": ring_n,
            "countdown_start_f": None, "t_countdown_start": None,
            "closing_start_f": None,   "t_closing_start": None,
            "closed_f": None,          "t_closed": None,
        })
        for state, field_f, field_t in (
            ("COUNTDOWN", "countdown_start_f", "t_countdown_start"),
            ("CLOSING",   "closing_start_f",   "t_closing_start"),
            ("CLOSED",    "closed_f",          "t_closed"),
        ):
            run = earliest_runs.get((ring_n, state))
            if run is None:
                continue
            f_obs = run["f_start"]
            f_start, conf = _rollback_start(ring_n, state, f_obs)
            if f_start is None:
                continue
            # Sanity: подтверждаем, что найденная точка действительно
            # принадлежит фазе (а не сошлась к границе шума).
            if not _verify_phase(ring_n, state, f_start):
                tqdm.write(f"  R{ring_n} {state}: VERIFY-FAIL "
                           f"@f={f_start} t={f_start/fps:.2f}s — пропускаем")
                continue
            ph[field_f] = f_start
            ph[field_t] = round(f_start / fps, 3)
            if conf != "high":
                ph.setdefault("confidence", {})[state] = conf
            transitions.append({
                "f": f_start,
                "t": round(f_start / fps, 3),
                "to": {"ring": ring_n, "state": state},
                "confidence": conf,
                "refine_method": "rollback",
            })
            tqdm.write(
                f"  R{ring_n} {state}: f={f_start} t={f_start/fps:.2f}s "
                f"(run x{run['n_samples']} @t={f_obs/fps:.1f}s) {conf}"
            )

    phases = [phases_map[k] for k in sorted(phases_map)]
    phases, rejected = _enforce_monotonic(phases)
    if rejected:
        for p in rejected:
            tqdm.write(f"[ring-scout] phantom phase rejected by monotonic: R{p['ring']}")
        # вычистить transitions с этих ring-ов
        rej_rings = {p["ring"] for p in rejected}
        transitions = [tr for tr in transitions if tr["to"]["ring"] not in rej_rings]
    derived = _derive_ring_constants(phases, fps)
    return {"transitions": transitions, "phases": phases, "derived": derived}


def _derive_ring_constants(phases: list[dict], fps: float) -> dict:
    """Из таймингов phases считаем константы: длительности CLOSING/COUNTDOWN.
    Если у фазы N не известен t_closed — оцениваем его как
    t_closing_start[N+1] − median(countdown_duration).
    """
    closing_durs: list[float] = []
    countdown_durs: list[float] = []
    for p in phases:
        if p.get("t_closing_start") is not None and p.get("t_closed") is not None:
            closing_durs.append(p["t_closed"] - p["t_closing_start"])
    by_ring = {p["ring"]: p for p in phases}
    for n, p in by_ring.items():
        nxt = by_ring.get(n + 1)
        if not nxt:
            continue
        if p.get("t_closed") is not None and nxt.get("t_closing_start") is not None:
            countdown_durs.append(nxt["t_closing_start"] - p["t_closed"])

    def med(xs: list[float]) -> float | None:
        if not xs:
            return None
        xs = sorted(xs)
        return round(xs[len(xs) // 2], 2)

    median_countdown = med(countdown_durs)
    # Доводка пропущенных t_closed по медианному countdown.
    if median_countdown is not None:
        for n, p in by_ring.items():
            nxt = by_ring.get(n + 1)
            if not nxt or p.get("t_closed") is not None:
                continue
            t_next = nxt.get("t_closing_start")
            if t_next is None:
                continue
            p["t_closed"] = round(t_next - median_countdown, 2)
            p["closed_f"] = int(round(p["t_closed"] * fps))
            p["closed_confidence"] = "derived"
    return {
        "closing_durations": [round(x, 2) for x in closing_durs],
        "countdown_durations": [round(x, 2) for x in countdown_durs],
        "median_closing": med(closing_durs),
        "median_countdown": median_countdown,
    }


def scout_eliminations(cap: cv2.VideoCapture, zones_scaled: list[dict],
                       start_f: int, end_f: int, fps: float,
                       reverse_step: int, lang: str,
                       refine_budget: int = 10,
                       refine_linear: int = 4,
                       refine_rollback: int = 0) -> dict[int, dict[str, Any]]:
    """Идёт от end_f к start_f крупным шагом, читает только elim-зоны
    у каждой команды. Возвращает {slot: {t_last_alive, f_first_dead, t_first_dead, ...}}.
    Затем для каждой команды бинпоиском уточняет точный кадр перехода."""
    elim_zones: dict[int, dict] = {}
    for z in zones_scaled:
        slot = team_slot(z["tag"])
        if slot is not None and z["name"] == "eliminated":
            elim_zones[slot] = z
    if not elim_zones:
        print("[hud_read][scout] zones 'eliminated' не найдены — нечего сканировать")
        return {}

    print(f"[hud_read][scout] reverse pass: {len(elim_zones)} teams, "
          f"step={reverse_step} frames ({reverse_step/fps:.1f}s)")

    # state[slot] = {"f_first_dead": int|None, "f_last_alive": int|None}
    state: dict[int, dict[str, Any]] = {
        s: {"f_first_dead": None, "f_last_alive": None} for s in elim_zones
    }

    total_iters = max(1, (end_f - start_f) // reverse_step + 1)
    pbar = tqdm(total=total_iters, unit="f", desc="scout-rev", dynamic_ncols=True)
    f = end_f - 1
    while f >= start_f:
        frame = read_frame(cap, f)
        if frame is None:
            f -= reverse_step
            pbar.update(1)
            continue
        alive_count = 0
        dead_count = 0
        for slot, z in elim_zones.items():
            st = state[slot]
            # Слот «решён», когда найдены и f_first_dead, и f_last_alive
            # СТРОГО раньше f_first_dead. Это защищает от пост-гейм оверлея,
            # где "ELIMINATED" исчезает и все слоты выглядят как alive.
            if (st["f_last_alive"] is not None
                    and st["f_first_dead"] is not None
                    and st["f_last_alive"] < st["f_first_dead"]):
                continue
            x, y, w, h = z["x"], z["y"], z["w"], z["h"]
            crop = frame[y:y + h, x:x + w]
            txt = ocr(crop, lang, digits_only=False, alnum_only=True,
                      calib_key=(z["tag"], z["name"]))
            is_dead = bool(RE_ELIM.search(txt))
            if is_dead:
                if st["f_first_dead"] is None or f < st["f_first_dead"]:
                    st["f_first_dead"] = f
                # Если ранее зафиксировали f_last_alive ПОЗЖЕ нового dead
                # (это был пост-гейм "alive") — сбросим, чтобы найти настоящий
                # f_last_alive строго до смерти на следующих шагах назад.
                if (st["f_last_alive"] is not None
                        and st["f_last_alive"] >= st["f_first_dead"]):
                    st["f_last_alive"] = None
                dead_count += 1
            else:
                # Принимаем "alive" только если оно строго раньше уже
                # известного f_first_dead (или f_first_dead ещё неизвестен).
                fd = st["f_first_dead"]
                if st["f_last_alive"] is None and (fd is None or f < fd):
                    st["f_last_alive"] = f
                alive_count += 1
        tqdm.write(f"scout f{f:>7} t={f/fps:6.1f}s  alive={alive_count} dead={dead_count} "
                   f"resolved={sum(1 for s in state.values() if s['f_last_alive'] is not None)}/"
                   f"{len(state)}")
        pbar.update(1)
        # Выход: для каждого слота либо нашли пару (f_first_dead, f_last_alive<f_first_dead),
        # либо мы дошли до start_f и слот действительно никогда не умирал.
        def _resolved(s: dict) -> bool:
            fd, la = s["f_first_dead"], s["f_last_alive"]
            return fd is not None and la is not None and la < fd
        if all(_resolved(s) for s in state.values()):
            break
        f -= reverse_step
    pbar.close()

    # ── refine: бинпоиск + линейный доводчик до кадровой точности ──
    print(f"[hud_read][scout] refine windows: binary≤{refine_budget} + "
          f"linear≤{refine_linear} per team"
          + (f" + rollback step={refine_rollback}" if refine_rollback > 0 else ""))
    for slot, st in state.items():
        lo = st["f_last_alive"]
        hi = st["f_first_dead"]
        if lo is None or hi is None or hi <= lo + 1:
            st["refine_method"] = "none"
            st["refine_window"] = (hi - lo) if (lo is not None and hi is not None) else None
            continue
        z = elim_zones[slot]
        # Stage A — бинпоиск.
        steps = 0
        while hi - lo > 1 and steps < refine_budget:
            mid = (lo + hi) // 2
            v = is_eliminated_at(cap, mid, z, lang)
            if v is None:
                break
            if v:
                hi = mid
            else:
                lo = mid
            steps += 1
        # Stage B — опциональный rollback скаут с мелким шагом
        # внутри окна [lo, hi] (на случай мерцания HUD).
        if refine_rollback > 0 and hi - lo > refine_rollback:
            cur = hi - refine_rollback
            while cur > lo:
                v = is_eliminated_at(cap, cur, z, lang)
                if v is None:
                    break
                if v:
                    hi = cur
                else:
                    lo = cur
                    break
                cur -= refine_rollback
        # Stage C — линейный доводчик: гарантирует кадровую точность.
        linear_used = 0
        while hi - lo > 1 and linear_used < refine_linear:
            cur = lo + 1
            v = is_eliminated_at(cap, cur, z, lang)
            if v is None:
                break
            if v:
                hi = cur
            else:
                lo = cur
            linear_used += 1
        st["f_first_dead"] = hi
        st["f_last_alive"] = lo
        st["refine_method"] = f"binary{steps}+linear{linear_used}"
        st["refine_window"] = hi - lo
        tqdm.write(f"  team {slot:>2}: elim at f~{hi} t~{hi/fps:.1f}s "
                   f"(window {hi-lo} frames, binary={steps} linear={linear_used})")

    # привести к человеку
    result: dict[int, dict[str, Any]] = {}
    for slot, st in sorted(state.items()):
        f_dead = st["f_first_dead"]
        result[slot] = {
            "f_first_dead": f_dead,
            "t_first_dead": round(f_dead / fps, 2) if f_dead is not None else None,
            "f_last_alive": st["f_last_alive"],
            "t_last_alive": round(st["f_last_alive"] / fps, 2)
                             if st["f_last_alive"] is not None else None,
            "refine_method": st.get("refine_method"),
            "refine_window": st.get("refine_window"),
        }
    return result


# ── main loop ────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--zones", type=Path, default=None)
    ap.add_argument("--mode", choices=("forward", "scout", "two-pass"),
                    default="forward",
                    help="forward = обычный проход; scout = только обратный поиск "
                         "таймингов вылетов; two-pass = scout + forward")
    ap.add_argument("--rings", action="store_true",
                    help="Включить высокоточный scout таймингов колец")
    ap.add_argument("--rings-only", action="store_true",
                    help="Только ring-scout (без elim-scout и без forward)")
    ap.add_argument("--ring-scout-step", type=int, default=600,
                    help="Шаг coarse-прохода ring-scout (кадров)")
    ap.add_argument("--ring-start-sec", type=float, default=0.0,
                    help="Нижняя граница окна ring-scout в секундах. "
                         "До этого момента плашка обычно ещё не отрисована.")
    ap.add_argument("--ring-refine-budget", type=int, default=10,
                    help="Бюджет бинпоиска ring-перехода")
    ap.add_argument("--ring-refine-linear", type=int, default=4,
                    help="Линейный доводчик ring-перехода")
    ap.add_argument("--ring-debug-sec", type=float, default=0.0,
                    help="Каждые N секунд сохранять PNG-скриншот зоны "
                         "'ring status' с распарсенным state в reports/ring_debug/. "
                         "0 = выключено. Типично: 30.")
    ap.add_argument("--reverse-step", type=int, default=1800,
                    help="Шаг обратного разведчика (кадров). 1800@30fps ≈ 60с")
    ap.add_argument("--refine-budget", type=int, default=10,
                    help="Сколько проб бинпоиска тратить на уточнение каждого вылета")
    ap.add_argument("--refine-linear", type=int, default=4,
                    help="Линейный доводчик после бинпоиска (кадров на команду)")
    ap.add_argument("--refine-rollback", type=int, default=0,
                    help="Опциональный rollback-скаут мелким шагом внутри окна (0=off)")
    ap.add_argument("--frame-step", type=int, default=600)
    ap.add_argument("--start-sec", type=float, default=0.0)
    ap.add_argument("--end-sec", type=float, default=0.0)
    ap.add_argument("--ignore-tail-sec", type=float, default=30.0,
                    help="Сколько секунд отрезать от конца анализа, если --end-sec/--end-frame не заданы. "
                         "Защищает scout колец/вылетов от финальной заглушки/пост-гейм экрана.")
    ap.add_argument("--start-frame", type=int, default=-1,
                    help="Точный стартовый кадр (приоритет над --start-sec)")
    ap.add_argument("--end-frame", type=int, default=-1,
                    help="Точный конечный кадр (приоритет над --end-sec)")
    ap.add_argument("--chunk-id", default="",
                    help="Префикс для overlays/crops, чтобы не пересекаться между воркерами")
    ap.add_argument("--eliminations", type=Path, default=None,
                    help="Готовый eliminations.json — пропустить scout в forward-режиме")
    ap.add_argument("--ocr-lang", default="eng")
    ap.add_argument("--tess-cmd", default=None, help="Полный путь к tesseract.exe (Windows)")
    ap.add_argument("--teams-vocab", type=Path, default=None,
                    help="JSON со списком известных тегов команд "
                         "(массив строк ИЛИ объект {\"teams\":[...]})")
    ap.add_argument("--overlay-every", type=int, default=1)
    ap.add_argument("--crop-first-n", type=int, default=3,
                    help="Сохранять кропы текстовых полей только для первых N кадров")
    ap.add_argument("--static-confirm", type=int, default=3,
                    help="Сколько одинаковых подряд значений считаем подтверждением статичного поля")
    ap.add_argument("--static-max-frames", type=int, default=8,
                    help="Максимум попыток на статичное поле, после — фиксируем мажоритарное значение")
    ap.add_argument("--out", type=Path, default=MODULE_DIR / "reports")
    ap.add_argument("--dump-pts", action="store_true",
                    help="Сохранять ВСЕ кропы pts-зон команд в reports/pts_crops/slot_NN/ "
                         "(raw + 4× апскейл). Нужно для отладки team_20 и сбора эталонов цифр.")
    ap.add_argument("--digit-templates", type=Path, default=None,
                    help="Папка с эталонами цифр 0.png..9.png (бинарные, белый фон). "
                         "Если задана — используется как fallback для pts, когда Tesseract вернул пусто/мусор.")
    args = ap.parse_args()

    # Структурный инвариант: hud_read всегда пишет в свой reports/.
    # Если хочешь обновить UI — гоняй sync_to_ui.py, не --out src/data/...
    out_resolved = args.out.resolve()
    if "src" in out_resolved.parts and "data" in out_resolved.parts:
        raise SystemExit(
            f"[hud_read] --out не должен указывать в src/data/* "
            f"(получено: {out_resolved}). Пиши в hud_read/reports/, "
            f"потом синкай через sync_to_ui.py."
        )

    if args.tess_cmd and pytesseract is not None:
        pytesseract.pytesseract.tesseract_cmd = args.tess_cmd

    # ── Словарь команд для snap OCR-результатов ────────────────────
    global KNOWN_TEAMS
    vocab_path = args.teams_vocab
    if vocab_path is None:
        # Авто-поиск: configs/teams.default.json рядом с модулем.
        guess = MODULE_DIR / "configs" / "teams.default.json"
        if guess.exists():
            vocab_path = guess
    if vocab_path and vocab_path.exists():
        try:
            raw = json.loads(vocab_path.read_text(encoding="utf-8"))
            items = raw if isinstance(raw, list) else raw.get("teams", [])
            KNOWN_TEAMS = [
                re.sub(r"[^A-Z0-9]", "", str(s).upper()).strip()
                for s in items
                if isinstance(s, (str, int))
            ]
            KNOWN_TEAMS = [t for t in KNOWN_TEAMS if 2 <= len(t) <= 5]
            print(f"[hud_read] team vocab: {vocab_path} ({len(KNOWN_TEAMS)} tags)")
        except Exception as e:
            print(f"[hud_read] team vocab read error: {e}")
    else:
        print("[hud_read] team vocab: <empty> (snap отключён)")

    zones_path = resolve_zones_path(args.zones)
    zones, (bw, bh) = load_zones(zones_path)
    print(f"[hud_read] zones: {zones_path}  base={bw}x{bh}  count={len(zones)}")

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "overlays").mkdir(exist_ok=True)
    (args.out / "crops").mkdir(exist_ok=True)
    if args.dump_pts:
        (args.out / "pts_crops").mkdir(exist_ok=True)

    # Загружаем эталоны цифр (если папка не задана — авто-поиск рядом с модулем).
    digit_tpl_path = args.digit_templates
    if digit_tpl_path is None:
        guess = MODULE_DIR / "configs" / "digits"
        if guess.is_dir():
            digit_tpl_path = guess
    load_digit_templates(digit_tpl_path)

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise SystemExit(f"[hud_read] не открылся видеофайл: {args.video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    sx, sy = fw / bw, fh / bh
    print(f"[hud_read] video: {fw}x{fh} fps={fps:.2f} frames={total}  scale=({sx:.3f},{sy:.3f})")

    zones_scaled = []
    for z in zones:
        zs = dict(z)
        zs["x"] = max(0, int(round(z["x"] * sx)))
        zs["y"] = max(0, int(round(z["y"] * sy)))
        zs["w"] = max(1, int(round(z["w"] * sx)))
        zs["h"] = max(1, int(round(z["h"] * sy)))
        zones_scaled.append(zs)

    start_f = int(args.start_sec * fps)
    explicit_end = args.end_sec > 0 or args.end_frame >= 0
    end_f = int(args.end_sec * fps) if args.end_sec > 0 else total
    if args.start_frame >= 0:
        start_f = args.start_frame
    if args.end_frame >= 0:
        end_f = args.end_frame
    if not explicit_end and args.ignore_tail_sec > 0 and total > 0:
        trimmed_end = max(start_f + 1, total - int(round(args.ignore_tail_sec * fps)))
        if trimmed_end < end_f:
            print(f"[hud_read] auto-trim tail: end f{end_f} → f{trimmed_end} "
                  f"(-{args.ignore_tail_sec:.1f}s, защита от post-game заглушки)")
            end_f = trimmed_end
    step = max(1, args.frame_step)

    # ── режимы scout / two-pass ────────────────────────────────────
    do_rings = args.rings or args.rings_only or args.mode in ("scout", "two-pass")
    # rings-only — короткий путь
    if args.rings_only:
        rs_start = max(start_f, int(args.ring_start_sec * fps))
        if args.ring_debug_sec > 0:
            save_ring_debug_screenshots(
                cap, zones_scaled, rs_start, end_f, fps,
                step_sec=args.ring_debug_sec,
                out_dir=args.out / "ring_debug",
                lang=args.ocr_lang,
            )
        ring_res = scout_rings(
            cap, zones_scaled, rs_start, end_f, fps,
            scout_step=max(1, args.ring_scout_step),
            lang=args.ocr_lang,
            refine_budget=max(1, args.ring_refine_budget),
            refine_linear=max(0, args.ring_refine_linear),
        )
        (args.out / "rings.json").write_text(
            json.dumps({
                "video": str(args.video),
                "fps": fps,
                "scout_step": args.ring_scout_step,
                "refine_budget": args.ring_refine_budget,
                "refine_linear": args.ring_refine_linear,
                "ring_start_sec": args.ring_start_sec,
                **ring_res,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[hud_read][ring-scout] rings.json → {args.out/'rings.json'}")
        cap.release()
        return 0

    if args.mode in ("scout", "two-pass"):
        if args.ring_debug_sec > 0:
            rs_start_dbg = max(start_f, int(args.ring_start_sec * fps))
            save_ring_debug_screenshots(
                cap, zones_scaled, rs_start_dbg, end_f, fps,
                step_sec=args.ring_debug_sec,
                out_dir=args.out / "ring_debug",
                lang=args.ocr_lang,
            )
        elim = scout_eliminations(cap, zones_scaled, start_f, end_f, fps,
                                  reverse_step=max(1, args.reverse_step),
                                  lang=args.ocr_lang,
                                  refine_budget=max(1, args.refine_budget),
                                  refine_linear=max(0, args.refine_linear),
                                  refine_rollback=max(0, args.refine_rollback))
        (args.out / "eliminations.json").write_text(
            json.dumps({
                "video": str(args.video),
                "fps": fps,
                "mode": args.mode,
                "reverse_step": args.reverse_step,
                "refine_budget": args.refine_budget,
                "refine_linear": args.refine_linear,
                "refine_rollback": args.refine_rollback,
                "teams": elim,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[hud_read][scout] eliminations.json → {args.out/'eliminations.json'}")
        # Ring scout — встроен в scout/two-pass по умолчанию
        rs_start = max(start_f, int(args.ring_start_sec * fps))
        ring_res = scout_rings(
            cap, zones_scaled, rs_start, end_f, fps,
            scout_step=max(1, args.ring_scout_step),
            lang=args.ocr_lang,
            refine_budget=max(1, args.ring_refine_budget),
            refine_linear=max(0, args.ring_refine_linear),
        )
        (args.out / "rings.json").write_text(
            json.dumps({
                "video": str(args.video),
                "fps": fps,
                "scout_step": args.ring_scout_step,
                "refine_budget": args.ring_refine_budget,
                "refine_linear": args.ring_refine_linear,
                "ring_start_sec": args.ring_start_sec,
                **ring_res,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[hud_read][ring-scout] rings.json → {args.out/'rings.json'}")
        if args.mode == "scout":
            cap.release()
            return 0
        # two-pass: сужаем диапазон forward-прохода до старта матча.
        # старт = самый ранний f_last_alive (или 0).
        earliest = min(
            (v["f_last_alive"] for v in elim.values()
             if v["f_last_alive"] is not None),
            default=start_f,
        )
        if earliest > start_f:
            print(f"[hud_read][two-pass] forward-окно сужено до f{earliest}+")
            start_f = earliest
    elif args.eliminations is not None and args.eliminations.exists():
        # forward-воркер: подгружаем готовые тайминги вылетов
        # чтобы пропускать elim-зоны у уже мёртвых команд.
        try:
            data = json.loads(args.eliminations.read_text(encoding="utf-8"))
            elim_loaded: dict[int, dict[str, Any]] = {
                int(k): v for k, v in data.get("teams", {}).items()
            }
            print(f"[hud_read] loaded eliminations for {len(elim_loaded)} teams "
                  f"from {args.eliminations}")
        except Exception as e:
            print(f"[hud_read] eliminations.json read error: {e}")
            elim_loaded = {}
    else:
        elim_loaded = {}

    chunk_prefix = (args.chunk_id + "_") if args.chunk_id else ""

    timeline: list[dict[str, Any]] = []
    # stats[(tag,name)] = {"total":N,"ocr":N,"parsed":N,"values":[...], "hashes":[]}
    stats: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "ocr": 0, "parsed": 0, "values": [], "hashes": []}
    )

    seen_per_field: dict[tuple[str, str], int] = defaultdict(int)
    # Статичные поля: фиксируем итог после N подтверждений (или N попыток).
    static_state: dict[tuple[str, str], dict[str, Any]] = {}
    for z in zones_scaled:
        tag, name = z["tag"], z["name"]
        is_static = (
            (tag == "hud" and name in STATIC_HUD_NAMES)
            or (team_slot(tag) is not None and name in STATIC_TEAM_NAMES)
        )
        if is_static:
            static_state[(tag, name)] = {"locked": None, "votes": defaultdict(int), "tries": 0}

    # Сырая телеметрия OCR для pts — по слотам список (frame, raw_text, parsed).
    pts_raw: dict[int, list[dict[str, Any]]] = defaultdict(list)

    def is_static_key(tag: str, name: str) -> bool:
        return (tag, name) in static_state

    processed = 0
    f = start_f
    total_iters = max(1, (end_f - start_f + step - 1) // step)
    pbar = tqdm(total=total_iters, unit="f", desc="hud-scan", dynamic_ncols=True)
    t0 = time.time()
    skipped_static = 0
    while f < end_f:
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, frame = cap.read()
        if not ok:
            break

        per_zone_value: dict[str, Any] = {}
        snap_hud: dict[str, Any] = {}
        teams_acc: dict[int, dict[str, Any]] = defaultdict(dict)

        for z in zones_scaled:
            tag, name, zid = z["tag"], z["name"], z["id"]
            x, y, w, h = z["x"], z["y"], z["w"], z["h"]
            crop = frame[y:y + h, x:x + w]
            st = stats[(tag, name)]
            st["total"] += 1

            # Зоны, которые помечены как «только геометрия» — пропускаем OCR.
            if (tag, name) in OCR_SKIP_ZONES:
                per_zone_value[zid] = None
                continue

            # Статичное поле уже зафиксировано — просто переиспользуем.
            if is_static_key(tag, name) and static_state[(tag, name)]["locked"] is not None:
                val = static_state[(tag, name)]["locked"]
                per_zone_value[zid] = val
                st["parsed"] += 1
                if name in IMAGE_NAMES:
                    st["hashes"].append(str(val))
                else:
                    st["ocr"] += 1
                skipped_static += 1
                slot = team_slot(tag)
                if slot is not None:
                    teams_acc[slot][name.replace(" ", "_")] = val
                elif tag == "hud":
                    snap_hud[name.replace(" ", "_")] = val
                continue

            if name in IMAGE_NAMES:
                if crop.size:
                    hsh = dhash(crop)
                    st["hashes"].append(hsh)
                    crop_dir = args.out / "crops" / f"{tag}__{name.replace(' ', '_')}"
                    crop_dir.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(crop_dir / f"f{chunk_prefix}{f:07d}.png"), crop)
                    per_zone_value[zid] = hsh[:8]
                    val = hsh
                else:
                    val = None
            else:
                alnum = name in ("name", "map name", "ring status")
                global _OCR_CACHE_HITS, _OCR_CACHE_MISS
                # dHash-кеш: если кроп визуально не изменился —
                # переиспользуем последний распарсенный value.
                crop_hash = dhash(crop) if crop.size else ""
                cache_key = (tag, name, crop_hash)
                if cache_key in _OCR_CACHE:
                    val = _OCR_CACHE[cache_key]
                    _OCR_CACHE_HITS += 1
                    st["ocr"] += 1  # считаем как успех
                    txt = "" if val is None else str(val)
                else:
                    txt = ocr(crop, args.ocr_lang,
                              digits_only=(name in DIGIT_NAMES),
                              alnum_only=alnum,
                              calib_key=(tag, name))
                    if txt:
                        st["ocr"] += 1
                    val = parse_field(tag, name, txt)
                    _OCR_CACHE[cache_key] = val
                    _OCR_CACHE_MISS += 1
                # Сырой дамп для pts — диагностика проблемных слотов.
                if name == "pts":
                    _slot = team_slot(tag)
                    if _slot is not None:
                        # Template-matching fallback: если Tesseract вернул пусто
                        # ИЛИ распарсилось как одиночная цифра 1/7 (типовые false-чтения
                        # двузначных значений у мелких pts), пробуем эталоны.
                        tpl_used = False
                        if _DIGIT_TPL and crop.size:
                            need_tpl = (not txt) or (
                                isinstance(val, int) and 0 <= val <= 9
                            )
                            if need_tpl:
                                tpl_txt = ocr_pts_templates(crop)
                                if tpl_txt:
                                    tpl_val = parse_field(tag, name, tpl_txt)
                                    # принимаем только если шаблон дал ≥ длины tesseract
                                    if tpl_val is not None and (
                                        not txt or len(tpl_txt) > len(txt)
                                    ):
                                        txt = tpl_txt
                                        val = tpl_val
                                        _OCR_CACHE[cache_key] = val
                                        tpl_used = True
                        pts_raw[_slot].append({
                            "frame": f,
                            "raw": (txt or "").strip(),
                            "parsed": val,
                            "via": "tpl" if tpl_used else "ocr",
                        })
                        # Сохранить кропы pts для отладки/сбора эталонов.
                        if args.dump_pts and crop.size:
                            sd = args.out / "pts_crops" / f"slot_{_slot:02d}"
                            sd.mkdir(parents=True, exist_ok=True)
                            cv2.imwrite(str(sd / f"f{chunk_prefix}{f:07d}_raw.png"), crop)
                            ch_, cw_ = crop.shape[:2]
                            big = cv2.resize(crop, (cw_ * 4, ch_ * 4),
                                             interpolation=cv2.INTER_CUBIC)
                            cv2.imwrite(str(sd / f"f{chunk_prefix}{f:07d}_x4.png"), big)
                if val is not None and val is not False:
                    st["parsed"] += 1
                if val not in (None, "", False):
                    st["values"].append(val)
                per_zone_value[zid] = val if val is not None else txt
                # Сохранить кроп для первых N кадров каждого поля.
                key = (tag, name)
                if seen_per_field[key] < args.crop_first_n and crop.size:
                    seen_per_field[key] += 1
                    crop_dir = args.out / "crops" / f"{tag}__{name.replace(' ', '_')}"
                    crop_dir.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(crop_dir / f"f{chunk_prefix}{f:07d}.png"), crop)

            # Голосование для статичных полей.
            if is_static_key(tag, name):
                ss = static_state[(tag, name)]
                ss["tries"] += 1
                if val not in (None, "", False):
                    ss["votes"][val] += 1
                # Зафиксировать если есть N подтверждений или исчерпали бюджет.
                top_val, top_n = (None, 0)
                if ss["votes"]:
                    top_val, top_n = max(ss["votes"].items(), key=lambda kv: kv[1])
                if top_n >= args.static_confirm or ss["tries"] >= args.static_max_frames:
                    ss["locked"] = top_val

            slot = team_slot(tag)
            if slot is not None:
                teams_acc[slot][name.replace(" ", "_")] = val
            elif tag == "hud":
                snap_hud[name.replace(" ", "_")] = val

        timeline.append({
            "frame": f,
            "t": round(f / fps, 3),
            "hud": snap_hud,
            "teams": [{"slot": s, **v} for s, v in sorted(teams_acc.items())],
        })

        if processed % max(1, args.overlay_every) == 0:
            ov = draw_overlay(frame, zones_scaled, per_zone_value)
            cv2.imwrite(str(args.out / "overlays" / f"hud_{chunk_prefix}{f:07d}.jpg"), ov,
                        [cv2.IMWRITE_JPEG_QUALITY, 80])

        # ── живой лог по кадру ─────────────────────────────────────
        alive_t = snap_hud.get("number_of_teams_alive")
        alive_p = snap_hud.get("number_of_players_alive")
        ring = snap_hud.get("ring_status")
        mp = snap_hud.get("map_name")
        gn = snap_hud.get("game_number")
        top_teams = []
        for slot in sorted(teams_acc)[:3]:
            td = teams_acc[slot]
            nm = td.get("name") or f"T{slot}"
            pts = td.get("pts")
            elim = "x" if td.get("eliminated") else ""
            top_teams.append(f"{slot}:{nm}{'/'+str(pts) if pts is not None else ''}{elim}")
        ring_str = (f"R{ring['ring']}{ring['state'][:3]}"
                    if isinstance(ring, dict) else "R?")
        elapsed = time.time() - t0
        rate = processed / elapsed if elapsed > 0 else 0
        line = (f"f{f:>7} t={f/fps:6.1f}s  M{gn or '?'} {mp or '?':<12}"
                f"  {alive_t or '?':>2}T/{alive_p or '?':>2}P  {ring_str:<8}"
                f"  {' '.join(top_teams)}")
        tqdm.write(line)
        pbar.set_postfix(fps=f"{rate:.2f}", static_skip=skipped_static,
                         ocr_cache=f"{_OCR_CACHE_HITS}/{_OCR_CACHE_HITS + _OCR_CACHE_MISS}",
                         refresh=False)
        pbar.update(1)

        processed += 1
        f += step

    pbar.close()
    cap.release()
    total_ocr = _OCR_CACHE_HITS + _OCR_CACHE_MISS
    cache_pct = round(100 * _OCR_CACHE_HITS / total_ocr) if total_ocr else 0
    print(f"[hud_read] processed={processed} static_skips={skipped_static} "
          f"ocr_cache_hits={_OCR_CACHE_HITS}/{total_ocr} ({cache_pct}%) "
          f"elapsed={time.time()-t0:.1f}s")

    # ── reports ─────────────────────────────────────────────────────
    timeline_name = f"hud_timeline.{args.chunk_id}.json" if args.chunk_id else "hud_timeline.json"
    (args.out / timeline_name).write_text(
        json.dumps({
            "video": str(args.video),
            "fps": fps,
            "frame_step": step,
            "zones_source": str(zones_path),
            "chunk_id": args.chunk_id or None,
            "start_frame": start_f,
            "end_frame": end_f,
            "timeline": timeline,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines: list[str] = []
    lines.append(f"hud_read — {processed} frames analyzed (step={step})")
    lines.append(f"video: {args.video}")
    lines.append(f"zones: {zones_path}")
    lines.append("")
    lines.append(f"{'tag':<10} {'name':<26} {'ok%':>5} {'parsed%':>8}  suggest  examples")
    for (tag, name), st in sorted(stats.items()):
        tot = st["total"] or 1
        is_img = name in IMAGE_NAMES
        if (tag, name) in OCR_SKIP_ZONES:
            ok_pct = 0
            parsed_pct = 0
            suggest = "SKIP (geometry only)"
            examples = ""
        elif is_img:
            uniq = len(set(st["hashes"]))
            ok_pct = 100 if st["hashes"] else 0
            parsed_pct = ok_pct
            suggest = "OK"
            if not st["hashes"]:
                suggest = "EMPTY"
            elif uniq <= max(1, tot // 20):
                suggest = "STATIC?  (zone might be misaligned)"
            examples = f"{uniq} unique hashes / {tot} frames"
        else:
            ok_pct = round(100 * st["ocr"] / tot)
            parsed_pct = round(100 * st["parsed"] / tot)
            if ok_pct < 40:
                suggest = "EMPTY/MISALIGNED"
            elif parsed_pct < max(20, ok_pct - 30):
                suggest = "TIGHTEN"
            else:
                suggest = "OK"
            vals = st["values"][:5]
            examples = ", ".join(str(v) for v in vals)
        lines.append(f"{tag:<10} {name:<26} {ok_pct:>4}% {parsed_pct:>7}%  {suggest:<22} {examples}")

    report_name = f"report.{args.chunk_id}.txt" if args.chunk_id else "report.txt"
    (args.out / report_name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))

    # ── Дамп голосов OCR для тегов команд (для отладки snap-словаря) ──
    # Файл team_tags_raw.json — по слотам top-N кандидатов с числом подтверждений.
    tag_votes: dict[int, tuple[str, list[tuple[Any, int]]]] = {}
    for (tag, name), ss in static_state.items():
        slot = team_slot(tag)
        if slot is None or name != "name":
            continue
        votes = sorted(ss.get("votes", {}).items(), key=lambda kv: -kv[1])
        if votes:
            tag_votes[slot] = (tag, votes[:5])
    if tag_votes:
        tags_name = f"team_tags_raw.{args.chunk_id}.json" if args.chunk_id else "team_tags_raw.json"
        (args.out / tags_name).write_text(
            json.dumps(
                {
                    "vocab": KNOWN_TEAMS,
                    "vocab_path": str(vocab_path) if vocab_path else None,
                    "slots": {
                        str(s): {
                            "locked": static_state[(tag, "name")]["locked"],
                            "candidates": [
                                {"value": v, "votes": n} for v, n in votes
                            ],
                        }
                        for s, (tag, votes) in sorted(tag_votes.items())
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[hud_read] team tag votes → {args.out / tags_name}")

    # ── Дамп сырых OCR-кандидатов для pts (диагностика проблемных слотов) ──
    if pts_raw:
        pts_name = f"pts_raw.{args.chunk_id}.json" if args.chunk_id else "pts_raw.json"
        # Сводка по слотам: топ raw-строк и счётчики accepted/rejected.
        from collections import Counter as _Counter
        summary: dict[str, Any] = {}
        for slot, entries in sorted(pts_raw.items()):
            raw_counter: _Counter = _Counter(e["raw"] for e in entries)
            parsed_counter: _Counter = _Counter(
                str(e["parsed"]) for e in entries if e["parsed"] is not None
            )
            accepted = sum(1 for e in entries if e["parsed"] is not None)
            summary[str(slot)] = {
                "frames": len(entries),
                "accepted": accepted,
                "rejected": len(entries) - accepted,
                "top_raw": [{"value": v, "count": n}
                            for v, n in raw_counter.most_common(8)],
                "top_parsed": [{"value": v, "count": n}
                               for v, n in parsed_counter.most_common(5)],
            }
        (args.out / pts_name).write_text(
            json.dumps(
                {"slots": summary,
                 "samples": {str(s): pts_raw[s][:20] for s in sorted(pts_raw)}},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[hud_read] pts raw OCR dump → {args.out / pts_name}")

    print(f"\n[hud_read] OK → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
