"""Load the API key (and other tunables) from an untracked local `.env`.

The OpenAI SDK reads ``OPENAI_API_KEY`` from the environment. Setting it per
shell session works but is easy to forget and easy to leak into a transcript;
a gitignored file next to the code is the usual answer. No dependency for
this - the format we need is ``KEY=VALUE``, and python-dotenv is a lot of
surface area to take on for that.

The real environment always wins over the file, so CI and a one-off
``$env:OPENAI_API_KEY = "..."`` still override without editing anything.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import config

ENV_FILE = config.REPO_ROOT / ".env"
EXAMPLE_FILE = config.REPO_ROOT / ".env.example"


def parse(text: str) -> dict[str, str]:
    """Parse `.env` text. Ignores blanks, comments and unparseable lines."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):  # tolerate a shell-sourceable file
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            continue
        value = value.strip()
        # Strip one layer of matching quotes; a key pasted from a dashboard
        # often arrives wrapped, and a stray quote in a secret is an auth
        # error two stack frames away from anything that names this file.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def is_placeholder(value: str | None) -> bool:
    """True if this is the template's stand-in rather than a real key.

    Copying .env.example and forgetting to edit it is the obvious first
    mistake, and without this check it surfaces as a 401 from inside the SDK
    that echoes the placeholder back - which reads like a key problem rather
    than a "you did not fill in the file" problem.
    """
    if value is None:
        return True
    stripped = value.strip()
    if not stripped:
        return True
    # The example file ships "sk-...". Strip the trailing dots and hyphen and
    # nothing but the prefix is left. A <bracketed> value is a template too.
    return stripped.rstrip(".-") in ("sk", "") or stripped.startswith("<")


def load(path: Path | None = None, *, override: bool = False) -> list[str]:
    """Load `path` (default: repo-root `.env`) into os.environ.

    Returns the names of the keys it set. Missing file is not an error - the
    file is optional, and the environment may already carry the key.
    """
    path = ENV_FILE if path is None else path
    if not path.is_file():
        return []
    # utf-8-sig: Notepad and PowerShell both write UTF-8 with a BOM, which
    # would otherwise become part of the first key's name.
    text = path.read_text(encoding="utf-8-sig")
    loaded = []
    for key, value in parse(text).items():
        if override or key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
