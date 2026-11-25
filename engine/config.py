#!/usr/bin/env python3
"""Project profile loading and validation for figma2qt.

Every project that uses figma2qt owns a ``.figma2qt/profile.yaml``. Three
values are mandatory and are never guessed or defaulted:

* ``qt.prefix``   - Qt install root (contains ``bin/rcc.exe``)
* ``font.family`` - font family exactly as Qt reports it
* ``font.dir``    - directory that contains the font files

Windows only.
"""

from __future__ import annotations

import copy
import glob
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PACKAGE_DIR = Path(__file__).resolve().parent
HOME_DIR = PACKAGE_DIR.parent

PROFILE_DIR_NAME = ".figma2qt"
PROFILE_FILE = "profile.yaml"
PAGES_FILE = "pages.yaml"
RCC_NAME = "rcc.exe"
DEFAULT_GENERATOR = "Visual Studio 16 2019"

PROFILE_TEMPLATE = """\
# figma2qt project profile.
# The fields marked REQUIRED have no defaults. While they are empty every
# figma2qt command stops and explains what is missing.

qt:
  # REQUIRED. Qt install root that contains bin/rcc.exe and plugins/.
  # Must be the Qt (5.15.x, MSVC x64) that bin/qt_preview.exe was built with.
  prefix: ""
  # Optional. CMake generator used by `preflight --build-harness`.
  # generator: "Visual Studio 16 2019"
  # Optional. Use another qt_preview.exe instead of the one shipped in bin/.
  # preview_exe: ""

font:
  # REQUIRED. Font family exactly as Qt reports it; used in generated QSS.
  family: ""
  # REQUIRED. Directory that contains the font files.
  dir: ""
  # Optional. File names or globs inside font.dir.
  # Default: every .ttf/.otf/.ttc file in the directory.
  # files: ["MyFont-*.ttf"]
  # Optional. Styles that must exist in the family (preflight fails otherwise).
  # required_styles: ["Regular", "Bold"]
  # Optional. QSS font-weight -> style name; copied into the agent prompts.
  # weights: {400: "Regular", 700: "Bold"}
  # Optional. Legacy family renames applied when rendering old .ui files ("old=new").
  # maps: []
  # Optional. Legacy family name to audit (reports whether it still resolves).
  # legacy_family: ""

# ---- optional settings below -------------------------------------------------
canvas: [1920, 1080]
max_rounds: 6
thresholds:
  ssim: 0.97
  mismatch_ratio: 0.015
  delta_e_mean: 4.0
semantic:
  required: true
  min_text_widgets: 1
  forbid_transparent_text: true
  forbid_card_images: true
assets:
  # Folders (relative to the project root) holding reusable images / .qrc files.
  # The agent matches Figma layers against these before exporting new images.
  search_dirs: []
  reuse: true
agy:
  mode: accept-edits
  agent: qt-ui-repairer
  timeout: 1200
  # model: ""
output:
  icons: icons
  qrc: resources.qrc
  resource_prefix: /res
# work_dir: ../figma2qt-work
"""

PAGES_TEMPLATE = """\
# Pages to restore. Add one from a Figma link with:
#   python <engine>/orchestrator.py add --url "<figma url>" --page-id login --ui login.ui --write
# Per-page overrides (thresholds, max_rounds, font, assets, agy, ...) go under the page.
pages: []
"""


class ProfileError(Exception):
    def __init__(self, problems: list[str], profile_path: Path | None = None):
        self.problems = list(problems)
        self.profile_path = profile_path
        super().__init__("; ".join(self.problems))

    def format(self) -> str:
        lines = [f"figma2qt profile is invalid: {self.profile_path}"]
        lines.extend(f"  - {problem}" for problem in self.problems)
        lines.append(
            "Create a template with `python <engine>/orchestrator.py init` "
            "and fill in the required fields."
        )
        return "\n".join(lines)


