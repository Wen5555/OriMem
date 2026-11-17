"""User-authorized local knowledge content preservation, without masking.

Structural invalid characters are handled for Markdown/SQLite interoperability.
The separate privacy.sanitize helper remains available for engineering handoffs.
"""

import re


def local_content(value):
    if isinstance(value, str):
        return re.sub("[\ud800-\udfff\x00]", "", value).replace(
            "\ufffd", "[INVALID SOURCE CHARACTER]"
        )
    if isinstance(value, (list, tuple)):
        return [local_content(x) for x in value]
    if isinstance(value, dict):
        return {str(k): local_content(v) for k, v in value.items()}
    return value
