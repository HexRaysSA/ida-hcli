"""String processing utilities."""

import re


def abbreviate(text: str, max_length: int) -> str:
    """
    Abbreviate a string to a maximum length, preserving word boundaries.

    Args:
        text: The text to abbreviate
        max_length: Maximum length of the result

    Returns:
        Abbreviated string with "..." suffix if truncated
    """
    if len(text) <= max_length:
        return text

    # Trim to the maximum length without breaking words
    trimmed = text[:max_length].strip()

    # Find the last space to avoid breaking words
    last_space_index = trimmed.rfind(" ")
    if last_space_index > 0:
        return trimmed[:last_space_index] + "..."

    # If no spaces found, just cut at max length
    return trimmed + "..."


def get_email_domain(email: str) -> str:
    """
    Extract the domain from an email address.

    Args:
        email: Email address

    Returns:
        Domain part of the email (lowercase)
    """
    at_index = email.find("@")
    if at_index != -1:
        return email[at_index + 1 :].lower()
    return ""


def camel_to_snake(name: str) -> str:
    """
    Convert camelCase to snake_case.

    Args:
        name: String in camelCase

    Returns:
        String in snake_case
    """
    # Insert underscore before uppercase letters that follow lowercase letters
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    # Insert underscore before uppercase letters that follow lowercase letters or numbers
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def snake_to_camel(name: str, first_upper: bool = False) -> str:
    """
    Convert snake_case to camelCase.

    Args:
        name: String in snake_case
        first_upper: Whether to capitalize the first letter

    Returns:
        String in camelCase
    """
    components = name.split("_")
    if first_upper:
        return "".join(word.capitalize() for word in components)
    else:
        return components[0] + "".join(word.capitalize() for word in components[1:])


def kebab_to_snake(name: str) -> str:
    """
    Convert kebab-case to snake_case.

    Args:
        name: String in kebab-case

    Returns:
        String in snake_case
    """
    return name.replace("-", "_")


def snake_to_kebab(name: str) -> str:
    """
    Convert snake_case to kebab-case.

    Args:
        name: String in snake_case

    Returns:
        String in kebab-case
    """
    return name.replace("_", "-")


def title_case(text: str) -> str:
    """
    Convert text to title case, handling common exceptions.

    Args:
        text: Input text

    Returns:
        Text in title case
    """
    # Words that should not be capitalized in title case (except at the start)
    minor_words = {
        "a",
        "an",
        "and",
        "as",
        "at",
        "but",
        "by",
        "for",
        "if",
        "in",
        "nor",
        "of",
        "on",
        "or",
        "so",
        "the",
        "to",
        "up",
        "yet",
    }

    words = text.split()
    if not words:
        return text

    # Always capitalize first word
    result = [words[0].capitalize()]

    for word in words[1:]:
        if word.lower() in minor_words:
            result.append(word.lower())
        else:
            result.append(word.capitalize())

    return " ".join(result)


def slugify(text: str, separator: str = "-") -> str:
    """
    Convert text to a URL-friendly slug.

    Args:
        text: Input text
        separator: Character to use as separator

    Returns:
        Slugified text
    """
    # Convert to lowercase and replace non-alphanumeric characters
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", separator, text)
    # Remove leading/trailing separators
    text = text.strip(separator)
    return text


def truncate_middle(text: str, max_length: int, separator: str = "...") -> str:
    """
    Truncate text in the middle, keeping start and end.

    Args:
        text: Text to truncate
        max_length: Maximum length
        separator: Separator to use in the middle

    Returns:
        Truncated text
    """
    if len(text) <= max_length:
        return text

    if max_length <= len(separator):
        return separator[:max_length]

    # Calculate how much space we have for actual content
    content_length = max_length - len(separator)
    start_length = content_length // 2
    end_length = content_length - start_length

    return text[:start_length] + separator + text[-end_length:]


def extract_words(text: str) -> list[str]:
    """
    Extract words from text, ignoring punctuation.

    Args:
        text: Input text

    Returns:
        List of words
    """
    return re.findall(r"\b\w+\b", text.lower())


def levenshtein_distance(s1: str, s2: str) -> int:
    """
    Calculate the Levenshtein distance between two strings.

    Args:
        s1: First string
        s2: Second string

    Returns:
        Levenshtein distance
    """
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            # Cost of insertions, deletions, or substitutions
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def similarity_ratio(s1: str, s2: str) -> float:
    """
    Calculate similarity ratio between two strings (0.0 to 1.0).

    Args:
        s1: First string
        s2: Second string

    Returns:
        Similarity ratio (1.0 = identical, 0.0 = completely different)
    """
    if s1 == s2:
        return 1.0

    max_length = max(len(s1), len(s2))
    if max_length == 0:
        return 1.0

    distance = levenshtein_distance(s1, s2)
    return 1.0 - (distance / max_length)


def find_best_match(target: str, candidates: list[str], threshold: float = 0.6) -> str | None:
    """
    Find the best matching string from a list of candidates.

    Args:
        target: Target string to match
        candidates: List of candidate strings
        threshold: Minimum similarity threshold

    Returns:
        Best matching candidate or None if no match above threshold
    """
    best_match = None
    best_ratio = 0.0

    for candidate in candidates:
        ratio = similarity_ratio(target.lower(), candidate.lower())
        if ratio > best_ratio and ratio >= threshold:
            best_ratio = ratio
            best_match = candidate

    return best_match


def normalize_whitespace(text: str) -> str:
    """
    Normalize whitespace in text (collapse multiple spaces, trim).

    Args:
        text: Input text

    Returns:
        Text with normalized whitespace
    """
    return " ".join(text.split())


def wrap_text(text: str, width: int, indent: str = "") -> list[str]:
    """
    Wrap text to specified width.

    Args:
        text: Text to wrap
        width: Maximum line width
        indent: Indentation for continuation lines

    Returns:
        List of wrapped lines
    """
    if width <= 0:
        return [text]

    words = text.split()
    if not words:
        return [""]

    lines = []
    current_line = words[0]

    for word in words[1:]:
        if len(current_line) + 1 + len(word) <= width:
            current_line += " " + word
        else:
            lines.append(current_line)
            current_line = indent + word

    if current_line:
        lines.append(current_line)

    return lines


def escape_html(text: str) -> str:
    """
    Escape HTML special characters.

    Args:
        text: Text to escape

    Returns:
        HTML-escaped text
    """
    escape_dict = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#x27;"}

    for char, escaped in escape_dict.items():
        text = text.replace(char, escaped)

    return text


def unescape_html(text: str) -> str:
    """
    Unescape HTML special characters.

    Args:
        text: HTML-escaped text

    Returns:
        Unescaped text
    """
    unescape_dict = {
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&#x27;": "'",
        "&#39;": "'",
    }

    for escaped, char in unescape_dict.items():
        text = text.replace(escaped, char)

    return text
