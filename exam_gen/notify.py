"""출제 완료·중단 알림 — 300문항은 2시간 넘게 걸리므로 끝나거나 멈추면 윈도우 알림을 띄운다.

별도 패키지 없이 PowerShell의 윈도우 토스트 알림을 쓴다. 실패해도(다른 OS, 알림 꺼짐) 출제에는 영향이 없다.
"""
from __future__ import annotations

import os
import subprocess
import sys

_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def _ps_quote(s: str) -> str:
    return s.replace("'", "''").replace("<", "＜").replace(">", "＞").replace("&", "＆")


def toast(title: str, message: str) -> bool:
    if sys.platform != "win32" or os.environ.get("EXAM_GEN_NO_NOTIFY"):
        return False
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$x.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{_ps_quote(title)}</text>"
        f"<text>{_ps_quote(message)}</text></binding></visual></toast>');"
        "$t = New-Object Windows.UI.Notifications.ToastNotification $x;"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{_APP_ID}').Show($t)")
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except Exception:  # noqa: BLE001
        return False


STATUS_KO = {"done": "출제 완료", "partial": "부분 완료 — '이어서 출제'로 남은 문항을 계속할 수 있습니다",
             "error": "오류로 멈춤 — 출제 진행 화면에서 원인을 확인하세요", "stopped": "중지됨"}


def batch_finished(project_name: str, batch: int, prog: dict):
    st = prog.get("status")
    if st not in STATUS_KO:
        return
    msg = f"{STATUS_KO[st]} · 통과 {prog.get('accepted', 0)} · 수동확인 {prog.get('manual', 0)}"
    toast(f"평가문항 출제 — {project_name} (배치 {batch})", msg)
