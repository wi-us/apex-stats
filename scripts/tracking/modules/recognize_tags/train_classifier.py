#!/usr/bin/env python3
"""
train_classifier.py — обучение MobileNetV3-small на dataset/labeled/{TAG}/*.png.

Особенности под наш кейс (мало данных, ~30 кропов/класс, 20 классов на матч):
- Stratified 80/20 train/val split (sklearn).
- Class-weighted CrossEntropy (компенсирует неравномерность).
- Сильные аугментации: rotate ±7°, перспектива, blur, JPEG noise, brightness/contrast,
  ColorJitter. Hue НЕ трогаем — цвет плашки важен для класса.
- Pretrained MobileNetV3-small, fine-tune всей сети с малым LR.
- AdamW + CosineAnnealingLR.
- Early stopping по val_acc (patience=8).
- Сохраняет models/tag_classifier.pt + labels.json + train_report.txt.

Использование:
  python train_classifier.py \
      --data    dataset/labeled \
      --out     models \
      --epochs  60 \
      --batch   32 \
      --img-size 48
"""
from __future__ import annotations

import argparse
import io
import json
import random
import time
from collections import Counter
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from PIL import Image, ImageFilter
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms
from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small
from tqdm import tqdm


# ---------- aug helpers ----------

class JpegNoise:
    """Прогон через JPEG random quality — имитирует артефакты стрима."""
    def __init__(self, q_lo=35, q_hi=85, p=0.5):
        self.q_lo, self.q_hi, self.p = q_lo, q_hi, p

    def __call__(self, img: Image.Image) -> Image.Image:
        if random.random() > self.p:
            return img
        buf = io.BytesIO()
        q = random.randint(self.q_lo, self.q_hi)
        img.convert("RGB").save(buf, format="JPEG", quality=q)
        buf.seek(0)
        return Image.open(buf).convert("RGB")


class RandomBlur:
    def __init__(self, p=0.3, r_lo=0.3, r_hi=1.2):
        self.p, self.r_lo, self.r_hi = p, r_lo, r_hi

    def __call__(self, img: Image.Image) -> Image.Image:
        if random.random() > self.p:
            return img
        r = random.uniform(self.r_lo, self.r_hi)
        return img.filter(ImageFilter.GaussianBlur(radius=r))


