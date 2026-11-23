"""ECPreader 安装向导：安装路径 / 数据路径 / 快捷方式均可自选。

打包成单文件后，内部携带 ECPReader.exe 与卸载程序；卸载入口写入 Windows「应用和功能」。
"""
from __future__ import annotations

import base64
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import winreg
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "ECPreader"
APP_VERSION = "1.5.0"
NAVY = "#1B2450"
CREAM = "#FAF6EA"
MINT = "#2ED9C3"
INK = "#1B2450"
MUTED = "#63718F"
LINE = "#E2DACA"

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
PAYLOAD = ROOT / "payload"
ASSETS = ROOT / "assets"
if not ASSETS.is_dir():  # 源码方式预览界面时回退到项目资源
    ASSETS = Path(__file__).resolve().parent.parent / "web" / "assets" / "img"
EXE_NAME = "ECPReader.exe"
UNINSTALL_NAME = "ECPreaderUninstall.exe"
UI_FONT = ("Microsoft YaHei UI", 10)


def appdata_config() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
    return Path(base) / APP_NAME / "settings.json"


def read_config() -> dict:
    try:
        data = json.loads(appdata_config().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def desktop_dir() -> Path:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "Desktop")
        return Path(os.path.expandvars(value))
    except OSError:
        return Path.home() / "Desktop"


def start_menu_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def run_hidden(command: list[str]) -> None:
    subprocess.run(
        command,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def create_shortcut(link: Path, target: Path, workdir: Path, icon: Path, description: str) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)

    def quote(value: object) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    script = (
        "$ws = New-Object -ComObject WScript.Shell\n"
        f"$s = $ws.CreateShortcut({quote(link)})\n"
        f"$s.TargetPath = {quote(target)}\n"
        f"$s.WorkingDirectory = {quote(workdir)}\n"
        f"$s.IconLocation = {quote(str(icon) + ',0')}\n"
        f"$s.Description = {quote(description)}\n"
        "$s.Save()\n"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    run_hidden(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded])


def perform_install(
    install: Path,
    data: Path,
    make_desktop: bool,
    make_startmenu: bool,
    log=print,
    progress=lambda value: None,
) -> None:
    """复制程序、写配置、建快捷方式并注册卸载信息（GUI 与静默安装共用）。"""
    log(f"安装位置：{install}")
    log(f"数据位置：{data}")
    progress(8)

    source_exe = PAYLOAD / EXE_NAME
    source_uninstall = PAYLOAD / UNINSTALL_NAME
    if not source_exe.is_file():
        raise RuntimeError("安装包内缺少 ECPReader.exe，请重新生成安装包。")
    install.mkdir(parents=True, exist_ok=True)
    target_exe = install / EXE_NAME
    log("正在复制主程序…")
    shutil.copy2(source_exe, target_exe)
    progress(62)

    target_uninstall = install / UNINSTALL_NAME
    if source_uninstall.is_file():
        shutil.copy2(source_uninstall, target_uninstall)
    log("正在写入配置…")
    config = read_config()
    config.update(
        {
            "data_dir": str(data),
            "install_dir": str(install),
            "installed_version": APP_VERSION,
        }
    )
    cfg_path = appdata_config()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    progress(70)

    if make_desktop:
        log("正在创建桌面快捷方式…")
        create_shortcut(
            desktop_dir() / f"{APP_NAME}.lnk",
            target_exe,
            install,
            target_exe,
            f"{APP_NAME} · 论文双语对照阅读器",
        )
    if make_startmenu:
        log("正在创建开始菜单快捷方式…")
        create_shortcut(
            start_menu_dir() / f"{APP_NAME}.lnk",
            target_exe,
            install,
            target_exe,
            f"{APP_NAME} · 论文双语对照阅读器",
        )
    progress(84)

    log("正在注册卸载信息…")
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ECPreader"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "treasure_moon")
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(install))
        winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(target_exe))
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{target_uninstall}"')
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        size_kb = 0
        for item in install.rglob("*"):
            if item.is_file():
                size_kb += item.stat().st_size // 1024
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
    progress(100)
    log("安装完成。")


def _arg_value(name: str, default: str | None = None) -> str | None:
    for index, arg in enumerate(sys.argv):
        if arg == name and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return default


