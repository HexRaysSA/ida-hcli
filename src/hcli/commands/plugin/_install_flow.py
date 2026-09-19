"""Shared install and upgrade flow: settings before mutation, results after."""

from __future__ import annotations

import logging
from collections.abc import Iterable

import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida.plugin import IDAMetadataDescriptor, PluginSettingDescriptor, iter_dependency_specs
from hcli.lib.ida.plugin.exceptions import InstallExecutionError
from hcli.lib.ida.plugin.execute import InstallResult, NodeResult
from hcli.lib.ida.plugin.install import PlannedOperation
from hcli.lib.ida.plugin.reference import parse_dependency_spec
from hcli.lib.ida.plugin.resolve import ConfigurationRequirement, InstallPlan, PlannedNode, ResolutionContext

from ._prompt import prompt_plugin_settings

logger = logging.getLogger(__name__)


def _split_config_item(item: str, flag: str) -> tuple[str, str]:
    if "=" not in item:
        raise ValueError(f"invalid {flag} format: {item}, expected key=value")
    key, value = item.split("=", 1)
    return key, value


def parse_configuration_arguments(
    plan: InstallPlan,
    root_name: str,
    config: Iterable[str],
    dependency_config: Iterable[str],
) -> dict[tuple[str, str], str]:
    """Turn ``--config`` and ``--dependency-config`` items into ``(target, key)`` values.

    A ``--config`` key with a dot targets the named component when the plan
    knows it; otherwise the whole key belongs to the root plugin.

    Raises:
        ValueError: an item is not ``key=value``, or a dependency item has no target.
    """
    values: dict[tuple[str, str], str] = {}
    for item in config:
        key, value = _split_config_item(item, "--config")
        if "." in key:
            prefix, suffix = key.split(".", 1)
            if plan.has_settings_target(prefix):
                values[(prefix, suffix)] = value
                continue
        values[(root_name, key)] = value
    for item in dependency_config:
        key, value = _split_config_item(item, "--dependency-config")
        if "." not in key:
            raise ValueError(f"invalid --dependency-config format: {item}, expected plugin.key=value")
        target, suffix = key.split(".", 1)
        values[(target, suffix)] = value
    return values


def _stored_values(
    context: ResolutionContext, plugin_name: str, settings: list[PluginSettingDescriptor]
) -> dict[str, str | bool]:
    existing: dict[str, str | bool] = {}
    for setting in settings:
        present, value = context.stored_setting(plugin_name, setting.key)
        if present and value is not None:
            existing[setting.key] = value
    return existing


def _prompt_root_settings(
    operation: PlannedOperation,
    configured_targets: set[str],
    requirements: dict[str, list[ConfigurationRequirement]],
) -> dict[tuple[str, str], str | bool]:
    answers_all: dict[tuple[str, str], str | bool] = {}
    root = operation.root
    if not root.mutates:
        return answers_all
    for _, descriptor in root.iter_manifests():
        name = descriptor.plugin.name
        if name.lower() in configured_targets or not descriptor.plugin.settings:
            continue
        if name != root.name:
            console.print(f"\nconfigure component [blue]{name}[/blue]:")
        existing = _stored_values(operation.context, name, descriptor.plugin.settings)
        answers = prompt_plugin_settings(descriptor.plugin.settings, existing)
        if answers is None:
            raise click.Abort()
        required_keys = {r.key for r in requirements.pop(name, [])}
        for key, answer in answers.items():
            setting = descriptor.plugin.get_setting(key)
            if key not in required_keys and setting.default == answer:
                continue
            answers_all[(name, key)] = answer
    return answers_all


def render_dependency_chain(plan: InstallPlan, name: str) -> str:
    """The root-to-dependency path that selected ``name``, as ``root -> mid -> name``."""
    lowered = name.lower()
    candidates = list(plan.ordered_nodes())
    for branch in plan.optional_branches:
        candidates.extend(branch.nodes.values())
    for node in candidates:
        if node.name.lower() == lowered:
            return " -> ".join([*node.selected_by.chain, node.name])
    return name


