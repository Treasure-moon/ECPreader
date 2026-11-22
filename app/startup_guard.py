"""启动阶段的轻量互斥：避免用户连续双击时同时拉起多个窗口。

这不是常驻的单实例锁：服务就绪、窗口加载完成后就会释放，因此用户之后
仍然可以正常多开。只有“还没启动完成”的这段时间内，重复启动会被忽略。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class StartupGuard:
    """基于数据目录锁文件的启动保护，进程崩溃后会自动判定为过期。"""

    def __init__(self, path: Path, stale_after: float = 120.0) -> None:
        self.path = Path(path)
        self.stale_after = max(10.0, float(stale_after))
        self.token = ""

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                if self._stale():
                    self._remove()
                    continue
                return False
            except OSError:
                # 数据目录不可写时不阻断启动，交给后续正常流程报错。
                return True
            self.token = uuid.uuid4().hex
            payload = json.dumps(
                {"pid": os.getpid(), "token": self.token, "at": time.time()},
                ensure_ascii=False,
            )
            try:
                os.write(fd, payload.encode("utf-8"))
            finally:
                os.close(fd)
            return True
        return False

    def release(self) -> None:
        if not self.token:
            return
        token = self.token
        self.token = ""
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if data.get("token") != token:
                return
        except (OSError, ValueError):
            return
        self._remove()

    def _stale(self) -> bool:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            age = time.time() - float(data.get("at") or 0)
            pid = int(data.get("pid") or 0)
        except (OSError, ValueError, TypeError):
            return True
        if age < 0 or age > self.stale_after:
            return True
        return not _pid_alive(pid)

    def _remove(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass