from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.gemini_quota_pool import DynamicFreeTierModelPool


@pytest.fixture
def mock_genai_client():
    with patch("src.gemini_quota_pool.genai.Client") as mock_client_cls:
        client_instance = MagicMock()
        mock_client_cls.return_value = client_instance
        yield client_instance


def test_init_discovers_and_scores_models(mock_genai_client: MagicMock) -> None:
    model_flash = MagicMock()
    model_flash.name = "models/gemini-1.5-flash"
    model_flash.supported_generation_methods = ["generateContent"]

    model_pro = MagicMock()
    model_pro.name = "models/gemini-1.5-pro"
    model_pro.supported_generation_methods = ["generateContent"]

    model_lite = MagicMock()
    model_lite.name = "models/gemini-1.5-flash-lite"
    model_lite.supported_generation_methods = ["generateContent"]

    mock_genai_client.models.list.return_value = [model_flash, model_pro, model_lite]

    pool = DynamicFreeTierModelPool(api_key="test-key")

    assert pool.models == [
        "gemini-1.5-pro",
        "gemini-1.5-flash",
        "gemini-1.5-flash-lite",
    ]
    assert pool.get_active_model() == "gemini-1.5-pro"


def test_init_discovery_failure_uses_fallback(mock_genai_client: MagicMock) -> None:
    mock_genai_client.models.list.side_effect = RuntimeError("API unreachable")

    pool = DynamicFreeTierModelPool(api_key="test-key")

    assert pool.models == ["gemini-1.5-pro", "gemini-1.5-flash"]
    assert pool.get_active_model() == "gemini-1.5-pro"


def test_init_empty_candidates_uses_fallback(mock_genai_client: MagicMock) -> None:
    model_unsupported = MagicMock()
    model_unsupported.name = "models/text-embedding-004"
    model_unsupported.supported_generation_methods = ["embedContent"]

    mock_genai_client.models.list.return_value = [model_unsupported]

    pool = DynamicFreeTierModelPool(api_key="test-key")

    assert pool.models == ["gemini-1.5-pro", "gemini-1.5-flash"]


def test_report_exhaustion_advances_and_rotates_model(mock_genai_client: MagicMock) -> None:
    mock_genai_client.models.list.side_effect = RuntimeError("Use fallback")
    pool = DynamicFreeTierModelPool()

    first_model = pool.get_active_model()
    assert first_model == "gemini-1.5-pro"

    pool.report_exhaustion("gemini-1.5-pro")

    assert "gemini-1.5-pro" in pool.exhausted_models
    second_model = pool.get_active_model()
    assert second_model == "gemini-1.5-flash"


def test_get_active_model_resets_pool_when_all_exhausted(mock_genai_client: MagicMock) -> None:
    mock_genai_client.models.list.side_effect = RuntimeError("Use fallback")
    pool = DynamicFreeTierModelPool()

    pool.report_exhaustion("gemini-1.5-pro")
    pool.report_exhaustion("gemini-1.5-flash")

    active_model = pool.get_active_model()
    assert active_model == "gemini-1.5-pro"
    assert len(pool.exhausted_models) == 0


def test_synthetic_probe_success(mock_genai_client: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.text = "OK"
    mock_genai_client.models.generate_content.return_value = mock_resp

    mock_genai_client.models.list.side_effect = RuntimeError("Use fallback")
    pool = DynamicFreeTierModelPool()

    assert pool.synthetic_probe("gemini-1.5-flash") is True
    mock_genai_client.models.generate_content.assert_called_once()


def test_synthetic_probe_failure(mock_genai_client: MagicMock) -> None:
    mock_genai_client.models.generate_content.side_effect = RuntimeError("Quota exhausted")

    mock_genai_client.models.list.side_effect = RuntimeError("Use fallback")
    pool = DynamicFreeTierModelPool()

    assert pool.synthetic_probe("gemini-1.5-flash") is False