def _prompt_dependency_settings(
    plan: InstallPlan,
    requirements: dict[str, list[ConfigurationRequirement]],
) -> dict[tuple[str, str], str | bool]:
    answers_all: dict[tuple[str, str], str | bool] = {}
    for name, group in requirements.items():
        console.print(f"\nconfigure dependency [blue]{name}[/blue] [dim]({render_dependency_chain(plan, name)})[/dim]:")
        descriptors = [r.descriptor for r in group]
        existing: dict[str, str | bool] = {r.key: r.current_value for r in group if r.current_value is not None}
        answers = prompt_plugin_settings(descriptors, existing)
        if answers is None:
            raise click.Abort()
        for key, answer in answers.items():
            answers_all[(name, key)] = answer
    return answers_all


def _group_by_target(requirements: Iterable[ConfigurationRequirement]) -> dict[str, list[ConfigurationRequirement]]:
    grouped: dict[str, list[ConfigurationRequirement]] = {}
    for requirement in requirements:
        grouped.setdefault(requirement.plugin_name, []).append(requirement)
    return grouped


def collect_configuration(
    operation: PlannedOperation,
    *,
    config: Iterable[str] = (),
    dependency_config: Iterable[str] = (),
) -> None:
    """Record setting values on the plan before anything is fetched or written.

    Command line values are validated first. On an interactive console the
    root plugin and its components are prompted for every promptable setting
    they lack, and dependencies for the required settings they lack. Missing
    settings of optional dependencies never block; the executor reports that
    branch as unavailable.

    Raises:
        ValueError: an unknown target or key, an invalid value, or required
            settings that still have no value.
        click.Abort: the user cancelled a prompt.
    """
    plan = operation.plan
    cli_values = parse_configuration_arguments(plan, operation.root.name, config, dependency_config)
    plan.apply_configuration_values(cli_values)

    if console.is_interactive:
        configured_targets = {target.lower() for target, _ in cli_values}
        requirements = _group_by_target(plan.missing_configuration(plan.all_branches()))
        answers = _prompt_root_settings(operation, configured_targets, requirements)
        answers.update(_prompt_dependency_settings(plan, requirements))
        plan.apply_configuration_values(answers)

    missing = plan.missing_configuration()
    if not missing:
        return
    arguments = ", ".join(r.argument() for r in missing)
    if console.is_interactive:
        raise ValueError(f"missing required settings; supply them with: {arguments}")
    raise ValueError(
        "plugin requires configuration but console is not interactive. "
        f"Please provide settings via command line: {arguments}"
    )


def _planned_node(plan: InstallPlan, node: NodeResult) -> PlannedNode | None:
    if node.branch is None:
        return plan.nodes.get(node.identity)
    return plan.optional_branches[node.branch].nodes.get(node.identity)


def _print_root(node: NodeResult, present_label: str) -> None:
    if node.outcome == "present":
        version = node.previous_version or node.version
        console.print(f"[green]{present_label}[/green] plugin: [blue]{node.name}[/blue]=={version}")
    elif node.outcome == "upgraded":
        console.print(f"[green]Upgraded[/green] plugin: [blue]{node.name}[/blue]=={node.version}")
    elif node.outcome == "editable":
        console.print(
            f"[green]Installed[/green] plugin: [blue]{node.name}[/blue]=={node.version} [yellow](editable)[/yellow]"
        )
    elif node.outcome == "installed":
        console.print(f"[green]Installed[/green] plugin: [blue]{node.name}[/blue]=={node.version}")


def _describe_edge(planned: PlannedNode | None) -> str:
    if planned is None or not planned.selected_by.chain:
        return ""
    kind = "required by" if planned.selected_by.required else "optional for"
    return f" [dim]({kind} {planned.selected_by.chain[-1]})[/dim]"


def _print_dependency(plan: InstallPlan, node: NodeResult) -> None:
    via = _describe_edge(_planned_node(plan, node))
    if node.outcome == "installed":
        console.print(f"  [green]Installed[/green] dependency: [blue]{node.name}[/blue]=={node.version}{via}")
    elif node.outcome == "upgraded":
        console.print(
            f"  [green]Upgraded[/green] dependency: [blue]{node.name}[/blue]=={node.version}"
            f" [dim](was {node.previous_version})[/dim]{via}"
        )
    elif node.outcome == "present":
        console.print(f"  [dim]Present[/dim] dependency: {node.name}{via}")


