import argparse
from pathlib import Path

from ultralytics import YOLO

from slot_utils import resolve_device


def main() -> None:
    parser = argparse.ArgumentParser(description="Train crop-based YOLOv8 slot classifier.")
    parser.add_argument("--data", type=Path, default=Path("datasets/slot_crop_v1"))
    parser.add_argument("--model", default="yolov8n-cls.pt")
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--imgsz", type=int, default=96)
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--project", type=Path, default=Path("runs/classify"))
    parser.add_argument("--name", default="slot_crop_classifier_v1")
    parser.add_argument("--patience", type=int, default=15)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    if not args.data.exists():
        raise FileNotFoundError(f"Dataset not found: {args.data}")

    device = resolve_device(args.device)
    print(f"Using device: {device}")

    model = YOLO(args.model)
    model.train(
        data=str(args.data.resolve()),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=device,
        project=str(args.project.resolve()),
        name=args.name,
        patience=args.patience,
        workers=args.workers,
        cache=False,
        plots=True,
        val=True,
    )


if __name__ == "__main__":
    main()
