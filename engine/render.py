#!/usr/bin/env python3
"""Helpers for compiling Qt resources and rendering .ui files headlessly.

All Qt and font locations come from the project profile (see config.py).
This module contains no machine-specific defaults.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import config

_SETTINGS: config.Settings | None = None


def configure(settings: config.Settings) -> None:
    global _SETTINGS
    _SETTINGS = settings


def settings() -> config.Settings:
    if _SETTINGS is None:
        raise RuntimeError("render.configure(settings) has not been called")
    return _SETTINGS


def qt_bin() -> Path:
    return settings().qt_bin


def qt_plugins() -> Path:
    return settings().qt_plugins


def qpreview_exe() -> Path:
    return settings().preview_exe


def rcc_exe() -> Path:
    return settings().rcc_exe


def font_dir() -> Path:
    return settings().font_dir


def build_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = str(qt_bin()) + os.pathsep + env.get("PATH", "")
    env["QT_PLUGIN_PATH"] = str(qt_plugins())
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QT_SCALE_FACTOR"] = "1"
    env["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    env["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
    return env


def _font_args(
    font_directory: Path | None = None, font_files: list[Path] | None = None
) -> list[str]:
    args = ["--font-dir", str(Path(font_directory or font_dir()).resolve())]
    files = settings().font_files if font_files is None else font_files
    for path in files:
        args.extend(["--font-file", str(Path(path).resolve())])
    return args


def compile_qrc(qrc_path: Path, rcc_path: Path) -> dict[str, Any]:
    qrc_path = Path(qrc_path).resolve()
    rcc_path = Path(rcc_path).resolve()
    rcc_path.parent.mkdir(parents=True, exist_ok=True)

    command = [str(rcc_exe()), "-binary", str(qrc_path), "-o", str(rcc_path)]
    completed = subprocess.run(
        command,
        cwd=str(qrc_path.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "rcc": str(rcc_path),
    }


def ensure_page_rcc(
    ui_dir: Path,
    qrc_path: Path | None,
    rcc_path: Path,
    resource_prefix: str = "/res",
) -> dict[str, Any]:
    """Compile a page .qrc, creating an empty one when the agent has not yet."""
    ui_dir = Path(ui_dir).resolve()
    rcc_path = Path(rcc_path).resolve()
    qrc_path = Path(qrc_path).resolve() if qrc_path else ui_dir / "resources.qrc"

    if not qrc_path.exists():
        qrc_path.parent.mkdir(parents=True, exist_ok=True)
        qrc_path.write_text(
            "<RCC>\n"
            f'  <qresource prefix="{resource_prefix}">\n'
            "  </qresource>\n"
            "</RCC>\n",
            encoding="utf-8",
        )

    return compile_qrc(qrc_path, rcc_path)


def render_ui(
    ui_path: Path,
    out_png: Path,
    *,
    rcc_paths: list[Path] | None = None,
    dump_json: Path | None = None,
    width: int = 0,
    height: int = 0,
    font_family: str | None = None,
    font_directory: Path | None = None,
    font_files: list[Path] | None = None,
    font_maps: list[str] | None = None,
    style: str = "",
    allow_missing_resources: bool = False,
) -> dict[str, Any]:
    ui_path = Path(ui_path).resolve()
    out_png = Path(out_png).resolve()
    out_png.parent.mkdir(parents=True, exist_ok=True)

    command = [
        str(qpreview_exe()),
        "--ui",
        str(ui_path),
        "--out",
        str(out_png),
        "--font-family",
        font_family or settings().font_family,
        *_font_args(font_directory, font_files),
    ]
    if width > 0:
        command.extend(["--width", str(width)])
    if height > 0:
        command.extend(["--height", str(height)])
    if dump_json:
        dump_path = Path(dump_json).resolve()
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        command.extend(["--dump-json", str(dump_path)])
    if settings().font_legacy_family:
        command.extend(["--legacy-family", settings().font_legacy_family])
    if style:
        command.extend(["--style", style])
    for rcc in rcc_paths or []:
        command.extend(["--rcc", str(Path(rcc).resolve())])
    for mapping in font_maps or []:
        command.extend(["--font-map", mapping])
    if allow_missing_resources:
        command.append("--allow-missing-resources")

    completed = subprocess.run(
        command,
        cwd=str(settings().project_root),
        env=build_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "png": str(out_png),
        "dump_json": str(dump_json) if dump_json else "",
    }


def font_audit(font_family: str | None = None) -> dict[str, Any]:
    command = [
        str(qpreview_exe()),
        "--font-audit",
        "--font-family",
        font_family or settings().font_family,
        *_font_args(),
    ]
    for style in settings().font_required_styles:
        command.extend(["--require-style", style])
    if settings().font_legacy_family:
        command.extend(["--legacy-family", settings().font_legacy_family])
    completed = subprocess.run(
        command,
        cwd=str(settings().project_root),
        env=build_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    data: dict[str, Any] = {}
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        data = {"raw": completed.stdout}
    return {
        "command": command,
        "returncode": completed.returncode,
        "data": data,
        "stderr": completed.stderr,
    }


def _main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument("--profile", type=Path, default=None)
    parser.add_argument("--ui", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--rcc", action="append", type=Path, default=[])
    parser.add_argument("--qrc", type=Path)
    parser.add_argument("--dump-json", type=Path)
    parser.add_argument("--width", type=int, default=0)
    parser.add_argument("--height", type=int, default=0)
    parser.add_argument("--font-family", default=None)
    parser.add_argument("--font-map", action="append", default=[])
    parser.add_argument("--style", default="")
    parser.add_argument("--allow-missing-resources", action="store_true")
    parser.add_argument("--font-audit", action="store_true")
    args = parser.parse_args(argv)

    root = args.project_root or config.find_project_root(Path.cwd())
    try:
        configure(config.build_settings(root, args.profile))
    except config.ProfileError as error:
        print(error.format(), file=sys.stderr)
        return 2

    if args.font_audit:
        result = font_audit(args.font_family)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["returncode"] == 0 else 1

    if not args.ui or not args.out:
        parser.error("--ui and --out are required")

    rcc_paths = list(args.rcc)
    if args.qrc:
        rcc_path = args.out.parent / (args.qrc.stem + ".rcc")
        compiled = compile_qrc(args.qrc, rcc_path)
        if compiled["returncode"] != 0:
            print(json.dumps(compiled, ensure_ascii=False, indent=2))
            return compiled["returncode"]
        rcc_paths.append(rcc_path)

    result = render_ui(
        args.ui,
        args.out,
        rcc_paths=rcc_paths,
        dump_json=args.dump_json,
        width=args.width,
        height=args.height,
        font_family=args.font_family,
        font_maps=args.font_map,
        style=args.style,
        allow_missing_resources=args.allow_missing_resources,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["returncode"] == 0 else result["returncode"]


if __name__ == "__main__":
    sys.exit(_main())