def build_transforms(img_size: int):
    train_tf = transforms.Compose([
        transforms.Resize((img_size + 6, img_size + 6)),
        transforms.RandomCrop((img_size, img_size)),
        transforms.RandomAffine(degrees=7, translate=(0.04, 0.04), scale=(0.92, 1.08), shear=3),
        transforms.RandomPerspective(distortion_scale=0.12, p=0.4),
        transforms.ColorJitter(brightness=0.25, contrast=0.25, saturation=0.15, hue=0.0),
        RandomBlur(p=0.35),
        JpegNoise(p=0.5),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    val_tf = transforms.Compose([
        transforms.Resize((img_size, img_size)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    return train_tf, val_tf


# ---------- dataset ----------

class TagDataset(Dataset):
    def __init__(self, items, label_to_idx, transform):
        self.items = items
        self.label_to_idx = label_to_idx
        self.transform = transform

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        path, label = self.items[i]
        img = Image.open(path).convert("RGB")
        return self.transform(img), self.label_to_idx[label]


def scan_dataset(root: Path):
    items = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        # пропускаем _review и пустые
        if d.name.startswith("_"):
            continue
        imgs = [p for p in d.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
        if not imgs:
            continue
        for p in imgs:
            items.append((p, d.name))
    return items


# ---------- train ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, type=Path, help="dataset/labeled")
    ap.add_argument("--out", required=True, type=Path, help="output dir for model + report")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--weight-decay", type=float, default=1e-4)
    ap.add_argument("--img-size", type=int, default=48)
    ap.add_argument("--val-ratio", type=float, default=0.2)
    ap.add_argument("--patience", type=int, default=8)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--num-workers", type=int, default=2)
    args = ap.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    args.out.mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[device] {device}")

    # ---- scan ----
    items = scan_dataset(args.data)
    if not items:
        raise SystemExit(f"No images found under {args.data}")
    labels_all = sorted({lbl for _, lbl in items})
    label_to_idx = {l: i for i, l in enumerate(labels_all)}
    counts = Counter(lbl for _, lbl in items)
    print(f"[data] {len(items)} images, {len(labels_all)} classes")
    print(f"[data] min/max per class: {min(counts.values())}/{max(counts.values())}")

    # classes с <2 примеров — нельзя стратифицировать, кидаем в train целиком
    train_only = [it for it in items if counts[it[1]] < 2]
    splittable  = [it for it in items if counts[it[1]] >= 2]
    y = [lbl for _, lbl in splittable]
    train_items, val_items = train_test_split(
        splittable, test_size=args.val_ratio, stratify=y, random_state=args.seed
    )
    train_items.extend(train_only)
    print(f"[split] train={len(train_items)}  val={len(val_items)}")

    train_tf, val_tf = build_transforms(args.img_size)
    train_ds = TagDataset(train_items, label_to_idx, train_tf)
    val_ds   = TagDataset(val_items,   label_to_idx, val_tf)

    # WeightedRandomSampler по обратной частоте класса
    train_counts = Counter(lbl for _, lbl in train_items)
    sample_w = [1.0 / train_counts[lbl] for _, lbl in train_items]
    sampler = torch.utils.data.WeightedRandomSampler(sample_w, num_samples=len(train_items), replacement=True)

    train_loader = DataLoader(train_ds, batch_size=args.batch, sampler=sampler,
                              num_workers=args.num_workers, pin_memory=(device.type == "cuda"))
    val_loader   = DataLoader(val_ds,   batch_size=args.batch, shuffle=False,
                              num_workers=args.num_workers, pin_memory=(device.type == "cuda"))

    # ---- model ----
    weights = MobileNet_V3_Small_Weights.IMAGENET1K_V1
    model = mobilenet_v3_small(weights=weights)
    in_feat = model.classifier[-1].in_features
    model.classifier[-1] = nn.Linear(in_feat, len(labels_all))
    model = model.to(device)

    # class weights для loss — компенсация дисбаланса по реальным частотам
    cls_w = torch.tensor(
        [1.0 / counts[l] for l in labels_all], dtype=torch.float32, device=device
    )
    cls_w = cls_w / cls_w.mean()
    criterion = nn.CrossEntropyLoss(weight=cls_w, label_smoothing=0.05)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)

    # ---- loop ----
    best_acc = 0.0
    best_epoch = -1
    stale = 0
    history = []
    ckpt_path = args.out / "tag_classifier.pt"

    for epoch in range(1, args.epochs + 1):
        t0 = time.time()
        model.train()
        tr_loss = 0.0
        tr_n = 0
        tr_correct = 0
        for xb, yb in tqdm(train_loader, desc=f"epoch {epoch:02d}/{args.epochs} train", leave=False):
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad(set_to_none=True)
            logits = model(xb)
            loss = criterion(logits, yb)
            loss.backward()
            optimizer.step()
            tr_loss += loss.item() * xb.size(0)
            tr_n += xb.size(0)
            tr_correct += (logits.argmax(1) == yb).sum().item()
        scheduler.step()

        # eval
        model.eval()
        va_correct = 0
        va_n = 0
        per_class_correct = Counter()
        per_class_total = Counter()
        with torch.no_grad():
            for xb, yb in val_loader:
                xb, yb = xb.to(device), yb.to(device)
                pred = model(xb).argmax(1)
                va_correct += (pred == yb).sum().item()
                va_n += xb.size(0)
                for p, t in zip(pred.cpu().tolist(), yb.cpu().tolist()):
                    per_class_total[t] += 1
                    if p == t:
                        per_class_correct[t] += 1

        tr_acc = tr_correct / max(tr_n, 1)
        va_acc = va_correct / max(va_n, 1)
        dt = time.time() - t0
        history.append({"epoch": epoch, "train_loss": tr_loss / max(tr_n, 1),
                        "train_acc": tr_acc, "val_acc": va_acc, "lr": scheduler.get_last_lr()[0]})
        print(f"[ep {epoch:02d}] loss={tr_loss/max(tr_n,1):.4f}  "
              f"train_acc={tr_acc:.3f}  val_acc={va_acc:.3f}  ({dt:.1f}s)")

        if va_acc > best_acc:
            best_acc = va_acc
            best_epoch = epoch
            stale = 0
            torch.save({
                "model_state": model.state_dict(),
                "labels": labels_all,
                "img_size": args.img_size,
                "arch": "mobilenet_v3_small",
            }, ckpt_path)
        else:
            stale += 1
            if stale >= args.patience:
                print(f"[early-stop] no improvement {args.patience} epochs (best ep {best_epoch} = {best_acc:.3f})")
                break

    # labels.json
    (args.out / "labels.json").write_text(
        json.dumps({"labels": labels_all, "img_size": args.img_size}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # report
    weakest = sorted(
        ((labels_all[c], per_class_correct[c] / per_class_total[c] if per_class_total[c] else 0.0,
          per_class_total[c])
         for c in per_class_total),
        key=lambda r: r[1],
    )[:10]
    lines = []
    lines.append(f"best_val_acc = {best_acc:.4f} @ epoch {best_epoch}")
    lines.append(f"classes = {len(labels_all)}  train={sum(train_counts.values())}  val={len(val_items)}")
    lines.append("")
    lines.append("weakest classes on val (acc, n):")
    for tag, acc, n in weakest:
        lines.append(f"  {tag:10s}  acc={acc:.3f}  n={n}")
    lines.append("")
    lines.append("class counts (total dataset):")
    for lbl in labels_all:
        lines.append(f"  {lbl:10s}  {counts[lbl]}")
    (args.out / "train_report.txt").write_text("\n".join(lines), encoding="utf-8")

    print(f"\n[done] best val_acc = {best_acc:.4f}")
    print(f"[done] saved: {ckpt_path}")
    print(f"[done] report: {args.out/'train_report.txt'}")


if __name__ == "__main__":
    main()