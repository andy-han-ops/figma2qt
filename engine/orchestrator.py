#!/usr/bin/env python3
"""Batch orchestrator for the Figma-to-Qt Widgets restoration pipeline.

The orchestrator is deterministic glue: it fetches Figma references, invokes
`agy` for generation/repair, renders .ui files headlessly, compares pixels,
and enforces pass/fail + scope guardrails. `agy` is the only LLM involved.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import yaml

import config
import diff
import figma_mcp
import render
import report
from state import StateStore

PACKAGE_DIR = Path(__file__).resolve().parent
HOME_DIR = PACKAGE_DIR.parent
DEFAULT_SCHEMA = PACKAGE_DIR / "schemas" / "agy_result.json"
AGY_EXE = os.environ.get("AGY_EXE", "agy")

# Filled in by setup_environment() before any command runs.
PROJECT_ROOT: Path = Path.cwd()
SETTINGS: config.Settings | None = None
PROFILE_DEFAULTS: dict[str, Any] = {}


def plugin_dir() -> Path:
    """Directory that holds skills/ (installed layout, or plugin/ in the repo)."""
    for candidate in (HOME_DIR, HOME_DIR / "plugin"):
        if (candidate / "skills").is_dir():
            return candidate
    return HOME_DIR


def skill_file(name: str) -> Path:
    return plugin_dir() / "skills" / name / "SKILL.md"


def common_prompt_values(page: dict[str, Any]) -> dict[str, Any]:
    font = page.get("font", {})
    search_dirs = page.get("assets", {}).get("search_dirs") or []
    return {
        "font_family": font.get("family")
        or (SETTINGS.font_family if SETTINGS else ""),
        "font_weights": config.font_weights_text(font),
        "engine_dir": PACKAGE_DIR.as_posix(),
        "python": Path(sys.executable).as_posix(),
        "asset_search_dirs": (
            "\n".join(f"  - {item}" for item in search_dirs)
            if search_dirs
            else "  (none configured)"
        ),
        "skill_path": skill_file("figma-to-qt-ui").as_posix(),
        "repair_skill_path": skill_file("figma-qt-repair").as_posix(),
    }


@dataclass
class PagePaths:
    page_dir: Path
    design_dir: Path
    reference: Path
    metadata: Path
    ui_dir: Path
    ui: Path
    icons_dir: Path
    qrc: Path
    rcc: Path
    assets_dir: Path | None
    assets_qrc: Path | None
    assets_rcc: Path | None
    render_dir: Path
    diff_dir: Path
    history_dir: Path
    best_dir: Path


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def parse_figma_url(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    file_key = ""
    if len(parts) >= 2 and parts[0] in {"design", "file", "board", "make"}:
        file_key = parts[1]
        if len(parts) >= 4 and parts[2] == "branch":
            # /design/<fileKey>/branch/<branchKey>/<fileName>
            file_key = parts[3]
    if not file_key:
        raise ValueError(
            "could not find fileKey in Figma URL; expected "
            "https://figma.com/design/<fileKey>/...?node-id=<node>"
        )

    query = parse_qs(parsed.query)
    node_id = ""
    for key in ("node-id", "node_id", "nodeId"):
        if key in query and query[key]:
            node_id = query[key][0].replace("-", ":")
            break

    return {"file_key": file_key, "node_id": node_id, "url": url}


def load_manifest(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = deep_merge(PROFILE_DEFAULTS, data.get("defaults", {}) or {})
    pages = []
    for page in data.get("pages", []):
        merged = deep_merge(defaults, page)
        merged.setdefault("id", f"page_{len(pages) + 1}")
        merged.setdefault("figma", {})
        merged.setdefault("output", {})
        merged.setdefault("thresholds", {})
        merged.setdefault("agy", {})
        pages.append(merged)
    return defaults, pages


def _resolve_optional_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def build_paths(work_dir: Path, page: dict[str, Any]) -> PagePaths:
    page_id = page["id"]
    page_dir = (work_dir / page_id).resolve()
    output = page.get("output", {})
    assets = page.get("assets", {})
    ui_name = output.get("ui") or f"{page_id}.ui"
    assets_qrc = _resolve_optional_path(assets.get("qrc"))
    return PagePaths(
        page_dir=page_dir,
        design_dir=page_dir / "design",
        reference=page_dir / "design" / "reference.png",
        metadata=page_dir / "design" / "metadata.json",
        ui_dir=page_dir / "ui",
        ui=page_dir / "ui" / ui_name,
        icons_dir=page_dir / "ui" / output.get("icons", "icons"),
        qrc=page_dir / "ui" / output.get("qrc", "resources.qrc"),
        rcc=page_dir / "build" / f"{Path(ui_name).stem}.rcc",
        assets_dir=_resolve_optional_path(assets.get("dir")),
        assets_qrc=assets_qrc,
        assets_rcc=(
            page_dir / "build" / "assets.rcc" if assets_qrc else None
        ),
        render_dir=page_dir / "render",
        diff_dir=page_dir / "diff",
        history_dir=page_dir / "history",
        best_dir=page_dir / "best",
    )


def ensure_page_dirs(paths: PagePaths) -> None:
    for directory in (
        paths.page_dir,
        paths.design_dir,
        paths.ui_dir,
        paths.icons_dir,
        paths.render_dir,
        paths.diff_dir,
        paths.history_dir,
        paths.best_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def target_size(page: dict[str, Any]) -> tuple[int, int]:
    size = page.get("target_size") or page.get("defaults", {}).get("target_size")
    if not size:
        return 1920, 1080
    return int(size[0]), int(size[1])


def thresholds(page: dict[str, Any]) -> dict[str, float]:
    return dict(diff.DEFAULT_THRESHOLDS, **(page.get("thresholds") or {}))


def git_status() -> set[str]:
    completed = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip()
    }


def check_scope_guard(
    before: set[str],
    after: set[str],
    allow_repo_changes: bool,
    strict: bool = False,
) -> None:
    new_entries = after - before
    if not new_entries or allow_repo_changes:
        return
    message = (
        "repository changes appeared during the agy run; review them. "
        "New changes:\n  " + "\n  ".join(sorted(new_entries))
    )
    if strict:
        raise RuntimeError(message)
    print("WARNING: " + message, file=sys.stderr)


def parse_stream(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _walk(value: Any) -> Any:
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def extract_final_result(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        result = event.get("result") if isinstance(event, dict) else None
        if isinstance(result, dict):
            response = result.get("response")
            if isinstance(response, str) and response.strip():
                try:
                    parsed = json.loads(response, strict=False)
                except json.JSONDecodeError:
                    parsed = None
                if (
                    isinstance(parsed, dict)
                    and isinstance(parsed.get("changed_files"), list)
                    and isinstance(parsed.get("diagnosis"), str)
                    and isinstance(parsed.get("confidence"), (int, float))
                ):
                    return parsed

        for node in _walk(event):
            if not isinstance(node, dict):
                continue
            if (
                isinstance(node.get("changed_files"), list)
                and isinstance(node.get("diagnosis"), str)
                and isinstance(node.get("confidence"), (int, float))
            ):
                return node
    return None


def extract_conversation_id(events: list[dict[str, Any]]) -> str | None:
    keys = ("conversation_id", "conversationId", "session_id", "sessionId", "thread_id")
    for event in reversed(events):
        for node in _walk(event):
            if isinstance(node, dict):
                for key in keys:
                    value = node.get(key)
                    if isinstance(value, str) and value:
                        return value
    return None


def extract_usage(events: list[dict[str, Any]]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for event in events:
        for node in _walk(event):
            if isinstance(node, dict) and isinstance(node.get("usage"), dict):
                for key, value in node["usage"].items():
                    if isinstance(value, int):
                        usage[key] = max(usage.get(key, 0), value)
    return usage


def run_agy(
    prompt: str,
    *,
    page_dir: Path,
    round_number: int,
    schema_path: Path,
    agy_config: dict[str, Any],
    conversation_id: str | None,
    agent: str | None,
) -> dict[str, Any]:
    log_path = page_dir / f"agy-round-{round_number}.log"
    stream_path = page_dir / f"agy-round-{round_number}.stream.jsonl"
    timeout = int(agy_config.get("timeout", 1200))

    command = [
        AGY_EXE,
        "-p",
        prompt,
        "--mode",
        str(agy_config.get("mode", "accept-edits")),
        "--output-format",
        "stream-json",
        "--json-schema",
        str(schema_path),
        "--print-timeout",
        f"{timeout}s",
        "--log-file",
        str(log_path),
        "--add-dir",
        str(page_dir),
        "--add-dir",
        str(HOME_DIR),
    ]
    if conversation_id:
        command.extend(["--conversation", conversation_id])
    if agy_config.get("model"):
        command.extend(["--model", str(agy_config["model"])])
    if agy_config.get("effort"):
        command.extend(["--effort", str(agy_config["effort"])])
    if agent:
        command.extend(["--agent", agent])
    if agy_config.get("skip_permissions"):
        command.append("--dangerously-skip-permissions")

    started = time.time()
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout + 120,
    )
    duration = round(time.time() - started, 2)
    stream_path.write_text(completed.stdout, encoding="utf-8")

    events = parse_stream(completed.stdout)
    if completed.returncode != 0:
        raise RuntimeError(
            f"agy failed (exit {completed.returncode}). "
            f"See {log_path}\n{completed.stderr[-2000:]}"
        )

    return {
        "returncode": completed.returncode,
        "duration": duration,
        "result": extract_final_result(events),
        "conversation_id": extract_conversation_id(events) or conversation_id,
        "usage": extract_usage(events),
        "stream": str(stream_path),
        "log": str(log_path),
        "stderr": completed.stderr,
    }


def prompt_from_template(name: str, values: dict[str, Any]) -> str:
    template_path = PACKAGE_DIR / "prompts" / name
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.safe_substitute(values)


def generate_prompt(
    page: dict[str, Any],
    paths: PagePaths,
    width: int,
    height: int,
) -> str:
    figma = page.get("figma", {})
    output = page.get("output", {})
    return prompt_from_template(
        "generate.md",
        {
            "page_id": page["id"],
            "file_key": figma.get("file_key", ""),
            "node_id": figma.get("node_id", ""),
            "figma_url": figma.get("url", ""),
            "assets_dir": page.get("assets", {}).get("dir", ""),
            "assets_qrc": page.get("assets", {}).get("qrc", ""),
            "assets_manifest": page.get("assets", {}).get("manifest", ""),
            "reuse_assets": (
                "true" if page.get("assets", {}).get("reuse") else "false"
            ),
            "reference_path": paths.reference.as_posix(),
            "ui_path": paths.ui.as_posix(),
            "icons_dir": paths.icons_dir.as_posix(),
            "qrc_path": paths.qrc.as_posix(),
            "resource_prefix": output.get("resource_prefix", "/res"),
            "width": width,
            "height": height,
            **common_prompt_values(page),
        },
    )


def repair_prompt(
    page: dict[str, Any],
    paths: PagePaths,
    report_path: Path,
    round_number: int,
) -> str:
    return prompt_from_template(
        "repair.md",
        {
            "page_id": page["id"],
            "round": round_number,
            "report_path": report_path.as_posix(),
            "ui_path": paths.ui.as_posix(),
            "icons_dir": paths.icons_dir.as_posix(),
            "qrc_path": paths.qrc.as_posix(),
            "resource_prefix": page.get("output", {}).get(
                "resource_prefix", "/res"
            ),
            "assets_dir": page.get("assets", {}).get("dir", ""),
            "assets_qrc": page.get("assets", {}).get("qrc", ""),
            "assets_manifest": page.get("assets", {}).get("manifest", ""),
            "reuse_assets": (
                "true" if page.get("assets", {}).get("reuse") else "false"
            ),
            "reference_path": paths.reference.as_posix(),
            "render_path": (paths.render_dir / f"round-{round_number}.png").as_posix(),
            **common_prompt_values(page),
        },
    )


def regenerate_prompt(
    page: dict[str, Any],
    paths: PagePaths,
    report_path: Path,
    width: int,
    height: int,
) -> str:
    base = generate_prompt(page, paths, width, height)
    return (
        base
        + "\n\nIMPORTANT: The previous UI had structural mismatches. Regenerate the "
        + ".ui from the Figma metadata instead of patching it. Previous diff report: "
        + report_path.as_posix()
        + "\n"
    )


def snapshot_ui(paths: PagePaths, round_number: int) -> None:
    destination = paths.history_dir / f"round-{round_number}" / "ui"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(paths.ui_dir, destination)


def promote_best(paths: PagePaths) -> None:
    destination = paths.best_dir / "ui"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(paths.ui_dir, destination)


def restore_best(paths: PagePaths) -> None:
    source = paths.best_dir / "ui"
    if not source.exists():
        return
    shutil.copytree(source, paths.ui_dir, dirs_exist_ok=True)


def _widget_text_properties(widget: ET.Element) -> tuple[str, str]:
    text = ""
    placeholder = ""
    for prop in widget.findall("property"):
        name = prop.get("name", "")
        string = prop.find("string")
        value = (string.text or "").strip() if string is not None else ""
        if name == "text":
            text = value
        elif name == "placeholderText":
            placeholder = value
    return text, placeholder


def semantic_audit(ui_path: Path) -> dict[str, Any]:
    tree = ET.parse(ui_path)
    root = tree.getroot()
    root_widget = root.find("widget")
    root_name = root_widget.get("name", "") if root_widget is not None else ""

    labels = 0
    buttons = 0
    inputs = 0
    texts: list[str] = []

    for widget in root.iter("widget"):
        cls = widget.get("class", "")
        text, placeholder = _widget_text_properties(widget)

        if cls == "QLabel" and text:
            labels += 1
            texts.append(text)
        elif cls in {"QPushButton", "QToolButton", "QRadioButton", "QCheckBox"} and text:
            buttons += 1
            texts.append(text)
        elif cls == "QLineEdit" and (placeholder or text):
            inputs += 1
            texts.append(placeholder or text)

    style_texts: list[str] = []
    for prop in root.iter("property"):
        if prop.get("name") != "styleSheet":
            continue
        string = prop.find("string")
        if string is not None and string.text:
            style_texts.append(string.text)
    style_text = "\n".join(style_texts)

    transparent_rules = len(
        re.findall(r"color\s*:\s*transparent", style_text, flags=re.IGNORECASE)
    )

    def container_has_text(widget: ET.Element) -> bool:
        for child in widget.iter("widget"):
            cls = child.get("class", "")
            if cls not in {
                "QLabel",
                "QPushButton",
                "QToolButton",
                "QRadioButton",
                "QCheckBox",
                "QLineEdit",
            }:
                continue
            text, placeholder = _widget_text_properties(child)
            if text or placeholder:
                return True
        return False

    card_widgets: set[str] = set()
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style_text):
        if "border-image" not in body and "background-image" not in body:
            continue
        for name in re.findall(r"#([A-Za-z_][A-Za-z0-9_]*)", selector):
            if name == root_name:
                continue
            widget = next(
                (item for item in root.iter("widget") if item.get("name") == name),
                None,
            )
            if widget is None:
                continue
            if widget.get("class") in {"QWidget", "QFrame"} and container_has_text(widget):
                card_widgets.add(name)

    return {
        "transparentTextRules": transparent_rules,
        "cardImageRules": len(card_widgets),
        "cardImageWidgets": sorted(card_widgets),
        "labelsWithText": labels,
        "buttonsWithText": buttons,
        "inputsWithPlaceholder": inputs,
        "totalTextWidgets": labels + buttons + inputs,
        "texts": texts[:50],
    }


def render_and_diff(
    page: dict[str, Any],
    paths: PagePaths,
    round_number: int,
) -> dict[str, Any]:
    width, height = target_size(page)
    resource_prefix = page.get("output", {}).get("resource_prefix", "/res")

    compiled = render.ensure_page_rcc(
        paths.ui_dir,
        paths.qrc,
        paths.rcc,
        resource_prefix=resource_prefix,
    )
    if compiled["returncode"] != 0:
        raise RuntimeError(
            "rcc failed:\n" + json.dumps(compiled, ensure_ascii=False, indent=2)
        )

    rcc_paths = [paths.rcc]
    if paths.assets_qrc and paths.assets_rcc:
        if not paths.assets_qrc.exists():
            raise RuntimeError(f"project assets qrc not found: {paths.assets_qrc}")
        compiled_assets = render.compile_qrc(paths.assets_qrc, paths.assets_rcc)
        if compiled_assets["returncode"] != 0:
            raise RuntimeError(
                "project assets rcc failed:\n"
                + json.dumps(compiled_assets, ensure_ascii=False, indent=2)
            )
        rcc_paths.append(paths.assets_rcc)

    render_png = paths.render_dir / f"round-{round_number}.png"
    dump_json = paths.render_dir / f"round-{round_number}.json"
    rendered = render.render_ui(
        paths.ui,
        render_png,
        rcc_paths=rcc_paths,
        dump_json=dump_json,
        width=width,
        height=height,
        font_family=page.get("font", {}).get("family") or SETTINGS.font_family,
        font_maps=page.get("font", {}).get("maps", []),
        style=page.get("render", {}).get("style", ""),
        allow_missing_resources=page.get("render", {}).get(
            "allow_missing_resources", False
        ),
    )
    if rendered["returncode"] != 0:
        raise RuntimeError(
            "qt_preview failed:\n"
            + json.dumps(rendered, ensure_ascii=False, indent=2)
        )

    round_dir = paths.diff_dir / f"round-{round_number}"
    result = diff.compare(
        reference_path=paths.reference,
        render_path=render_png,
        out_dir=round_dir,
        thresholds=thresholds(page),
    )

    semantic = semantic_audit(paths.ui)
    result["semantic"] = semantic
    semantic_config = page.get("semantic", {})
    if semantic_config.get("required", False):
        min_text = int(semantic_config.get("min_text_widgets", 0))
        forbid_transparent = semantic_config.get(
            "forbid_transparent_text", True
        )
        semantic_passed = semantic["totalTextWidgets"] >= min_text
        if forbid_transparent and semantic["transparentTextRules"] > 0:
            semantic_passed = False
        forbid_card_images = semantic_config.get("forbid_card_images", True)
        if forbid_card_images and semantic["cardImageRules"] > 0:
            semantic_passed = False
        result["semantic_pass"] = semantic_passed
        result["pass"] = bool(result["pass"] and semantic_passed)
    else:
        result["semantic_pass"] = True

    result["render"] = rendered
    result["dump_json"] = str(dump_json)
    (round_dir / "report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def fetch_page_reference(
    client: figma_mcp.FigmaMCP,
    page: dict[str, Any],
    paths: PagePaths,
) -> dict[str, Any]:
    figma = page.get("figma", {})
    file_key = figma.get("file_key", "")
    node_id = figma.get("node_id", "")
    if not file_key or not node_id:
        raise RuntimeError(
            f"page {page['id']} has no figma.file_key / figma.node_id"
        )
    width, height = target_size(page)
    ensure_page_dirs(paths)
    result = client.fetch_reference(
        file_key,
        node_id,
        paths.reference,
        max_dimension=max(width, height),
        metadata_path=paths.metadata,
    )
    try:
        from PIL import Image

        with Image.open(paths.reference) as image:
            result["reference_size"] = list(image.size)
    except Exception:
        result["reference_size"] = []
    return result


def page_font_config(page: dict[str, Any]) -> dict[str, Any]:
    return page.get("font", {})


def run_page(
    page: dict[str, Any],
    *,
    work_dir: Path,
    args: argparse.Namespace,
    store: StateStore,
    client: figma_mcp.FigmaMCP | None,
) -> dict[str, Any]:
    page_id = page["id"]
    paths = build_paths(work_dir, page)
    ensure_page_dirs(paths)
    width, height = target_size(page)
    agy_config = page.get("agy", {})
    schema_path = Path(agy_config.get("schema", str(DEFAULT_SCHEMA)))

    figma = page.get("figma", {})
    store.upsert_page(
        page_id,
        figma_file=figma.get("file_key", ""),
        node_id=figma.get("node_id", ""),
        status="running",
    )

    if args.refresh or not paths.reference.exists():
        if args.skip_fetch:
            raise RuntimeError(
                f"reference missing for {page_id} and --skip-fetch was set"
            )
        if client is None:
            client = figma_mcp.FigmaMCP(args.mcp_url)
        fetched = fetch_page_reference(client, page, paths)
        print(json.dumps({"page": page_id, "fetch": fetched}, ensure_ascii=False))

    if args.regenerate and paths.ui_dir.exists() and not args.dry_run:
        archive = paths.history_dir / "pre-generate"
        if archive.exists():
            shutil.rmtree(archive)
        shutil.copytree(paths.ui_dir, archive)
        shutil.rmtree(paths.ui_dir)
        paths.ui_dir.mkdir(parents=True, exist_ok=True)
        paths.icons_dir.mkdir(parents=True, exist_ok=True)

    if args.skip_agy and not paths.ui.exists():
        raise RuntimeError(
            f"UI missing for {page_id} and --skip-agy was set"
        )
    if not args.skip_agy and (args.regenerate or not paths.ui.exists()):
        prompt = generate_prompt(page, paths, width, height)
        (paths.page_dir / "prompt-generate.txt").write_text(prompt, encoding="utf-8")
        if args.dry_run:
            print(f"[dry-run] wrote {paths.page_dir / 'prompt-generate.txt'}")
            return {"page": page_id, "status": "dry-run"}
        before = git_status()
        generated = run_agy(
            prompt,
            page_dir=paths.page_dir,
            round_number=0,
            schema_path=schema_path,
            agy_config=agy_config,
            conversation_id=None,
            agent=None,
        )
        after = git_status()
        check_scope_guard(
            before,
            after,
            args.allow_repo_changes,
            strict=args.strict_guard,
        )
        conversation_id = generated["conversation_id"]
        if generated.get("result"):
            (paths.page_dir / "agy-result-generate.json").write_text(
                json.dumps(generated["result"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    else:
        conversation_id = store.get_page(page_id) or {}
        conversation_id = conversation_id.get("conversation_id")

    max_rounds = int(page.get("max_rounds", 6))
    best_ssim = float(store.get_page(page_id).get("best_ssim") or 0.0) if store.get_page(page_id) else 0.0
    stagnant_rounds = 0
    regenerated = False
    last_report: dict[str, Any] | None = None
    last_round = 0

    for round_number in range(0, max_rounds + 1):
        last_round = round_number
        if args.dry_run:
            print(f"[dry-run] would render round {round_number} for {page_id}")
            return {"page": page_id, "status": "dry-run"}

        last_report = render_and_diff(page, paths, round_number)
        report_path = paths.diff_dir / f"round-{round_number}" / "report.json"
        store.add_round(
            page_id,
            round_number,
            last_report,
            changed_files=[],
            conversation_id=conversation_id,
        )
        report.build_page_report(paths.page_dir, page_id)

        ssim = float(last_report["metrics"]["ssim"])
        print(
            json.dumps(
                {
                    "page": page_id,
                    "round": round_number,
                    "pass": last_report["pass"],
                    "ssim": ssim,
                    "mismatch_ratio": last_report["metrics"]["mismatch_ratio"],
                    "delta_e_mean": last_report["metrics"]["delta_e_mean"],
                },
                ensure_ascii=False,
            )
        )

        if last_report["pass"]:
            promote_best(paths)
            store.upsert_page(
                page_id,
                figma_file=figma.get("file_key", ""),
                node_id=figma.get("node_id", ""),
                status="passed",
                round_number=round_number,
                best_ssim=ssim,
                conversation_id=conversation_id,
            )
            return {"page": page_id, "status": "passed", "round": round_number}

        if ssim > best_ssim + 0.002:
            best_ssim = ssim
            stagnant_rounds = 0
            promote_best(paths)
        else:
            stagnant_rounds += 1

        if round_number >= max_rounds:
            break

        if args.skip_agy:
            break

        snapshot_ui(paths, round_number)
        if stagnant_rounds >= 2 and not regenerated:
            prompt = regenerate_prompt(page, paths, report_path, width, height)
            regenerated = True
            stagnant_rounds = 0
        else:
            prompt = repair_prompt(page, paths, report_path, round_number)

        prompt_path = paths.page_dir / f"prompt-repair-{round_number + 1}.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        before = git_status()
        repaired = run_agy(
            prompt,
            page_dir=paths.page_dir,
            round_number=round_number + 1,
            schema_path=schema_path,
            agy_config=agy_config,
            conversation_id=conversation_id,
            agent=agy_config.get("agent"),
        )
        after = git_status()
        check_scope_guard(
            before,
            after,
            args.allow_repo_changes,
            strict=args.strict_guard,
        )
        conversation_id = repaired["conversation_id"] or conversation_id
        if repaired.get("result"):
            (paths.page_dir / f"agy-result-round-{round_number + 1}.json").write_text(
                json.dumps(repaired["result"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    restore_best(paths)
    store.upsert_page(
        page_id,
        figma_file=figma.get("file_key", ""),
        node_id=figma.get("node_id", ""),
        status="failed",
        round_number=last_round,
        best_ssim=best_ssim,
        conversation_id=conversation_id,
    )
    report.build_page_report(paths.page_dir, page_id)
    return {
        "page": page_id,
        "status": "failed",
        "round": last_round,
        "best_ssim": best_ssim,
        "last_report": str(
            paths.diff_dir / f"round-{last_round}" / "report.json"
        ),
    }


def _run_build_harness(settings: config.Settings, build_dir: Path | None) -> dict[str, Any]:
    build_dir = (build_dir or HOME_DIR / "build" / "qt_preview").resolve()
    configure = subprocess.run(
        [
            "cmake",
            "-S",
            str(PACKAGE_DIR / "qt_preview"),
            "-B",
            str(build_dir),
            "-G",
            settings.generator,
            "-A",
            "x64",
            f"-DCMAKE_PREFIX_PATH={settings.qt_prefix.as_posix()}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    build = subprocess.run(
        ["cmake", "--build", str(build_dir), "--config", "Release"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    built = build_dir / "Release" / "qt_preview.exe"
    copied_to = ""
    if build.returncode == 0 and built.exists():
        target = HOME_DIR / "bin" / "qt_preview.exe"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built, target)
        copied_to = str(target)
    return {
        "configure_returncode": configure.returncode,
        "build_returncode": build.returncode,
        "configure_stdout": configure.stdout[-2000:],
        "build_stdout": build.stdout[-2000:],
        "copied_to": copied_to,
    }


def command_preflight(args: argparse.Namespace) -> int:
    settings = SETTINGS
    assert settings is not None
    checks: dict[str, Any] = {
        "project_root": str(settings.project_root),
        "profile": str(settings.profile_path),
    }

    if args.build_harness and not settings.preview_exe.exists():
        checks["build_harness"] = _run_build_harness(
            settings, args.harness_build_dir
        )

    checks["agy"] = shutil.which(AGY_EXE) or ""
    checks["qt_prefix"] = str(settings.qt_prefix)
    checks["rcc"] = str(render.rcc_exe())
    checks["rcc_exists"] = render.rcc_exe().exists()
    checks["qt_preview"] = str(render.qpreview_exe())
    checks["qt_preview_exists"] = render.qpreview_exe().exists()
    checks["font_files"] = [str(path) for path in settings.font_files]
    if checks["qt_preview_exists"]:
        checks["fonts"] = render.font_audit()
    else:
        checks["fonts"] = {
            "returncode": 1,
            "data": {},
            "stderr": "qt_preview.exe is missing; run scripts/build_preview.ps1",
        }
    checks["figma_mcp"] = {}
    try:
        client = figma_mcp.FigmaMCP()
        checks["figma_mcp"] = {
            "url": client.url,
            "tools": [tool.get("name", "") for tool in client.list_tools()],
        }
    except Exception as error:
        checks["figma_mcp"] = {"error": str(error)}

    required_tools = {"get_metadata", "get_design_context", "get_screenshot"}
    available_tools = set(checks["figma_mcp"].get("tools", []))
    checks["figma_tools_ok"] = required_tools.issubset(available_tools)
    checks["fonts_ok"] = checks["fonts"]["returncode"] == 0
    checks["critical_ok"] = bool(
        checks["agy"]
        and checks["qt_preview_exists"]
        and checks["rcc_exists"]
        and checks["fonts_ok"]
    )

    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if checks["critical_ok"] else 1


def command_init(args: argparse.Namespace) -> int:
    root = (args.project_root or Path.cwd()).resolve()
    results = config.write_templates(root, force=args.force)
    print(json.dumps({"project_root": str(root), "files": results}, indent=2))
    print(
        "Fill the required fields in .figma2qt/profile.yaml: "
        "qt.prefix, font.family, font.dir.",
        file=sys.stderr,
    )
    return 0


def command_profile(args: argparse.Namespace) -> int:
    settings = SETTINGS
    assert settings is not None
    font = settings.profile.get("font", {})
    payload = {
        "project_root": str(settings.project_root),
        "profile": str(settings.profile_path),
        "pages_manifest": str(args.manifest),
        "work_dir": str(args.work_dir),
        "engine_dir": PACKAGE_DIR.as_posix(),
        "plugin_dir": plugin_dir().as_posix(),
        "qt_prefix": settings.qt_prefix.as_posix(),
        "rcc": render.rcc_exe().as_posix(),
        "qt_preview": render.qpreview_exe().as_posix(),
        "qt_preview_exists": render.qpreview_exe().exists(),
        "font": {
            "family": settings.font_family,
            "dir": settings.font_dir.as_posix(),
            "files": [path.as_posix() for path in settings.font_files],
            "required_styles": settings.font_required_styles,
            "weights": font.get("weights") or {},
            "maps": font.get("maps") or [],
        },
        "canvas": PROFILE_DEFAULTS.get("target_size", [1920, 1080]),
        "thresholds": thresholds(PROFILE_DEFAULTS),
        "semantic": PROFILE_DEFAULTS.get("semantic", {}),
        "assets": PROFILE_DEFAULTS.get("assets", {}),
        "output": PROFILE_DEFAULTS.get("output", {}),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _semantic_verdict(
    page: dict[str, Any], semantic: dict[str, Any]
) -> tuple[bool, list[str]]:
    config_block = page.get("semantic", {})
    reasons: list[str] = []
    min_text = int(config_block.get("min_text_widgets", 0))
    if semantic["totalTextWidgets"] < min_text:
        reasons.append(
            f"only {semantic['totalTextWidgets']} real text widgets (< {min_text})"
        )
    if config_block.get("forbid_transparent_text", True) and semantic["transparentTextRules"] > 0:
        reasons.append("QSS contains color: transparent")
    if config_block.get("forbid_card_images", True) and semantic["cardImageRules"] > 0:
        reasons.append(
            "text-bearing container uses border-image/background-image: "
            + ", ".join(semantic["cardImageWidgets"])
        )
    return not reasons, reasons


def command_verify(args: argparse.Namespace) -> int:
    settings = SETTINGS
    assert settings is not None
    page = deep_merge(PROFILE_DEFAULTS, {"id": "verify"})
    if args.width and args.height:
        width, height = args.width, args.height
    else:
        width, height = target_size(page)

    ui = args.ui.resolve()
    reference = args.reference.resolve()
    for label, item in (("ui", ui), ("reference", reference)):
        if not item.exists():
            print(json.dumps({"pass": False, "error": f"{label} not found: {item}"}))
            return 1

    out_dir = args.out_dir.resolve()
    build_dir = out_dir / "build"
    render_png = out_dir / "render" / f"{args.name}.png"
    dump_json = out_dir / "render" / f"{args.name}.json"
    diff_dir = out_dir / "diff" / args.name

    rcc_paths: list[Path] = []
    page_qrc = args.qrc.resolve() if args.qrc else ui.parent / "icons" / "resources.qrc"
    if page_qrc.exists():
        compiled = render.compile_qrc(page_qrc, build_dir / "page.rcc")
        if compiled["returncode"] != 0:
            print(json.dumps({"pass": False, "rcc_error": compiled}, ensure_ascii=False, indent=2))
            return 1
        rcc_paths.append(build_dir / "page.rcc")
    elif args.qrc:
        print(json.dumps({"pass": False, "error": f"qrc not found: {page_qrc}"}))
        return 1
    for index, qrc in enumerate(args.project_qrc, start=1):
        compiled = render.compile_qrc(qrc, build_dir / f"project_{index}.rcc")
        if compiled["returncode"] != 0:
            print(json.dumps({"pass": False, "rcc_error": compiled}, ensure_ascii=False, indent=2))
            return 1
        rcc_paths.append(build_dir / f"project_{index}.rcc")

    rendered = render.render_ui(
        ui,
        render_png,
        rcc_paths=rcc_paths,
        dump_json=dump_json,
        width=width,
        height=height,
        font_family=page.get("font", {}).get("family") or settings.font_family,
        font_maps=page.get("font", {}).get("maps", []),
        allow_missing_resources=True,
    )
    if rendered["returncode"] != 0:
        print(json.dumps({"pass": False, "render_error": rendered}, ensure_ascii=False, indent=2))
        return 1

    result = diff.compare(
        reference_path=reference,
        render_path=render_png,
        out_dir=diff_dir,
        thresholds=thresholds(page),
    )
    semantic = semantic_audit(ui)
    semantic_ok, semantic_reasons = _semantic_verdict(page, semantic)

    dump = json.loads(dump_json.read_text(encoding="utf-8"))
    inline = dump.get("inlineQss", {})
    missing = inline.get("missingResources", [])
    families = inline.get("fontFamilies", [])
    expected_family = page.get("font", {}).get("family") or settings.font_family
    font_ok = all(family == expected_family for family in families)
    embedded = inline.get("embeddedPixmaps", 0)

    problems = list(semantic_reasons)
    if missing:
        problems.append(f"missing resources: {missing}")
    if not font_ok:
        problems.append(f"font families other than {expected_family!r}: {families}")
    if embedded:
        problems.append(f"{embedded} embedded pixmap/iconset tags")
    if not result.get("pass"):
        problems.append("pixel metrics below thresholds")

    summary = {
        "pass": bool(result.get("pass")) and not (problems and len(problems) > 0 and any(
            not p.startswith("pixel metrics") for p in problems
        )),
        "pixel_pass": bool(result.get("pass")),
        "metrics": result.get("metrics"),
        "thresholds": thresholds(page),
        "problems": problems,
        "semantic": semantic,
        "missing_resources": missing,
        "font_families": families,
        "worst_regions": result.get("worst_regions", [])[:5],
        "render": str(render_png),
        "dump": str(dump_json),
        "report_dir": str(diff_dir),
    }
    (diff_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["pass"] else 1


def _find_text_widget(node: dict[str, Any]) -> dict[str, Any] | None:
    if node.get("text") and node.get("class") in {"QLabel", "QPushButton"}:
        return node
    for child in node.get("children", []):
        found = _find_text_widget(child)
        if found:
            return found
    return None


def command_selftest(args: argparse.Namespace) -> int:
    settings = SETTINGS
    assert settings is not None
    template = HOME_DIR / "examples" / "selftest" / "selftest.ui"
    if not template.exists():
        print(json.dumps({"pass": False, "error": f"missing {template}"}))
        return 1
    if not render.qpreview_exe().exists():
        print(
            json.dumps(
                {
                    "pass": False,
                    "error": f"qt_preview.exe not found: {render.qpreview_exe()}",
                }
            )
        )
        return 1

    work = args.work_dir / "selftest"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    ui = work / "selftest.ui"
    ui.write_text(
        template.read_text(encoding="utf-8").replace(
            "@FONT_FAMILY@", settings.font_family
        ),
        encoding="utf-8",
    )
    png = work / "render.png"
    dump_json = work / "render.json"
    rendered = render.render_ui(
        ui, png, dump_json=dump_json, width=640, height=360
    )
    if rendered["returncode"] != 0:
        print(json.dumps({"pass": False, "render_error": rendered}, ensure_ascii=False, indent=2))
        return 1
    reference = work / "reference.png"
    shutil.copy2(png, reference)
    result = diff.compare(
        reference_path=reference,
        render_path=png,
        out_dir=work / "diff",
        thresholds=dict(
            diff.DEFAULT_THRESHOLDS,
            ssim=0.999,
            mismatch_ratio=0.0001,
            delta_e_mean=0.1,
        ),
    )
    dump = json.loads(dump_json.read_text(encoding="utf-8"))
    audit = dump.get("fontAudit", {})
    text_widget = _find_text_widget(dump.get("widget", {})) or {}
    family_present = bool(audit.get("familyPresent"))
    resolved_family = text_widget.get("fontFamily", "")
    font_resolves = resolved_family == settings.font_family
    summary = {
        "pass": bool(result.get("pass")) and family_present and font_resolves,
        "ssim": (result.get("metrics") or {}).get("ssim"),
        "font_family_present": family_present,
        "font_resolved_family": resolved_family,
        "font_expected_family": settings.font_family,
        "font_styles": audit.get("styles", []),
        "work_dir": str(work),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not family_present or not font_resolves:
        print(
            "The configured font did not load. Check font.dir / font.files and "
            "that font.family matches the family name inside the font file.",
            file=sys.stderr,
        )
    return 0 if summary["pass"] else 1


def command_add(args: argparse.Namespace) -> int:
    parsed = parse_figma_url(args.url)
    if not parsed["node_id"]:
        raise SystemExit(
            "Figma URL must include node-id. In Figma, right-click the target "
            "frame and use 'Copy link to selection'."
        )

    if not args.manifest.exists():
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(config.PAGES_TEMPLATE, encoding="utf-8")
    data = yaml.safe_load(args.manifest.read_text(encoding="utf-8")) or {}
    pages = data.setdefault("pages", [])
    entry = next(
        (page for page in pages if page.get("id") == args.page_id), None
    )
    if entry is None:
        entry = {"id": args.page_id}
        pages.append(entry)
    elif not args.force:
        raise SystemExit(
            f"page {args.page_id} already exists; use --force to update it"
        )

    figma = entry.setdefault("figma", {})
    figma["file_key"] = parsed["file_key"]
    figma["node_id"] = parsed["node_id"]
    figma["url"] = args.url

    output = entry.setdefault("output", {})
    output.setdefault("ui", args.ui or f"{args.page_id}.ui")
    if args.ui:
        output["ui"] = args.ui

    result = {
        "page": entry["id"],
        "figma": figma,
        "output": output,
        "written": bool(args.write),
    }

    if args.write:
        backup = args.manifest.with_suffix(args.manifest.suffix + ".bak")
        shutil.copy2(args.manifest, backup)
        args.manifest.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        result["backup"] = str(backup)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.write:
        print("dry-run; pass --write to update the manifest", file=sys.stderr)
    return 0


def command_fetch(args: argparse.Namespace) -> int:
    _, pages = load_manifest(args.manifest)
    client = figma_mcp.FigmaMCP(args.mcp_url)
    results = []
    for page in pages:
        if args.page and page["id"] not in args.page:
            continue
        paths = build_paths(args.work_dir, page)
        results.append(
            {
                "page": page["id"],
                "result": fetch_page_reference(client, page, paths),
            }
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def command_run(args: argparse.Namespace) -> int:
    _, pages = load_manifest(args.manifest)
    selected = [
        page for page in pages if not args.page or page["id"] in args.page
    ]
    if not selected:
        print("no pages selected", file=sys.stderr)
        return 2

    args.work_dir.mkdir(parents=True, exist_ok=True)
    store = StateStore(args.work_dir / "state.db")
    client: figma_mcp.FigmaMCP | None = None
    results = []
    try:
        for page in selected:
            try:
                existing = store.get_page(page["id"])
                if (
                    existing
                    and existing.get("status") == "passed"
                    and not args.force
                    and not args.regenerate
                    and not args.refresh
                ):
                    results.append(
                        {"page": page["id"], "status": "skipped"}
                    )
                    continue
                results.append(
                    run_page(
                        page,
                        work_dir=args.work_dir,
                        args=args,
                        store=store,
                        client=client,
                    )
                )
            except Exception as error:
                store.upsert_page(
                    page["id"],
                    figma_file=page.get("figma", {}).get("file_key", ""),
                    node_id=page.get("figma", {}).get("node_id", ""),
                    status="error",
                )
                results.append(
                    {"page": page["id"], "status": "error", "error": str(error)}
                )
                print(
                    json.dumps(results[-1], ensure_ascii=False),
                    file=sys.stderr,
                )
                if not args.keep_going:
                    break
    finally:
        store.close()

    index = report.build_index(
        args.work_dir, [result["page"] for result in results]
    )
    print(json.dumps({"results": results, "index": str(index)}, ensure_ascii=False, indent=2))
    return (
        0
        if all(
            result.get("status") in {"passed", "skipped", "dry-run"}
            for result in results
        )
        else 1
    )


def _collect_text(value: Any) -> list[str]:
    parts: list[str] = []
    if isinstance(value, dict):
        if value.get("type") == "text" and isinstance(value.get("text"), str):
            parts.append(value["text"])
        for item in value.values():
            parts.extend(_collect_text(item))
    elif isinstance(value, list):
        for item in value:
            parts.extend(_collect_text(item))
    return parts


def command_discover(args: argparse.Namespace) -> int:
    client = figma_mcp.FigmaMCP(args.mcp_url)
    metadata = client.get_metadata(args.file_key, args.node_id or None)
    text = "\n".join(_collect_text(metadata)) or json.dumps(metadata, ensure_ascii=False)
    import re

    pattern = re.compile(r'id="(\d+:\d+)"[^>]*name="([^"]*)"')
    seen: list[dict[str, str]] = []
    for node_id, name in pattern.findall(text):
        seen.append({"node_id": node_id, "name": name})

    print(
        json.dumps(
            {
                "file_key": args.file_key,
                "candidates": seen,
                "metadata_size": len(text),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_report(args: argparse.Namespace) -> int:
    _, pages = load_manifest(args.manifest)
    page_ids = args.page or [page["id"] for page in pages]
    outputs = []
    for page_id in page_ids:
        outputs.append(str(report.build_page_report(args.work_dir / page_id, page_id)))
    outputs.append(str(report.build_index(args.work_dir, page_ids)))
    print(json.dumps({"reports": outputs}, ensure_ascii=False, indent=2))
    if args.open:
        import webbrowser

        webbrowser.open(Path(outputs[-1]).as_uri())
    return 0


def command_status(args: argparse.Namespace) -> int:
    store = StateStore(args.work_dir / "state.db")
    try:
        payload = {"pages": store.list_pages()}
    finally:
        store.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Project that owns .figma2qt/ (default: nearest ancestor of the cwd).",
    )
    parser.add_argument("--profile", type=Path, default=None)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--mcp-url", default=figma_mcp.DEFAULT_URL)

    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create .figma2qt/ templates")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=command_init)

    profile = subparsers.add_parser("profile", help="print the resolved profile")
    profile.set_defaults(func=command_profile)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--build-harness", action="store_true")
    preflight.add_argument("--harness-build-dir", type=Path, default=None)
    preflight.set_defaults(func=command_preflight)

    selftest = subparsers.add_parser(
        "selftest", help="render a bundled .ui with the profile font and diff it"
    )
    selftest.set_defaults(func=command_selftest)

    verify = subparsers.add_parser(
        "verify", help="render a .ui, diff it against a reference and audit it"
    )
    verify.add_argument("--ui", type=Path, required=True)
    verify.add_argument("--reference", type=Path, required=True)
    verify.add_argument("--out-dir", type=Path, required=True)
    verify.add_argument("--name", default="check")
    verify.add_argument("--qrc", type=Path, default=None)
    verify.add_argument("--project-qrc", action="append", type=Path, default=[])
    verify.add_argument("--width", type=int, default=0)
    verify.add_argument("--height", type=int, default=0)
    verify.set_defaults(func=command_verify)

    add = subparsers.add_parser("add")
    add.add_argument("--url", required=True)
    add.add_argument("--page-id", required=True)
    add.add_argument("--ui", default="")
    add.add_argument("--write", action="store_true")
    add.add_argument("--force", action="store_true")
    add.set_defaults(func=command_add)

    fetch = subparsers.add_parser("fetch")
    fetch.add_argument("--page", action="append", default=[])
    fetch.set_defaults(func=command_fetch)

    run = subparsers.add_parser("run")
    run.add_argument("--page", action="append", default=[])
    run.add_argument("--regenerate", action="store_true")
    run.add_argument("--refresh", action="store_true")
    run.add_argument("--force", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--skip-fetch", action="store_true")
    run.add_argument("--skip-agy", action="store_true")
    run.add_argument("--allow-repo-changes", action="store_true")
    run.add_argument("--strict-guard", action="store_true")
    run.add_argument("--keep-going", action="store_true")
    run.set_defaults(func=command_run)

    discover = subparsers.add_parser("discover")
    discover.add_argument("--file-key", required=True)
    discover.add_argument("--node-id", default="")
    discover.set_defaults(func=command_discover)

    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("--page", action="append", default=[])
    report_parser.add_argument("--open", action="store_true")
    report_parser.set_defaults(func=command_report)

    status = subparsers.add_parser("status")
    status.set_defaults(func=command_status)

    return parser


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def resolve_paths(args: argparse.Namespace) -> None:
    """Fill project root / profile / manifest / work dir without validating."""
    global PROJECT_ROOT
    root = (args.project_root or config.find_project_root(Path.cwd())).resolve()
    PROJECT_ROOT = root
    args.project_root = root
    args.profile = (args.profile or config.default_profile_path(root)).resolve()
    if args.manifest is None:
        args.manifest = config.default_pages_path(args.profile)
    args.manifest = Path(args.manifest).resolve()
    if args.work_dir is None:
        profile_data = None
        if args.profile.is_file():
            try:
                profile_data = config.load_profile(args.profile)
            except config.ProfileError:
                profile_data = None
        args.work_dir = config.resolve_work_dir(root, profile_data)
    args.work_dir = Path(args.work_dir).resolve()


def setup_environment(args: argparse.Namespace) -> None:
    """Resolve paths, load + validate the profile, configure the renderer."""
    global SETTINGS, PROFILE_DEFAULTS
    resolve_paths(args)
    settings = config.build_settings(args.project_root, args.profile, args.work_dir)
    SETTINGS = settings
    render.configure(settings)
    PROFILE_DEFAULTS = config.profile_defaults(settings.profile)


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            pass
        elif args.command in {"add", "discover", "report", "status"}:
            resolve_paths(args)
        else:
            setup_environment(args)
    except config.ProfileError as error:
        print(error.format(), file=sys.stderr)
        return 2

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
