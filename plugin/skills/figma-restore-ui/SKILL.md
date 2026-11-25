---
name: figma-restore-ui
description: 通过 Figma 链接还原 Qt Widgets 页面。用户说“通过 Figma 还原 UI”“参考这个 Figma 链接还原 xxx.ui”“还原这个 Figma 页面”时使用。读取项目的 .figma2qt/profile.yaml，解析 fileKey/nodeId，生成语义化 inline QSS 的 .ui，复用项目图片资源，并用 figma2qt verify 做静态样式自验证和多轮修正。
---

# Figma 还原 Qt UI（端到端流程）

## 触发方式

用户只需要说类似：

- `通过 Figma 还原 UI：<Figma链接>，页面 id <page_id>`
- `参考 <Figma链接> 还原 <page_id>.ui`
- `还原这个 Figma 页面：<Figma链接>`

不要要求用户重复粘贴长提示词。缺少 URL 时问 URL；缺少页面 id 时从 frame 名称推导并确认。

## 第 0 步：定位引擎并读取项目 profile

```powershell
$ENGINE = if ($env:FIGMA2QT_HOME) { "$env:FIGMA2QT_HOME\engine" } else { "$env:USERPROFILE\.gemini\config\plugins\figma2qt\engine" }
python "$ENGINE\orchestrator.py" profile
```

命令在目标项目目录内运行（或加 `--project-root <项目根>`）。输出是 JSON，后面所有路径都从它读取，不要自己猜：

- `work_dir`：工作根目录，页面工作目录为 `<work_dir>/<page_id>`
- `qt_preview`、`rcc`：渲染与资源编译工具（由验证命令内部调用，不需要手动调用）
- `font.family`、`font.weights`：字体与字重映射
- `canvas`、`thresholds`、`semantic`：目标尺寸、通过阈值、语义约束
- `assets.search_dirs`：项目里可复用图片/qrc 所在目录

如果命令报 profile 无效或不存在：

1. 把错误原样告诉用户。
2. 不存在时运行 `python "$ENGINE\orchestrator.py" init` 生成模板。
3. **`qt.prefix`、`font.family`、`font.dir` 是必填项，绝不能自己猜测或填默认值**，请用户填写后再继续。

## 输入与路径

从 Figma URL 解析：

- `fileKey` = `/design/<fileKey>/`
- `nodeId` = `node-id` 参数，`-` 转 `:`
- branch URL 使用 `branchKey` 作为 `fileKey`

```text
WORK   = <work_dir>/<page_id>
UI     = WORK/ui/<page_id>.ui
ICONS  = WORK/ui/icons
QRC    = WORK/ui/icons/resources.qrc
REF    = WORK/design/reference.png
```

先加载 `figma-to-qt-ui` skill 读取详细规范。

## 硬性规范

1. 只生成一个 `.ui`；所有 QSS 内联在根 widget 的 `styleSheet`；不要 `.qss`。
2. 文字必须是真实控件文字：`QLabel.text`、`QPushButton.text`、`QLineEdit.placeholderText`。
3. 禁止 `color: transparent`。
4. 禁止整卡/整按钮截图作为唯一视觉。
5. 禁止在包含文字控件的 `QWidget/QFrame` 上使用 `border-image` 或 `background-image`。
6. 卡片/面板用 QSS：`background-color`、`border`、`border-radius`、`qlineargradient`。
7. 按钮/标签页/输入框各自用 QSS 背景/边框/圆角和 `:hover`/`:pressed`/`:checked`。
8. 图片只用于：页面背景、Logo、独立图标、纯装饰图。
9. 字体统一使用 profile 的 `font.family`，字重按 `font.weights` 映射。
10. 保持 `objectName` 稳定、可读。
11. 不要修改 C++/CMake，不要写回项目源码目录。

## 项目资源复用

1. 如果用户指定了目标目录、qrc 或资源目录，优先使用它。
2. 否则在 profile 的 `assets.search_dirs` 中按文件名/语义匹配；该列表为空时在项目内搜索图片目录和 `.qrc`。
3. 命中某个项目 qrc 的，直接引用 `url(:/res/<文件名>)`（以该 qrc 的 prefix 为准）。
4. 多个 qrc 有同名文件时，优先目标模块；不确定时问用户。
5. 验证时用 `--project-qrc` 传入实际用到的 qrc（可多个）。
6. 没命中的，才从 Figma 截图到 `ICONS`，并在页面 `QRC` 里补 `<file>`。
7. 页面 qrc 不使用 alias；qrc 和 PNG 放在同一目录（`ui/icons/`）。

qrc 示例：

```xml
<RCC>
  <qresource prefix="/res">
    <file>btn_normal.png</file>
    <file>btn_hover.png</file>
  </qresource>
</RCC>
```

## 获取 Figma 信息

```powershell
python "$ENGINE\figma_mcp.py" --metadata --file-key <fileKey> --node-id <nodeId>
python "$ENGINE\figma_mcp.py" --design-context --file-key <fileKey> --node-id <nodeId> --asset-dir <ICONS>
python "$ENGINE\figma_mcp.py" --screenshot --file-key <fileKey> --node-id <node> --out <png>
```

如果使用 `call_mcp_tool`：

- `ServerName = "figma-dev-mode-mcp-server"`
- `ToolName = get_metadata / get_design_context / get_screenshot`

不要使用 Blender MCP。参考图不存在时保存到 `REF`（对 frame 根节点截图）。

## 生成

1. 用写文件工具直接写 `UI` 的 XML。
2. 不要用 Python 三引号字符串拼 XML。
3. 先写完整语义化 `.ui`，再迭代修正。
4. 不要生成 `test_proto.ui` 之类临时文件。
5. 布局优先 `QHBoxLayout`/`QVBoxLayout`/`QGridLayout`；只有根窗口用绝对尺寸。

## 静态样式自验证

只验证静态样式：间距、字体、字号、字重、颜色、圆角、边框、背景、图标、布局。不需要验证点击、信号槽、动态行为。

每轮修改后执行一条命令（它会编译 qrc、渲染、做像素对比和语义审计）：

```powershell
python "$ENGINE\orchestrator.py" verify --ui <UI> --reference <REF> --out-dir <WORK> `
  [--project-qrc <PROJECT_QRC_1> --project-qrc <PROJECT_QRC_2>]
```

输出 JSON：`pass`、`metrics`（ssim / mismatch_ratio / delta_e_mean）、`problems`、`semantic`、`missing_resources`、`worst_regions`、`render`。阈值取自 profile，不要自己硬编码。`pass` 为 false 时按 `problems` 和 `worst_regions` 修：

- 边缘错位 -> 间距/margin/固定尺寸/对齐
- 文字差异 -> 字体 family/size/weight/颜色
- 平坦颜色差异 -> QSS 颜色/透明度
- 圆角/边框差异 -> QSS `border-radius`/`border`
- 资源错误 -> 检查 qrc `<file>` 和 `url(:/res/...)`

最多 5 轮。通过后再做一次独立复核：重新读 `.ui`，重新运行 `verify`，确认指标没有回退。

## 最终输出

```json
{
  "changed_files": ["..."],
  "diagnosis": "...",
  "confidence": 0.0,
  "verification": {
    "ssim": 0.0,
    "mismatch_ratio": 0.0,
    "delta_e_mean": 0.0,
    "text_widgets": 0,
    "missing_resources": []
  }
}
```
