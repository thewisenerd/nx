from pathlib import Path

import pytest
from click.testing import CliRunner

from nx.cli import nx


def _invoke_verify(root: Path, *options: str):
    return CliRunner().invoke(nx, [*options, "-C", str(root), "verify"])


def test_default_log_level_suppresses_debug(tmp_path: Path) -> None:
    result = _invoke_verify(tmp_path)

    assert result.exit_code == 0, result.output
    assert "[debug" not in result.output


@pytest.mark.parametrize(
    "options",
    [
        ("--log-level", "DEBUG"),
        ("-X",),
        ("--log-level", "CRITICAL", "--debug"),
    ],
)
def test_debug_logging_can_be_enabled(tmp_path: Path, options: tuple[str, ...]) -> None:
    result = _invoke_verify(tmp_path, *options)

    assert result.exit_code == 0, result.output
    assert "[debug" in result.output
