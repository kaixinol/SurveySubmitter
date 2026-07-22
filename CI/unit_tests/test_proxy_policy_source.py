from __future__ import annotations

import survey_submitter.network.proxy.source as proxy_source
from survey_submitter.providers.common import (
    SURVEY_PROVIDER_WJX,
)


class ProxyPolicySourceTests:
    def test_normalize_proxy_source_falls_back_to_custom_for_invalid_value(self) -> None:
        assert proxy_source.normalize_proxy_source("bad-source") == proxy_source.PROXY_SOURCE_CUSTOM
        assert proxy_source.normalize_proxy_source(None) == proxy_source.PROXY_SOURCE_CUSTOM

    def test_set_and_get_proxy_source_round_trip(self) -> None:
        original = proxy_source.get_proxy_source()
        try:
            proxy_source.set_proxy_source(proxy_source.PROXY_SOURCE_CUSTOM)
            assert proxy_source.get_proxy_source() == proxy_source.PROXY_SOURCE_CUSTOM
        finally:
            proxy_source.set_proxy_source(original)

    def test_proxy_source_mode_helpers(self) -> None:
        assert proxy_source.is_custom_proxy_source(proxy_source.PROXY_SOURCE_CUSTOM)
        assert proxy_source.is_custom_proxy_source(proxy_source.PROXY_SOURCE_CUSTOM)

    def test_answer_duration_mapping(self) -> None:
        assert (
            proxy_source.get_proxy_ttl_for_answer_duration(100)
            == 100 + proxy_source.PROXY_TTL_GRACE_SECONDS
        )
        assert proxy_source.get_proxy_minute_by_answer_seconds(10) == 1
        assert (
            proxy_source.get_proxy_minute_by_answer_seconds(250, provider=SURVEY_PROVIDER_WJX) == 1
        )

    def test_set_proxy_occupy_minute_by_answer_duration_uses_max_seconds(self) -> None:
        original = proxy_source.get_proxy_occupy_minute()
        try:
            minute = proxy_source.set_proxy_occupy_minute_by_answer_duration(
                (30, 280),
                provider=SURVEY_PROVIDER_WJX,
            )
            assert minute == 1
            assert proxy_source.get_proxy_occupy_minute() == 1
        finally:
            proxy_source._proxy_occupy_minute = original

    def test_validate_and_effective_proxy_api_url(self) -> None:
        original_source = proxy_source.get_proxy_source()
        original_override = proxy_source.get_custom_proxy_api_override()
        try:
            proxy_source.set_proxy_source(proxy_source.PROXY_SOURCE_CUSTOM)
            assert (
                proxy_source.set_proxy_api_override("https://proxy.example/api")
                == "https://proxy.example/api"
            )
            assert proxy_source.get_custom_proxy_api_override() == "https://proxy.example/api"
            assert proxy_source.has_custom_proxy_api_override()
            assert proxy_source.is_custom_proxy_source()
        finally:
            proxy_source.set_proxy_source(original_source)
            proxy_source.set_proxy_api_override(original_override)

    def test_set_proxy_api_override_rejects_invalid_scheme(self) -> None:
        try:
            proxy_source.set_proxy_api_override("ftp://bad.example")
        except ValueError as exc:
            assert "http://" in str(exc)
        else:
            raise AssertionError("expected ValueError for invalid proxy API scheme")

    def test_area_code_helpers(self) -> None:
        original = proxy_source.get_proxy_area_code()
        try:
            assert proxy_source.set_proxy_area_code("110000") == "110000"
            assert proxy_source.get_default_proxy_area_code() == "110000"
            assert proxy_source.get_proxy_area_code() == "110000"
            assert proxy_source._resolve_default_pool("110000") == proxy_source.PROXY_POOL_QUALITY
            assert proxy_source._resolve_default_pool("110100") == proxy_source.PROXY_POOL_QUALITY
            assert proxy_source.set_proxy_area_code(None) is None
            assert proxy_source.get_proxy_area_code() is None
        finally:
            proxy_source.set_proxy_area_code(original)
