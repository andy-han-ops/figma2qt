---
name: figma-to-qt-ui
description: Generate a Qt Widgets .ui file from a Figma frame using inline QSS and Qt resource images, with the font family configured in the project's .figma2qt/profile.yaml. Use for Figma-to-Qt restoration, Qt Designer .ui generation, or QSS-in-.ui work.
---

# Figma to Qt Widgets (inline QSS)

The font family, weight mapping, canvas size and asset folders are **project
settings**, not constants of this skill. Read them with:

```powershell
$ENGINE = if ($env:FIGMA2QT_HOME) { "$env:FIGMA2QT_HOME\engine" } else { "$env:USERPROFILE\.gemini\config\plugins\figma2qt\engine" }
python "$ENGINE\orchestrator.py" profile
```

(When the task prompt already states the font family and weight mapping, use
those.) Never invent `qt.prefix`, `font.family` or `font.dir`.

## Hard output contract

Every page produces exactly:

```text
<page>/ui/
├── <stem>.ui          # the only UI file; QSS is inline
└── icons/
    ├── *.png          # PNG assets
    └── resources.qrc  # plain <file> entries; no aliases
```

Rules:

- **No standalone `.qss` file.** All QSS goes into the root widget's
  `<property name="styleSheet"><string notr="true">...</string></property>`.
- No `<pixmap>`, `<iconset>`, `<normaloff>`, `<normalon>`.
- Every image is referenced as `url(:/res/<filename>)` and has a matching
  `<file><filename></file>` entry in `icons/resources.qrc`. Do not use aliases.
- Font family is exactly the profile's `font.family`. Never emit another family
  name.
- Text must be real Qt text. Do not bake text into a PNG and make the widget
  text transparent.
- Cards/panels use `QWidget`/`QFrame` + QSS background/border/radius.
- Buttons/tabs use `QPushButton` with real text and QSS states.
- Inputs use `QLineEdit` with real placeholder text.
- Use images only for backgrounds, logos, standalone icons, and illustrations.
- Do not modify C++/CMake or any file outside the page work directory.

## Semantic UI contract (default, overrides screenshot-first guidance)

- Every visible text string from Figma must appear as a Qt text property:
  `QLabel.text`, `QAbstractButton.text`, or `QLineEdit.placeholderText`.
- Never use `color: transparent` on `QLabel`, `QPushButton`, `QLineEdit`, or
  `QCheckBox`.
- Never use a whole-card or whole-button screenshot as the only visual.
- Use QSS for card backgrounds, borders, radius, button backgrounds, and
  `:hover` / `:pressed` / `:checked` states.
- Images are allowed only for page background photo/illustration, logo,
  standalone icon, and decorative images with no runtime text.
- A button/tab may use a small background image if the design requires it, but
  its text must remain a real Qt text property.
- A card/panel must not use `border-image` when it contains text widgets.
- If pixel fidelity conflicts with this semantic contract, the semantic
  contract wins.

## Project asset reuse

When the task provides project asset folders, a qrc, or a manifest mapping:

- Prefer existing project PNGs over Figma screenshots.
- Match assets by filename, layer name, or the mapping file.
- If an asset already exists in a project qrc, reference its existing
  `url(:/<prefix>/<filename>)` directly and do not copy it.
- Otherwise copy the PNG into `icons/` and add `<file><filename></file>` to
  `icons/resources.qrc`.
- Only call `get_screenshot` for nodes that are missing from the project
  assets.

## Font weights

QSS numeric `font-weight` must resolve to a real style of the configured
family. Use the profile's `font.weights` mapping (QSS weight -> style name).
If Figma requests a weight the family does not provide, report the missing
weight instead of silently accepting the fallback.

## Workflow

1. Extract `fileKey` and `nodeId` from the Figma URL.
2. Call `get_metadata` to inspect the frame tree.
3. Call `get_design_context` with `excludeScreenshot: true` to build the widget
   inventory: node id, position, size, text, states.
