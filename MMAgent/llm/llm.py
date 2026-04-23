import os
import time
from urllib.parse import urlsplit

import openai
from dotenv import load_dotenv

load_dotenv()


def _is_true(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_model_name(model_name):
    name = (model_name or "").strip()
    model_aliases = {
        "gpt-4o": "gpt-5",
        "gpt-4": "gpt-5",
        "gpt-3.5-turbo": "gpt-4.1-mini",
        "deepseek-chat": "gpt-4.1-mini",
        "deepseek-reasoner": "gpt-5.2",
        "deepseek-r1": "gpt-5.2",
        "qwen2.5-72b-instruct": "gpt-5",
    }
    return model_aliases.get(name, name)


class LLM:

    usages = []

    def __init__(self, model_name, key, logger=None, user_id=None):
        self.model_name = _normalize_model_name(model_name)
        self.logger = logger
        self.user_id = user_id
        self.debug = _is_true(os.getenv("LLM_DEBUG"), default=True)
        self.call_count = 0
        self.api_key = (key or "").strip()
        self.api_base = ""
        self.proxy_url = ""
        self.default_headers = {}
        self.request_timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))
        self.usages = []

        self._configure_default_api()

        if not self.api_key:
            raise ValueError("API key is required. Pass --key or set it in your runtime environment.")

        self._build_client()
        self._log(
            "client initialized "
            f"model={self.model_name} "
            f"base={self.api_base} "
            f"proxy={self.proxy_url or 'none'} "
            f"timeout={self.request_timeout_seconds}s"
        )

    def _log(self, message):
        if self.debug:
            now = time.strftime("%H:%M:%S")
            print(f"[LLM {now}] {message}", flush=True)

    def _configure_default_api(self):
        self.api_base = (
            os.getenv("OPENAI_API_BASE")
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        ).strip()
        self.proxy_url = (
            os.getenv("LLM_PROXY_URL")
            or os.getenv("HTTPS_PROXY")
            or os.getenv("HTTP_PROXY")
            or ""
        ).strip()
        self.default_headers = {}

    def _build_client(self):
        client_kwargs = {
            "api_key": self.api_key,
            "base_url": self.api_base,
        }
        if self.default_headers:
            client_kwargs["default_headers"] = self.default_headers

        http_client_kwargs = {"timeout": self.request_timeout_seconds}
        if self.proxy_url:
            proxy_for_client = self.proxy_url
            if self.proxy_url.lower().startswith("socks"):
                try:
                    import socksio  # noqa: F401
                except ModuleNotFoundError:
                    parsed = urlsplit(self.proxy_url)
                    if parsed.hostname and parsed.port:
                        proxy_for_client = f"http://{parsed.hostname}:{parsed.port}"
                        print(
                            "SOCKS proxy URL detected without socksio; "
                            f"falling back to HTTP proxy: {proxy_for_client}"
                        )
                    else:
                        raise ValueError(
                            "SOCKS proxy URL is invalid and socksio is not available. "
                            "Set LLM_PROXY_URL to an HTTP proxy URL like http://127.0.0.1:2080."
                        )
            http_client_kwargs["proxy"] = proxy_for_client
        client_kwargs["http_client"] = openai.DefaultHttpxClient(**http_client_kwargs)
        self.client = openai.Client(**client_kwargs)

    def reset(self, api_key=None, api_base=None, model_name=None, proxy_url=None, default_headers=None):
        if api_key:
            self.api_key = api_key
        if api_base:
            self.api_base = api_base
        if model_name:
            self.model_name = _normalize_model_name(model_name)
        if proxy_url is not None:
            self.proxy_url = proxy_url
        if default_headers is not None:
            self.default_headers = default_headers
        self._build_client()

    def generate(self, prompt, system="You are a helpful assistant.", usage=True):
        self.call_count += 1
        call_id = self.call_count
        started_at = time.time()
        self._log(
            f"call#{call_id} start model={self.model_name} "
            f"prompt_chars={len(prompt)} system_chars={len(system)}"
        )
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                top_p=1.0,
                frequency_penalty=0.0,
                presence_penalty=0.0,
            )
            answer = response.choices[0].message.content
            usage_stats = {
                "completion_tokens": response.usage.completion_tokens,
                "prompt_tokens": response.usage.prompt_tokens,
                "total_tokens": response.usage.total_tokens,
            }
            elapsed = time.time() - started_at
            self._log(
                f"call#{call_id} done in {elapsed:.2f}s "
                f"tokens={usage_stats['total_tokens']}"
            )
            if self.logger:
                self.logger.info(
                    f"[LLM] UserID: {self.user_id} Key: {self.api_key}, Model: {self.model_name}, Usage: {usage_stats}"
                )
            if usage:
                self.usages.append(usage_stats)
            return answer

        except Exception as e:
            elapsed = time.time() - started_at
            self._log(f"call#{call_id} error after {elapsed:.2f}s: {type(e).__name__}: {e}")
            return f"An error occurred: {e}"

    def get_total_usage(self):
        total_usage = {
            "completion_tokens": 0,
            "prompt_tokens": 0,
            "total_tokens": 0,
        }
        for usage in self.usages:
            for key, value in usage.items():
                total_usage[key] += value
        return total_usage

    def clear_usage(self):
        self.usages = []
