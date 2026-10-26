import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "input_17-43.pdf"
_TMP = Path(tempfile.mkdtemp(prefix="examgen_test_"))
os.environ["EXAM_GEN_PROJECTS"] = str(_TMP / "projects")
os.environ["EXAM_GEN_NO_NOTIFY"] = "1"  # 테스트 중 윈도우 알림을 띄우지 않는다
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def sample_project():
    if not SAMPLE.exists():
        pytest.skip("샘플 원고 input_17-43.pdf 없음")
    from exam_gen.store import Project
    p = Project.create("테스트 원고")
    p.add_source(SAMPLE)
    p.set_page_map("s1", 17, "printed", True)
    yield p


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)
