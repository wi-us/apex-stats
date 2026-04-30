"""
Debug visualizer for team elimination detection.

Shows coarse+refine sampling process on side panels and prints final elimination
timings detected by the main analysis algorithm.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.analysis.app.batch_analyze import (  # noqa: E402
    _build_panel_slots,
    _calibrate_team_slots_from_frame,
    _detect_team_color_square_in_roi,
    _presence_score_in_slot,
    _sorted_team_ids,
    detect_team_eliminations_timeline,
    normalize_map_name,
)
from team_tracking.tracking_settings import get_all_teams_for_map  # noqa: E402


def _draw_slot(frame, slot, label: str, color, thickness: int = 2) -> None:
    x, y, w, h = slot
    cv2.rectangle(frame, (x, y), (x + w, y + h), color, thickness)
    cv2.putText(frame, label, (x + 4, max(14, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)


def _show_frame_with_score(
    cap: cv2.VideoCapture,
    frame_idx: int,
    team_id: str,
    team_color_bgr: tuple[int, int, int],
    slot: tuple[int, int, int, int],
    color_square_rel: Optional[tuple[float, float, float, float]],
    threshold: float,
    phase: str,
    wait_ms: int,
) -> bool:
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
    ret, frame = cap.read()
    if not ret:
        return False

    score = _presence_score_in_slot(frame, slot, team_color_bgr, color_square_rel)
    alive = score >= threshold
    status_text = "ALIVE" if alive else "ELIM_CANDIDATE"
    status_color = (0, 220, 0) if alive else (0, 80, 255)

    _draw_slot(frame, slot, f"{team_id} slot", (255, 255, 255), 2)
    if color_square_rel is not None:
        x, y, w, h = slot
        rx, ry, rw, rh = color_square_rel
        qx = x + int(rx * w)
        qy = y + int(ry * h)
        qw = max(4, int(rw * w))
        qh = max(4, int(rh * h))
        cv2.rectangle(frame, (qx, qy), (qx + qw, qy + qh), (0, 255, 255), 2)
        cv2.putText(frame, "color-square", (qx + 2, max(16, qy - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
    cv2.putText(
        frame,
        f"{phase} frame={frame_idx} score={score:.4f} threshold={threshold:.4f} status={status_text}",
        (24, 34),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        status_color,
        2,
    )

    cv2.imshow("Elimination Debug", frame)
    key = cv2.waitKey(wait_ms) & 0xFF
    if key in (27, ord("q")):
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug elimination detector pass")
    parser.add_argument("--video", required=True, help="Video path")
    parser.add_argument("--map", required=True, help="Map id, e.g. mp_olympus")
    parser.add_argument("--team", type=int, help="Optional single team number to debug")
    parser.add_argument("--start-seconds", type=float, default=0.0, help="Start seconds")
    parser.add_argument("--end-seconds", type=float, default=375.0, help="End seconds")
    parser.add_argument("--coarse-step", type=int, default=10000, help="Coarse step in frames")
    parser.add_argument("--refine-step", type=int, default=1000, help="Refine step in frames")
    parser.add_argument("--tolerance-frames", type=int, default=300, help="Final binary-search tolerance")
    parser.add_argument("--wait-ms", type=int, default=350, help="Wait per frame in visualization")
    args = parser.parse_args()

    map_name = normalize_map_name(args.map)
    teams = get_all_teams_for_map(map_name)
    if not teams:
        raise ValueError(f"No team configs found for map '{map_name}'")

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {args.video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    start_frame = max(0, int(args.start_seconds * fps))
    end_frame = max(start_frame, int(args.end_seconds * fps))

    elimination = detect_team_eliminations_timeline(
        video_path=args.video,
        fps=fps,
        team_configs=teams,
        start_seconds=args.start_seconds,
        end_seconds=args.end_seconds,
        coarse_step_frames=args.coarse_step,
        refine_step_frames=args.refine_step,
        tolerance_frames=args.tolerance_frames,
    )

    team_ids = _sorted_team_ids(teams)
    if args.team is not None:
        team_id = f"TEAM_{args.team}"
        team_ids = [team_id] if team_id in teams else []
    if not team_ids:
        raise ValueError("No teams selected for debug")

    team_colors: dict[str, tuple[int, int, int]] = {}
    for team_id in _sorted_team_ids(teams):
        cfg = teams.get(team_id, {})
        color = cfg.get("display_color_bgr", cfg.get("color_bgr", (180, 180, 180)))
        team_colors[team_id] = (int(color[0]), int(color[1]), int(color[2]))

    slots = _build_panel_slots()
    team_color_square_rel: dict[str, tuple[float, float, float, float]] = {}
    calibration_frame = start_frame + min(int(max(0, end_frame - start_frame) * 0.15), int(max(1.0, fps) * 60))
    calibration_frame = int(max(start_frame, min(end_frame, calibration_frame)))
    cap.set(cv2.CAP_PROP_POS_FRAMES, calibration_frame)
    calib_ok, calib_frame = cap.read()
    if calib_ok and calib_frame is not None:
        slots = _calibrate_team_slots_from_frame(calib_frame, _sorted_team_ids(teams), team_colors)
        for team_id_all in _sorted_team_ids(teams):
            slot_all = slots.get(team_id_all)
            if slot_all is None:
                continue
            x, y, w, h = slot_all
            x2 = min(calib_frame.shape[1], x + w)
            y2 = min(calib_frame.shape[0], y + h)
            x = max(0, x)
            y = max(0, y)
            if x >= x2 or y >= y2:
                continue
            slot_roi = calib_frame[y:y2, x:x2]
            if slot_roi.size == 0:
                continue
            sq_x, sq_y, sq_w, sq_h = _detect_team_color_square_in_roi(slot_roi, team_colors[team_id_all])
            sq_w = max(4, min(slot_roi.shape[1] - sq_x, sq_w))
            sq_h = max(4, min(slot_roi.shape[0] - sq_y, sq_h))
            if sq_w <= 0 or sq_h <= 0:
                continue
            team_color_square_rel[team_id_all] = (
                float(sq_x) / max(1.0, float(slot_roi.shape[1])),
                float(sq_y) / max(1.0, float(slot_roi.shape[0])),
                float(sq_w) / max(1.0, float(slot_roi.shape[1])),
                float(sq_h) / max(1.0, float(slot_roi.shape[0])),
            )
        print(f"[debug] slot calibration applied at frame={calibration_frame}")
    else:
        print(f"[debug] slot calibration skipped (frame={calibration_frame}), using defaults")

    print("=== Elimination detection summary ===")
    for team_id in team_ids:
        info = elimination.get(team_id, {"eliminated": False})
        print(team_id, info)

    for team_id in team_ids:
        cfg = teams[team_id]
        color = cfg.get("display_color_bgr", cfg.get("color_bgr", (180, 180, 180)))
        team_color_bgr = (int(color[0]), int(color[1]), int(color[2]))
        slot = slots.get(team_id)
        if slot is None:
            continue

        info = elimination.get(team_id, {})
        threshold = float(info.get("threshold", 0.03))
        coarse_dead = int(info.get("coarseDeadFrame", end_frame))
        coarse_alive = int(info.get("coarseAliveFrame", start_frame))
        final_frame = int(info.get("eliminationFrame", coarse_dead))
        is_eliminated = bool(info.get("eliminated", False))

        print(f"\n--- {team_id} ---")
        print(
            f"threshold={threshold:.4f} coarseDeadFrame={coarse_dead} "
            f"coarseAliveFrame={coarse_alive} finalFrame={final_frame}"
        )
        print(f"eliminated={is_eliminated} reason={info.get('method')}")

        # Coarse phase visualization (reverse: end -> start)
        print("Coarse phase (reverse)...")
        for frame_idx in range(end_frame, start_frame - 1, -max(1, args.coarse_step)):
            if not _show_frame_with_score(
                cap=cap,
                frame_idx=frame_idx,
                team_id=team_id,
                team_color_bgr=team_color_bgr,
                slot=slot,
                color_square_rel=team_color_square_rel.get(team_id),
                threshold=threshold,
                phase="COARSE",
                wait_ms=args.wait_ms,
            ):
                cap.release()
                cv2.destroyAllWindows()
                return

        if not is_eliminated:
            print("No elimination detected for this team in selected window. Skip refine.")
            continue

        # Refine phase visualization between alive/dead anchors
        print("Refine phase...")
        refine_start = max(start_frame, min(coarse_alive, coarse_dead))
        refine_end = min(end_frame, max(coarse_alive, coarse_dead))
        for frame_idx in range(refine_start, refine_end + 1, max(1, args.refine_step)):
            if not _show_frame_with_score(
                cap=cap,
                frame_idx=frame_idx,
                team_id=team_id,
                team_color_bgr=team_color_bgr,
                slot=slot,
                color_square_rel=team_color_square_rel.get(team_id),
                threshold=threshold,
                phase="REFINE",
                wait_ms=args.wait_ms,
            ):
                cap.release()
                cv2.destroyAllWindows()
                return

        # Final frame freeze
        _show_frame_with_score(
            cap=cap,
            frame_idx=final_frame,
            team_id=team_id,
            team_color_bgr=team_color_bgr,
            slot=slot,
            color_square_rel=team_color_square_rel.get(team_id),
            threshold=threshold,
            phase="FINAL",
            wait_ms=0,
        )
        print("Press any key in window to continue...")
        if cv2.waitKey(0) & 0xFF in (27, ord("q")):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

