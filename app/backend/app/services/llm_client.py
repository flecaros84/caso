from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

import requests

from app.config import settings


class GitHubModelsClient:
    """Client for GitHub Models-compatible chat/completions endpoints.

    Includes a global throttle so several service calls do not hit the
    provider too quickly during a full candidate analysis.
    """

    _lock = threading.Lock()
    _last_request_time = 0.0

    def __init__(self, model: str | None = None) -> None:
        self.enabled = bool(settings.use_llm and settings.github_token)
        self.endpoint = settings.github_models_endpoint
        self.model = model or settings.github_model
        self.token = settings.github_token
        self.request_delay_seconds = float(settings.llm_request_delay_seconds)
        self.max_retries = int(settings.llm_max_retries)
        self.retry_base_seconds = int(settings.llm_retry_base_seconds)

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1500,
    ) -> dict[str, Any] | list[Any] | None:
        if not self.enabled:
            return None

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }

        for attempt in range(self.max_retries + 1):
            self._wait_before_request()

            try:
                print(
                    f"[LLM REQUEST] endpoint={self.endpoint} model={self.model} "
                    f"attempt={attempt + 1}/{self.max_retries + 1}"
                )

                response = requests.post(self.endpoint, headers=headers, json=payload, timeout=120)

                # Some providers/models do not support response_format. Retry once without it.
                if response.status_code in {400, 422} and "response_format" in response.text.lower():
                    payload.pop("response_format", None)
                    response = requests.post(self.endpoint, headers=headers, json=payload, timeout=120)

                if response.status_code == 429:
                    wait_seconds = self._get_retry_wait_seconds(response, attempt)
                    print(f"[LLM RATE LIMIT] 429 Too Many Requests. Waiting {wait_seconds} seconds...")
                    time.sleep(wait_seconds)
                    continue

                if response.status_code >= 500:
                    wait_seconds = self._get_server_error_wait_seconds(attempt)
                    print(f"[LLM SERVER ERROR] {response.status_code}. Waiting {wait_seconds} seconds...")
                    time.sleep(wait_seconds)
                    continue

                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                parsed = self._parse_json(content)
                if parsed is not None:
                    print("[LLM SUCCESS] Valid JSON received.")
                return parsed

            except requests.exceptions.HTTPError:
                print("[LLM HTTP ERROR]")
                print(f"Status code: {response.status_code}")
                print(f"Response body: {response.text}")
                return None

            except requests.exceptions.Timeout:
                wait_seconds = self._get_server_error_wait_seconds(attempt)
                print(f"[LLM TIMEOUT] Waiting {wait_seconds} seconds before retry...")
                time.sleep(wait_seconds)
                continue

            except Exception as exc:
                print(f"[LLM ERROR] {exc}")
                return None

        print("[LLM FALLBACK] Max retries reached. Using local fallback when available.")
        return None

    def _wait_before_request(self) -> None:
        with GitHubModelsClient._lock:
            now = time.time()
            elapsed = now - GitHubModelsClient._last_request_time
            remaining = self.request_delay_seconds - elapsed

            if remaining > 0:
                print(f"[LLM THROTTLE] Waiting {remaining:.1f} seconds before next request...")
                time.sleep(remaining)

            GitHubModelsClient._last_request_time = time.time()

    def _get_retry_wait_seconds(self, response: requests.Response, attempt: int) -> int:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(int(retry_after), 120)
            except ValueError:
                pass
        return min(self.retry_base_seconds * (2 ** attempt), 120)

    def _get_server_error_wait_seconds(self, attempt: int) -> int:
        return min(self.retry_base_seconds * (attempt + 1), 120)

    def _parse_json(self, content: str) -> dict[str, Any] | list[Any] | None:
        cleaned = content.strip()
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"(\{.*\}|\[.*\])", cleaned, flags=re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass

        print("[LLM RAW OUTPUT]")
        print(content)
        return None