@dataclass
class Settings:
    project_root: Path
    profile_path: Path
    profile: dict[str, Any]
    qt_prefix: Path
    preview_exe: Path
    generator: str
    font_family: str
    font_dir: Path
    font_files: list[Path]
    font_required_styles: list[str]
    font_legacy_family: str
    work_dir: Path

    @property
    def qt_bin(self) -> Path:
        return self.qt_prefix / "bin"

    @property
    def qt_plugins(self) -> Path:
        return self.qt_prefix / "plugins"

    @property
    def rcc_exe(self) -> Path:
        return self.qt_bin / RCC_NAME


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def find_project_root(start: Path) -> Path:
    """Nearest ancestor containing ``.figma2qt/``; otherwise ``start``."""
    start = Path(start).resolve()
    for candidate in [start, *start.parents]:
        if (candidate / PROFILE_DIR_NAME).is_dir():
            return candidate
    return start


def default_profile_path(root: Path) -> Path:
    return Path(root) / PROFILE_DIR_NAME / PROFILE_FILE


def default_pages_path(profile_path: Path) -> Path:
    return Path(profile_path).parent / PAGES_FILE


def _blank(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return not text or text.upper().startswith("REPLACE")


def _as_list(value: Any) -> list[Any] | None:
    if value is None:
        return []
    return value if isinstance(value, list) else None


def resolve_font_files(font_dir: Path, patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        for match in sorted(glob.glob(str(Path(font_dir) / pattern))):
            path = Path(match)
            if path.is_file() and path not in files:
                files.append(path)
    return files


def validate_profile(profile: Any) -> list[str]:
    if not isinstance(profile, dict):
        return ["the profile must be a YAML mapping"]

    problems: list[str] = []

    qt = profile.get("qt")
    qt = qt if isinstance(qt, dict) else {}
    if _blank(qt.get("prefix")):
        problems.append(
            "qt.prefix is required (Qt install root, e.g. C:/Qt/5.15.2/msvc2019_64)"
        )
    else:
        prefix = Path(str(qt["prefix"])).expanduser()
        if not prefix.is_dir():
            problems.append(f"qt.prefix does not exist: {prefix}")
        elif not (prefix / "bin" / RCC_NAME).is_file():
            problems.append(f"qt.prefix has no bin/{RCC_NAME}: {prefix}")

    font = profile.get("font")
    font = font if isinstance(font, dict) else {}
    if _blank(font.get("family")):
        problems.append(
            'font.family is required (family exactly as Qt reports it, e.g. "Inter")'
        )
    font_dir: Path | None = None
    if _blank(font.get("dir")):
        problems.append("font.dir is required (directory that contains the font files)")
    else:
        font_dir = Path(str(font["dir"])).expanduser()
        if not font_dir.is_dir():
            problems.append(f"font.dir does not exist: {font_dir}")
            font_dir = None

    files = _as_list(font.get("files"))
    if files is None or not all(isinstance(item, str) for item in files):
        problems.append("font.files must be a list of file names or globs")
    elif files and font_dir is not None and not resolve_font_files(font_dir, files):
        problems.append(f"font.files matched no file in {font_dir}: {files}")

    styles = _as_list(font.get("required_styles"))
    if styles is None or not all(isinstance(item, str) for item in styles):
        problems.append("font.required_styles must be a list of style names")

    maps = _as_list(font.get("maps"))
    if maps is None or not all(
        isinstance(item, str) and "=" in item for item in maps
    ):
        problems.append('font.maps must be a list of "old=new" strings')

    weights = font.get("weights")
    if weights is not None:
        if not isinstance(weights, dict):
            problems.append("font.weights must map QSS weight -> style name")
        else:
            for key in weights:
                try:
                    int(key)
                except (TypeError, ValueError):
                    problems.append(f"font.weights key is not a number: {key!r}")

    canvas = profile.get("canvas")
    if canvas is not None:
        if not (
            isinstance(canvas, list)
            and len(canvas) == 2
            and all(isinstance(item, int) and item > 0 for item in canvas)
        ):
            problems.append("canvas must be [width, height] with positive integers")

    return problems


def resolve_work_dir(root: Path, profile: dict[str, Any] | None) -> Path:
    """``work_dir`` from the profile (relative to the project), else a sibling folder."""
    configured = (profile or {}).get("work_dir")
    if not _blank(configured):
        path = Path(str(configured)).expanduser()
        return (path if path.is_absolute() else Path(root) / path).resolve()
    return (Path(root).parent / "figma2qt-work").resolve()


def load_profile(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise ProfileError([f"profile not found: {path}"], path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ProfileError([f"invalid YAML: {error}"], path) from error
    return data if data is not None else {}


def build_settings(
    project_root: Path,
    profile_path: Path | None = None,
    work_dir: Path | None = None,
) -> Settings:
    root = Path(project_root).resolve()
    profile_path = Path(profile_path or default_profile_path(root)).resolve()
    profile = load_profile(profile_path)
    problems = validate_profile(profile)
    if problems:
        raise ProfileError(problems, profile_path)

    qt = profile["qt"]
    font = profile["font"]
    font_dir = Path(str(font["dir"])).expanduser().resolve()
    patterns = font.get("files") or []

    preview = qt.get("preview_exe") or ""
    if not _blank(preview):
        preview_exe = Path(str(preview)).expanduser()
    else:
        preview_exe = HOME_DIR / "bin" / "qt_preview.exe"
    import os

    if os.environ.get("QT_PREVIEW_EXE"):
        preview_exe = Path(os.environ["QT_PREVIEW_EXE"])

    resolved_work = (
        Path(work_dir) if work_dir is not None else resolve_work_dir(root, profile)
    )

    return Settings(
        project_root=root,
        profile_path=profile_path,
        profile=profile,
        qt_prefix=Path(str(qt["prefix"])).expanduser().resolve(),
        preview_exe=preview_exe.resolve(),
        generator=str(qt.get("generator") or DEFAULT_GENERATOR),
        font_family=str(font["family"]).strip(),
        font_dir=font_dir,
        font_files=resolve_font_files(font_dir, patterns),
        font_required_styles=list(font.get("required_styles") or []),
        font_legacy_family=str(font.get("legacy_family") or ""),
        work_dir=resolved_work.resolve(),
    )


def profile_defaults(profile: dict[str, Any]) -> dict[str, Any]:
    """Per-page defaults derived from the profile (merged under pages.yaml)."""
    skip = {"qt", "canvas", "work_dir"}
    defaults = copy.deepcopy({k: v for k, v in profile.items() if k not in skip})
    if profile.get("canvas"):
        defaults["target_size"] = list(profile["canvas"])
    font = defaults.setdefault("font", {})
    font.setdefault("maps", [])
    return defaults


def font_weights_text(font: dict[str, Any]) -> str:
    weights = font.get("weights") or {}
    if weights:
        rows = sorted(weights.items(), key=lambda item: int(item[0]))
        return "\n".join(f"   {int(key)} -> {value}" for key, value in rows)
    return (
        "   (no mapping configured; use numeric QSS weights that resolve to the "
        "styles reported by the font audit)"
    )


def write_templates(root: Path, force: bool = False) -> list[dict[str, str]]:
    root = Path(root).resolve()
    target_dir = root / PROFILE_DIR_NAME
    target_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for name, text in ((PROFILE_FILE, PROFILE_TEMPLATE), (PAGES_FILE, PAGES_TEMPLATE)):
        path = target_dir / name
        if path.exists() and not force:
            results.append({"file": str(path), "status": "exists (kept)"})
            continue
        path.write_text(text, encoding="utf-8", newline="\n")
        results.append({"file": str(path), "status": "written"})
    return results
