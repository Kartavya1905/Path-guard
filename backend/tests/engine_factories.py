"""Small builders for engine tests (plain inputs, no API or database)."""

from pathguard_engine import Plan, Prerequisite, Project, Task, new_id

OWNER = "usr_demo0001"


def project(**kw) -> Project:
    base = dict(id=new_id("project"), owner_id=OWNER, title="Demo",
                start="Checkout change approved", goal="Reliable launch")
    return Project(**(base | kw))


def tasks(*titles: str) -> list[Task]:
    return [Task(id=new_id("task"), title=t, position=i) for i, t in enumerate(titles)]


def prereq(before: Task | str, after: Task | str) -> Prerequisite:
    b = before.id if isinstance(before, Task) else before
    a = after.id if isinstance(after, Task) else after
    return Prerequisite(id=new_id("prerequisite"), before_task_id=b, after_task_id=a)


def plan(ts: list[Task] | None = None, ps: list[Prerequisite] | None = None) -> Plan:
    return Plan(project=project(), tasks=ts or [], prerequisites=ps or [])
