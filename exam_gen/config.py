"""경로와 출제 기준(criteria.yaml) 로딩."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

PKG_DIR = Path(__file__).resolve().parent
ROOT_DIR = PKG_DIR.parent
PROJECTS_DIR = Path(os.environ.get("EXAM_GEN_PROJECTS", ROOT_DIR / "projects"))
DATA_DIR = ROOT_DIR / "data"
TEMPLATE_XLSX = PKG_DIR / "templates" / "excel_template.xlsx"
PROMPTS_DIR = PKG_DIR / "llm" / "prompts"
PROMPT_VERSION = "2026-10-26.1"


@lru_cache(maxsize=1)
def criteria() -> dict:
    with open(PKG_DIR / "spec" / "criteria.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def type_name(t: str) -> str:
    return criteria()["type_names"][t]
