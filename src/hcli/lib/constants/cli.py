import questionary

CHECKBOX_STYLE = questionary.Style(
    [
        ("question", "bold"),
        ("answer", "fg:#f44336 bold"),
        ("pointer", "fg:#673ab7 bold"),
        ("highlighted", "fg:#673ab7 bold"),
        ("selected", "noinherit bold"),
        ("separator", "fg:#cc5454"),
        ("instruction", ""),
        ("text", ""),
    ]
)

SELECT_STYLE = questionary.Style(
    [
        ("question", "bold"),
        ("answer", "fg:#f44336 bold"),
        ("pointer", "fg:#673ab7 bold"),
        ("highlighted", "fg:#673ab7 bold"),
        ("selected", "noinherit bold"),
        ("separator", "fg:#cc5454"),
        ("instruction", ""),
        ("text", ""),
    ]
)
