"""LLM 客户端：OpenAI 兼容接口，支持任意兼容网关（智谱 GLM / DeepSeek / 内部网关等）。

未配置 API Key 时 available=False，流水线自动降级为离线规则模式，保证 Demo 永远可运行。
"""
from __future__ import annotations

import json
import os
import re

from dotenv import load_dotenv

load_dotenv()


class LLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = os.getenv("LLM_API_KEY", "") if api_key is None else api_key.strip()
        self.base_url = (
            os.getenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
            if base_url is None
            else base_url.strip()
        )
        self.model = os.getenv("LLM_MODEL", "glm-4.6") if model is None else model.strip()
        self.temperature = (
            float(os.getenv("LLM_TEMPERATURE", "0.1"))
            if temperature is None
            else float(temperature)
        )
        self.timeout = float(os.getenv("LLM_TIMEOUT", "120")) if timeout is None else float(timeout)
        self._client = None
        if self.api_key:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )

    @property
    def available(self) -> bool:
        return self._client is not None

    def chat_json(self, system: str, user: str) -> list | dict:
        """调用 LLM 并解析 JSON 输出（自动剥离 ```json 围栏、截取首尾括号）。"""
        assert self._client is not None, "LLM 未配置"
        resp = self._client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = resp.choices[0].message.content or ""
        return _parse_json(content)


def _parse_json(text: str) -> list | dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
    start = min(starts) if starts else -1
    if start < 0:
        raise ValueError(f"LLM 输出中未找到 JSON：{text[:200]}")
    end = max(text.rfind("]"), text.rfind("}"))
    return json.loads(text[start : end + 1])
