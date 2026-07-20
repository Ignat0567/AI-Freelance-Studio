"""Point frozen certifi consumers at the extension-neutral public CA trust store."""

from pathlib import Path
import sys

import certifi
import certifi.core


bundle = Path(sys._MEIPASS) / "certifi" / "cacert.bundle"


def where() -> str:
    return str(bundle)


def contents() -> str:
    return bundle.read_text(encoding="ascii")


certifi.where = where
certifi.contents = contents
certifi.core.where = where
certifi.core.contents = contents
