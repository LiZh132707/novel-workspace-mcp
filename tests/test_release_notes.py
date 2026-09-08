import pytest

from scripts.release_notes import extract_release_notes


def test_release_notes_exclude_plans_other_versions_and_reference_footer():
    text = "# Changelog\n\n## [Unreleased]\nFuture\n## [2.8.0] - 2026-09-08\n### Added\nNew feature\n## [2.7.0]\nOld feature\n[2.7.0]: https://example.test\n"
    assert extract_release_notes(text, "v2.8.0") == "## 2.8.0\n\n### Added\nNew feature\n"
    assert extract_release_notes(text, "2.7.0") == "## 2.7.0\n\nOld feature\n"


@pytest.mark.parametrize("text,version", [
    ("## [2.8.0]\nHello", "Unreleased"),
    ("## [2.8.0]\nHello", "2.8.1"),
    ("## [2.8.0]\nHello\n## [2.8.0]\nDuplicate", "2.8.0"),
    ("## [2.8.0]\n", "2.8.0"),
])
def test_release_notes_fail_closed(text, version):
    with pytest.raises(ValueError):
        extract_release_notes(text, version)
