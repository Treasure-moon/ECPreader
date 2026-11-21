"""ECPreader 卸载程序：删除程序、快捷方式与卸载注册信息，可选删除数据目录。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import winreg
from pathlib import Path

import tkinter as tk
from tkinter import messagebox

APP_NAME = "ECPreader"
NAVY = "#1B2450"
CREAM = "#FAF6EA"
INK = "#1B2450"
MUTED = "#63718F"
LINE = "#E2DACA"
UI_FONT = ("Microsoft YaHei UI", 10)
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ECPreader"


def appdata_config() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
    return Path(base) / APP_NAME / "settings.json"


def read_config() -> dict:
    try:
        data = json.loads(appdata_config().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def desktop_link() -> Path:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "Desktop")
        return Path(os.path.expandvars(value)) / f"{APP_NAME}.lnk"
    except OSError:
        return Path.home() / "Desktop" / f"{APP_NAME}.lnk"


def start_menu_link() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"


def safe_data_dir(raw: str, install: Path) -> Path | None:
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute() or len(str(path)) < 4:
        return None
    if path.anchor and path == Path(path.anchor):
        return None
    if install == path or install.is_relative_to(path) or path.is_relative_to(install):
        # 数据目录就在安装目录里或反过来，交给删除安装目录处理，避免误删上级目录
        return None
    return path


def schedule_cleanup(install: Path) -> None:
    """程序自身还在运行，交给临时批处理在进程退出后删除安装目录。"""
    fd, script = tempfile.mkstemp(prefix="ecp-clean-", suffix=".bat")
    try:
        os.write(fd, b'@echo off\r\ntimeout /t 3 /nobreak >nul\r\nrmdir /s /q "%~1"\r\ndel "%~f0"\r\n')
    finally:
        os.close(fd)
    try:
        subprocess.Popen(
            ["cmd", "/c", script, str(install)],
            cwd=tempfile.gettempdir(),
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
    except OSError:
        pass


def perform_uninstall(install: Path, data_dir: Path | None, delete_data: bool, log=print) -> bool:
    exe = install / "ECPReader.exe"
    if exe.exists():
        probe = exe.with_name("ECPReader.probe")
        try:
            exe.rename(probe)
            probe.rename(exe)
        except OSError as exc:
            raise RuntimeError("检测到 ECPreader 正在运行，请先关闭软件再卸载。") from exc

    for link in (desktop_link(), start_menu_link()):
        try:
            link.unlink()
        except OSError:
            pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass

    removed_data = False
    if delete_data and data_dir:
        if data_dir.exists():
            shutil.rmtree(data_dir, ignore_errors=False)
        removed_data = True
        log(f"已删除数据目录：{data_dir}")
        try:
            appdata_config().unlink()
            appdata_config().parent.rmdir()
        except OSError:
            pass
    else:
        try:
            config = read_config()
            config.pop("install_dir", None)
            config.pop("installed_version", None)
            appdata_config().write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    log("快捷方式与卸载注册信息已清理。")
    return removed_data


def silent_uninstall() -> int:
    install = Path(sys.executable).resolve().parent
    config = read_config()
    data_dir = safe_data_dir(str(config.get("data_dir") or ""), install)
    log_path = Path(os.environ.get("TEMP", str(Path.home()))) / "ECPreader-uninstall.log"

    def log(text: str) -> None:
        try:
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(text + "\n")
        except OSError:
            pass

    try:
        removed = perform_uninstall(install, data_dir, "--remove-data" in sys.argv, log=log)
        log("数据目录已删除。" if removed else "数据目录已保留。")
        schedule_cleanup(install)
        return 0
    except Exception as exc:  # noqa: BLE001
        log(f"卸载失败：{exc}")
        return 1


class Uninstaller:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"卸载 {APP_NAME}")
        self.root.geometry("620x400")
        self.root.resizable(False, False)
        self.root.configure(bg=CREAM)
        self.install = Path(sys.executable).resolve().parent
        self.config = read_config()
        self.data_dir = safe_data_dir(str(self.config.get("data_dir") or ""), self.install)
        self.delete_data = tk.BooleanVar(value=False)

        header = tk.Frame(root, bg=NAVY, height=84)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text=APP_NAME, bg=NAVY, fg="#FFFFFF", font=("Microsoft YaHei UI", 18, "bold")).pack(
            side="left", padx=28
        )
        tk.Label(header, text="卸载向导", bg=NAVY, fg="#B9C4DB", font=UI_FONT).pack(side="left")

        body = tk.Frame(root, bg=CREAM)
        body.pack(fill="both", expand=True, padx=30, pady=20)
        tk.Label(body, text="将从这台电脑移除 ECPreader", bg=CREAM, fg=INK, font=("Microsoft YaHei UI", 14, "bold")).pack(
            anchor="w"
        )
        tk.Label(
            body,
            text=f"程序位置：{self.install}\n快捷方式、卸载入口会一并删除。",
            bg=CREAM,
            fg=MUTED,
            justify="left",
            font=UI_FONT,
        ).pack(anchor="w", pady=(8, 16))

        card = tk.Frame(body, bg="#FFFFFF", highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="x")
        if self.data_dir:
            tk.Checkbutton(
                card,
                text="同时删除数据目录（文档、译文、历史与 API Key）",
                variable=self.delete_data,
                bg="#FFFFFF",
                fg=INK,
                activebackground="#FFFFFF",
                selectcolor="#FFFFFF",
                font=UI_FONT,
                anchor="w",
            ).pack(fill="x", padx=16, pady=(14, 2))
            tk.Label(
                card,
                text=str(self.data_dir),
                bg="#FFFFFF",
                fg=MUTED,
                font=("Consolas", 9),
                anchor="w",
                wraplength=520,
                justify="left",
            ).pack(fill="x", padx=16, pady=(0, 14))
        else:
            tk.Label(
                card,
                text="数据目录将保留（可在设置中查看具体位置）。",
                bg="#FFFFFF",
                fg=MUTED,
                font=UI_FONT,
                anchor="w",
            ).pack(fill="x", padx=16, pady=16)

        footer = tk.Frame(body, bg=CREAM)
        footer.pack(fill="x", side="bottom", pady=(18, 0))
        tk.Button(
            footer,
            text="取消",
            command=root.destroy,
            bg=CREAM,
            fg=INK,
            relief="flat",
            bd=0,
            padx=18,
            pady=8,
            cursor="hand2",
            font=UI_FONT,
        ).pack(side="right")
        tk.Button(
            footer,
            text="卸载",
            command=self.uninstall,
            bg="#B4342F",
            fg="#FFFFFF",
            activebackground="#9A2A26",
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            padx=26,
            pady=8,
            cursor="hand2",
            font=UI_FONT,
        ).pack(side="right", padx=(0, 10))

    def uninstall(self) -> None:
        if not messagebox.askyesno("确认卸载", "确定要卸载 ECPreader 吗？"):
            return
        try:
            removed_data = perform_uninstall(self.install, self.data_dir, self.delete_data.get())
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("卸载未完成", f"删除过程中出错：\n{exc}\n\n可以手动删除：{self.install}")
            return

        message = "ECPreader 已卸载。"
        message += "\n数据目录已删除。" if removed_data else "\n数据目录已保留。"
        messagebox.showinfo("卸载完成", message)
        self.root.destroy()
        schedule_cleanup(self.install)


def main() -> None:
    if "--silent" in sys.argv:
        raise SystemExit(silent_uninstall())
    root = tk.Tk()
    Uninstaller(root)
    root.mainloop()


if __name__ == "__main__":
    main()