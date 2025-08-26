from __future__ import annotations

import questionary

from hcli.lib.api.customer import Customer, customer
from hcli.lib.console import console
from hcli.lib.constants import cli

EXIT_MESSAGES: list[str] = []


async def safe_ask_async(questionary_obj, exit_message: str = "Operation cancelled."):
    """
    Wrapper for questionary ask_async() calls with KeyboardInterrupt handling.

    Args:
        questionary_obj: The questionary object (e.g., questionary.select(...))
        exit_message: Message to display when interrupted

    Returns:
        The result from questionary.ask_async()

    Raises:
        SystemExit: When user presses CTRL+C
    """
    try:
        return await questionary_obj.unsafe_ask_async()
    except KeyboardInterrupt:
        console.print(f"\n[yellow]{exit_message}[/yellow]")
        raise SystemExit(0)


def exit_with_messages(code: int = 1) -> None:
    if EXIT_MESSAGES:
        for msg in EXIT_MESSAGES:
            print(msg)
    raise SystemExit(code)


async def select_customer() -> Customer | None:
    """
    Select a customer interactively or return the single customer if only one exists.

    Returns:
        Selected customer or None if no customers available
    """
    customers = await customer.get_customers()

    if len(customers) > 1:
        console.print("\n[bold]Available customers:[/bold]")
        for i, cust in enumerate(customers, 1):
            name_parts = []
            if cust.first_name:
                name_parts.append(cust.first_name)
            if cust.last_name:
                name_parts.append(cust.last_name)
            if cust.company:
                name_parts.append(f"({cust.company})")

            display_name = " ".join(name_parts) if name_parts else cust.email
            console.print(f"  {i}. [{cust.id}] {display_name}")

        choices = []
        for i, cust in enumerate(customers, 1):
            name_parts = []
            if cust.first_name:
                name_parts.append(cust.first_name)
            if cust.last_name:
                name_parts.append(cust.last_name)
            if cust.company:
                name_parts.append(f"({cust.company})")

            display_name = " ".join(name_parts) if name_parts else cust.email
            choices.append(f"{i}. [{cust.id}] {display_name}")

        selection = await safe_ask_async(
            questionary.select(
                "Select customer:",
                choices=choices,
                default=choices[0],
                style=cli.SELECT_STYLE,
            )
        )

        choice_num = int(selection.split(".")[0])

        return customers[choice_num - 1]

    elif len(customers) == 1:
        return customers[0]
    else:
        console.print("[red]No customers found[/red]")
        exit_with_messages(1)
        return None
