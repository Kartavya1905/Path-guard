"""Learning from past reviewed risks: similarity choice, privacy of numbers, prompt trimming."""

import json

from pathguard_engine import RiskCandidate, ScenarioGraph, ScenarioNode
from pathguard_engine.ai import build_prompt
from pathguard_engine.examples import checkout_launch
from pathguard_engine.retrieval import SourceProject, select_examples


def project(pid: str, title: str, context: str, step: str, risks: list[tuple[str, str, str]]):
    node = ScenarioNode(id=f"nod_{pid}000000", kind="step", label=step)
    return SourceProject(
        project_id=f"prj_{pid}000000",
        title=title,
        context_text=context,
        graph=ScenarioGraph(
            nodes=[node],
            risks=[
                RiskCandidate(
                    id=f"rsk_{pid}{i:06d}",
                    category="manual",
                    title=t,
                    trigger=trig,
                    rationale="",
                    consequence="c",
                    affected_node_ids=[node.id],
                    review_status=status,
                )
                for i, (t, trig, status) in enumerate(risks)
            ],
        ),
    )


PAYMENTS = project(
    "pay",
    "Payments revamp",
    "checkout payment provider launch",
    "Build integration",
    [
        ("Webhook retries duplicate orders", "The payment provider resends a webhook", "accepted"),
        ("Refund flow untested", "Refunds are not in the test plan", "edited"),
        ("Blockchain receipts", "Someone suggests a blockchain", "rejected"),
        ("Still undecided", "A suggestion nobody reviewed", "suggested"),
        ("Declines spike", "About 30% of cards decline", "accepted"),  # contains a number
    ],
)
ONBOARDING = project(
    "hr",
    "Staff onboarding",
    "new hires laptops accounts",
    "Order laptops",
    [
        ("Laptop shipping delay", "Supplier is out of stock", "accepted"),
    ],
)


def test_similar_accepted_risks_are_chosen_and_rejected_ones_become_avoid():
    learned, avoided = select_examples(
        "checkout payment integration launch webhook", [ONBOARDING, PAYMENTS]
    )
    titles = [e.title for e in learned]
    assert titles[0] == "Webhook retries duplicate orders"
    assert "Laptop shipping delay" not in titles  # unrelated project scores 0
    assert "Still undecided" not in titles  # unreviewed suggestions teach nothing
    assert "Declines spike" not in titles  # text with a numeric estimate is never shown
    assert [e.title for e in avoided] == ["Blockchain receipts"]
    assert all(e.score > 0 for e in learned + avoided)


def test_selection_is_deterministic_and_limited():
    a = select_examples("payment refund webhook", [PAYMENTS, ONBOARDING], accepted=1, rejected=0)
    b = select_examples("payment refund webhook", [ONBOARDING, PAYMENTS], accepted=1, rejected=0)
    assert a == b and len(a[0]) == 1 and a[1] == []
    assert select_examples("anything", []) == ([], [])


def test_prompt_carries_examples_and_trims_them_first():
    plan, graph = checkout_launch()
    learn = [
        {
            "step": "Build integration",
            "title": f"Example {i}",
            "trigger": "t" * 200,
            "consequence": "c",
        }
        for i in range(4)
    ]
    full = build_prompt(plan, graph, None, learn_from=learn, avoid=["Blockchain receipts"])
    payload = json.loads(full.messages[1]["content"])
    assert len(payload["learn_from"]) == 4 and payload["avoid"] == ["Blockchain receipts"]
    assert full.examples_sent == 4 and full.avoid_sent == 1
    assert "learn_from" in full.messages[0]["content"]  # the model is told how to use them

    small = build_prompt(plan, graph, None, max_chars=1500, learn_from=learn, avoid=["x"])
    assert small.examples_sent < 4 and small.avoid_sent == 0  # examples go before steps
    assert small.node_ids == full.node_ids
    assert full.digest != build_prompt(plan, graph, None).digest  # cache key changes too
