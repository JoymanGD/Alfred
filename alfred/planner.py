import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PlanItem:
    id: int
    text: str
    done: bool = False
    created_at: str = ""
    completed_at: str | None = None


@dataclass
class PlanState:
    goal: str = ""
    items: list[PlanItem] | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if self.items is None:
            self.items = []


class PlanStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> PlanState:
        if not self.path.exists():
            return PlanState()

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return PlanState()

        items = [
            PlanItem(
                id=int(item.get("id", index + 1)),
                text=str(item.get("text", "")).strip(),
                done=bool(item.get("done", False)),
                created_at=str(item.get("created_at", "")),
                completed_at=item.get("completed_at"),
            )
            for index, item in enumerate(data.get("items") or [])
            if str(item.get("text", "")).strip()
        ]
        return PlanState(
            goal=str(data.get("goal", "")).strip(),
            items=items,
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )

    def save(self, state: PlanState):
        if not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)

        normalized = PlanState(
            goal=state.goal.strip(),
            items=[
                PlanItem(
                    id=index + 1,
                    text=item.text.strip(),
                    done=bool(item.done),
                    created_at=item.created_at or _utc_now(),
                    completed_at=item.completed_at,
                )
                for index, item in enumerate(state.items or [])
                if item.text.strip()
            ],
            created_at=state.created_at or _utc_now(),
            updated_at=_utc_now(),
        )
        payload = {
            "goal": normalized.goal,
            "items": [asdict(item) for item in normalized.items],
            "created_at": normalized.created_at,
            "updated_at": normalized.updated_at,
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def clear(self):
        self.save(PlanState())
