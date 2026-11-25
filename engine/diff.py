#!/usr/bin/env python3
"""Pixel comparison for Figma-to-Qt UI restoration.

The tool compares a Figma reference render with a Qt preview render and
produces machine-readable metrics plus human-reviewable artifacts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
from skimage.color import deltaE_ciede2000, rgb2lab
from skimage.feature import canny
from skimage.metrics import structural_similarity


DEFAULT_THRESHOLDS = {
    "ssim": 0.97,
    "mismatch_ratio": 0.015,
    "delta_e_mean": 4.0,
}


def _load_rgb(path: Path, background: tuple[int, int, int] = (0, 0, 0)) -> Image.Image:
    image = Image.open(path)
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        image = image.convert("RGBA")
        base = Image.new("RGBA", image.size, background + (255,))
        image = Image.alpha_composite(base, image)
    return image.convert("RGB")


def _gray(rgb: np.ndarray) -> np.ndarray:
    return np.dot(rgb[..., :3].astype(np.float64), [0.299, 0.587, 0.114])


def _heatmap(delta_e: np.ndarray) -> np.ndarray:
    t = np.clip(delta_e / 30.0, 0.0, 1.0)
    red = np.clip(1.5 - np.abs(4.0 * t - 3.0), 0.0, 1.0)
    green = np.clip(1.5 - np.abs(4.0 * t - 2.0), 0.0, 1.0)
    blue = np.clip(1.5 - np.abs(4.0 * t - 1.0), 0.0, 1.0)
    return (np.stack([red, green, blue], axis=-1) * 255.0).astype(np.uint8)


def _label(image: Image.Image, text: str) -> Image.Image:
    labelled = image.copy()
    draw = ImageDraw.Draw(labelled)
    draw.rectangle([0, 0, 86, 20], fill=(0, 0, 0))
    draw.text((6, 4), text, fill=(255, 255, 255))
    return labelled


def _side_by_side(images: list[Image.Image], labels: list[str], out_path: Path) -> None:
    labelled = [_label(image, label) for image, label in zip(images, labels)]
    width = sum(image.width for image in labelled)
    height = max(image.height for image in labelled)
    canvas = Image.new("RGB", (width, height), (20, 20, 20))
    x = 0
    for image in labelled:
        canvas.paste(image, (x, 0))
        x += image.width
    canvas.save(out_path)


def _tile_hint(
    ref_tile: np.ndarray,
    render_tile: np.ndarray,
    tile_delta_e: np.ndarray,
) -> str:
    gray_ref = _gray(ref_tile).astype(np.float64) / 255.0
    gray_render = _gray(render_tile).astype(np.float64) / 255.0
    edges_ref = canny(gray_ref, sigma=1.0).astype(np.float32)
    edges_render = canny(gray_render, sigma=1.0).astype(np.float32)
    edge_diff = float(np.mean(np.abs(edges_ref - edges_render)))
    color_delta = float(np.mean(tile_delta_e))
    if edge_diff > 0.08:
        return "edge_shift"
    if color_delta > 6.0:
        return "color"
    return "texture"


def _worst_regions(
    ref_rgb: np.ndarray,
    render_rgb: np.ndarray,
    delta_e: np.ndarray,
    tiles_x: int,
    tiles_y: int,
    top_k: int,
) -> list[dict[str, Any]]:
    height, width = delta_e.shape
    regions: list[dict[str, Any]] = []

    for ty in range(tiles_y):
        for tx in range(tiles_x):
            x0 = int(tx * width / tiles_x)
            x1 = int((tx + 1) * width / tiles_x)
            y0 = int(ty * height / tiles_y)
            y1 = int((ty + 1) * height / tiles_y)

            tile_ref = ref_rgb[y0:y1, x0:x1]
            tile_render = render_rgb[y0:y1, x0:x1]
            tile_delta = delta_e[y0:y1, x0:x1]

            try:
                tile_ssim = float(
                    structural_similarity(
                        _gray(tile_ref),
                        _gray(tile_render),
                        data_range=255,
                    )
                )
            except Exception:
                tile_ssim = 0.0

            regions.append(
                {
                    "bbox": [x0, y0, x1 - x0, y1 - y0],
                    "ssim": round(tile_ssim, 6),
                    "delta_e": round(float(np.mean(tile_delta)), 4),
                    "hint": _tile_hint(tile_ref, tile_render, tile_delta),
                }
            )

    regions.sort(key=lambda region: (region["ssim"], -region["delta_e"]))
    return regions[:top_k]


def compare(
    reference_path: Path,
    render_path: Path,
    out_dir: Path,
    thresholds: dict[str, float] | None = None,
    tiles: tuple[int, int] = (12, 8),
    top_k: int = 4,
) -> dict[str, Any]:
    thresholds = dict(DEFAULT_THRESHOLDS, **(thresholds or {}))
    out_dir.mkdir(parents=True, exist_ok=True)
    crops_dir = out_dir / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    reference = _load_rgb(reference_path)
    render_original = _load_rgb(render_path)
    render_size = list(render_original.size)

    size_match = reference.size == render_original.size
    reference_ratio = reference.width / max(1, reference.height)
    render_ratio = render_original.width / max(1, render_original.height)
    aspect_match = abs(reference_ratio - render_ratio) <= 0.01 * reference_ratio

    if not size_match and aspect_match:
        # Figma Dev Mode may return a preview at 1024 px wide while the frame
        # is 1920 px. Compare at the reference resolution and report target
        # coordinates separately.
        render = render_original.resize(reference.size, Image.LANCZOS)
        scaled_match = True
    else:
        render = render_original
        scaled_match = False

    ref_rgb = np.asarray(reference)
    render_rgb = np.asarray(render)

    ssim = float(
        structural_similarity(
            _gray(ref_rgb),
            _gray(render_rgb),
            data_range=255,
        )
    )

    lab_ref = rgb2lab(ref_rgb.astype(np.float64) / 255.0)
    lab_render = rgb2lab(render_rgb.astype(np.float64) / 255.0)
    delta_e = deltaE_ciede2000(lab_ref, lab_render)

    channel_diff = np.abs(
        ref_rgb.astype(np.int16) - render_rgb.astype(np.int16)
    ).max(axis=2)
    matching = (delta_e <= 5.0) & (channel_diff <= 16)
    tolerant_matching = ndimage.binary_dilation(matching, iterations=1)
    mismatch = ~tolerant_matching

    metrics = {
        "ssim": round(ssim, 6),
        "mismatch_ratio": round(float(np.mean(mismatch)), 6),
        "delta_e_mean": round(float(np.mean(delta_e)), 4),
        "delta_e_p95": round(float(np.percentile(delta_e, 95)), 4),
    }

    heatmap = _heatmap(delta_e)
    diff_image = Image.fromarray(heatmap)
    diff_path = out_dir / "diff.png"
    diff_image.save(diff_path)

    overlay = np.stack([_gray(ref_rgb)] * 3, axis=-1).astype(np.float64)
    overlay[mismatch] = overlay[mismatch] * 0.25 + np.array([255.0, 0.0, 0.0]) * 0.75
    overlay_image = Image.fromarray(np.clip(overlay, 0, 255).astype(np.uint8))
    overlay_path = out_dir / "overlay.png"
    overlay_image.save(overlay_path)

    side_path = out_dir / "side_by_side.png"
    _side_by_side(
        [reference, render, diff_image],
        ["FIGMA", "QT", "DELTA E"],
        side_path,
    )

    worst = _worst_regions(
        ref_rgb,
        render_rgb,
        delta_e,
        tiles[0],
        tiles[1],
        top_k,
    )

    scale_x = render_size[0] / reference.width if reference.width else 1.0
    scale_y = render_size[1] / reference.height if reference.height else 1.0
    for region in worst:
        x, y, width, height = region["bbox"]
        region["bbox_target"] = [
            round(x * scale_x),
            round(y * scale_y),
            round(width * scale_x),
            round(height * scale_y),
        ]

    crop_paths: list[str] = []
    for index, region in enumerate(worst):
        x, y, width, height = region["bbox"]
        crop_ref = reference.crop((x, y, x + width, y + height))
        crop_render = render.crop((x, y, x + width, y + height))
        crop_canvas = Image.new(
            "RGB",
            (crop_ref.width + crop_render.width, max(crop_ref.height, crop_render.height)),
            (20, 20, 20),
        )
        crop_canvas.paste(_label(crop_ref, "FIGMA"), (0, 0))
        crop_canvas.paste(_label(crop_render, "QT"), (crop_ref.width, 0))
        crop_path = crops_dir / f"region_{index}.png"
        crop_canvas.save(crop_path)
        crop_paths.append(str(crop_path))
        region["crop"] = str(crop_path)

    passed = (
        (size_match or scaled_match)
        and metrics["ssim"] >= thresholds["ssim"]
        and metrics["mismatch_ratio"] <= thresholds["mismatch_ratio"]
        and metrics["delta_e_mean"] <= thresholds["delta_e_mean"]
    )

    report: dict[str, Any] = {
        "pass": bool(passed),
        "size_match": bool(size_match),
        "scaled_match": bool(scaled_match),
        "reference_size": list(reference.size),
        "render_size": render_size,
        "comparison_size": list(reference.size),
        "reference_scale": [
            round(reference.width / render_size[0], 6) if render_size[0] else 1.0,
            round(reference.height / render_size[1], 6) if render_size[1] else 1.0,
        ],
        "metrics": metrics,
        "thresholds": thresholds,
        "worst_regions": worst,
        "artifacts": {
            "reference": str(reference_path),
            "render": str(render_path),
            "diff": str(diff_path),
            "overlay": str(overlay_path),
            "side_by_side": str(side_path),
            "crops": crop_paths,
        },
    }

    report_path = out_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--render", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--ssim", type=float, default=DEFAULT_THRESHOLDS["ssim"])
    parser.add_argument(
        "--mismatch",
        type=float,
        default=DEFAULT_THRESHOLDS["mismatch_ratio"],
    )
    parser.add_argument(
        "--delta-e",
        type=float,
        default=DEFAULT_THRESHOLDS["delta_e_mean"],
    )
    parser.add_argument("--tiles", default="12x8")
    parser.add_argument("--top-k", type=int, default=4)
    args = parser.parse_args(argv)

    tiles_x, tiles_y = (int(value) for value in args.tiles.lower().split("x", 1))
    report = compare(
        reference_path=args.reference,
        render_path=args.render,
        out_dir=args.out_dir,
        thresholds={
            "ssim": args.ssim,
            "mismatch_ratio": args.mismatch,
            "delta_e_mean": args.delta_e,
        },
        tiles=(tiles_x, tiles_y),
        top_k=args.top_k,
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
