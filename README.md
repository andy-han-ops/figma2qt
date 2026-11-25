# figma2qt

把 Figma 画面还原成 Qt Widgets `.ui`（inline QSS、真实文字控件），并用无头渲染 + 像素对比自动验证、修正。
以 Antigravity 全局插件的形式安装一次，所有项目都能用。

仅支持 **Windows + Qt 5.15 (MSVC x64)**。

## 它由什么组成

| 目录 | 内容 |
|---|---|
| `plugin/` | 插件壳：`plugin.json`、3 个 skill、1 个 agent、规则 |
| `engine/` | Python 引擎（orchestrator / render / diff / figma_mcp）、prompts、C++ 预览程序 `qt_preview` |
| `examples/selftest/` | 自检用的通用 `.ui` |
| `profiles/example.yaml` | profile 模板（与 `orchestrator.py init` 生成的一致） |
| `install.ps1` / `uninstall.ps1` | 安装 / 卸载 |
| `scripts/build_preview.ps1` | 编译 `qt_preview.exe` 到 `bin/` |
| `tests/` | profile 校验的单元测试 |

> 仓库里的预览程序源码保存为 `engine/qt_preview/main.cpp.txt`，CMake 构建时复制成 `main.cpp`。
> 这是为了在启用了“源码透明加密”的机器上，Git 里仍然是可读的明文。

## 前置条件

- Python 3.10+，`pip install -r engine/requirements.txt`
- Qt 5.15.x `msvc2019_64`（含 `Qt5UiTools`、`bin/rcc.exe`）
- Visual Studio 2019 x64 + CMake（只在编译 `qt_preview` 时需要）
- Antigravity CLI `agy`，并启用 `figma-dev-mode-mcp-server`
- Figma Desktop 打开目标文件（Dev Mode MCP 在本机）
- 你的项目要用的字体已安装/可访问

## 安装

```powershell
git clone <repo-url> E:\Work\figma2qt
cd E:\Work\figma2qt
scripts\build_preview.ps1 -QtPrefix D:/path/to/Qt/5.15.2/msvc2019_64   # 生成 bin\qt_preview.exe
.\install.ps1 -InstallDeps
```

`install.ps1` 会把插件复制到 `%USERPROFILE%\.gemini\config\plugins\figma2qt`，并设置用户环境变量
`FIGMA2QT_HOME`（重新打开终端 / 重启 Antigravity 后生效）。升级时 `git pull` 后重新运行即可。

卸载：`.\uninstall.ps1`

## 在项目里启用

```powershell
cd <你的项目>
python "$env:FIGMA2QT_HOME\engine\orchestrator.py" init       # 生成 .figma2qt/profile.yaml 和 pages.yaml
```

编辑 `.figma2qt/profile.yaml`。**以下三项是必填项，没有默认值，缺一项所有命令都会报错停止：**

```yaml
qt:
  prefix: D:/path/to/Qt/5.15.2/msvc2019_64   # 必须含 bin/rcc.exe；要与编译 qt_preview 用的 Qt 一致
font:
  family: "Your Font Family"                 # Qt 里看到的字体族名，会写进生成的 QSS
  dir: C:/path/to/fonts                      # 字体文件所在目录
```

常用可选项（完整说明见生成的模板）：

| 配置 | 作用 |
|---|---|
| `font.files` | 只加载目录里的指定文件/通配符；默认加载目录下全部 `.ttf/.otf/.ttc` |
| `font.required_styles` | 字体族必须具备的样式名，缺失则 `preflight` 失败 |
| `font.weights` | QSS 字重到样式名的映射，会写进 agent 提示词 |
| `font.maps` / `font.legacy_family` | 旧 `.ui` 的字体族改名 / 旧族名审计 |
| `canvas` | 目标尺寸，默认 1920x1080 |
| `thresholds` | `ssim` / `mismatch_ratio` / `delta_e_mean` 通过阈值 |
| `assets.search_dirs` | 项目里可复用图片和 `.qrc` 的目录（相对项目根） |
| `work_dir` | 产物目录，默认是项目根旁边的 `figma2qt-work` |

然后检查工具链：

```powershell
python "$env:FIGMA2QT_HOME\engine\orchestrator.py" preflight   # agy / Qt / 字体 / Figma MCP
python "$env:FIGMA2QT_HOME\engine\orchestrator.py" selftest    # 用 profile 里的字体渲染内置 .ui，应得到 passed
```

## 使用

**对话方式（推荐）**：直接对 agent 说

```text
通过 Figma 还原 UI：<Figma 链接>，页面 id login
```

agent 会加载 `figma-restore-ui` skill：读取 profile → 取 Figma 信息 → 写 `.ui` → 用 `verify` 自验证并修正。

**命令方式**：

```powershell
$o = "$env:FIGMA2QT_HOME\engine\orchestrator.py"
python $o add --url "https://www.figma.com/design/<fileKey>/<name>?node-id=1-2" --page-id login --ui login.ui --write
python $o run --page login                       # 取参考图、生成、渲染、对比、修正，最多 max_rounds 轮
python $o verify --ui <x.ui> --reference <ref.png> --out-dir <dir> [--project-qrc <a.qrc> ...]
python $o profile                                # 查看解析后的完整配置
```

全局参数（`--project-root`、`--profile`、`--manifest`、`--work-dir`）要写在子命令前面。
默认从当前目录向上查找 `.figma2qt/` 作为项目根。

## 产物

```text
<work_dir>/<page>/
├── design/reference.png, metadata.json
├── ui/<stem>.ui, ui/icons/, ui/icons/resources.qrc
├── render/round-N.png|json, diff/round-N/report.json
├── best/, history/, report.html
```

## 约定（inline QSS contract）

- 一个页面只产出一个 `.ui`，所有 QSS 内联在根 widget 的 `styleSheet`，不生成 `.qss`
- 文字必须是真实控件文字，禁止 `color: transparent`
- 含文字的容器禁止 `border-image` / `background-image`
- 图片用 `url(:/res/<文件名>)`，qrc 不使用 alias，禁止 `<pixmap>` / `<iconset>`
- 字体族只能是 profile 里的 `font.family`

## 开发

```powershell
python -m unittest discover -s tests -v
```

## Windows 源码加密提示

在启用透明加密的机器上，`.cpp` 等源码对 Python / Git Bash / Node 可能是密文，只有被授权的进程
（MSVC、PowerShell）能读到明文。本仓库的 `qt_preview` 源码用 `.txt` 扩展名保存以规避这个问题；
`.py`、`.ui`、`.md`、`.json`、`.yaml` 不受影响。

## 排错

- **profile 无效**：按报错补全 `qt.prefix` / `font.family` / `font.dir`，或重新 `init`。
- **`qt_preview.exe` 找不到**：运行 `scripts\build_preview.ps1`，或在 profile 里设 `qt.preview_exe`。
- **渲染里字体不对**：运行 `selftest`；检查 `font.family` 是否与字体文件内部的族名一致，`font.files` 是否匹配到文件。
- **图片空白**：qrc 缺少 `<file>`，或没有用 `--project-qrc` 传入项目 qrc；看 `verify` 输出的 `missing_resources`。
- **Qt DLL 找不到**：`qt.prefix` 与编译 `qt_preview` 的 Qt 不一致。
