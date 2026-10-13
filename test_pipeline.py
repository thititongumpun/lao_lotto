"""Self-check for the subprocess pipeline contract: the result line emitted by
pipeline.py must round-trip through content_router._extract_result, and plain
log lines must be ignored. Run: python test_pipeline.py"""

import json
import tempfile
from pathlib import Path

from pipeline import RESULT_SENTINEL
from content_router import _extract_result


def test_round_trip():
    payload = {"status": "ok", "facebook": "ok", "predict": {"txt": "x"}}
    line = RESULT_SENTINEL + json.dumps(payload, ensure_ascii=False)
    assert _extract_result(line) == payload


def test_log_lines_ignored():
    assert _extract_result("[PIPELINE] Uploading to YouTube…") is None
    assert _extract_result("") is None


def test_garbage_after_sentinel_is_safe():
    assert _extract_result(RESULT_SENTINEL + "not json") is None


def test_cleanup_keeps_output_dir():
    from pipeline import _cleanup_pipeline_files
    d = Path(tempfile.mkdtemp()) / "out"
    (d / "sub").mkdir(parents=True)
    (d / "a.png").write_text("x")
    (d / "sub" / "b.png").write_text("x")
    _cleanup_pipeline_files({}, output_dir=str(d))
    assert d.is_dir() and not any(d.iterdir())


if __name__ == "__main__":
    test_round_trip()
    test_log_lines_ignored()
    test_garbage_after_sentinel_is_safe()
    test_cleanup_keeps_output_dir()
    print("ok")
