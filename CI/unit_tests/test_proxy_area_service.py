from __future__ import annotations

from survey_submitter.network.proxy import areas as service


def _reset_area_caches() -> None:
    service._AREA_CODES_CACHE = None
    service._SUPPORTED_CODES_CACHE = None
    service._SUPPORTED_AREA_LIST_CACHE = None
    service._SUPPORTED_AREA_CITY_CODE_INDEX_CACHE = None


class ProxyAreaServiceTests:
    @staticmethod
    def _fake_area_codes_for_supported_area(supported_only: bool = False):
        assert supported_only is False
        return [
            {
                "code": "440000",
                "name": "广东省",
                "cities": [{"code": "440100", "name": "广州市"}],
            },
            {
                "code": "110000",
                "name": "北京市",
                "cities": [{"code": "110100", "name": "市辖区"}],
            },
        ]

    def test_load_supported_area_codes_ignores_comments_and_tracks_all(self, patch_attrs) -> None:
        _reset_area_caches()
        patch_attrs(
            (
                service,
                "_read_asset_text",
                lambda name: (
                    "北京 110100\n# 注释\nbad-line\n全部 all\n上海 310100\n"
                    if name == "area.txt"
                    else ""
                ),
            )
        )

        codes, has_all = service.load_supported_area_codes()

        assert codes == {"110100", "310100"}
        assert has_all is True

    def test_load_area_codes_filters_supported_cities(self, patch_attrs) -> None:
        _reset_area_caches()
        payload = {
            "provinces": [
                {
                    "code": "110000",
                    "name": "北京市",
                    "cities": [
                        {"code": "110100", "name": "市辖区"},
                        {"code": "110200", "name": "不支持"},
                    ],
                },
                {
                    "code": "310000",
                    "name": "上海市",
                    "cities": [{"code": "310100", "name": "市辖区"}],
                },
            ]
        }
        patch_attrs(
            (
                service,
                "_read_asset_text",
                lambda name: (
                    '{"provinces":[{"code":"110000","name":"北京市","cities":[{"code":"110100","name":"市辖区"},{"code":"110200","name":"不支持"}]},{"code":"310000","name":"上海市","cities":[{"code":"310100","name":"市辖区"}]}]}'
                    if name == "area_codes_2022.json"
                    else "北京 110100\n"
                ),
            )
        )

        all_areas = service.load_area_codes(supported_only=False)
        supported = service.load_area_codes(supported_only=True)

        assert all_areas == payload["provinces"]
        assert supported == [
            {
                "code": "110000",
                "name": "北京市",
                "cities": [{"code": "110100", "name": "市辖区"}],
            }
        ]

    def test_supported_area_cache_uses_local_codes(self, patch_attrs) -> None:
        _reset_area_caches()
        patch_attrs(
            (
                service,
                "load_supported_area_codes",
                lambda: ({"440100", "110100"}, False),
            ),
            (
                service,
                "load_area_codes",
                self._fake_area_codes_for_supported_area,
            ),
        )

        assert service.resolve_proxy_area_for_source("default", "440100") == "440100"
        assert service.resolve_proxy_area_for_source("supported_area", "440100") == "广州"
        assert service.resolve_proxy_area_for_source("supported_area", "bad") == ""
        assert service.build_supported_area_city_code_index() == {"440100": "广州", "110100": "北京"}