def _collect_warnings(result: InstallResult) -> list[str]:
    plan = result.plan
    warnings: list[str] = list(plan.warnings)
    nodes = list(plan.ordered_nodes())
    for branch in plan.optional_branches:
        nodes.extend(branch.nodes.values())
    for node in nodes:
        warnings.extend(node.warnings)
    return list(dict.fromkeys(warnings))


def report_install_result(result: InstallResult, *, present_label: str = "Already installed") -> None:
    """Print what the operation did: roots first, then dependencies, unavailable optionals, warnings."""
    plan = result.plan
    roots = set(plan.roots)
    for node in result.nodes:
        if node.identity in roots:
            _print_root(node, present_label)
    for node in result.nodes:
        if node.identity not in roots:
            _print_dependency(plan, node)
    for branch, reason in result.unavailable_optionals:
        console.print(f"  [yellow]Unavailable[/yellow] optional dependency: {branch.edge.spec.plugin}: {reason}")
    for warning in _collect_warnings(result):
        console.print(f"[yellow]Warning[/yellow]: {warning}")


def report_install_failure(error: InstallExecutionError) -> None:
    """Print a rolled-back failure, naming what was undone and any recovery paths."""
    console.print(f"[red]Error[/red]: {error}")
    result = error.result
    if not isinstance(result, InstallResult):
        return
    rolled_back = [node.display for node in result.nodes if node.outcome == "rolled_back"]
    if rolled_back:
        console.print(f"Rolled back: {', '.join(rolled_back)}")
    if result.recovery is not None and result.recovery.retained_paths:
        console.print("Kept for manual recovery:")
        for path in result.recovery.retained_paths:
            console.print(f"  {path}")


def report_interrupted_install(error: BaseException) -> None:
    """Print what an interrupted operation rolled back and any recovery paths."""
    console.print("[red]Interrupted[/red]: installation was cancelled")
    result = getattr(error, "install_result", None)
    if not isinstance(result, InstallResult):
        return
    rolled_back = [node.display for node in result.nodes if node.outcome == "rolled_back"]
    if rolled_back:
        console.print(f"Rolled back: {', '.join(rolled_back)}")
    if result.recovery is not None and result.recovery.retained_paths:
        console.print("Kept for manual recovery:")
        for path in result.recovery.retained_paths:
            console.print(f"  {path}")


def _declared_dependencies(metadata: IDAMetadataDescriptor) -> dict[str, str]:
    declared: dict[str, str] = {}
    for _, spec in iter_dependency_specs(metadata):
        try:
            parsed = parse_dependency_spec(spec.plugin)
        except ValueError:
            continue
        declared[parsed.name.lower()] = spec.plugin
    return declared


def find_dropped_dependencies(old: IDAMetadataDescriptor, new: IDAMetadataDescriptor) -> tuple[list[str], list[str]]:
    """Dependency specs that ``new`` no longer declares, and those whose spec text changed.

    Returns ``(removed, changed)``: removed holds the old spec strings of names
    that vanished; changed holds ``old -> new`` for names whose version or host
    constraint differs.
    """
    before = _declared_dependencies(old)
    after = _declared_dependencies(new)
    removed = [before[name] for name in sorted(before) if name not in after]
    changed = [
        f"{before[name]} -> {after[name]}" for name in sorted(before) if name in after and before[name] != after[name]
    ]
    return removed, changed


def report_dropped_dependencies(old: IDAMetadataDescriptor, new: IDAMetadataDescriptor) -> None:
    """Print dependencies an upgrade stopped declaring or now constrains differently."""
    removed, changed = find_dropped_dependencies(old, new)
    if removed:
        console.print(f"[yellow]Note[/yellow]: these dependencies were removed from [blue]{new.plugin.name}[/blue]:")
        for spec in removed:
            console.print(f"  {spec}")
        console.print("They remain installed; remove them manually if no longer needed.")
    if changed:
        console.print(f"[yellow]Note[/yellow]: these dependency constraints changed in [blue]{new.plugin.name}[/blue]:")
        for line in changed:
            console.print(f"  {line}")
