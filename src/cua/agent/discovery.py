import os
from dataclasses import dataclass, field

import anthropic
from dotenv import load_dotenv

from cua.agent.browser_tools import BrowserToolExecutor
from cua.agent.prompts import CUSTOM_TOOLS, SYSTEM_PROMPT_TEMPLATE
from cua.core.models import RecordedStep
from cua.guardrails.policy import PolicyEngine
from cua.surface.playwright_surface import PlaywrightSurface

load_dotenv()

BROWSER_TOOLSET = {"type": "browser_toolset_20260801"}


@dataclass
class DiscoveryResult:
    outcome: str  # "success" | "stuck" | "max_steps" | "no_action"
    summary: str
    outputs: dict = field(default_factory=dict)
    steps: list[RecordedStep] = field(default_factory=list)
    model: str = ""
    transcript_length: int = 0


def run_discovery(
    goal: str,
    target_url: str,
    allowed_domain: str,
    credentials: dict[str, str] | None = None,
    model: str | None = None,
    max_steps: int = 20,
    headless: bool = True,
    policy: PolicyEngine | None = None,
    evidence_dir: str = "evidence/runs",
) -> DiscoveryResult:
    """Runs the LLM-driven observe -> decide -> act loop against a live
    Surface until the model reports success/stuck or max_steps is hit.

    Every action the model requests still passes through PolicyEngine inside
    Surface.act() -- the loop itself grants no extra trust the guardrails
    don't already enforce.

    `credentials`, if given (e.g. {"username": "teller1", "password":
    "teller123"}), goes into the system prompt in plaintext -- discovery has
    no way to type a password without seeing it. This is a one-time,
    necessary cost of LLM-driven discovery; deterministic replay (Part 6)
    has no LLM in its loop at all, so it never exposes a secret to a model in
    the first place.
    """
    model = model or os.environ.get("DISCOVERY_MODEL", "claude-sonnet-5")
    client = anthropic.Anthropic()

    surface = PlaywrightSurface(base_url=f"http://{allowed_domain}", policy=policy, headless=headless, evidence_dir=evidence_dir)
    executor = BrowserToolExecutor(surface)

    credentials_block = (
        "\n".join(f"  {k}: {v}" for k, v in credentials.items()) if credentials else "  (none provided)"
    )
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        goal=goal, target_url=target_url, allowed_domain=allowed_domain, credentials_block=credentials_block
    )
    messages: list[dict] = [
        {"role": "user", "content": f"Begin. Navigate to {target_url} and accomplish the goal described in your instructions."}
    ]
    tools = [BROWSER_TOOLSET, *CUSTOM_TOOLS]

    outcome, summary, outputs = "max_steps", f"Stopped after reaching the step limit ({max_steps}).", {}

    try:
        for _ in range(max_steps):
            response = client.messages.create(
                model=model,
                max_tokens=2048,
                system=system_prompt,
                tools=tools,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            done = False
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                toolset_name = getattr(block, "toolset_name", None)

                if block.name == "report_success":
                    outcome = "success"
                    summary = block.input.get("summary", "")
                    outputs = block.input.get("outputs", {}) or {}
                    tool_results.append(_tool_result(block.id, "Recorded.", toolset_name))
                    done = True
                    break
                if block.name == "report_stuck":
                    outcome = "stuck"
                    summary = block.input.get("reason", "")
                    tool_results.append(_tool_result(block.id, "Recorded.", toolset_name))
                    done = True
                    break
                if block.name == "dismiss_dialog":
                    text = executor.dismiss_dialog(bool(block.input.get("accept", True)))
                    tool_results.append(_tool_result(block.id, text, toolset_name))
                    continue
                if toolset_name and toolset_name != "browser":
                    tool_results.append(
                        _tool_result(
                            block.id,
                            f"Error: this environment only supports the 'browser' toolset, not '{toolset_name}'.",
                            toolset_name,
                            is_error=True,
                        )
                    )
                    continue

                content, is_error = executor.dispatch(block.name, block.input or {})
                tool_results.append(_tool_result(block.id, content, toolset_name, is_error=is_error))

            if done:
                break
            if not tool_results:
                outcome = "no_action"
                summary = "Model stopped taking actions without calling report_success or report_stuck."
                break
            messages.append({"role": "user", "content": tool_results})
    finally:
        surface.close()

    return DiscoveryResult(
        outcome=outcome,
        summary=summary,
        outputs=outputs,
        steps=executor.steps,
        model=model,
        transcript_length=len(messages),
    )


def _tool_result(tool_use_id: str, content, toolset_name: str | None, is_error: bool = False) -> dict:
    result: dict = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if toolset_name:
        result["toolset_name"] = toolset_name
    if is_error:
        result["is_error"] = True
    return result
