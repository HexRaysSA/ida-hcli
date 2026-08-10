"""String processing utilities."""


def get_email_domain(email: str) -> str:
    """The lowercased domain part of an email address, or "" if there is no `@`."""
    at_index = email.find("@")
    if at_index != -1:
        return email[at_index + 1 :].lower()
    return ""
