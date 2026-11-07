"""Validated, declarative inventory of Alfred's executable capabilities."""

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    path = Path(__file__).with_name("action_catalog.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    categories = data["categories"]
    actions = data["actions"]
    if not categories or not actions:
        raise ValueError("Action catalog is empty")
    used = set()
    for category_id, category in categories.items():
        if not category.get("description") or not category.get("actions"):
            raise ValueError(f"Incomplete category: {category_id}")
        for action_id in category["actions"]:
            if action_id not in actions:
                raise ValueError(f"Unknown catalog action: {action_id}")
            used.add(action_id)
    if used != set(actions):
        raise ValueError(f"Uncategorized actions: {set(actions) - used}")
    for action_id, action in actions.items():
        if not action.get("description") or not isinstance(action.get("args"), dict):
            raise ValueError(f"Incomplete action: {action_id}")
        if action.get("confirmation") not in {"never", "always", "conditional"}:
            raise ValueError(f"Invalid confirmation policy: {action_id}")
    return data


def category_criteria() -> dict[str, str]:
    return {name: info["description"] for name, info in load_catalog()["categories"].items()}


def action_help(category: str) -> str:
    catalog = load_catalog()
    action_ids = catalog["categories"][category]["actions"]
    return "\n".join(
        f"{name}: {catalog['actions'][name]['description']} Args: "
        + json.dumps(catalog["actions"][name]["args"], ensure_ascii=False)
        for name in action_ids
    )


def confirmation_policy(action: str) -> str:
    return load_catalog()["actions"][action]["confirmation"]
