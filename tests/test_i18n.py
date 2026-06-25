"""Verify all languages have the same keys as English."""

import re
from pathlib import Path


def test_all_languages_complete():
    """Verify all languages define the same set of keys."""
    content = (Path(__file__).parent.parent / "src/photofetch/static/i18n.js").read_text(encoding="utf-8")
    # Extract keys per language by parsing key:"value" at block level
    # Keys are always word chars immediately followed by :" at the start or after ,
    langs = {}
    for m in re.finditer(r'(\w{2}):\{', content):
        code = m.group(1)
        start = m.end()
        # Find matching } — skip content inside quotes
        depth = 1
        i = start
        while i < len(content) and depth > 0:
            if content[i] == '"':
                i = content.index('"', i + 1) + 1
                continue
            if content[i] == '{':
                depth += 1
            elif content[i] == '}':
                depth -= 1
            i += 1
        block = content[start:i - 1]
        # Keys are at the start of key:"..." pairs
        keys = set(re.findall(r'(?:^|,)(\w+):"', block))
        langs[code] = keys

    en_keys = langs.get("en", set())
    assert len(en_keys) > 20, f"EN only has {len(en_keys)} keys"

    for code, keys in langs.items():
        missing = en_keys - keys
        assert not missing, f"{code} missing keys: {sorted(missing)}"
        extra = keys - en_keys
        assert not extra, f"{code} has extra keys: {sorted(extra)}"
