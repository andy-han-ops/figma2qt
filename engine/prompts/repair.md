You are repairing a Qt Widgets .ui file to reduce the pixel difference against
a Figma reference.

Page: ${page_id}
Round: ${round}
Diff report: ${report_path}
Figma reference: ${reference_path}
Current Qt render: ${render_path}
Editable UI: ${ui_path}
Icons directory: ${icons_dir}
Resource qrc: ${qrc_path}
Resource prefix: ${resource_prefix}
Font family: ${font_family}
Main skill: ${skill_path}
Repair skill: ${repair_skill_path}

FIGMA ACCESS
- To fetch a missing asset, run:
    ${python} "${engine_dir}/figma_mcp.py" --screenshot --file-key <key> --node-id <node> --out <png>
- Do NOT use the Blender MCP or ServerName "blender".

PROJECT ASSETS
- Project asset directory: ${assets_dir}
- Existing project qrc: ${assets_qrc}
- Asset manifest / mapping: ${assets_manifest}
- Reuse project assets: ${reuse_assets}
Prefer existing project resources. Candidate asset folders (relative to the
project root):
${asset_search_dirs}

READ FIRST
- ${report_path}
- The crop images listed in worst_regions
- ${reference_path} and ${render_path}

Note: if `scaled_match` is true, the reference is a preview at a smaller
resolution. `worst_regions[].bbox` is in reference pixels; use
`worst_regions[].bbox_target` for Qt coordinates in the .ui.

SEMANTIC RULES (MANDATORY)
- The page must remain semantic and interactive.
- Never replace a real QLabel/QPushButton/QLineEdit with a screenshot.
- Never set `color: transparent` on QLabel, QPushButton, QLineEdit, or QCheckBox.
- Cards/panels use QSS background-color/border/radius; do not add a whole-card
  screenshot.
- Never add `border-image` or `background-image` to a QWidget/QFrame card that
  contains text widgets.
- Buttons/tabs/inputs need their own QSS background/border; do not rely on a
  card screenshot for their visual.
- Use images only for page background, logo, standalone icons, and illustrations.
- If text is misaligned, adjust font family/size/weight, padding, or geometry.
  Do not bake the text into an image.

GOAL
Make the smallest change that reduces the worst regions. Prefer inline QSS,
layout spacing, fixed sizes, and font/color fixes over rewriting the page.

HARD RULES
1. Only edit ${ui_path}, files under ${icons_dir}, and ${qrc_path}.
2. QSS must stay inline in the root widget styleSheet. Never create a .qss file.
3. Use url(:${resource_prefix}/<filename>) for image-only assets. If you add
   an icon, add `<file><filename></file>` to ${qrc_path}. Do not use aliases.
4. Use font-family: "${font_family}". Weight mapping (QSS font-weight -> style):
${font_weights}
5. Do not modify C++/CMake files or files outside the page work directory.
6. Do not run shell commands except the Figma helper CLI for a missing
   image-only asset. The orchestrator re-renders after your edit.
7. Keep objectName values stable.
8. If the diff is dominated by text, check the font family/style before
   changing geometry. If it is dominated by edges, check position/size/spacing.
   If it is dominated by flat color, check the QSS color/opacity.

Return ONLY a JSON object matching the configured schema:
{"changed_files": ["..."], "diagnosis": "...", "confidence": 0.0}
