import argparse
from pathlib import Path

from ultralytics import YOLO

from slot_utils import resolve_device


def main() -> None:
    parser = argparse.ArgumentParser(description="Train YOLOv8 slot detector.")
    parser.add_argument("--data", type=Path, default=Path("datasets/slot_plates_v1/data.yaml"))
    parser.add_argument("--model", default="yolov8n.pt", help="YOLOv8 base model")
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--device", default="auto", help="auto, 0 for CUDA, cpu for CPU")
    parser.add_argument("--project", type=Path, default=Path("runs/detect"))
    parser.add_argument("--name", default="slot_plate_v1")
    parser.add_argument("--patience", type=int, default=25)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    if not args.data.exists():
        raise FileNotFoundError(f"data.yaml not found: {args.data}")

    device = resolve_device(args.device)
    print(f"Using device: {device}")

    model = YOLO(args.model)
    model.train(
        data=str(args.data),
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