4. Call `get_screenshot` only for image-only nodes: page background, logo,
   standalone icons, and illustrations. Do not screenshot text-bearing
   buttons, tabs, cards, or inputs.
5. Build `<stem>.ui` with layouts. Use absolute geometry only for the root
   window size; use `QVBoxLayout`/`QHBoxLayout`/`QGridLayout` inside.
6. Write the inline styleSheet on the root widget.
7. Write `icons/resources.qrc`.
8. Run `python "$ENGINE\orchestrator.py" verify ...` (see the
   `figma-restore-ui` skill).

## Widget mapping

| Figma element | Qt widget | .ui properties |
|---|---|---|
| Button frame | `QPushButton` | real text, QSS background/border/radius, `:hover`/`:pressed` |
| Tab frame | `QPushButton` | real text, `checkable=true`, QSS `:checked` |
| Card / panel | `QWidget`/`QFrame` | QSS background/border/radius; no card screenshot |
| Logo | `QLabel` | image only, `scaledContents=true`, `Fixed` size |
| Standalone icon | `QLabel` | image only, `scaledContents=true`, `Fixed` size |
| Text | `QLabel` | real text, alignment, QSS font/color |
| Input | `QLineEdit` | real placeholder, QSS border/radius/padding |
| Status row | `QWidget`/`QFrame` | QSS background/border + `QLabel` text + small icon |
| List | `QListWidget` | `NoFrame`, item styling in inline QSS |
| Spacer | `<spacer>` | orientation + sizeHint |

Never screenshot a text-bearing button, tab, card, or input. Use real Qt
widgets and QSS.

## Inline styleSheet template

Replace `<font.family>` with the profile's family.

```xml
<property name="styleSheet">
 <string notr="true">#LoginPage *
{
    font-family: "<font.family>";
}
#LoginPage {
    background-color: #0E1726;
}
#cardWidget {
    background-color: rgba(16, 23, 38, 0.95);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 16px;
}
#btnLogin {
    background-color: #1664FF;
    color: #FFFFFF;
    font-size: 16px;
    font-weight: 700;
    border-radius: 8px;
    border: none;
}
#btnLogin:hover {
    background-color: #337CFF;
}
#btnLogin:pressed {
    background-color: #0F50D2;
}
#editUsername {
    background-color: #1B222C;
    border: 1px solid #2B3545;
    border-radius: 8px;
    padding-left: 40px;
    color: #FFFFFF;
}
</string>
</property>
```

Prefer root-level `#objectName` selectors. Use per-widget styleSheet only for
local overrides or widgets created dynamically in C++.

## resources.qrc template

Place this file at `ui/icons/resources.qrc`, next to the PNGs:

```xml
<RCC>
  <qresource prefix="/res">
    <file>bg.png</file>
    <file>logo.png</file>
  </qresource>
</RCC>
```

Do not use `alias`. The resource path is `:/res/<filename>`.

## Verification checklist

`orchestrator.py verify` checks all of these and lists failures in `problems`:

1. XML parses and the root widget has a `styleSheet`.
2. Every `url(:/...)` resolves to a compiled resource (`missing_resources` is empty).
3. `embeddedPixmaps == 0`; no separate `.qss` file.
4. Every font-family reference equals the profile's `font.family`.
5. No `color: transparent`; no text-bearing container with `border-image`.
6. Enough real text widgets (`semantic.min_text_widgets`).
7. SSIM / mismatch / DeltaE within the profile thresholds.

## Common pitfalls

1. Relative `url(icons/...)` breaks outside the build directory. Use
   `url(:/res/...)` only.
2. `border-image` is for image-only widgets/backgrounds; do not use it for a
   card/button that contains real text.
3. Tabs must use real text plus `:checked` QSS; do not screenshot the tab.
4. Overlapping icons must be declared after the widget they cover.
5. `contentsOnly: true` is mandatory for transparent assets.
6. Qt 5 QSS needs numeric `font-weight` values that match the family's styles.
7. A missing `<file>` entry renders as a blank image, not an error. Always
   verify the file list.
