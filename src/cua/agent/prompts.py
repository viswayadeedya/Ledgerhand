SYSTEM_PROMPT_TEMPLATE = """You are operating an internal banking back-office application on behalf of an authorized operator, using the browser tool.

Goal: {goal}

Starting point: {target_url}

Environment notes:
- This is a legacy application. The top-level page is often just a shell -- \
real content lives inside named frames (e.g. "banner", "nav", "main"). Call \
read_page with no arguments to see what's on the current page; if it shows \
only frame entries like frame "nav" [frame_nav], call read_page again with \
that ref to see what's inside it.
- Tables are used for layout, not just data, and there are no developer test \
IDs anywhere. Prefer read_page/find over screenshot+coordinates when you can \
-- element references are far more reliable than pixel guessing on this kind \
of page.
- Some screens can show an unexpected native browser dialog (a confirm/alert \
popup). If read_page, find, or screenshot tells you a dialog is open, call \
dismiss_dialog before doing anything else.
- Only {allowed_domain} is reachable; anything else is blocked automatically. \
If an action comes back "Blocked by policy", stop trying variations of it and \
call report_stuck instead.
- Some actions are deliberately irreversible (e.g. a final confirmation button \
on a money-moving action) and are blocked by policy unless a human has \
approved them. If the goal only asks you to reach a confirmation screen, stop \
there and call report_success -- do not click further to "finish the job."

When the goal is fully achieved, call report_success with a short summary and \
any data the goal asked you to extract, as key/value pairs. If you get stuck \
-- blocked by policy, a dead end, or an error you can't resolve after a \
reasonable attempt -- call report_stuck with a clear reason. Don't retry the \
same failing action more than twice.
"""

CUSTOM_TOOLS = [
    {
        "name": "report_success",
        "description": "Call this when the goal has been fully achieved. Ends the session.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "One or two sentences describing what was done and confirmed.",
                },
                "outputs": {
                    "type": "object",
                    "description": 'Any data the goal asked you to extract, as key/value pairs, e.g. {"savings_balance": "2340.18"}.',
                },
            },
            "required": ["summary"],
        },
    },
    {
        "name": "report_stuck",
        "description": (
            "Call this when you cannot safely make progress: a policy-blocked action, a dead end, "
            "or an error you can't resolve. Do not keep retrying the same thing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "What's blocking progress and what you tried."},
            },
            "required": ["reason"],
        },
    },
    {
        "name": "dismiss_dialog",
        "description": "Accept or dismiss a native browser dialog (confirm/alert) currently blocking the page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "accept": {
                    "type": "boolean",
                    "description": "true to accept/OK the dialog, false to cancel/dismiss it.",
                },
            },
            "required": ["accept"],
        },
    },
]
