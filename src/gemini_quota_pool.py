from __future__ import annotations

import logging
import threading

from google import genai
from google.genai import types

logger = logging.getLogger("debug_agent_mvp.quota_pool")


class DynamicFreeTierModelPool:
    _instance: DynamicFreeTierModelPool | None = None
    _lock: threading.Lock = threading.Lock()

    def __init__(self, api_key: str | None = None) -> None:
        self.client = genai.Client(api_key=api_key)
        self.models: list[str] = []
        self.current_index: int = 0
        self.exhausted_models: set[str] = set()
        self._refresh_pool()

    def _refresh_pool(self) -> None:
        with self._lock:
            try:
                raw_models = self.client.models.list()
                candidate_models: list[str] = []
                for m in raw_models:
                    name = getattr(m, "name", "")
                    supported_actions = getattr(m, "supported_generation_methods", [])
                    if "generateContent" in supported_actions or "gemini" in name.lower():
                        clean_name = name.split("/")[-1]
                        candidate_models.append(clean_name)

                if not candidate_models:
                    candidate_models = ["gemini-1.5-pro", "gemini-1.5-flash"]

                scored: list[tuple[int, str]] = []
                for model in candidate_models:
                    score = 0
                    lower_m = model.lower()
                    if "pro" in lower_m:
                        score += 100
                    if "1.5" in lower_m:
                        score += 50
                    if "flash" in lower_m:
                        score += 10
                    if "lite" in lower_m:
                        score -= 20
                    scored.append((score, model))

                scored.sort(key=lambda x: x[0], reverse=True)
                self.models = [m for _, m in scored]
                logger.info("Discovered and ranked Gemini models: %s", self.models)
            except Exception as exc:
                logger.warning(
                    "Failed to dynamically discover models (%s). Using fallback queue.", exc
                )
                self.models = ["gemini-1.5-pro", "gemini-1.5-flash"]

    def get_active_model(self) -> str:
        with self._lock:
            if not self.models:
                self._refresh_pool()

            attempts = len(self.models)
            for _ in range(attempts):
                model = self.models[self.current_index]
                if model not in self.exhausted_models:
                    return model
                self.current_index = (self.current_index + 1) % len(self.models)

            logger.warning("All models marked exhausted. Resetting quota rotation pool.")
            self.exhausted_models.clear()
            return self.models[0]

    def report_exhaustion(self, model_name: str) -> None:
        with self._lock:
            logger.warning("Model %s hit quota saturation. Rotating...", model_name)
            self.exhausted_models.add(model_name)
            self.current_index = (self.current_index + 1) % len(self.models)

    def synthetic_probe(self, model_name: str) -> bool:
        try:
            response = self.client.models.generate_content(
                model=model_name,
                contents="System probe check.",
                config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=5),
            )
            return response.text is not None
        except Exception as exc:
            logger.error("Synthetic probe failed for model %s: %s", model_name, exc)
            return False
