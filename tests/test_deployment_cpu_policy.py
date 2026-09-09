"""Static deployment policy checks, not a Compose parser or container qualification."""

import re
from pathlib import Path

DEPLOYMENT = Path(__file__).resolve().parents[1] / "deployment"


def setting(text, key):
    matches = re.findall(rf"^    {re.escape(key)}: (.+)$", text, re.MULTILINE)
    assert len(matches) == 1, f"expected one explicit {key}"
    return matches[0]


def test_default_memory_and_total_memory_swap_limits_match():
    text = (DEPLOYMENT / "compose.yaml").read_text()
    assert setting(text, "mem_limit") == "${DOCUMENT_FILES_MEMORY_LIMIT:-16g}"
    assert setting(text, "memswap_limit") == setting(text, "mem_limit")
    assert setting(text, "pull_policy") == "never"
    assert setting(text, "read_only") == "true"
    assert not re.search(r"^    (?:gpus|devices|privileged):", text, re.MULTILINE)


def test_qualification_requires_exact_16_gib_without_swap_or_network_devices():
    text = (DEPLOYMENT / "compose.cpu-qualification.yaml").read_text()
    assert setting(text, "mem_limit") == str(16 * 1024**3)
    assert setting(text, "memswap_limit") == setting(text, "mem_limit")
    assert setting(text, "runtime") == "runc"
    assert setting(text, "network_mode") == "none"
    for key in ("networks", "ports", "devices"):
        assert setting(text, key) == "!reset []"
    assert "      NVIDIA_VISIBLE_DEVICES: void" in text
    assert not re.search(r"^    (?:gpus|privileged):", text, re.MULTILINE)
