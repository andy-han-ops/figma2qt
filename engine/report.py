#!/usr/bin/env python3
"""Build per-page and batch HTML reports for the Figma-to-Qt pipeline."""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import webbrowser
from pathlib import Path
from typing import Any


CSS = """
body { font-family: "Segoe UI", "Microsoft YaHei", sans-serif; margin: 24px;
       background: #0f141a; color: #e6edf3; }
h1, h2, h3 { color: #ffffff; }
a { color: #58a6ff; text-decoration: none; }
a:hover { text-decoration: underline; }
table { border-collapse: collapse; margin: 12px 0 24px; width: 100%; }
th, td { border: 1px solid #2b3545; padding: 8px 10px; text-align: left; }
th { background: #1b222c; }
.pass { color: #3fb950; font-weight: 700; }
.fail { color: #f85149; font-weight: 700; }
.muted { color: #8b949e; }
.card { background: #161b22; border: 1px solid #2b3545; border-radius: 8px;
        padding: 16px; margin: 16px 0; }
.images { display: flex; gap: 12px; flex-wrap: wrap; }
.images img { max-width: 100%; border: 1px solid #2b3545; border-radius: 6px; }
.small { font-size: 12px; }
"""


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _rel(path: Path, base: Path) -> str:
    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:
        return path.as_posix()


def _status_badge(passed: bool) -> str:
    return '<span class="pass">PASS</span>' if passed else '<span class="fail">FAIL</span>'


def build_page_report(page_dir: Path, page_id: str) -> Path:
    page_dir = page_dir.resolve()
    reports = sorted(
        page_dir.glob("diff/round-*/report.json"),
        key=lambda path: int(path.parent.name.split("-")[-1]),
    )
    latest = _read_json(reports[-1]) if reports else None

    rows: list[str] = []
    sections: list[str] = []

    for report_path in reports:
        report = _read_json(report_path)
        if not report:
            continue
        round_name = report_path.parent.name
        round_number = round_name.split("-")[-1]
        metrics = report.get("metrics", {})
        artifacts = report.get("artifacts", {})
        rows.append(
            "<tr>"
            f"<td>{html.escape(round_number)}</td>"
            f"<td>{_status_badge(bool(report.get('pass')))}</td>"
            f"<td>{metrics.get('ssim', 0):.5f}</td>"
            f"<td>{metrics.get('mismatch_ratio', 0):.4%}</td>"
            f"<td>{metrics.get('delta_e_mean', 0):.3f}</td>"
            f"<td><a href=\"{_rel(Path(artifacts.get('side_by_side', '')), page_dir)}\">side</a>"
            f" / <a href=\"{_rel(Path(artifacts.get('diff', '')), page_dir)}\">diff</a>"
            f" / <a href=\"{_rel(Path(artifacts.get('overlay', '')), page_dir)}\">overlay</a></td>"
            "</tr>"
        )

        image_links: list[str] = []
        for key in ("side_by_side", "diff", "overlay"):
            value = artifacts.get(key)
            if value:
                image_links.append(
                    f"<div><div class=\"small muted\">{html.escape(key)}</div>"
                    f"<a href=\"{_rel(Path(value), page_dir)}\">"
                    f"<img src=\"{_rel(Path(value), page_dir)}\" style=\"max-width:48vw\"></a></div>"
                )

        region_rows: list[str] = []
        for index, region in enumerate(report.get("worst_regions", [])):
            crop = region.get("crop", "")
            crop_link = (
                f"<a href=\"{_rel(Path(crop), page_dir)}\">crop</a>"
                if crop
                else ""
            )
            region_rows.append(
                "<tr>"
                f"<td>{index}</td>"
                f"<td>{html.escape(str(region.get('bbox')))}</td>"
                f"<td>{region.get('ssim', 0):.4f}</td>"
                f"<td>{region.get('delta_e', 0):.3f}</td>"
                f"<td>{html.escape(str(region.get('hint', '')))}</td>"
                f"<td>{crop_link}</td>"
                "</tr>"
            )

        sections.append(
            f"""
            <div class="card">
              <h2>Round {html.escape(round_number)} {_status_badge(bool(report.get('pass')))}</h2>
              <div class="images">{''.join(image_links)}</div>
              <h3>Worst regions</h3>
              <table>
                <tr><th>#</th><th>bbox</th><th>SSIM</th><th>DeltaE</th><th>hint</th><th>crop</th></tr>
                {''.join(region_rows)}
              </table>
            </div>
            """
        )

    font_audit: dict[str, Any] = {}
    dump_files = sorted(page_dir.glob("render/round-*.json"))
    if dump_files:
        dump = _read_json(dump_files[-1]) or {}
        font_audit = dump.get("fontAudit", {})

    latest_metrics = latest.get("metrics", {}) if latest else {}
    latest_semantic = latest.get("semantic", {}) if latest else {}
    latest_status = bool(latest.get("pass")) if latest else False

    content = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>figma2qt - {html.escape(page_id)}</title>
  <style>{CSS}</style>
