"""Вызовы сервисов с моделями: embedder (SigLIP) и vLLM (Qwen3-VL).

Модели живут в своих контейнерах и могут упасть или перезапускаться минутами.
api при этом должен отвечать быстро и понятно, а не ждать таймаута на каждом
запросе: после сбоя сервис помечается недоступным на `cooldown_s`, и запросы
в это время сразу получают «недоступно». Потом — одна пробная попытка.
"""

from __future__ import annotations

import logging
import threading
import time

import httpx

from wine_logging import trace_headers

logger = logging.getLogger("remote")


class ServiceUnavailable(RuntimeError):
    """Сервис с моделью не ответил: упал, перезапускается или ещё грузит модель."""

    def __init__(self, service: str, reason: str) -> None:
        super().__init__(f"{service}: {reason}")
        self.service = service
        self.reason = reason


class EmbedderUnavailable(ServiceUnavailable):
    pass


class QdrantUnavailable(ServiceUnavailable):
    pass


class InvalidImageError(Exception):
    """Загруженный файл не является изображением."""


class Breaker:
    """Предохранитель: после сбоя не дёргать сервис `cooldown_s` секунд.

    Состояние пишется в лог только при переходах «работает → недоступен →
    снова работает» — не на каждом запросе.
    """

    def __init__(self, service: str, cooldown_s: float) -> None:
        self.service = service
        self.cooldown_s = cooldown_s
        self._open_until = 0.0
        self._down_since: float | None = None
        self._reason = ""
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return time.monotonic() >= self._open_until

    @property
    def state(self) -> str:
        return "ok" if self._down_since is None else f"недоступен: {self._reason}"

    def check(self, error: type[ServiceUnavailable] = ServiceUnavailable) -> None:
        if not self.available:
            raise error(self.service, self._reason or "недоступен")

    def failure(self, reason: str) -> None:
        with self._lock:
            self._open_until = time.monotonic() + self.cooldown_s
            self._reason = reason
            if self._down_since is None:
                self._down_since = time.monotonic()
                logger.error("%s недоступен (%s) — пробую снова через %.0f с",
                             self.service, reason, self.cooldown_s)

    def success(self) -> None:
        with self._lock:
            if self._down_since is not None:
                logger.warning("%s снова доступен (не отвечал %.0f с)",
                               self.service, time.monotonic() - self._down_since)
            self._down_since = None
            self._open_until = 0.0


def describe_error(exc: Exception) -> str:
    """Короткая причина для лога: «нет соединения», «таймаут 30 с», «HTTP 500»."""
    if isinstance(exc, httpx.ConnectError):
        return "нет соединения"
    if isinstance(exc, httpx.TimeoutException):
        return "таймаут"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return f"{type(exc).__name__}: {exc}"[:200]


class EmbedderClient:
    """Фото → вектор SigLIP 2 в сервисе embedder."""

    def __init__(self, url: str, *, timeout_s: float, connect_timeout_s: float, cooldown_s: float) -> None:
        self.url = url.rstrip("/")
        # trust_env=False: исходящий трафик машины может идти через прокси, а сервис — локальный
        self._http = httpx.AsyncClient(
            base_url=self.url,
            timeout=httpx.Timeout(timeout_s, connect=connect_timeout_s),
            trust_env=False,
            limits=httpx.Limits(max_connections=32),
        )
        self.breaker = Breaker("embedder", cooldown_s)

    async def embed(self, raw: bytes, filename: str = "photo") -> tuple[list[float], float]:
        """Вектор и время модели, мс. Не изображение — InvalidImageError."""
        self.breaker.check(EmbedderUnavailable)
        try:
            response = await self._http.post(
                "/v1/embed", files={"image": (filename, raw)}, headers=trace_headers()
            )
            if response.status_code == 400:
                self.breaker.success()
                raise InvalidImageError(response.json().get("detail", "не изображение"))
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self.breaker.failure(describe_error(exc))
            raise EmbedderUnavailable("embedder", describe_error(exc)) from exc
        self.breaker.success()
        payload = response.json()
        return payload["vector"], payload["encode_ms"]

    async def health(self) -> dict | None:
        try:
            response = await self._http.get("/health", timeout=2)
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        return response.json()

    async def close(self) -> None:
        await self._http.aclose()
