"""exo: generic mechanism for a personal AI system. No principal data lives here."""

__version__ = "0.2.0"

# One codename per MAJOR version, from the exopet evolution line (docs/VERSIONING.md).
CODENAMES = ("egg", "cell", "tile", "cube", "frame")


def codename(version: str = __version__) -> str:
    """The codename for a version's MAJOR: egg, cell, tile, cube, frame, then 4D, 5D and so on."""
    major = int(version.split(".")[0])
    return CODENAMES[major] if major < len(CODENAMES) else f"{major}D"


def display_version(version: str = __version__) -> str:
    """`0.2.0 (egg)`, the form shown to people."""
    return f"{version} ({codename(version)})"