</head>
<body>
  <h1>{html.escape(page_id)} {_status_badge(latest_status)}</h1>
  <p class="muted">Latest: SSIM {latest_metrics.get('ssim', 0):.5f}
     | mismatch {latest_metrics.get('mismatch_ratio', 0):.4%}
     | DeltaE {latest_metrics.get('delta_e_mean', 0):.3f}</p>
  <div class="card">
    <h2>Rounds</h2>
    <table>
      <tr><th>Round</th><th>Status</th><th>SSIM</th><th>Mismatch</th><th>DeltaE</th><th>Artifacts</th></tr>
      {''.join(rows)}
    </table>
  </div>
  <div class="card">
    <h2>Font audit</h2>
    <pre class="small">{html.escape(json.dumps(font_audit, ensure_ascii=False, indent=2))}</pre>
  </div>
  <div class="card">
    <h2>Semantic audit</h2>
    <pre class="small">{html.escape(json.dumps(latest_semantic, ensure_ascii=False, indent=2))}</pre>
  </div>
  {''.join(sections)}
</body>
</html>
"""
    out_path = page_dir / "report.html"
    out_path.write_text(content, encoding="utf-8")
    return out_path


def build_index(work_dir: Path, page_ids: list[str] | None = None) -> Path:
    work_dir = work_dir.resolve()
    reports_dir = work_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    if not page_ids:
        page_ids = sorted(
            path.name for path in work_dir.iterdir()
            if path.is_dir() and (path / "diff").exists()
        )

    rows: list[str] = []
    for page_id in page_ids:
        page_dir = work_dir / page_id
        report_paths = sorted(
            page_dir.glob("diff/round-*/report.json"),
            key=lambda path: int(path.parent.name.split("-")[-1]),
        )
        latest = _read_json(report_paths[-1]) if report_paths else None
        metrics = latest.get("metrics", {}) if latest else {}
        status = bool(latest.get("pass")) if latest else False
        page_report = page_dir / "report.html"
        if not page_report.exists() and latest:
            page_report = build_page_report(page_dir, page_id)
        rows.append(
            "<tr>"
            f"<td><a href=\"{_rel(page_report, reports_dir)}\">{html.escape(page_id)}</a></td>"
            f"<td>{_status_badge(status) if latest else '<span class=\"muted\">pending</span>'}</td>"
            f"<td>{metrics.get('ssim', 0):.5f}</td>"
            f"<td>{metrics.get('mismatch_ratio', 0):.4%}</td>"
            f"<td>{metrics.get('delta_e_mean', 0):.3f}</td>"
            "</tr>"
        )

    content = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>figma2qt report index</title>
  <style>{CSS}</style>
</head>
<body>
  <h1>figma2qt report index</h1>
  <table>
    <tr><th>Page</th><th>Status</th><th>SSIM</th><th>Mismatch</th><th>DeltaE</th></tr>
    {''.join(rows)}
  </table>
</body>
</html>
"""
    out_path = reports_dir / "index.html"
    out_path.write_text(content, encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--page", action="append", default=[])
    parser.add_argument("--index", action="store_true")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)

    outputs: list[Path] = []
    if args.page:
        for page_id in args.page:
            outputs.append(build_page_report(args.work_dir / page_id, page_id))
    if args.index or not args.page:
        outputs.append(build_index(args.work_dir, args.page or None))

    for output in outputs:
        print(output)
    if args.open and outputs:
        webbrowser.open(outputs[-1].as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
