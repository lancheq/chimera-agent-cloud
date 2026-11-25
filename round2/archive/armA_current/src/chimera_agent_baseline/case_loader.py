"""Runtime case loader.

Reads the per-patient ``<case>/structured-prompt.json`` shipped at
``data/task<N>/agent_input/`` and renders the baseline agent's prompt
narrative through a Jinja template. Returns one query dict per case in
the form ``{case_id, task, context}``.

The companion ``<case>/clinical.json`` is read separately by the MCP
server (:mod:`chimera_agent_baseline.tools.base`); it never reaches this
module.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def render_baseline_prompt(
    payload: dict[str, Any],
    templates_dir: Path | str = "templates/prompts",
    template_name: str = "agent_prompt.j2",
) -> str:
    """Render the agent prompt from a ``structured-prompt.json`` payload.

    Participants can swap ``template_name`` for their own Jinja file. The
    template receives the full flat ``structured-prompt.json`` payload in scope and
    renders only the urologist form's visible "Clinical data" panel — the
    masked EHR documents (reports, notes, labs, PSA history, family
    history) are served by the MCP tools, never inlined here.
    """
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(templates_dir)), keep_trailing_newline=True)
    return env.get_template(template_name).render(**payload)


def load_cases(
    cases_dir: Path | str,
    *,
    task: int = 1,
    templates_dir: Path | str = "templates/prompts",
    template_name: str = "agent_prompt.j2",
) -> list[dict[str, Any]]:
    """Read per-case ``structured-prompt.json`` files into agent queries.

    Each entry has shape ``{case_id, task, context}``. ``context`` is
    the rendered prompt narrative — the form's task question is
    interpolated into it by the Jinja template.

    Any subdirectory containing a ``structured-prompt.json`` is treated as a case;
    case directories are named ``PT-<id>`` (task 1) or ``T2-<n>``
    (task 2), so we do not filter on a name prefix.

    Fails loudly: a missing directory, malformed JSON, or an input
    directory with no cases all raise rather than being silently skipped.
    """
    cases_dir = Path(cases_dir)
    if not cases_dir.is_dir():
        raise FileNotFoundError(f"cases_dir {cases_dir} is not a directory")

    out: list[dict[str, Any]] = []
    for sub in sorted(p for p in cases_dir.iterdir() if p.is_dir() and (p / "structured-prompt.json").exists()):
        prompt_path = sub / "structured-prompt.json"
        try:
            payload = json.loads(prompt_path.read_text())
        except json.JSONDecodeError as e:
            raise ValueError(f"{prompt_path}: invalid JSON: {e}") from e
        out.append(
            {
                "case_id": payload["case_id"],
                "task": payload.get("task", task),
                "context": render_baseline_prompt(payload, templates_dir, template_name),
            }
        )
    if not out:
        raise FileNotFoundError(f"No cases (subdirectories with structured-prompt.json) found in {cases_dir}")
    return out
