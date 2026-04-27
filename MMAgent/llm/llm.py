import os
import time
from urllib.parse import urlsplit

import openai
import httpx
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()


def _is_true(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_model_name(model_name):
    name = (model_name or "").strip()
    model_aliases = {
        # Legacy / common aliases mapped onto currently-available models.
        "gpt-5": "gpt-5.4",
        "gpt-4o": "gpt-5.4",
        "gpt-4": "gpt-5.4",
        "gpt-4.1": "gpt-5-mini",
        "gpt-4.1-mini": "gpt-5-mini",
        "gpt-3.5-turbo": "gpt-5-mini",
        "deepseek-chat": "gpt-5-mini",
        "deepseek-reasoner": "gpt-5.2",
        "deepseek-r1": "gpt-5.2",
        "qwen2.5-72b-instruct": "gpt-5.4",
    }
    normalized = model_aliases.get(name, name)
    # If an unknown model is passed, fall back to a safe default.
    allowed = {
        "claude-opus-4.6",
        "claude-sonnet-4.5",
        "gpt-5-mini",
        "gpt-5.2",
        "gpt-5.4",
        "gpt-5.4-nano",
        "gpt-5-nano",
    }
    return normalized if normalized in allowed else "gpt-5.4"


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
        self.max_retries = int(os.getenv("LLM_MAX_RETRIES", "2"))
        self.retry_backoff_seconds = float(os.getenv("LLM_RETRY_BACKOFF_SECONDS", "2.0"))
        self.max_rate_limit_wait_seconds = float(os.getenv("LLM_MAX_RATE_LIMIT_WAIT_SECONDS", "300"))
        self.min_retry_sleep_seconds = float(os.getenv("LLM_MIN_RETRY_SLEEP_SECONDS", "5.0"))
        self.rate_limit_reset_buffer_seconds = float(os.getenv("LLM_RATE_LIMIT_RESET_BUFFER_SECONDS", "1.0"))
        self.rate_limit_fallback_sleep_seconds = float(os.getenv("LLM_RATE_LIMIT_FALLBACK_SLEEP_SECONDS", "30.0"))
        self.enable_streaming = _is_true(os.getenv("LLM_STREAM", "true"), default=True)
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

    def _compute_rate_limit_sleep(self, message: str, attempt: int) -> float:
        """
        Best-effort: parse LiteLLM-style error strings like:
        '... Limit resets at: 2026-04-26 04:50:22 UTC'
        and return seconds to sleep, bounded by max_rate_limit_wait_seconds.
        """
        base = max(self.retry_backoff_seconds * attempt, self.min_retry_sleep_seconds)
        if not message:
            return min(self.max_rate_limit_wait_seconds, base)
        marker = "Limit resets at:"
        if marker not in message:
            return min(self.max_rate_limit_wait_seconds, base)
        try:
            ts = message.split(marker, 1)[1].strip()
            # Expected format: "YYYY-MM-DD HH:MM:SS UTC"
            ts = ts.replace("UTC", "").strip()
            reset_at = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            wait = (reset_at - now).total_seconds()
            # Add a small buffer to avoid racing the reset boundary.
            wait = max(wait + self.rate_limit_reset_buffer_seconds, base)
            return min(self.max_rate_limit_wait_seconds, wait)
        except Exception:
            # Fallback: longer wait when a reset hint exists.
            return min(
                self.max_rate_limit_wait_seconds,
                max(base, self.rate_limit_fallback_sleep_seconds),
            )

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

        # Use a total timeout so long generations don't hit shorter read timeouts.
        timeout = httpx.Timeout(self.request_timeout_seconds)
        http_client_kwargs = {"timeout": timeout}
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
        last_err = None
        attempts = max(1, self.max_retries + 1)
        for attempt in range(1, attempts + 1):
            try:
                if self.enable_streaming:
                    try:
                        stream = self.client.chat.completions.create(
                            model=self.model_name,
                            messages=[
                                {"role": "system", "content": system},
                                {"role": "user", "content": prompt},
                            ],
                            temperature=0.7,
                            top_p=1.0,
                            frequency_penalty=0.0,
                            presence_penalty=0.0,
                            stream_options={"include_usage": True},
                            stream=True,
                        )
                    except Exception as e:
                        # Some OpenAI-compatible middleware may not support stream_options.
                        self._log(f"call#{call_id} stream_options unsupported: {type(e).__name__}: {e}")
                        stream = self.client.chat.completions.create(
                            model=self.model_name,
                            messages=[
                                {"role": "system", "content": system},
                                {"role": "user", "content": prompt},
                            ],
                            temperature=0.7,
                            top_p=1.0,
                            frequency_penalty=0.0,
                            presence_penalty=0.0,
                            stream=True,
                        )
                    chunks = []
                    usage_stats = None
                    for event in stream:
                        if event.choices and event.choices[0].delta and event.choices[0].delta.content:
                            chunks.append(event.choices[0].delta.content)
                        # Some OpenAI-compatible gateways include usage on the final chunk.
                        if getattr(event, "usage", None) is not None:
                            usage_stats = {
                                "completion_tokens": event.usage.completion_tokens,
                                "prompt_tokens": event.usage.prompt_tokens,
                                "total_tokens": event.usage.total_tokens,
                            }
                    answer = "".join(chunks)
                    if usage_stats is None:
                        usage_stats = {"completion_tokens": 0, "prompt_tokens": 0, "total_tokens": 0}
                else:
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
                    f"tokens={usage_stats.get('total_tokens', 0)}"
                )
                if self.logger:
                    self.logger.info(
                        f"[LLM] UserID: {self.user_id} Key: {self.api_key}, Model: {self.model_name}, Usage: {usage_stats}"
                    )
                if usage:
                    self.usages.append(usage_stats)
                return answer

            except (openai.RateLimitError,) as e:
                last_err = e
                elapsed = time.time() - started_at
                self._log(
                    f"call#{call_id} rate_limited after {elapsed:.2f}s "
                    f"(attempt {attempt}/{attempts}): {type(e).__name__}: {e}"
                )
                if attempt < attempts:
                    time.sleep(self._compute_rate_limit_sleep(str(e), attempt))
                    continue
                break
            except (openai.APIError,) as e:
                # Some OpenAI-compatible gateways (e.g. LiteLLM) wrap rate limits into APIError.
                msg = str(e)
                if "RateLimitError" in msg or "rate limit" in msg.lower():
                    last_err = e
                    elapsed = time.time() - started_at
                    self._log(
                        f"call#{call_id} rate_limited after {elapsed:.2f}s "
                        f"(attempt {attempt}/{attempts}): {type(e).__name__}: {e}"
                    )
                    # If the error string contains a reset timestamp, do a bounded wait.
                    if attempt < attempts:
                        wait_s = self._compute_rate_limit_sleep(msg, attempt)
                        time.sleep(wait_s)
                        continue
                raise
            except (openai.APITimeoutError, httpx.TimeoutException) as e:
                last_err = e
                elapsed = time.time() - started_at
                self._log(
                    f"call#{call_id} timeout after {elapsed:.2f}s "
                    f"(attempt {attempt}/{attempts}): {type(e).__name__}: {e}"
                )
                if attempt < attempts:
                    time.sleep(self.retry_backoff_seconds * attempt)
                    continue
                break
            except Exception as e:
                elapsed = time.time() - started_at
                self._log(f"call#{call_id} error after {elapsed:.2f}s: {type(e).__name__}: {e}")
                return f"An error occurred: {e}"

        return f"An error occurred: {last_err}"

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
