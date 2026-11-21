"""凭据安全：Windows DPAPI 保护主密钥 + AES-256-GCM 加密仓库。

设计目标：
  * 任何 API Key、令牌、邮箱等敏感字段只以密文形式存在于磁盘；
  * 主密钥由 Windows DPAPI 绑定当前用户账户（CryptProtectData），
    换成其他 Windows 账户 / 换机器都无法解出；
  * 仓库文件采用 AES-256-GCM（认证加密），防篡改；
  * 前端永远拿不到明文密钥，只拿到掩码。
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import secrets
import sys
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import config

_ENTROPY = b"ECPReader/vault/v1"
_MAGIC = b"ECPV1"

VAULT_FILENAME = "vault.bin"
KEY_FILENAME = "vault.key"


class SecurityError(RuntimeError):
    """加密/解密失败。"""


# --------------------------------------------------------------- Windows DPAPI
class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _make_blob(data: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob._buffer = buffer  # 保持引用，避免被 GC
    return blob


def _dpapi(protect: bool, data: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    src = _make_blob(data)
    entropy = _make_blob(_ENTROPY)
    out = _DataBlob()

    if protect:
        fn = crypt32.CryptProtectData
    else:
        fn = crypt32.CryptUnprotectData

    fn.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    fn.restype = wintypes.BOOL

    description = "ECPreader 凭据仓库" if protect else None
    ok = fn(
        ctypes.byref(src),
        description,
        ctypes.byref(entropy),
        None,
        None,
        0x1,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(out),
    )
    if not ok:
        raise SecurityError(
            "Windows DPAPI 调用失败（错误码 %d）" % ctypes.get_last_error()
        )
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def _protect_bytes(data: bytes) -> bytes:
    if sys.platform.startswith("win"):
        return _dpapi(True, data)
    # 非 Windows 回退：文件权限 0600（功能可用，强度弱于 DPAPI）
    return b"PLAIN:" + data


def _unprotect_bytes(blob: bytes) -> bytes:
    if blob.startswith(b"PLAIN:"):
        return blob[len(b"PLAIN:"):]
    if not sys.platform.startswith("win"):
        raise SecurityError("当前系统不支持 DPAPI，无法解密凭据")
    return _dpapi(False, blob)


# --------------------------------------------------------------------- Vault
class Vault:
    """加密的键值仓库，落盘只有一个密文文件。"""

    def __init__(self, data_dir: Path | None = None) -> None:
        base = data_dir or config.DATA_DIR
        self.vault_path = base / VAULT_FILENAME
        self.key_path = base / KEY_FILENAME
        self._lock = threading.RLock()
        self._cache: dict[str, Any] | None = None

    # ---- 主密钥
    def _create_key(self) -> bytes:
        key = secrets.token_bytes(32)
        self.key_path.write_bytes(_protect_bytes(key))
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return key

    def _master_key(self) -> bytes:
        if self.key_path.exists():
            try:
                return _unprotect_bytes(self.key_path.read_bytes())
            except Exception as exc:  # 换机 / 换账户后无法解密
                raise SecurityError(
                    "无法解密本地凭据仓库（可能更换了 Windows 账户或电脑）。"
                    "请在设置中清除凭据并重新填写。"
                ) from exc
        return self._create_key()

    # ---- 读写
    def _load(self) -> dict[str, Any]:
        if self._cache is not None:
            return self._cache
        if not self.vault_path.exists():
            self._cache = {}
            return {}
        raw = self.vault_path.read_bytes()
        if not raw.startswith(_MAGIC):
            raise SecurityError("凭据仓库格式不正确")
        nonce, ciphertext = raw[len(_MAGIC):len(_MAGIC) + 12], raw[len(_MAGIC) + 12:]
        try:
            plain = AESGCM(self._master_key()).decrypt(nonce, ciphertext, _MAGIC)
        except Exception as exc:
            raise SecurityError("凭据仓库校验失败（文件可能损坏或被篡改）") from exc
        self._cache = json.loads(plain.decode("utf-8"))
        return self._cache

    def _save(self) -> None:
        assert self._cache is not None
        nonce = os.urandom(12)
        payload = json.dumps(self._cache, ensure_ascii=False).encode("utf-8")
        ciphertext = AESGCM(self._master_key()).encrypt(nonce, payload, _MAGIC)
        tmp = self.vault_path.with_suffix(".tmp")
        tmp.write_bytes(_MAGIC + nonce + ciphertext)
        os.replace(tmp, self.vault_path)
        try:
            os.chmod(self.vault_path, 0o600)
        except OSError:
            pass

    def all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._load())

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._load().get(key, default)

    def set_many(self, values: dict[str, Any]) -> None:
        with self._lock:
            data = self._load()
            for k, v in values.items():
                if v is None:
                    data.pop(k, None)
                else:
                    data[k] = v
            self._save()

    def clear(self) -> None:
        with self._lock:
            self._cache = {}
            self._save()

    # ---- 状态
    def status(self) -> dict[str, Any]:
        configured = False
        try:
            configured = bool(self._load())
        except SecurityError:
            pass
        return {
            "protection": "dpapi" if sys.platform.startswith("win") else "file",
            "vault_file": str(self.vault_path),
            "configured": configured,
            "exists": self.vault_path.exists(),
        }


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••••••"
    return value[:3] + "••••••••" + value[-4:]


VAULT = Vault()


def session_token() -> str:
    return secrets.token_urlsafe(32)


def base64_encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")