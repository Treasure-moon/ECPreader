"""统一的翻译错误模型：错误码 + 中文提示 + 可操作建议。"""
from __future__ import annotations

from typing import Any

DEFAULT_MESSAGES: dict[str, dict[str, str]] = {
    "config_missing": {
        "message": "尚未配置 AI 翻译引擎",
        "hint": "打开「翻译设置 → AI 引擎」，选择服务商并填写 API Key；或先使用免密钥的「传统机翻」。",
    },
    "unauthorized": {
        "message": "API Key 无效或没有访问权限",
        "hint": "请在「翻译设置 → AI 引擎」中检查 Key 是否填写正确、是否已过期，以及该账号是否已开通所填模型。",
    },
    "quota": {
        "message": "接口额度不足（余额或配额已用尽）",
        "hint": "请到服务商控制台充值或更换 Key，也可以临时切换到「传统机翻」或本地模型。",
    },
    "rate_limit": {
        "message": "请求过于频繁，已被接口限流",
        "hint": "程序已自动退避重试。若持续失败，请在设置中把「并发数」降到 1–2 后重新翻译。",
    },
    "free_quota": {
        "message": "免费接口的当日额度已用完，翻译已停止",
        "hint": "MyMemory 匿名模式每天约 5000 字符。可在「翻译设置 → 免费引擎」填写邮箱提高额度，或改用 AI 翻译。",
    },
    "model_not_found": {
        "message": "模型不存在或模型名写错了",
        "hint": "核对模型名称与服务商文档是否一致；若使用本地 Ollama，请先执行 ollama pull <模型名>。",
    },
    "model_not_running": {
        "message": "本地模型服务未启动",
        "hint": "请先启动 Ollama（命令行运行 ollama serve）或 LM Studio 的 Local Server，确认地址与端口后点「测试连接」。",
    },
    "timeout": {
        "message": "接口响应超时",
        "hint": "网络较慢或服务繁忙，自动重试后仍未成功。请检查网络/代理，或降低并发、换用其他引擎后重试。",
    },
    "network": {
        "message": "无法连接到翻译服务",
        "hint": "请检查网络连接与代理设置。国内网络通常无法访问 Google 免费接口，可改用 MyMemory 或配置 AI API。",
    },
    "bad_response": {
        "message": "接口返回的内容无法解析",
        "hint": "可能是服务端异常或返回被截断。请重试；若使用 AI 引擎，可尝试降低并发或更换模型。",
    },
    "context_too_long": {
        "message": "文本超出模型的上下文长度",
        "hint": "请在设置中换用长上下文模型（如 moonshot-v1-32k），或先分段翻译该文档。",
    },
    "upstream_error": {
        "message": "翻译服务内部错误",
        "hint": "服务商暂时不可用。请稍后重试，或切换到其他引擎。",
    },
    "empty_result": {
        "message": "翻译结果为空",
        "hint": "接口没有返回译文。请重试，或检查该段是否包含可翻译的文字。",
    },
    "parse_error": {
        "message": "文档解析失败",
        "hint": "请确认文件未加密、不是扫描版 PDF；必要时用 Word 另存为 .docx 后重试。",
    },
    "file_too_large": {
        "message": "文件超过大小限制",
        "hint": "单个文件请控制在 100MB 以内。",
    },
    "unknown": {
        "message": "翻译失败（未知错误）",
        "hint": "请重试；如果反复出现，请查看应用数据目录中的日志或更换翻译引擎。",
    },
}


class TranslationError(Exception):
    def __init__(
        self,
        code: str = "unknown",
        message: str | None = None,
        hint: str | None = None,
        detail: str = "",
    ) -> None:
        info = DEFAULT_MESSAGES.get(code, DEFAULT_MESSAGES["unknown"])
        self.code = code
        self.message = message or info["message"]
        self.hint = hint or info["hint"]
        self.detail = detail
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "detail": self.detail[:500],
        }


def error_payload(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, TranslationError):
        return exc.to_dict()
    return {
        "code": "unknown",
        "message": DEFAULT_MESSAGES["unknown"]["message"],
        "hint": DEFAULT_MESSAGES["unknown"]["hint"],
        "detail": str(exc)[:500],
    }