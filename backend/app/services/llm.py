"""模型客户端封装(OpenAI 兼容接口)。

设计原则:
- 密钥只在后端读取,不暴露给前端;
- 任何一次调用失败都返回 None,由上层走兜底逻辑,而不是让整个流程崩掉。
"""

import json
import logging
import re
import threading
from typing import Any, Dict, List, Optional

from .. import config

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self) -> None:
        self.model = config.OPENAI_MODEL
        self.base_url = _normalize_base_url(config.OPENAI_BASE_URL)
        self._client = None
        # 失败状态按线程隔离:抽取现在是并发调用模型的,若共用一份状态,
        # A 块的错误会污染 B 块的降级判断(甚至把正常结果误判成致命错误)。
        self._local = threading.local()

        if config.OPENAI_API_KEY:
            try:
                from openai import OpenAI

                self._client = OpenAI(
                    api_key=config.OPENAI_API_KEY,
                    base_url=self.base_url,
                    timeout=60.0,
                    max_retries=1,
                )
            except Exception as exc:  # 依赖缺失或参数异常
                logger.warning("模型客户端初始化失败,进入离线兜底模式:%s", exc)
        else:
            logger.warning("未配置 OPENAI_API_KEY,进入离线兜底模式")

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def last_error(self) -> str:
        """当前线程最近一次调用失败的原因;调用成功时为空字符串。"""
        return getattr(self._local, "error", "")

    @property
    def fatal_error(self) -> bool:
        """凭证或地址类错误(401/403/404):重试和降级都没有意义,应直接失败。"""
        return getattr(self._local, "status", None) in (401, 403, 404)

    def chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 1500,
        json_mode: bool = False,
    ) -> Optional[str]:
        if not self.available:
            return None

        try:
            content = self._complete(system, user, temperature, max_tokens, json_mode)
            self._remember_success()
            return content
        except Exception as exc:
            # 部分 OpenAI 兼容端点不支持 response_format,降级为纯提示约束重试一次
            if json_mode and self._looks_like_format_error(exc):
                logger.warning("端点不支持 JSON 模式,改为提示约束重试:%s", exc)
                try:
                    content = self._complete(system, user, temperature, max_tokens, False)
                    self._remember_success()
                    return content
                except Exception as retry_exc:
                    logger.warning("模型调用失败:%s", retry_exc)
                    self._remember_failure(retry_exc)
                    return None
            logger.warning("模型调用失败:%s", exc)
            self._remember_failure(exc)
            return None

    def _remember_success(self) -> None:
        """成功后清空失败状态,避免上一块的错误影响下一块的降级判断。"""
        self._local.error = ""
        self._local.status = None

    def _remember_failure(self, exc: Exception) -> None:
        self._local.error = _summarize_exception(exc)
        self._local.status = getattr(exc, "status_code", None)

    def _complete(
        self,
        system: str,
        user: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> Optional[str]:
        kwargs: Dict[str, Any] = {}
        if json_mode:
            # 强约束模型只输出 JSON,显著降低「解析失败 → 整体降级为规则抽取」的概率
            kwargs["response_format"] = {"type": "json_object"}
        # 推理模型会把「思考」计入 max_tokens:预算不足时正文为空且
        # finish_reason=length。这里自动翻倍预算重试,否则长文本块会被整块
        # 降级成规则抽取(实测 max_tokens=2000 对推理模型明显不够)。
        budget = max_tokens
        while True:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=budget,
                **kwargs,
            )
            content = (response.choices[0].message.content or "").strip()
            if content:
                return content
            # 没有正文又不是被预算截断,说明模型确实没输出内容
            if getattr(response.choices[0], "finish_reason", None) != "length":
                return None
            if budget >= config.LLM_MAX_OUTPUT_TOKENS:
                raise RuntimeError(
                    f"模型思考占满了输出预算(max_tokens={budget}),未产出正文;"
                    "该端点为推理模型,请调大 LLM_MAX_OUTPUT_TOKENS 或改用非推理模型"
                )
            budget = min(budget * 2, config.LLM_MAX_OUTPUT_TOKENS)
            logger.warning("模型思考占满输出预算,自动提高到 max_tokens=%s 重试", budget)

    @staticmethod
    def _looks_like_format_error(exc: Exception) -> bool:
        """判断异常是否由「端点不支持 JSON 模式」引起,避免无谓的重试。"""
        message = str(exc).lower()
        return any(
            hint in message
            for hint in (
                "response_format",
                "json_object",
                "json mode",
                "unsupported",
                "invalid_request",
                "400",
            )
        )

    def chat_json(
        self,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 1500,
        json_mode: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """要求模型返回 JSON,解析失败时返回 None 交给上层兜底。"""
        text = self.chat(
            system,
            user,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
        if not text:
            # 调用本身失败时 last_error 已记录,这里只补「模型返回空内容」的情形
            if not self.last_error:
                self._local.error = "模型返回了空内容"
            return None
        payload = self._parse_json(text)
        if payload is None:
            self._local.error = "模型输出无法解析为 JSON:" + re.sub(r"\s+", " ", text)[:80]
        return payload

    @staticmethod
    def _parse_json(text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        cleaned = text.strip()
        # 去掉 ```json ... ``` 包裹
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
        # 去掉尾随逗号(模型最常见的语法错误)
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)

        candidates = [cleaned]
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            candidates.append(cleaned[start : end + 1])
        # 输出被 max_tokens 截断时,尝试补齐缺失的括号
        repaired = _close_brackets(cleaned[start:] if start != -1 else cleaned)
        if repaired:
            candidates.append(repaired)

        for candidate in candidates:
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
                if isinstance(data, list):
                    # 模型偶尔只输出实体数组
                    return {"entities": data, "relations": []}
            except Exception:
                continue
        logger.warning("模型输出不是合法 JSON,已丢弃:%s", text[:200])
        return None


def _close_brackets(text: str) -> Optional[str]:
    """JSON 被截断时,按栈补齐未闭合的 } 与 ],尽力挽救一次输出。"""
    if not text:
        return None
    stack: List[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]" and stack:
            stack.pop()
    if not stack:
        return None
    closing = "".join("}" if opener == "{" else "]" for opener in reversed(stack))
    return text + ('"' if in_string else "") + closing


def _normalize_base_url(raw: str) -> str:
    """把配置里的地址归一化成 OpenAI SDK 需要的「根地址」。

    SDK 会自己在 base_url 后面拼 /chat/completions,所以 .env 里填了完整路径
    就会重复拼接(.../v1/chat/completions/chat/completions)而报 404 —— 这是
    最常见的配置错误,这里统一兜住:
        https://host/v1/chat/completions -> https://host/v1
        https://host                     -> https://host/v1
    """
    url = (raw or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        while url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    if not url:
        return "https://api.openai.com/v1"
    # 只剩域名时补版本段,否则 SDK 会请求 https://host/chat/completions
    if "://" in url and "/" not in url.split("://", 1)[1]:
        url += "/v1"
    return url


def _summarize_exception(exc: Exception) -> str:
    """把 SDK 异常压成一句人能看懂的话。

    原始异常里塞满了 JSON、request_id 等噪声:直接展示到界面既读不懂,
    又会让每个文本块的降级原因各不相同而无法去重(文档错误栏会爆炸)。
    """
    text = re.sub(r"\s+", " ", str(exc))
    match = re.search(r"'message_zh':\s*'([^']+)'", text) or re.search(
        r"'message':\s*'([^']+)'", text
    )
    detail = (match.group(1) if match else text)[:160]
    status = getattr(exc, "status_code", None)
    return f"{status} {detail}" if status else detail


_client: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
