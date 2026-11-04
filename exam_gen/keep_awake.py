"""출제 중 윈도우 자동 절전 막기 — 출제가 돌아가는 스레드에서 켜고, 끝나면 끈다.

SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)는 '사용자가 아무것도 안 해서' 들어가는 절전만 막는다.
화면 끄기는 막지 않는다(전기 절약). 노트북 덮개 닫기·직접 절전·종료는 막을 수 없다.
윈도우가 아니거나 호출에 실패해도 출제에는 영향이 없다.
"""
from __future__ import annotations

import contextlib
import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _set(flags: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception:  # noqa: BLE001
        return False


@contextlib.contextmanager
def keep_awake():
    """with keep_awake(): ... — 블록 안에서 자동 절전을 막는다(같은 스레드에서 써야 한다)."""
    ok = _set(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        yield ok
    finally:
        if ok:
            _set(ES_CONTINUOUS)
