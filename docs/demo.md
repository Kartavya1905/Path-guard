# Demo script

This takes about 10 minutes. It works locally (http://localhost:3600 after `docker compose up -d --build`) and on the hosted demo. Every number in the seeded examples is hypothetical and labeled so.

The same script runs automatically as `frontend/e2e/demo.spec.ts`. Steps 2–6 there use a copy of the seeded launch, so the example stays clean.

## 1. Start from almost nothing

1. On **Projects**, choose **New project**.
2. Enter only a starting point (for example "Team agrees to try PathGuard") and a goal ("First pilot completed"). Choose **Create project**.
3. Choose **Open workspace**, then **Generate map**. You get Start → Goal. Map checks say "Ready to analyze", and Probability says the link needs an estimate.

## 2. Review a suggested risk and trace it

1. Open **Checkout launch (example)** from Projects.
2. Under **Risks and suggestions → To review**, select **Unclear requirements**.
3. The map dims everything except what leads into **Confirm requirements** (causes, outlined amber) and what follows from the "Scope ambiguity found" branch (effects, outlined red).
4. The right panel shows the trigger, rationale, consequence, suggested mitigation, impact, and likelihood (unknown).
5. Choose **Accept**.

## 3. An unknown and a hypothetical estimate

1. Select **Waiting for payment provider approval**, a suggested delay branch drawn dashed, then its incoming link **Delay from Build integration**. Its link label shows **?**, and Probability says **Unknown (no number)**. Nothing is filled in for it.
2. Select **Confirm requirements** and its link **Success to Build integration**. The label reads **85% hyp.**, and the editor shows Source **Hypothetical** with its stated assumption.

## 4. Baseline vs checklist

1. Switch the centre panel to **Versions**. The seeded example already has v1 Baseline and v2 Requirements checklist. On your own projects, use **Save current map as baseline**.
2. To build a variant yourself:
   - Under **Create a variant with a mitigation**, start from v1 and name the mitigation "Requirements checklist", with an expected effect.
   - Change **Scope is clear** to 95 and **Scope is ambiguous** to 5, each with an assumption.
   - If you change only one, the server refuses the variant and names the other branch.
3. **Compare** shows clean first-pass success **+8.55 pp** (72.675% → 81.225%), eventual success +3.3922638 pp, and the two changed branches with their before and after estimates. It flags that both rely on hypothetical estimates.

## 5. Persistence

Reload the page. The map, your accepted risk, any review notes and both versions are still there. Versions can't be edited, so the baseline is exactly what it was.

## 6. Report, risk register, outcome

1. Choose **Report**. The working map is labeled **DRAFT**. Sections: map snapshot, results, risk register, mitigations and owners, assumptions, estimate sources, open evidence gaps, review status.
2. Choose **Print / save as PDF** and **Download risk register (CSV)**.
3. To get a **REVIEWED** report, an owner or reviewer picks a saved version whose suggestions are all decided and chooses **Sign off as reviewed**. The transaction example's baseline is ready for this.
4. Back in the workspace, open **Outcomes** and record what happened. For example: v1, "Scope confirmed?: Confirm requirements → Build integration", 36 of 40 eligible runs, context `checkout`.
5. Under **Estimate candidate**, check that branch with context `checkout`:
   - With 40 runs you get a measured candidate (90%), with its count, date range and comparability rule.
   - An owner or reviewer can approve it. The other branch must be entered explicitly, and the old map is saved as a snapshot version first.
