# figma2qt rules

When the user asks to restore a Qt UI from Figma ("通过 Figma 还原 UI", a Figma
link plus a target `.ui`, "还原这个 Figma 页面"):

- Load the `figma-restore-ui` skill and follow it end to end. Do not ask the user
  to paste long prompts.
- Read the project's `.figma2qt/profile.yaml` (via `orchestrator.py profile`).
  `qt.prefix`, `font.family` and `font.dir` are required and must never be
  guessed or defaulted; if they are missing, ask the user to fill them.
- Parse `fileKey` and `nodeId` from the Figma URL; derive the page id from the
  frame name and confirm it when the user did not give one.

Qt `.ui` conventions for this pipeline (apply to `*.ui` files and to anything
under a `figma2qt-work` directory):

- A page produces one `.ui` file, `icons/`, and `resources.qrc`; never a
  standalone `.qss`.
- QSS is inline in the root widget `<property name="styleSheet">`.
- Visible text is real widget text (`QLabel.text`, `QPushButton.text`,
  `QLineEdit.placeholderText`); never `color: transparent`.
- Containers that hold text widgets never use `border-image` / `background-image`.
- Images use `url(:/res/<filename>)` with a matching `<file>` entry in the qrc;
  no aliases; no `<pixmap>`, `<iconset>`, `<normaloff>`, `<normalon>`.
- Font family and weights come from the profile, never from memory.
- Automated repair rounds may only edit the page's `.ui`, `icons/` and
  `resources.qrc`; never C++/CMake or other pages.
- Keep `objectName` values stable so QSS selectors and C++ lookups keep working.
