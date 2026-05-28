#!/usr/bin/env python3
"""
preprocess_masks.py

Create OpenDroneMap-compatible foreground masks for close-range human-head / bust scans.

For each image:
    frame_0001.jpg      -> frame_0001_mask.JPG

ODM mask convention:
    white = keep / reconstruct
    black = ignore / background

This script intentionally uses only OpenCV + NumPy so it can run in the same
virtual environment as the web UI:
    pip install opencv-python
"""

import argparse
import os
from pathlib import Path
from typing import Iterable, Tuple

import cv2
import numpy as np

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def is_source_image(path: Path) -> bool:
    """True for real input photos, False for generated ODM masks/debug files."""
    name = path.name.lower()
    if path.suffix.lower() not in IMAGE_EXTS:
        return False
    if name.endswith("_mask.jpg") or name.endswith("_mask.jpeg") or name.endswith("_mask.png"):
        return False
    if "_overlay" in name or "_debug" in name:
        return False
    return True


def iter_images(input_dir: Path) -> Iterable[Path]:
    for path in sorted(input_dir.iterdir()):
        if path.is_file() and is_source_image(path):
            yield path


def mask_path_for(image_path: Path) -> Path:
    # ODM expects <stem>_mask.JPG beside the original image.
    return image_path.with_name(f"{image_path.stem}_mask.JPG")


def largest_connected_component(mask: np.ndarray) -> np.ndarray:
    """Keep only the largest white blob. Reduces background islands."""
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if num_labels <= 1:
        return mask

    largest_label = 1
    largest_area = stats[1, cv2.CC_STAT_AREA]
    for label in range(2, num_labels):
        area = stats[label, cv2.CC_STAT_AREA]
        if area > largest_area:
            largest_area = area
            largest_label = label

    output = np.zeros_like(mask)
    output[labels == largest_label] = 255
    return output


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """Fill holes inside the foreground mask."""
    h, w = mask.shape[:2]
    flood = mask.copy()
    flood_mask = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(flood, flood_mask, (0, 0), 255)
    holes = cv2.bitwise_not(flood)
    return cv2.bitwise_or(mask, holes)


def smooth_mask(mask: np.ndarray, close_size: int = 17, open_size: int = 5) -> np.ndarray:
    """Clean jagged edges and small speckles."""
    if close_size > 1:
        close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_size, close_size))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel)

    if open_size > 1:
        open_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_size, open_size))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, open_kernel)

    mask = cv2.GaussianBlur(mask, (5, 5), 0)
    _, mask = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)
    return mask


def center_rect(width: int, height: int, margin_x: float, margin_y_top: float, margin_y_bottom: float) -> Tuple[int, int, int, int]:
    """
    Foreground rectangle for GrabCut.

    For a head/bust scan, the person should be centered. Keep the rectangle broad
    enough to include hair and ears, while leaving border background for GrabCut.
    """
    x = int(width * margin_x)
    y = int(height * margin_y_top)
    rect_w = int(width * (1.0 - 2.0 * margin_x))
    rect_h = int(height * (1.0 - margin_y_top - margin_y_bottom))

    x = max(1, min(x, width - 2))
    y = max(1, min(y, height - 2))
    rect_w = max(2, min(rect_w, width - x - 1))
    rect_h = max(2, min(rect_h, height - y - 1))
    return x, y, rect_w, rect_h


def make_grabcut_mask(
    image_bgr: np.ndarray,
    iterations: int = 5,
    margin_x: float = 0.08,
    margin_y_top: float = 0.03,
    margin_y_bottom: float = 0.03,
) -> np.ndarray:
    """Generate a foreground mask with OpenCV GrabCut."""
    h, w = image_bgr.shape[:2]
    rect = center_rect(w, h, margin_x, margin_y_top, margin_y_bottom)

    grab_mask = np.zeros((h, w), np.uint8)
    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)

    cv2.grabCut(
        image_bgr,
        grab_mask,
        rect,
        bgd_model,
        fgd_model,
        iterations,
        cv2.GC_INIT_WITH_RECT,
    )

    mask = np.where(
        (grab_mask == cv2.GC_FGD) | (grab_mask == cv2.GC_PR_FGD),
        255,
        0,
    ).astype(np.uint8)

    mask = largest_connected_component(mask)
    mask = fill_holes(mask)
    mask = smooth_mask(mask)
    return mask


