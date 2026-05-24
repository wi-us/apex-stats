#!/usr/bin/env python3
"""
paddle_ocr_test/run_test.py

Прогоняет PaddleOCR по кропам плашек и сверяет результат со словарём
команд из SQLite (scripts/algs_api/data/algs.sqlite, таблица teams).

Структура входа:
  --crops-dir может быть:
    * плоская папка с PNG       -> GT неизвестен (accuracy не считаем)
    * <crops-dir>/<TAG>/*.png   -> имя папки = ground truth tag

Выход:
  reports/results.json — построчно по каждому файлу
  reports/summary.txt  — accuracy overall + по тегам + топ ошибок
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from paddleocr import PaddleOCR
except ImportError as e:
    raise SystemExit("[err] paddleocr не установлен: pip install paddlepaddle paddleocr") from e

try:
    from rapidfuzz import process as fuzz_process
    from rapidfuzz import fuzz as fuzz_scorer
except ImportError as e:
    raise SystemExit("[err] rapidfuzz не установлен: pip install rapidfuzz") from e


IMG_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def normalize(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


# ---------------------------------------------------------------------------
# Словарь команд
# ---------------------------------------------------------------------------
def load_vocab(db_path: Path, extra_aliases: Optional[Path]) -> Tuple[Dict[str, str], List[str]]:
    """
    Возвращает:
      alias_to_tag: {"GAMBLERS": "GMBL", "GMBL": "GMBL", ...}
      canonical_tags: список канонических коротких тегов
    """
    alias_to_tag: Dict[str, str] = {}
    canonical: List[str] = []

    if db_path.exists():
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT name, short_name FROM teams "
            "WHERE COALESCE(disbanded,0)=0"
        ).fetchall()
        con.close()
        for r in rows:
            full = normalize(r["name"] or "")
            short = normalize(r["short_name"] or "")
            tag = short or full
            if not tag:
                continue
            canonical.append(tag)
            for a in (tag, full, short):
                if a:
                    alias_to_tag.setdefault(a, tag)
            # дополнительно: куски длинных имён (последнее слово)
            words = re.split(r"\s+", (r["name"] or "").upper())
            for w in words:
                nw = normalize(w)
                if len(nw) >= 4:
                    alias_to_tag.setdefault(nw, tag)
        print(f"[vocab] из SQLite: {len(canonical)} команд, {len(alias_to_tag)} алиасов")
    else:
        print(f"[warn] SQLite не найдена: {db_path}")

    if extra_aliases and extra_aliases.exists():
        cfg = json.loads(extra_aliases.read_text(encoding="utf-8"))
        for tag, aliases in (cfg.get("aliases") or {}).items():
            tag_n = normalize(tag)
            if tag_n not in canonical:
                canonical.append(tag_n)
            for a in aliases:
                na = normalize(a)
                if na:
                    alias_to_tag[na] = tag_n
        print(f"[vocab] + aliases.json: итого {len(alias_to_tag)} алиасов")

    return alias_to_tag, sorted(set(canonical))


# ---------------------------------------------------------------------------
# Матчинг OCR-строк против словаря
# ---------------------------------------------------------------------------
def match_vocab(texts: List[Tuple[str, float]],
                alias_to_tag: Dict[str, str]) -> Tuple[Optional[str], float, str]:
    """
    texts: [(raw_text, conf 0..1)]
    Возвращает (tag | None, score 0..100, best_alias).
    """
    if not texts:
        return None, 0.0, ""
    aliases = list(alias_to_tag.keys())
    best: Tuple[Optional[str], float, str] = (None, 0.0, "")
    for raw, conf in texts:
        norm = normalize(raw)
        if len(norm) < 2:
            continue
        if norm in alias_to_tag:
            score = 100.0 * max(conf, 0.3)
            if score > best[1]:
                best = (alias_to_tag[norm], 100.0, norm)
            continue
        m = fuzz_process.extractOne(norm, aliases, scorer=fuzz_scorer.ratio)
        if not m:
            continue
        alias, ratio, _ = m
        # длинный алиас + хороший ratio = доверяем; короткие требуют exact (выше)
        if len(alias) <= 3 and ratio < 100:
            continue
        if ratio < 70:
            continue
        score = ratio * max(conf, 0.3)
        if score > best[1]:
            best = (alias_to_tag[alias], ratio, alias)
    return best


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--crops-dir", required=True, type=Path)
    ap.add_argument("--db", type=Path,
                    default=Path(__file__).resolve().parents[3]
                    / "algs_api" / "data" / "algs.sqlite")
    ap.add_argument("--aliases", type=Path, default=None,
                    help="Опциональный JSON с {'aliases': {TAG: [..]}}")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "reports")
    ap.add_argument("--limit", type=int, default=0,
                    help="0 = все файлы; иначе случайная выборка для быстрого теста")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--use-gpu", action="store_true")
    ap.add_argument("--device", default=None,
                    help="Например cpu | gpu | gpu:0. По умолчанию авто.")
    ap.add_argument("--upscale", type=float, default=2.0,
                    help="Множитель апскейла кропа перед OCR (плашки мелкие).")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    alias_to_tag, canonical = load_vocab(args.db, args.aliases)
    if not alias_to_tag:
        raise SystemExit("[err] словарь пуст — проверь --db / --aliases")

    # Список файлов
    files: List[Tuple[Path, Optional[str]]] = []
    crops = args.crops_dir
    if not crops.exists():
        raise SystemExit(f"[err] нет каталога {crops}")
    # структура с GT (подпапки) или плоская
    subdirs = [p for p in crops.iterdir() if p.is_dir()]
    if subdirs:
        for d in subdirs:
            gt = normalize(d.name) or None
            for p in d.rglob("*"):
                if p.suffix.lower() in IMG_EXTS:
                    files.append((p, gt))
    else:
        for p in crops.rglob("*"):
            if p.suffix.lower() in IMG_EXTS:
                files.append((p, None))
    if not files:
        raise SystemExit(f"[err] нет изображений в {crops}")

    if args.limit and args.limit < len(files):
        import random
        random.seed(42)
        files = random.sample(files, args.limit)
    print(f"[info] файлов на обработку: {len(files)}")

    ocr_kwargs = {"lang": args.lang, "use_textline_orientation": True}
    if args.device:
        ocr_kwargs["device"] = args.device
    elif args.use_gpu:
        ocr_kwargs["device"] = "gpu"
    ocr = PaddleOCR(**ocr_kwargs)

    # OpenCV для апскейла
    import cv2
    import numpy as np

    results: List[dict] = []
    correct = 0
    counted = 0
    by_tag_total: Counter = Counter()
    by_tag_correct: Counter = Counter()
    confusion: Counter = Counter()

    for i, (path, gt) in enumerate(files, 1):
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            continue
        if args.upscale and args.upscale != 1.0:
            img = cv2.resize(img, None, fx=args.upscale, fy=args.upscale,
                             interpolation=cv2.INTER_CUBIC)
        try:
            if hasattr(ocr, "predict"):
                raw = ocr.predict(img)
            else:
                raw = ocr.ocr(img)
        except Exception as e:
            results.append({"file": str(path), "error": str(e), "gt": gt})
            continue
        items: List[Tuple[str, float]] = []
        # Новый API (>=3.x): list[dict] с rec_texts / rec_scores
        if raw and isinstance(raw, list) and raw and isinstance(raw[0], dict):
            res0 = raw[0]
            texts = res0.get("rec_texts") or []
            scores = res0.get("rec_scores") or []
            for t, s in zip(texts, scores):
                try:
                    items.append((str(t), float(s)))
                except (TypeError, ValueError):
                    continue
        # Старый API: [[ [box, (text, conf)], ... ]]
        elif raw and raw[0]:
            for line in raw[0]:
                try:
                    text, conf = line[1][0], float(line[1][1])
                    items.append((text, conf))
                except (IndexError, TypeError, ValueError):
                    continue
        tag, ratio, alias = match_vocab(items, alias_to_tag)
        rec = {
            "file": str(path),
            "gt": gt,
            "ocr": items,
            "matched_tag": tag,
            "match_alias": alias,
            "match_ratio": round(ratio, 2),
        }
        if gt:
            counted += 1
            by_tag_total[gt] += 1
            ok = (tag == gt)
            rec["correct"] = ok
            if ok:
                correct += 1
                by_tag_correct[gt] += 1
            else:
                confusion[(gt, tag or "?")] += 1
        results.append(rec)
        if i % 50 == 0:
            print(f"  ocr {i}/{len(files)}  acc={correct}/{counted}")

    # Запись
    (args.out / "results.json").write_text(
        json.dumps({"items": results}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    lines: List[str] = []
    lines.append(f"files: {len(files)}  with_gt: {counted}")
    if counted:
        acc = correct / counted * 100
        lines.append(f"overall accuracy: {correct}/{counted} = {acc:.1f}%\n")
        lines.append("per-tag accuracy:")
        for tag in sorted(by_tag_total):
            tot = by_tag_total[tag]
            ok = by_tag_correct[tag]
            lines.append(f"  {tag:10s}  {ok:4d}/{tot:<4d}  {ok/tot*100:5.1f}%")
        lines.append("\ntop confusions (gt -> predicted):")
        for (g, p), n in confusion.most_common(30):
            lines.append(f"  {g:10s} -> {p:10s}  x{n}")
    summary = "\n".join(lines)
    (args.out / "summary.txt").write_text(summary, encoding="utf-8")
    print("\n" + summary)
    print(f"\n[ok] reports -> {args.out}")


if __name__ == "__main__":
    main()