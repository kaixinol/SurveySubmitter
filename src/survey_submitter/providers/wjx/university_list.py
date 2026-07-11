from __future__ import annotations

import json
import os
import random

_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "assets")


class UniversityList:
    _instance: UniversityList | None = None

    def __init__(self) -> None:
        self._entries: list[list[str]] = self._load()

    @classmethod
    def get(cls) -> UniversityList:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def _load() -> list[list[str]]:
        path = os.path.join(_ASSETS_DIR, "university_list.json")
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)

    def sample_name(self) -> str:
        if not self._entries:
            return "北京大学"
        entry = random.choice(self._entries)
        return entry[1] if len(entry) >= 2 else str(entry[0])

    def names_by_province(self, province: str) -> list[str]:
        return [e[1] for e in self._entries if len(e) >= 2 and e[0] == province]

    @staticmethod
    def is_university_verify(verify_type: str) -> bool:
        return "高校" in str(verify_type or "")


_university_list: UniversityList | None = None


def get_university_list() -> UniversityList:
    return UniversityList.get()


def sample_university_name() -> str:
    return get_university_list().sample_name()


__all__ = [
    "UniversityList",
    "get_university_list",
    "sample_university_name",
]
