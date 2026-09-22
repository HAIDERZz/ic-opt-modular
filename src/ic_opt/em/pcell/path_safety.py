from __future__ import annotations

from pathlib import PureWindowsPath


def validate_output_file_name(value: str, field_name: str) -> str:
    if value in {"", ".", ".."}:
        raise ValueError(f"{field_name} must be a file name")
    if "/" in value or "\\" in value:
        raise ValueError(f"{field_name} must be a file name")
    if PureWindowsPath(value).drive:
        raise ValueError(f"{field_name} must be a file name")
    return value
