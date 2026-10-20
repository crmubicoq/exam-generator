"""jinja2 프롬프트 렌더링 (tsbookmaker prompt_loader의 str.format 중괄호 충돌 문제를 피한다)."""
from __future__ import annotations

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ..config import PROMPTS_DIR

_env = Environment(loader=FileSystemLoader(str(PROMPTS_DIR)), undefined=StrictUndefined,
                   trim_blocks=False, lstrip_blocks=False, keep_trailing_newline=True)


def render(name: str, **kw) -> str:
    return _env.get_template(name).render(**kw)
