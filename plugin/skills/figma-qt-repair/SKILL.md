---
name: figma-qt-repair
description: Repair an existing inline-QSS Qt .ui against a Figma reference using a pixel diff report and worst-region crops. Use in figma2qt repair rounds after a render/diff cycle.
---

# Figma Qt Repair

## Inputs

- `report.json` with `metrics` and `worst_regions`
- crop images for the worst regions
- the Figma reference PNG
- the current Qt render PNG
- the editable `.ui` file
- the font family and weight mapping stated in the task prompt (from the
  project's `.figma2qt/profile.yaml`)

Read the report and crops first. Compare the reference and render before editing.

## Repair order

1. **Fonts**: if the diff is text-shaped, check that `font-family` equals the
   configured family and that the numeric weight maps to a real style. Do not
   change geometry to hide a font problem.
2. **Resources**: if a region is blank or has the wrong picture, check
   `url(:/res/...)` file entries and `icons/resources.qrc`.
3. **Geometry**: if edges are shifted, fix layout spacing, margins, fixed
   sizes, or alignment.
4. **Color**: if edges align but flat areas differ, fix QSS colors, opacity,
   border radius, or background.
5. **Structure**: if the widget tree is fundamentally wrong, prefer a full
   regeneration from Figma metadata over repeated patches.

## Hard rules

- Edit only the page's `.ui`, files under its `icons/`, and
  `icons/resources.qrc`.
- QSS stays inline in the root widget `styleSheet`. Never create a `.qss` file.
- Images use `url(:/res/<filename>)`; add `<file><filename></file>` to
  `icons/resources.qrc` when adding icons. Do not use aliases.
- Use only the configured font family; never introduce another family name.
- Keep `objectName` values stable.
- Do not modify C++/CMake or files outside the page work directory.
- Do not run shell commands; the orchestrator re-renders.

## Output

Return only the configured JSON:

```json
{
  "changed_files": ["..."],
  "diagnosis": "short explanation",
  "confidence": 0.0
}
```
