<p align="center">
  <img src="web/assets/img/logo-256.png" width="96" alt="ECPreader">
</p>

<h1 align="center">ECPreader</h1>

<p align="center">本地运行的论文双语对照阅读器</p>

把 PDF / Word 论文拖进窗口：左侧保留论文原貌，右侧在同一位置显示中文译文；也可以切换逐句对照，选中句子两边同步高亮，双击单词查看词典释义和语境意译。

文档、译文、历史和 API Key 都只保存在本机。只有待翻译的句子会发送到你选择的翻译服务。

## 下载

最新版：**[GitHub Releases](https://github.com/Treasure-moon/ECPreader/releases/latest)**

| 文件 | 说明 |
| --- | --- |
| [ECPreader-Setup-1.5.0.exe](https://github.com/Treasure-moon/ECPreader/releases/latest/download/ECPreader-Setup-1.5.0.exe) | 安装版，推荐。可选安装路径、数据路径和快捷方式，可在「应用和功能」中卸载 |
| [ECPReader.exe](https://github.com/Treasure-moon/ECPreader/releases/latest/download/ECPReader.exe) | 免安装单文件版，双击运行 |

Windows 10 / 11，不需要安装 Python。

程序没有代码签名，首次运行时如果 Windows 提示「已保护你的电脑」，点「更多信息 → 仍要运行」即可。

各版本的更新说明见 **[Releases](https://github.com/Treasure-moon/ECPreader/releases)**。

## 功能

- **两层顶栏**：上层全局栏放标签页与引擎、设置入口，下层阅读栏放翻译、视图、缩放与查找，互不挤占。
- **多标签页**：同时打开、翻译、阅读多篇论文，每篇论文的状态互不影响。
- **全屏阅读**：隐藏所有界面元素专注阅读，`Esc` 退出，左侧可随时唤出目录。
- **PDF / Word**：支持文本型 PDF 和 `.docx`，保留标题、段落、图表说明、公式和参考文献结构。
- **原版对照**：整页按原样渲染，中文按原坐标覆盖，公式、图片、双栏排版不会被拆乱。
- **逐句对照**：正文重排成左右两栏，共享滚动位置，适合精读。
- **划选联动**：在任一栏选中句子，另一栏同步高亮；双击单词就地查词。
- **文档内查找**：`Ctrl+F` 精确查找原文或译文，只高亮匹配字符，可上下跳转。
- **两种引擎**：免费机翻（MyMemory / Google / Bing），或 OpenAI 兼容的 AI 接口（DeepSeek、OpenAI、硅基流动、Ollama 等）。
- **术语一致**：可维护术语表，AI 模式会结合上下文统一译法。
- **可控成本**：按段分批翻译，失败可补译，单段可重译。
- **导出**：Markdown 对照、双语 HTML、JSON、纯英文 TXT。
- **本地历史**：SQLite 记录每次翻译，可搜索、导出、清空。
- **数据加密**：API Key 使用 Windows DPAPI + AES-256-GCM 加密保存。

## 使用

1. 安装并启动 ECPreader。
2. 把 PDF 或 `.docx` 拖进窗口，或者点「选择文件」。
3. 选择翻译引擎，点「翻译全文」。
4. 左侧看原文，右侧看译文；选中句子会同步定位，双击单词可查词。
5. 想同时读几篇，点顶栏 `＋` 或直接再拖入文件；点标签切换，`Alt+W` 关掉当前标签。
6. 点阅读栏 `⛶` 进入全屏阅读，`Esc` 退出；鼠标移到屏幕左边缘可唤出目录。

免费机翻开箱即用。使用 AI 翻译前，在「翻译设置」里填写接口地址、模型和 API Key。

数据目录可以在安装时选择，也可以随时在「翻译设置 → 数据与安全」中更改并迁移。

## 从源码运行

需要 Python 3.10 或更高版本（开发使用 3.12）：

```text
start.bat
```

首次运行会自动创建虚拟环境并安装依赖，之后直接启动桌面窗口。

## 打包

```text
build_exe.bat
```

生成 `dist\ECPReader.exe`（免安装单文件版）。

```text
build_installer.bat
```

生成 `installer\dist\ECPreader-Setup-1.5.0.exe`（安装包，内含卸载程序）。

## 数据与隐私

- 论文只在本机解析；只有被翻译的句子会发送到所选翻译服务。
- 默认数据目录：安装版为安装时选择的目录，源码版为项目下的 `data\`。
- 数据目录包含 `app.db`（文档、译文、历史）、`vault.*`（加密凭据）、`uploads/`、`pages/`。
- `vault.key` 由 Windows DPAPI 绑定当前账户，换账户或换电脑后需要重新填写 API Key。
- 项目不包含埋点、遥测或云端同步。
- 仓库和安装包不包含任何 API Key、文档或历史记录，首次启动是空白状态。

## 反馈

问题反馈请联系：**treasure_moon@163.com**

## License

[MIT](LICENSE)