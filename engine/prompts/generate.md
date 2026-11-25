/figma-to-qt-ui

You are generating an INTERACTIVE, SEMANTIC Qt Widgets page from a Figma design.

Page: ${page_id}
Figma fileKey: ${file_key}
Figma nodeId: ${node_id}
Figma URL: ${figma_url}
Reference screenshot: ${reference_path}
Output UI: ${ui_path}
Icons directory: ${icons_dir}
Resource qrc: ${qrc_path}
Resource prefix: ${resource_prefix}
Target size: ${width}x${height}
Font family: ${font_family}
Main skill: ${skill_path}
Repair skill: ${repair_skill_path}

SEMANTIC UI CONTRACT (MANDATORY)
- Every visible text string from Figma must be a real Qt text property:
  QLabel.text, QAbstractButton.text, or QLineEdit.placeholderText.
- Never bake text into a PNG and make the widget text transparent.
- Never use `color: transparent` on QLabel, QPushButton, QLineEdit, or QCheckBox.
- Cards/panels use QWidget/QFrame with QSS background-color, border, and
  border-radius. Do not use a whole-card screenshot as the only visual.
- Never use `border-image` or `background-image` on a QWidget/QFrame that
  contains text widgets. This includes the login card/panel.
- Buttons/tabs/inputs must have their own QSS background/border/radius; do not
  rely on a card screenshot for their visual.
- For card gradients, use QSS `qlineargradient` instead of a screenshot.
- Buttons/tabs use QPushButton with real text and QSS `:hover`, `:pressed`,
  and `:checked` states.
- Inputs use QLineEdit with real placeholder text and QSS border/radius/padding.
- Images are allowed only for:
  - page background photo/illustration
  - logo
  - standalone icons
  - decorative images with no runtime text
- A button/tab may use a small background image if the design requires it, but
  its text must remain a real Qt text property.
- If pixel fidelity conflicts with this semantic contract, the semantic
  contract wins.

HARD OUTPUT CONTRACT
1. Produce exactly one .ui file at ${ui_path}. Do NOT create a .qss file.
2. Put all QSS inside the root widget:
   <property name="styleSheet"><string notr="true">...</string></property>
3. Use real Qt widgets and real text as required by the semantic contract.
4. For image-only assets that are NOT already available in the project qrc,
   save them as PNGs under ${icons_dir}.
5. Reference every image as url(:${resource_prefix}/<filename>). If the file
   already exists in the project qrc, use it directly; otherwise add a matching
   `<file><filename></file>` entry to ${qrc_path}. Do not use aliases.
6. Do not use <pixmap>, <iconset>, <normaloff>, or <normalon>.
7. Use font-family: "${font_family}". Weight mapping (QSS font-weight -> style):
${font_weights}
8. Keep objectName values stable and descriptive.
9. Do not modify C++/CMake files or any file outside the page work directory.

FIGMA ACCESS (IMPORTANT)
- Preferred: run the helper CLI with run_command:
    ${python} "${engine_dir}/figma_mcp.py" --metadata --file-key ${file_key} --node-id ${node_id}
    ${python} "${engine_dir}/figma_mcp.py" --design-context --file-key ${file_key} --node-id ${node_id} --asset-dir ${icons_dir}
    ${python} "${engine_dir}/figma_mcp.py" --screenshot --file-key ${file_key} --node-id <node> --out <png>
- If you use call_mcp_tool, ServerName must be "figma-dev-mode-mcp-server";
  ToolName is get_metadata / get_design_context / get_screenshot.
- Do NOT use the Blender MCP or ServerName "blender".
- You may run shell commands only for the helper CLI and for saving PNGs.

PROJECT ASSETS
- Project asset directory: ${assets_dir}
- Existing project qrc: ${assets_qrc}
- Optional asset manifest / mapping: ${assets_manifest}
- Reuse project assets: ${reuse_assets}

Prefer existing project resources. Candidate asset folders (relative to the
project root; when none are listed, search the project for image folders and
.qrc files):
${asset_search_dirs}
Match assets by filename, layer name, or semantic role.

If assets come from multiple project qrcs, compile each qrc used and pass
multiple `--rcc` files to the renderer.

- If the asset already exists in the existing project qrc (${assets_qrc}),
  reference it directly as url(:/res/<filename>) and do not copy it.
- Otherwise copy the PNG into ${icons_dir} and add
  `<file><filename></file>` to ${qrc_path}.
- If an optional manifest is provided, you may use it to resolve ambiguous
  names. If no manifest is provided, match by filename/layer name and do not
  block on a missing manifest.
- Do not use a project asset that contains baked-in text for a text-bearing
  widget. Build those widgets semantically instead.

IMPORTANT WRITING RULE
- Use `write_to_file` to write ${ui_path} directly as XML.
- Do NOT embed the .ui XML in Python triple-quoted strings and do NOT run
  ad-hoc Python scripts to create the .ui.
- Write a first complete semantic .ui early, then refine it. Do not spend time
  on throwaway prototypes such as test_proto.ui.

WORKFLOW
- Use the Figma helpers above to inspect the node. Read the main skill and
  repair skill first.
- Build the .ui with layouts, not absolute geometry (only the root window size
  may use geometry/minimumSize/maximumSize).
- Create real QLabel/QPushButton/QLineEdit widgets for all text, tabs, buttons,
  and inputs.
- Build the card/panel with QSS background/border/radius; do not screenshot the
  card.
- Use QSS for card backgrounds, borders, radius, colors, font sizes/weights,
  and hover/pressed/checked states.
- Use get_screenshot only for image-only nodes: background, logo, standalone
  icons, and illustrations. Do not screenshot text-bearing buttons, tabs,
  cards, or inputs.
- Generate icons/ and `<file>` entries in icons/resources.qrc only for
  image-only assets that are not already present in the project qrc.
- Verify:
  - the XML parses
  - every image url(...) has a matching `<file>` entry
  - every referenced PNG exists
  - no QSS rule sets color: transparent
  - every visible Figma text string exists as a Qt text property
  - cards/panels use QSS, not whole-card screenshots

Return ONLY a JSON object matching the configured schema:
{"changed_files": ["..."], "diagnosis": "...", "confidence": 0.0}
