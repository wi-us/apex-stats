#!/usr/bin/env python3
"""
ocr_tags.py — OCR-калибратор slot_id → team_tag.

На входе:
  --detections  detect_plates/reports/detections.json (bbox плашек по кадрам)
  --video       исходное видео (то же, что в detect_plates)
  --teams       configs/teams.<match>.json со списком известных тегов
  --out         каталог для slot_tags.json и debug-кропов

На каждом slot_id берём топ-N кадров по score (только source=='detect',
без recovered_level), вырезаем плашку в исходном кадре, апскейлим, маскируем
белый текст, прогоняем Tesseract (psm=7, whitelist=A-Z0-9), голосуем за
ближайший тег через rapidfuzz. Выход: slot_tags.json с {tag, conf, votes, alts}.

Пример:
  python ocr_tags.py \
      --detections ../detect_plates/reports/detections.json \
      --video ../../game_sp.mp4 \
      --teams configs/teams.m-test-g1.json \
      --zones ../../configs/zones.vod.json \
      --out reports --top-n 30
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

try:
    import pytesseract
except ImportError as e:
    raise SystemExit("[err] pytesseract не установлен: pip install pytesseract") from e

try:
    from rapidfuzz import process as fuzz_process
    from rapidfuzz import fuzz as fuzz_scorer
except ImportError as e:
    raise SystemExit("[err] rapidfuzz не установлен: pip install rapidfuzz") from e


TESS_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
def _tess_cfg(psm: int) -> str:
    return f"--oem 1 --psm {psm} -c tessedit_char_whitelist={TESS_WHITELIST}"


# ---------------------------------------------------------------------------
# ROI миникарты из zones.vod.json (повтор логики detect_plates)
# ---------------------------------------------------------------------------
def _scale(z: dict, base_w: int, base_h: int, fw: int, fh: int) -> Tuple[int, int, int, int]:
    sx, sy = fw / base_w, fh / base_h
    x = int(round(z["x"] * sx)); y = int(round(z["y"] * sy))
    w = int(round(z["w"] * sx)); h = int(round(z["h"] * sy))
    x = max(0, min(fw - 1, x)); y = max(0, min(fh - 1, y))
    w = max(1, min(fw - x, w)); h = max(1, min(fh - y, h))
    return x, y, w, h


def pick_minimap_zone(zones_cfg: dict, fw: int, fh: int) -> Tuple[int, int, int, int]:
    base_w, base_h = zones_cfg.get("base", [1920, 1080])
    cands = zones_cfg["zones"]
    match = [z for z in cands if z.get("tag") == "minimap"]
    if not match:
        match = sorted(cands, key=lambda z: z["w"] * z["h"], reverse=True)[:1]
    if not match:
        raise RuntimeError("zones.vod.json: minimap zone not found")
    return _scale(match[0], base_w, base_h, fw, fh)


# ---------------------------------------------------------------------------
# Подготовка изображения плашки под OCR
# ---------------------------------------------------------------------------
def preprocess_variants(roi: np.ndarray, scale: int = 3) -> List[np.ndarray]:
    """
    Возвращает несколько готовых под OCR картинок (чёрный текст на белом):
      v1: Otsu по L, авто-полярность (хорошо для цветного чипа с белым текстом)
      v2: бинаризация светлого текста на тёмной подложке (порог по L >= 180)
      v3: адаптивный threshold (фолбэк)
    """
    if roi.size == 0:
        return []
    big = cv2.resize(roi, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    lab = cv2.cvtColor(big, cv2.COLOR_BGR2LAB)
    L = lab[:, :, 0]

    out: List[np.ndarray] = []
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))

    # v1: Otsu + авто-полярность
    _th, m1 = cv2.threshold(L, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    fg = float(np.mean(m1 > 0))
    if fg > 0.5:
        m1 = cv2.bitwise_not(m1)
    inv1 = cv2.bitwise_not(m1)
    inv1 = cv2.morphologyEx(inv1, cv2.MORPH_CLOSE, k, iterations=1)
    out.append(inv1)

    # v2: явная ветка "светлый текст на тёмном" — без auto-инверсии
    _th2, m2 = cv2.threshold(L, 180, 255, cv2.THRESH_BINARY)
    inv2 = cv2.bitwise_not(m2)
    inv2 = cv2.morphologyEx(inv2, cv2.MORPH_CLOSE, k, iterations=1)
    out.append(inv2)

    # v3: адаптивный
    adp = cv2.adaptiveThreshold(L, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY_INV, 15, 4)
    adp_ratio = float(np.mean(adp > 0))
    if 0.005 < adp_ratio < 0.5:
        inv3 = cv2.bitwise_not(adp)
        out.append(inv3)

    return out


def ocr_one(img: np.ndarray, psm: int = 7) -> Tuple[str, float]:
    """Возвращает (text, mean_conf 0..100)."""
    try:
        data = pytesseract.image_to_data(
            img, config=_tess_cfg(psm), output_type=pytesseract.Output.DICT,
        )
    except pytesseract.TesseractError:
        return "", 0.0
    txts, confs = [], []
    for t, c in zip(data["text"], data["conf"]):
        t = (t or "").strip()
        if not t:
            continue
        try:
            cf = float(c)
        except (TypeError, ValueError):
            cf = -1.0
        if cf < 0:
            continue
        txts.append(t)
        confs.append(cf)
    if not txts:
        return "", 0.0
    text = re.sub(r"[^A-Z0-9]", "", "".join(txts).upper())
    conf = float(np.mean(confs)) if confs else 0.0
    return text, conf


def ocr_multi(roi: np.ndarray) -> List[Tuple[str, float]]:
    """OCR по нескольким препроцессам и нескольким psm. Дедупликация по text."""
    results: Dict[str, float] = {}
    for prep in preprocess_variants(roi):
        for psm in (7, 6):
            t, c = ocr_one(prep, psm=psm)
            if not t:
                continue
            # храним лучшую conf по тексту
            if t not in results or c > results[t]:
                results[t] = c
    return list(results.items())


# ---------------------------------------------------------------------------
# Выбор кадров для OCR
# ---------------------------------------------------------------------------
def pick_samples(detections: dict, top_n: int) -> Dict[str, List[dict]]:
    """slot_id -> top-N записей {frame, t, bbox_roi, score}."""
    by_slot: Dict[str, List[dict]] = defaultdict(list)
    for f in detections.get("frames", []):
        for b in f.get("boxes", []):
            feat = b.get("feat") or {}
            if feat.get("recovered_level"):
                continue
            if b.get("source") != "detect":
                continue
            slot = str(feat.get("team_key") or feat.get("slot")
                       or feat.get("dominant_team_id") or "")
            if not slot or slot == "None":
                continue
            by_slot[slot].append({
                "frame": f["frame"], "t": f.get("t"),
                "bbox": b["bbox"], "score": float(b.get("score", 0.0)),
            })
    # топ-N по score
    out: Dict[str, List[dict]] = {}
    for slot, items in by_slot.items():
        items.sort(key=lambda x: x["score"], reverse=True)
        out[slot] = items[:top_n]
    return out


# ---------------------------------------------------------------------------
# Голосование
# ---------------------------------------------------------------------------
def vote(ocr_results: List[Tuple[str, float, float]],
         alias_to_tag: Dict[str, str],
         min_match_ratio: float = 70.0) -> Tuple[Optional[str], float, dict]:
    """
    ocr_results:  [(text, ocr_conf, detect_score)]
    alias_to_tag: {"GAMBLERS": "GMBL", "GMBL": "GMBL", ...}
    Возвращает (best_tag | None, total_weight, debug_dict)
    """
    alias_keys = list(alias_to_tag.keys())
    weights: Dict[str, float] = defaultdict(float)
    debug = {"raw": [], "rejected": []}
    for text, ocr_conf, score in ocr_results:
        if not text:
            continue
        # Отсекаем мусор: одиночные символы и слишком короткий OCR.
        # "L" не должен матчиться с "ELTE" через WRatio=90.
        if len(text) < 3:
            # Разрешаем только если есть алиас ровно такой же длины и точное совпадение.
            if text in alias_to_tag:
                tag = alias_to_tag[text]
                w = 1.0 * max(ocr_conf / 100.0, 0.2) * max(0.1, score)
                weights[tag] += w
                debug["raw"].append({"text": text, "alias": text, "tag": tag,
                                     "ratio": 100.0, "ocr_conf": ocr_conf,
                                     "score": score, "short_exact": True})
            else:
                debug["rejected"].append({"text": text, "reason": "too_short"})
            continue
        m = fuzz_process.extractOne(text, alias_keys,
                                    scorer=fuzz_scorer.ratio)
        if not m:
            debug["rejected"].append({"text": text, "reason": "no_match"})
            continue
        alias, ratio, _idx = m
        tag = alias_to_tag[alias]
        # OCR-текст должен покрывать большую часть алиаса.
        # С fuzz.ratio (Levenshtein на полных строках) разница длин уже
        # снижает ratio, но дополнительно требуем минимальную длину.
        min_text_len = max(3, int(len(alias) * 0.6))
        if len(text) < min_text_len:
            debug["rejected"].append({"text": text, "alias": alias,
                                      "ratio": ratio,
                                      "reason": "text_shorter_than_alias"})
            continue
        # Пороги по длине алиаса (fuzz.ratio = 100 * 2*matches / (la+lt)):
        # короткие алиасы дают высокий ratio даже на 1-2 совпавших символах,
        # поэтому требуем выше; длинные — ниже из-за OCR-ошибок.
        la = len(alias)
        if la <= 4:
            eff_min = 80.0
        elif la <= 6:
            eff_min = 70.0
        elif la <= 9:
            eff_min = 62.0
        else:
            eff_min = 55.0
        debug["raw"].append({"text": text, "alias": alias, "tag": tag,
                             "ratio": ratio, "ocr_conf": ocr_conf,
                             "score": score})
        if ratio < eff_min:
            debug["rejected"].append({"text": text, "alias": alias,
                                      "ratio": ratio, "min": eff_min,
                                      "reason": "low_ratio"})
            continue
        # вес = (ratio/100)^2 * max(ocr_conf/100, 0.2) * score * coverage
        # coverage = min(len(text), len(alias)) / len(alias) — поощряем
        # длинные совпадения (STALLIONS) над короткими (FFEE→FREE).
        # ratio^2 усиливает разрыв между сильными и пограничными матчами.
        coverage = min(len(text), la) / la
        w = ((ratio / 100.0) ** 2) * max(ocr_conf / 100.0, 0.2) \
            * max(0.1, score) * coverage
        weights[tag] += w
    if not weights:
        return None, 0.0, debug
    best = max(weights.items(), key=lambda kv: kv[1])
    debug["weights"] = dict(weights)
    return best[0], best[1], debug


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detections", required=True, type=Path)
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--teams", required=True, type=Path,
                    help="JSON со списком тегов: {tags:[...]}.")
    ap.add_argument("--zones", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--top-n", type=int, default=30)
    ap.add_argument("--min-match-ratio", type=float, default=70.0)
    ap.add_argument("--pad-frac", type=float, default=1.0,
                    help="Доля расширения bbox по горизонтали вправо (хвост названия).")
    ap.add_argument("--pad-frac-v", type=float, default=0.25,
                    help="Доля расширения bbox по вертикали (название над/под чипом).")
    ap.add_argument("--save-debug-crops", action="store_true")
    ap.add_argument("--tesseract-cmd", default=None,
                    help="Полный путь до tesseract.exe (Windows).")
    args = ap.parse_args()

    if args.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = args.tesseract_cmd

    detections = json.loads(args.detections.read_text(encoding="utf-8"))
    teams_cfg = json.loads(args.teams.read_text(encoding="utf-8"))
    known_tags: List[str] = list(teams_cfg.get("tags", []))
    if not known_tags:
        raise SystemExit(f"[err] нет тегов в {args.teams}")
    aliases_cfg = teams_cfg.get("aliases") or {t: [t] for t in known_tags}
    alias_to_tag: Dict[str, str] = {}
    for tag, aliases in aliases_cfg.items():
        for a in aliases:
            alias_to_tag[a.upper()] = tag
        alias_to_tag.setdefault(tag.upper(), tag)
    print(f"[info] алиасов: {len(alias_to_tag)} -> {len(known_tags)} тегов")
    zones_cfg = json.loads(args.zones.read_text(encoding="utf-8"))

    args.out.mkdir(parents=True, exist_ok=True)
    debug_dir = args.out / "debug_crops"
    if args.save_debug_crops:
        debug_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise SystemExit(f"[err] не открыт {args.video}")
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    rx, ry, rw, rh = pick_minimap_zone(zones_cfg, fw, fh)

    samples = pick_samples(detections, args.top_n)
    print(f"[info] slots с детекциями: {len(samples)}; "
          f"тегов в словаре: {len(known_tags)}")

    # Собираем уникальные кадры, чтобы каждое seek делать один раз
    needed: Dict[int, List[Tuple[str, List[int], float]]] = defaultdict(list)
    for slot, items in samples.items():
        for it in items:
            needed[it["frame"]].append((slot, it["bbox"], it["score"]))

    per_slot_ocr: Dict[str, List[Tuple[str, float, float]]] = defaultdict(list)
    for i, frame_idx in enumerate(sorted(needed.keys())):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        roi = frame[ry:ry + rh, rx:rx + rw]
        for slot, bbox, score in needed[frame_idx]:
            x, y, w, h = bbox
            # горизонталь — асимметрично, в основном вправо (текст справа от чипа)
            pad_x_l = max(2, int(w * 0.05))
            pad_x_r = max(4, int(w * args.pad_frac))
            pad_y   = max(2, int(h * args.pad_frac_v))
            x1 = max(0, x - pad_x_l); y1 = max(0, y - pad_y)
            x2 = min(roi.shape[1], x + w + pad_x_r)
            y2 = min(roi.shape[0], y + h + pad_y)
            crop = roi[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            ocrs = ocr_multi(crop)
            if not ocrs:
                per_slot_ocr[slot].append(("", 0.0, score))
            for text, conf in ocrs:
                per_slot_ocr[slot].append((text, conf, score))
            if args.save_debug_crops:
                best_text = ocrs[0][0] if ocrs else "_NA"
                base = debug_dir / f"{slot}_f{frame_idx:07d}_{best_text}"
                cv2.imwrite(str(base.with_suffix(".raw.png")), crop)
                preps = preprocess_variants(crop)
                for i_p, p in enumerate(preps):
                    cv2.imwrite(str(base.with_suffix(f".prep{i_p}.png")), p)
        if (i + 1) % 50 == 0:
            print(f"  ocr: {i+1}/{len(needed)} кадров")
    cap.release()

    results: Dict[str, dict] = {}
    assignments: Dict[str, str] = {}
    for slot in sorted(per_slot_ocr.keys(), key=lambda s: -len(per_slot_ocr[s])):
        tag, weight, dbg = vote(per_slot_ocr[slot], alias_to_tag,
                                min_match_ratio=args.min_match_ratio)
        results[slot] = {
            "tag": tag, "weight": round(weight, 3),
            "samples": len(per_slot_ocr[slot]),
            "debug": dbg,
        }
        if tag:
            assignments[slot] = tag

    # Sanity: разрешение коллизий (одинаковый tag у двух slot_id)
    tag_to_slots: Dict[str, List[str]] = defaultdict(list)
    for s, t in assignments.items():
        tag_to_slots[t].append(s)
    for tag, slots in tag_to_slots.items():
        if len(slots) <= 1:
            continue
        # оставить за слотом с большим весом
        slots_sorted = sorted(slots, key=lambda s: -results[s]["weight"])
        winner = slots_sorted[0]
        for loser in slots_sorted[1:]:
            results[loser]["needs_review"] = f"collision_with:{winner}"
            results[loser]["tag"] = None
            assignments.pop(loser, None)

    out_path = args.out / "slot_tags.json"
    out_path.write_text(json.dumps({
        "video": str(args.video),
        "detections": str(args.detections),
        "teams_dict": known_tags,
        "top_n": args.top_n,
        "min_match_ratio": args.min_match_ratio,
        "assignments": assignments,
        "per_slot": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] slot_tags -> {out_path}")
    print(f"[ok] назначено {len(assignments)}/{len(per_slot_ocr)} слотов")
    for s in sorted(per_slot_ocr.keys()):
        r = results[s]
        flag = " ⚠" if r.get("needs_review") else ""
        print(f"  {s:8s} -> {r['tag'] or '?':6s}  w={r['weight']:.2f}  n={r['samples']}{flag}")


if __name__ == "__main__":
    main()