def silent_install() -> int:
    """--silent 供自动安装/测试使用，不弹任何窗口。"""
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    profile = Path(os.environ.get("USERPROFILE", str(Path.home())))
    install = Path(_arg_value("--install-dir") or local / "Programs" / APP_NAME)
    data = Path(_arg_value("--data-dir") or profile / "Documents" / APP_NAME)
    log_path = Path(os.environ.get("TEMP", str(Path.home()))) / "ECPreader-setup.log"

    def log(text: str) -> None:
        try:
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(text + "\n")
        except OSError:
            pass

    try:
        perform_install(
            install,
            data,
            "--no-desktop" not in sys.argv,
            "--no-startmenu" not in sys.argv,
            log=log,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        log(f"安装失败：{exc}")
        return 1


class Installer:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"{APP_NAME} 安装向导")
        self.root.geometry("760x560")
        self.root.minsize(760, 560)
        self.root.configure(bg=CREAM)
        icon = ASSETS / "logo.ico"
        if icon.is_file():
            try:
                self.root.iconbitmap(str(icon))
            except tk.TclError:
                pass

        cfg = read_config()
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        profile = Path(os.environ.get("USERPROFILE", str(Path.home())))
        self.install_dir = tk.StringVar(value=str(cfg.get("install_dir") or local / "Programs" / APP_NAME))
        self.data_dir = tk.StringVar(value=str(cfg.get("data_dir") or profile / "Documents" / APP_NAME))
        self.make_desktop = tk.BooleanVar(value=True)
        self.make_startmenu = tk.BooleanVar(value=True)
        self.launch_after = tk.BooleanVar(value=True)
        self.page = 0
        self.busy = False
        self.queue: queue.Queue = queue.Queue()

        self._build_header()
        self.body = tk.Frame(root, bg=CREAM)
        self.body.pack(fill="both", expand=True, padx=34, pady=(24, 0))
        self._build_footer()
        self.show(0)

    # ------------------------------------------------------------------ chrome
    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=NAVY, height=104)
        header.pack(fill="x")
        header.pack_propagate(False)
        logo_path = ASSETS / "logo-64.png"
        if logo_path.is_file():
            try:
                self.logo = tk.PhotoImage(file=str(logo_path))
                tk.Label(header, image=self.logo, bg=NAVY).pack(side="left", padx=(30, 16))
            except tk.TclError:
                pass
        text = tk.Frame(header, bg=NAVY)
        text.pack(side="left", pady=18)
        tk.Label(text, text=APP_NAME, bg=NAVY, fg="#FFFFFF", font=("Microsoft YaHei UI", 20, "bold")).pack(anchor="w")
        tk.Label(
            text,
            text="论文双语对照阅读器 · 本地运行，数据只保存在你的电脑",
            bg=NAVY,
            fg="#B9C4DB",
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", pady=(2, 0))
        tk.Label(header, text=f"v{APP_VERSION}", bg=NAVY, fg="#8FA3C4", font=("Segoe UI", 10)).pack(side="right", padx=28)

    def _build_footer(self) -> None:
        footer = tk.Frame(self.root, bg=CREAM)
        footer.pack(fill="x", padx=34, pady=18)
        self.hint = tk.Label(footer, text="", bg=CREAM, fg=MUTED, font=("Microsoft YaHei UI", 9))
        self.hint.pack(side="left")
        self.next_btn = tk.Button(
            footer,
            text="下一步",
            command=self.on_next,
            bg=NAVY,
            fg="#FFFFFF",
            activebackground="#273266",
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            padx=26,
            pady=8,
            cursor="hand2",
            font=UI_FONT,
        )
        self.next_btn.pack(side="right")
        self.back_btn = tk.Button(
            footer,
            text="上一步",
            command=self.on_back,
            bg=CREAM,
            fg=INK,
            activebackground="#EFE9DB",
            relief="flat",
            bd=0,
            padx=18,
            pady=8,
            cursor="hand2",
            font=UI_FONT,
        )
        self.back_btn.pack(side="right", padx=(0, 8))

    def clear_body(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()

    def show(self, page: int) -> None:
        self.page = page
        self.clear_body()
        self.back_btn.configure(state="normal" if page in (1, 2, 3) else "disabled")
        self.next_btn.configure(state="normal")
        if page == 0:
            self.next_btn.configure(text="开始安装")
            self.hint.configure(text="按「下一步」可自定义安装位置与数据位置")
            self.page_welcome()
        elif page == 1:
            self.next_btn.configure(text="下一步")
            self.hint.configure(text="程序文件约 120 MB")
            self.page_path("安装位置", "选择 ECPreader 程序的安装目录。", self.install_dir, True)
        elif page == 2:
            self.next_btn.configure(text="下一步")
            self.hint.configure(text="之后可在软件内「数据与安全」里随时更改")
            self.page_path("数据存放位置", "文档、译文、历史与加密密钥都会保存在这里。", self.data_dir, False)
        elif page == 3:
            self.next_btn.configure(text="安装")
            self.hint.configure(text="点击「安装」开始复制文件")
            self.page_options()
        elif page == 4:
            self.next_btn.configure(text="安装中…", state="disabled")
            self.back_btn.configure(state="disabled")
            self.hint.configure(text="正在安装，请稍候…")
            self.page_progress()
        elif page == 5:
            self.next_btn.configure(text="完成")
            self.back_btn.configure(state="disabled")
            self.hint.configure(text="")
            self.page_done()

    # ------------------------------------------------------------------ pages
    def title(self, text: str, sub: str) -> None:
        tk.Label(self.body, text=text, bg=CREAM, fg=INK, font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
        tk.Label(
            self.body,
            text=sub,
            bg=CREAM,
            fg=MUTED,
            font=UI_FONT,
            wraplength=660,
            justify="left",
        ).pack(anchor="w", pady=(6, 18))

    def page_welcome(self) -> None:
        self.title("欢迎安装 ECPreader", "把 PDF / Word 论文拖进来，左边原文、右边中文，完全在本机运行。")
        card = tk.Frame(self.body, bg="#FFFFFF", highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="x", pady=4)
        points = [
            "安装位置、数据位置都可以自己选，不会强制写到 C 盘用户目录。",
            "可创建桌面快捷方式；数据目录以后也能在「翻译设置 → 数据与安全」里修改。",
            "卸载入口会注册到 Windows「应用和功能」，卸载时可选择是否保留数据。",
            "不需要管理员权限；API Key 只以加密形式保存在你自己的电脑上。",
        ]
        for item in points:
            tk.Label(
                card,
                text="•  " + item,
                bg="#FFFFFF",
                fg="#3C476C",
                font=UI_FONT,
                wraplength=640,
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=20, pady=9)

    def page_path(self, heading: str, sub: str, variable: tk.StringVar, is_install: bool) -> None:
        self.title(heading, sub)
        row = tk.Frame(self.body, bg=CREAM)
        row.pack(fill="x")
        entry = tk.Entry(
            row,
            textvariable=variable,
            font=("Segoe UI", 10),
            relief="flat",
            bg="#FFFFFF",
            fg=INK,
            highlightthickness=1,
            highlightbackground=LINE,
            highlightcolor=MINT,
        )
        entry.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 10))
        tk.Button(
            row,
            text="浏览…",
            command=lambda: self.browse(variable),
            bg="#FFFFFF",
            fg=INK,
            relief="flat",
            bd=0,
            padx=16,
            pady=7,
            cursor="hand2",
            highlightbackground=LINE,
            highlightthickness=1,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left")
        note = (
            "提示：安装到 C:\\Program Files 需要管理员权限；默认位置无需管理员。"
            if is_install
            else "提示：可以选 D 盘或移动硬盘；首次启动会在该目录创建数据库。"
        )
        tk.Label(self.body, text=note, bg=CREAM, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(12, 0))
        if is_install:
            try:
                free = shutil.disk_usage(Path(variable.get()).anchor).free
                tk.Label(
                    self.body,
                    text=f"该磁盘可用空间：{free / (1024 ** 3):.1f} GB（需要约 220 MB）",
                    bg=CREAM,
                    fg=MUTED,
                    font=("Microsoft YaHei UI", 9),
                ).pack(anchor="w", pady=(6, 0))
            except OSError:
                pass

    def page_options(self) -> None:
        self.title("快捷方式与完成选项", "安装完成后可以立即打开 ECPreader。")
        card = tk.Frame(self.body, bg="#FFFFFF", highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="x")
        options = [
            (self.make_desktop, "创建桌面快捷方式", "桌面会出现带 logo 的 ECPreader 图标"),
            (self.make_startmenu, "创建开始菜单快捷方式", "开始菜单 → 所有程序 → ECPreader"),
            (self.launch_after, "安装完成后立即启动 ECPreader", ""),
        ]
        for variable, label, note in options:
            row = tk.Frame(card, bg="#FFFFFF")
            row.pack(fill="x", padx=18, pady=10)
            tk.Checkbutton(
                row,
                text=label,
                variable=variable,
                bg="#FFFFFF",
                fg=INK,
                activebackground="#FFFFFF",
                selectcolor="#FFFFFF",
                font=UI_FONT,
                anchor="w",
            ).pack(side="left")
            if note:
                tk.Label(row, text=note, bg="#FFFFFF", fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(
                    side="left", padx=(12, 0)
                )
        summary = f"安装到：{self.install_dir.get()}\n数据存到：{self.data_dir.get()}"
        tk.Label(
            self.body,
            text=summary,
            bg="#F6F1E5",
            fg="#3C476C",
            justify="left",
            anchor="w",
            font=("Microsoft YaHei UI", 9),
            padx=14,
            pady=12,
        ).pack(fill="x", pady=16)

    def page_progress(self) -> None:
        self.title("正在安装", "正在复制程序文件并创建快捷方式，请不要关闭窗口。")
        self.progress = ttk.Progressbar(self.body, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(6, 14))
        self.log = tk.Text(
            self.body,
            height=12,
            bg="#FFFFFF",
            fg="#3C476C",
            relief="flat",
            highlightbackground=LINE,
            highlightthickness=1,
            font=("Consolas", 9),
        )
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")
        threading.Thread(target=self.worker, daemon=True).start()
        self.root.after(120, self.poll_queue)

    def page_done(self) -> None:
        self.title("安装完成", "ECPreader 已安装，可以从桌面图标或开始菜单启动。")
        card = tk.Frame(self.body, bg="#FFFFFF", highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="x")
        tk.Label(
            card,
            text=f"安装位置：{self.install_dir.get()}",
            bg="#FFFFFF",
            fg="#3C476C",
            font=UI_FONT,
            anchor="w",
        ).pack(fill="x", padx=20, pady=(16, 4))
        tk.Label(
            card,
            text=f"数据位置：{self.data_dir.get()}",
            bg="#FFFFFF",
            fg="#3C476C",
            font=UI_FONT,
            anchor="w",
        ).pack(fill="x", padx=20, pady=(0, 4))
        tk.Label(
            card,
            text="以后可以在软件内「翻译设置 → 数据与安全」里更改数据位置。",
            bg="#FFFFFF",
            fg=MUTED,
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        ).pack(fill="x", padx=20, pady=(0, 16))
        if self.launch_after.get():
            tk.Label(self.body, text="正在启动 ECPreader…", bg=CREAM, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(
                anchor="w", pady=12
            )
            self.root.after(600, self.launch_app)

    # ------------------------------------------------------------------ actions
    def browse(self, variable: tk.StringVar) -> None:
        current = variable.get()
        chosen = filedialog.askdirectory(
            initialdir=current if Path(current).exists() else str(Path.home()),
            mustexist=False,
        )
        if chosen:
            variable.set(str(Path(chosen)))

    def on_back(self) -> None:
        if not self.busy and self.page > 0:
            self.show(self.page - 1)

    def on_next(self) -> None:
        if self.busy:
            return
        if self.page == 0:
            self.show(1)
        elif self.page == 1:
            if self.validate_dir(self.install_dir.get(), "安装位置"):
                self.show(2)
        elif self.page == 2:
            if self.validate_dir(self.data_dir.get(), "数据位置"):
                self.show(3)
        elif self.page == 3:
            if self.validate_dir(self.install_dir.get(), "安装位置"):
                self.start_install()
        elif self.page == 5:
            self.root.destroy()

    def validate_dir(self, raw: str, label: str) -> bool:
        raw = raw.strip().strip('"')
        if not raw:
            messagebox.showwarning("路径为空", f"请选择{label}。")
            return False
        path = Path(os.path.expandvars(raw)).expanduser()
        if not path.is_absolute():
            messagebox.showwarning("路径无效", f"{label}必须是完整路径，例如 D:/ECPreader。")
            return False
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("无法创建目录", f"{label}无法创建：\n{exc}\n\n请换一个位置，或换到有写权限的目录。")
            return False
        probe = path / ".ecp-write-test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            messagebox.showerror("目录不可写", f"{label}没有写入权限：\n{path}\n\n请换一个目录，或以管理员身份运行安装程序。")
            return False
        if label == "安装位置":
            self.install_dir.set(str(path))
        else:
            self.data_dir.set(str(path))
        return True

    def start_install(self) -> None:
        if not self.validate_dir(self.data_dir.get(), "数据位置"):
            return
        self.busy = True
        self.show(4)

    def log_line(self, text: str) -> None:
        self.queue.put(("log", text))

    def worker(self) -> None:
        try:
            perform_install(
                Path(self.install_dir.get()),
                Path(self.data_dir.get()),
                self.make_desktop.get(),
                self.make_startmenu.get(),
                log=self.log_line,
                progress=lambda value: self.queue.put(("progress", value)),
            )
            self.queue.put(("done", None))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("error", str(exc)))

    def poll_queue(self) -> None:
        try:
            while True:
                kind, value = self.queue.get_nowait()
                if kind == "log":
                    self.log.configure(state="normal")
                    self.log.insert("end", str(value) + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                elif kind == "progress":
                    self.progress["value"] = value
                elif kind == "done":
                    self.busy = False
                    self.show(5)
                elif kind == "error":
                    self.busy = False
                    messagebox.showerror("安装失败", f"安装过程中出错：\n{value}")
                    self.show(3)
        except queue.Empty:
            pass
        if self.busy:
            self.root.after(120, self.poll_queue)

    def launch_app(self) -> None:
        try:
            exe = Path(self.install_dir.get()) / EXE_NAME
            subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True)
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    if "--silent" in sys.argv:
        raise SystemExit(silent_install())
    # 不启用 DPI 感知：让 Windows 统一缩放 Tk 窗口，避免 125%/150% 缩放下内容溢出。
    root = tk.Tk()
    Installer(root)
    root.mainloop()


if __name__ == "__main__":
    main()