"""全局配置：路径、默认设置、AI 服务预设。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

APP_NAME = "ECPreader"
APP_VERSION = "1.7.0"

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
    WEB_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR)) / "web"
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    WEB_DIR = BASE_DIR / "web"


def _user_config_path() -> Path:
    """安装器与「设置 → 数据与安全」共用的用户配置（不在 data 里，避免自我引用）。"""
    base = os.environ.get("APPDATA")
    root = Path(base) if base else (Path.home() / ".config")
    return root / "ECPreader" / "settings.json"


USER_CONFIG_FILE = _user_config_path()
DATA_DIR_FALLBACK = ""  # 配置的数据目录不可用时，记录原因并退回默认目录


def read_user_config() -> dict:
    try:
        data = json.loads(USER_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_user_config(values: dict) -> None:
    target = USER_CONFIG_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    merged = {**read_user_config(), **values}
    fd, tmp = tempfile.mkstemp(prefix="settings-", suffix=".json", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _resolve_data_dir() -> Path:
    raw = os.environ.get("ECP_DATA_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    configured = str(read_user_config().get("data_dir") or "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_absolute():
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                probe = candidate / ".ecp-write-probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
                return candidate.resolve()
            except OSError as exc:
                global DATA_DIR_FALLBACK
                DATA_DIR_FALLBACK = f"{candidate}（{exc.strerror or exc}）"
                print(f"[config] 数据目录不可用，改用默认目录：{DATA_DIR_FALLBACK}")
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        sibling = exe_dir.parent / "data"
        if (sibling / "app.db").exists():
            return sibling
        return (exe_dir / "data").resolve()
    return (BASE_DIR / "data").resolve()


DATA_DIR = _resolve_data_dir()
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"
PAGE_DIR = DATA_DIR / "pages"
VAULT_FILE = DATA_DIR / "vault.bin"
VAULT_KEY_FILE = DATA_DIR / "vault.key"
DB_FILE = DATA_DIR / "app.db"

HOST = os.environ.get("ECP_HOST", "127.0.0.1")
PORT = int(os.environ.get("ECP_PORT", "8765"))

MAX_UPLOAD_MB = 100
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

# ---------------------------------------------------------------- 默认设置
DEFAULT_SETTINGS: dict[str, object] = {
    "engine_mode": "free",          # free | ai
    "free_provider": "auto",        # auto | mymemory | google | bing
    "free_email": "",               # MyMemory 可选邮箱（提高免费额度）
    "ai_provider": "deepseek",      # 见 AI_PRESETS
    "ai_base_url": "https://api.deepseek.com/v1",
    "ai_model": "deepseek-chat",
    "ai_temperature": 0.2,
    "ai_concurrency": 3,
    "translate_references": False,  # 参考文献是否翻译
    "translate_formulas": False,
    "font_size": 16,
    "theme": "light",
    "glossary": "",                 # 自定义术语表，每行 "english=中文"
    "show_page_badges": True,
    "update_check": True,           # 阅读助手是否去仓库问一次最新版本（可关）
    "chat_budget": 24000,           # 阅读助手的论文上下文上限（字符）
    "chat_full_on_summary": False,  # 总结/通读全文时是否发送整篇（默认关，省额度）
    "free_cached_provider": "",     # 内部：上次探测成功的免费服务
}

# 预置的 OpenAI 兼容 / 本地模型服务
AI_PRESETS: dict[str, dict[str, object]] = {
    "deepseek": {
        "label": "DeepSeek 官方 API",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "kind": "openai",
        "note": "需在 platform.deepseek.com 申请 API Key",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "kind": "openai",
        "note": "国内访问通常需要代理",
    },
    "siliconflow": {
        "label": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-72B-Instruct",
        "kind": "openai",
        "note": "国内可直连，注册有免费额度",
    },
    "moonshot": {
        "label": "Moonshot Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-32k",
        "kind": "openai",
        "note": "长文本能力强，适合整段论文",
    },
    "custom": {
        "label": "自定义 OpenAI 兼容接口",
        "base_url": "",
        "model": "",
        "kind": "openai",
        "note": "任何兼容 /chat/completions 的服务（vLLM、one-api、中转等）",
    },
    "ollama": {
        "label": "本地 Ollama",
        "base_url": "http://127.0.0.1:11434",
        "model": "qwen2.5:7b",
        "kind": "ollama",
        "note": "先运行 ollama serve 并 ollama pull 模型",
    },
    "lmstudio": {
        "label": "本地 LM Studio",
        "base_url": "http://127.0.0.1:1234/v1",
        "model": "local-model",
        "kind": "openai",
        "note": "在 LM Studio 中开启 Local Server",
    },
}

FREE_PROVIDERS = {
    "auto": {"label": "自动选择（推荐）", "note": "自动探测可用的免费接口"},
    "mymemory": {"label": "MyMemory（免密钥）", "note": "国内可直连，匿名每日约 5000 字符"},
    "google": {"label": "Google 翻译（免密钥）", "note": "国内通常无法访问，海外可用"},
    "bing": {"label": "Bing 翻译（免密钥）", "note": "海外可用，国内可能无法获取令牌"},
}


def ensure_dirs() -> None:
    for path in (DATA_DIR, UPLOAD_DIR, EXPORT_DIR, PAGE_DIR):
        path.mkdir(parents=True, exist_ok=True)