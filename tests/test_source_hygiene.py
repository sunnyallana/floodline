"""Guards against a failure seen twice in this repo: shell quoting turned regex word boundaries into backspace
characters (\\x08), which silently made the patterns never match."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {"\n", "\t", "\r"}


def test_no_control_characters_in_source():
    offenders = []
    for path in [*ROOT.joinpath("src").rglob("*.py"), *ROOT.joinpath("tests").rglob("*.py")]:
        text = path.read_text(encoding="utf-8")
        bad = sorted({hex(ord(c)) for c in text if ord(c) < 32 and c not in ALLOWED})
        if bad:
            offenders.append(f"{path.relative_to(ROOT)}: {', '.join(bad)}")
    assert not offenders, "control characters in source:\n" + "\n".join(offenders)
