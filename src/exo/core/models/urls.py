"""Display-only provider URLs. Connection code keeps the configured endpoint unchanged."""

from urllib.parse import urlsplit, urlunsplit


def display_url(value: object) -> str:
    """Keep a URL's shape but replace its entire userinfo. Invalid endpoints get no input echoed back.

    This protects authority credentials, not secrets someone puts in the path, query or fragment.
    Never use the returned URL for a connection.
    """
    if value == "":
        return ""
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        return "its configured address"
    try:
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.netloc or not parsed.hostname or \
                "\\" in parsed.netloc or parsed.netloc.endswith(":"):
            return "its configured address"
        _ = parsed.port  # validate numeric range and reject an unbracketed IPv6 authority
    except ValueError:
        return "its configured address"
    if "@" not in parsed.netloc:
        return value
    authority = "***@" + parsed.netloc.rsplit("@", 1)[1]
    return urlunsplit((parsed.scheme, authority, parsed.path, parsed.query, parsed.fragment))
