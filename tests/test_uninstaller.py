import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from uninstall import get_data_dir, format_size, get_dir_size


def test_get_data_dir_returns_path():
    p = get_data_dir()
    assert isinstance(p, Path)
    assert "OmatKryptoLompakot" in str(p) or "omatkryptolompakot" in str(p)


def test_format_size():
    assert format_size(500) == "500.0 B"
    assert format_size(1024) == "1.0 KB"
    assert format_size(1024 * 1024 * 4) == "4.0 MB"


def test_get_dir_size_with_tempdir():
    with tempfile.TemporaryDirectory() as tmpdir:
        tp = Path(tmpdir)
        (tp / "test.txt").write_bytes(b"A" * 1000)
        assert get_dir_size(tp) == 1000


def test_uninstall_dry_run_subprocess():
    res = subprocess.run(
        [sys.executable, "uninstall.py", "--dry-run"],
        capture_output=True,
        text=True
    )
    assert res.returncode == 0
    assert "[INFO] --dry-run valittu" in res.stdout