def make_chroma_mask(
    image_bgr: np.ndarray,
    hue_min: int = 35,
    hue_max: int = 90,
    sat_min: int = 40,
    val_min: int = 35,
) -> np.ndarray:
    """
    Remove green-ish background. Use this only if your background is green/blue/teal.
    Output is white foreground, black background.
    """
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    lower = np.array([hue_min, sat_min, val_min], dtype=np.uint8)
    upper = np.array([hue_max, 255, 255], dtype=np.uint8)
    bg = cv2.inRange(hsv, lower, upper)
    fg = cv2.bitwise_not(bg)

    fg = largest_connected_component(fg)
    fg = fill_holes(fg)
    fg = smooth_mask(fg)
    return fg


def overlay_debug(image_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Create a visual debug overlay with red background tint."""
    overlay = image_bgr.copy()
    background = mask == 0
    overlay[background] = (0.55 * overlay[background] + 0.45 * np.array([0, 0, 255])).astype(np.uint8)
    edges = cv2.Canny(mask, 50, 150)
    overlay[edges > 0] = (0, 255, 255)
    return overlay


def process_one(
    image_path: Path,
    mode: str,
    overwrite: bool,
    debug: bool,
    iterations: int,
    margin_x: float,
    margin_y_top: float,
    margin_y_bottom: float,
) -> bool:
    out_path = mask_path_for(image_path)
    if out_path.exists() and not overwrite:
        print(f"[SKIP] Mask exists: {out_path.name}", flush=True)
        return True

    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        print(f"[WARN] Could not read image: {image_path}", flush=True)
        return False

    if mode == "grabcut":
        mask = make_grabcut_mask(
            image,
            iterations=iterations,
            margin_x=margin_x,
            margin_y_top=margin_y_top,
            margin_y_bottom=margin_y_bottom,
        )
    elif mode == "chroma":
        mask = make_chroma_mask(image)
    else:
        raise ValueError(f"Unknown mode: {mode}")

    ok = cv2.imwrite(str(out_path), mask, [cv2.IMWRITE_JPEG_QUALITY, 100])
    if not ok:
        print(f"[ERROR] Failed writing mask: {out_path}", flush=True)
        return False

    white_ratio = float(np.count_nonzero(mask)) / float(mask.size)
    print(f"[MASK] {image_path.name} -> {out_path.name} foreground={white_ratio:.1%}", flush=True)

    if debug:
        debug_dir = image_path.parent / "mask_debug"
        debug_dir.mkdir(exist_ok=True)
        dbg = overlay_debug(image, mask)
        debug_path = debug_dir / f"{image_path.stem}_overlay.jpg"
        cv2.imwrite(str(debug_path), dbg, [cv2.IMWRITE_JPEG_QUALITY, 90])

    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate ODM-compatible foreground masks.")
    parser.add_argument("input_dir", help="Folder containing captured images")
    parser.add_argument("--mode", choices=["grabcut", "chroma"], default=os.getenv("MASK_MODE", "grabcut"))
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing masks")
    parser.add_argument("--debug", action="store_true", help="Write mask_debug/*_overlay.jpg files")
    parser.add_argument("--iterations", type=int, default=int(os.getenv("MASK_ITERATIONS", "5")))
    parser.add_argument("--margin-x", type=float, default=float(os.getenv("MASK_MARGIN_X", "0.08")))
    parser.add_argument("--margin-y-top", type=float, default=float(os.getenv("MASK_MARGIN_Y_TOP", "0.03")))
    parser.add_argument("--margin-y-bottom", type=float, default=float(os.getenv("MASK_MARGIN_Y_BOTTOM", "0.03")))
    args = parser.parse_args()

    input_dir = Path(args.input_dir).expanduser().resolve()
    if not input_dir.is_dir():
        print(f"[ERROR] Input folder not found: {input_dir}", flush=True)
        return 1

    images = list(iter_images(input_dir))
    if not images:
        print(f"[ERROR] No source images found in: {input_dir}", flush=True)
        return 1

    print(f"[INFO] Mask mode: {args.mode}", flush=True)
    print(f"[INFO] Images: {len(images)}", flush=True)

    ok_count = 0
    for image_path in images:
        try:
            if process_one(
                image_path=image_path,
                mode=args.mode,
                overwrite=args.overwrite,
                debug=args.debug,
                iterations=args.iterations,
                margin_x=args.margin_x,
                margin_y_top=args.margin_y_top,
                margin_y_bottom=args.margin_y_bottom,
            ):
                ok_count += 1
        except Exception as exc:
            print(f"[ERROR] Mask failed for {image_path.name}: {exc}", flush=True)

    print(f"[INFO] Masks created/verified: {ok_count}/{len(images)}", flush=True)
    return 0 if ok_count == len(images) else 2


if __name__ == "__main__":
    raise SystemExit(main())
