"""Output formatting utilities for consistent CLI output."""

import json
from enum import Enum
from typing import Any


class OutputFormat(Enum):
    """Output format enumeration."""

    JSON = "json"
    TEXT = "text"


def get_by_path(obj: Any, path: str) -> Any:
    """Get a value from an object by dot-separated path."""
    if not path:
        return obj

    current = obj
    parts = path.split(".")

    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return None

        if current is None:
            return None

    return current


def output(
    value: dict[str, Any] | Any = None,
    format_type: OutputFormat = OutputFormat.TEXT,
    filter_path: str | None = None,
) -> None:
    """
    Output a value in the specified format.

    Args:
        value: The value to output
        format_type: The output format (JSON or TEXT)
        filter_path: Optional dot-separated path to filter the output
    """
    if value is None:
        value = {}

    if format_type == OutputFormat.JSON:
        # Apply filter if specified
        if filter_path:
            # Remove leading dot if present
            clean_path = filter_path.lstrip(".")
            filtered_value = get_by_path(value, clean_path)
        else:
            filtered_value = value

        # Output based on type
        if isinstance(filtered_value, str):
            # Print raw string without quotes for string values
            print(filtered_value)
        else:
            # Print other types as JSON
            print(json.dumps(filtered_value, indent=2, default=str))
    else:
        # Default to JSON format for text output as well
        print(json.dumps(value, indent=2, default=str))


def output_json(value: Any, indent: int = 2) -> None:
    """Output a value as formatted JSON."""
    print(json.dumps(value, indent=indent, default=str))


def output_table(data: list, headers: list | None = None, show_headers: bool = True) -> None:
    """
    Output data as a simple table.

    Args:
        data: List of dictionaries or lists representing rows
        headers: Optional list of headers
        show_headers: Whether to show headers
    """
    if not data:
        return

    # Convert data to list of lists if needed
    if isinstance(data[0], dict):
        if headers is None:
            headers = list(data[0].keys())
        rows = [[str(row.get(header, "")) for header in headers] for row in data]
    else:
        rows = [[str(cell) for cell in row] for row in data]

    if headers and show_headers:
        rows.insert(0, [str(header) for header in headers])

    # Calculate column widths
    if not rows:
        return

    col_widths = [max(len(row[i]) if i < len(row) else 0 for row in rows) for i in range(max(len(row) for row in rows))]

    # Print rows
    for i, row in enumerate(rows):
        formatted_row = []
        for j, cell in enumerate(row):
            width = col_widths[j] if j < len(col_widths) else 0
            formatted_row.append(cell.ljust(width))

        print("  ".join(formatted_row))

        # Print separator after headers
        if i == 0 and headers and show_headers:
            separator = "  ".join("-" * width for width in col_widths)
            print(separator)


def output_list(items: list, bullet: str = "•") -> None:
    """Output a list of items with bullets."""
    for item in items:
        print(f"{bullet} {item}")


def output_key_value(data: dict[str, Any], separator: str = ": ") -> None:
    """Output key-value pairs."""
    for key, value in data.items():
        print(f"{key}{separator}{value}")


def format_size(size_bytes: int) -> str:
    """Format a size in bytes to human readable format."""
    if size_bytes == 0:
        return "0 B"

    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)

    while size >= 1024.0 and i < len(size_names) - 1:
        size /= 1024.0
        i += 1

    return f"{size:.1f} {size_names[i]}"


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human readable format."""
    if seconds < 60:
        return f"{seconds:.1f}s"

    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.1f}m"

    hours = minutes / 60
    if hours < 24:
        return f"{hours:.1f}h"

    days = hours / 24
    return f"{days:.1f}d"


def truncate_string(text: str, max_length: int, suffix: str = "...") -> str:
    """Truncate a string to a maximum length."""
    if len(text) <= max_length:
        return text

    return text[: max_length - len(suffix)] + suffix


def colorize(text: str, color: str) -> str:
    """
    Colorize text for terminal output.

    Basic ANSI color support. Colors: red, green, yellow, blue, magenta, cyan, white
    """
    colors = {
        "red": "\033[31m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "blue": "\033[34m",
        "magenta": "\033[35m",
        "cyan": "\033[36m",
        "white": "\033[37m",
        "reset": "\033[0m",
    }

    color_code = colors.get(color.lower(), "")
    reset_code = colors["reset"]

    return f"{color_code}{text}{reset_code}" if color_code else text


def success(message: str) -> None:
    """Print a success message."""
    print(colorize(f"✓ {message}", "green"))


def error(message: str) -> None:
    """Print an error message."""
    print(colorize(f"✗ {message}", "red"))


def warning(message: str) -> None:
    """Print a warning message."""
    print(colorize(f"⚠ {message}", "yellow"))


def info(message: str) -> None:
    """Print an info message."""
    print(colorize(f"ℹ {message}", "blue"))


def progress(message: str) -> None:
    """Print a progress message."""
    print(colorize(f"⋯ {message}", "cyan"))
