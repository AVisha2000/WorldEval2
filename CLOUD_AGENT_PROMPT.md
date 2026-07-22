# Ready-to-paste prompt for the cloud agent

You are continuing an unfinished Labyrinth capability benchmark in a replacement repository. This is
a high-detail engineering handoff. Do not start by redesigning the solution and do not touch the
original closed-submission repository.

Repository and branch:

- Repository: `https://github.com/AVisha2000/WorldEval2.git`
- Branch: `codex/labyrinth-benchmark-handoff`
- The expected branch-tip SHA is supplied in the message accompanying this prompt. Verify it before
  doing any work.
- Forbidden repository: `AVisha2000/WorldEval` (never clone for editing, commit to it, or push it).

Start exactly like this:

```bash
git clone https://github.com/AVisha2000/WorldEval2.git
cd WorldEval2
git switch --track origin/codex/labyrinth-benchmark-handoff
git status --short --branch
git rev-parse HEAD
git remote -v
```

Confirm that `git rev-parse HEAD` matches the handoff SHA and that both origin URLs point to
`AVisha2000/WorldEval2`. Stop if either check fails.

Your first required action is to read `CLOUD_AGENT_HANDOVER.md` completely, from first line to last.
Treat it as the source of truth for architecture, frozen hashes, credential rules, known passing
tests, known failing broad-suite tests, exact CLI commands, live-run gates, artifact policy, Git scope,
and final deliverables. Then inspect `git status` and the changed files it names. Continue the existing
implementation; do not replace it wholesale.

Important state:

- The 840-race live programme has not been run.
- No live API call is authorized until every offline/provider-free gate in the handover passes.
- The required programme is 120 pilot races, 600 unskilled baseline races, and 120 skill-on races,
  with three model episodes per race.
- Raw season data belongs only under ignored `runs/`.
- A cloud `OPENAI_API_KEY` must come from the cloud secret manager/process environment. Never request
  that a key be pasted into chat, never print it, and never persist it.
- Never persist prompts, observations, raw responses, scratchpads, chain-of-thought, navigation
  memory, provider requests, or credentials.
- Do not merge to or push `main` until the full live season, analysis/report, artifact audit, visual QA,
  and all required gates are complete.
- Preserve and never stage the unrelated Remotion/gallery files listed in the handover.

Work in this order:

1. Reinstall the editable package with dev/benchmark extras and install dashboard dependencies.
2. Reproduce the focused green benchmark/runtime/provider suites recorded in the handover.
3. Investigate the explicitly documented broad-suite failures and teardown hang. Do not call them
   fixed without isolated evidence. Keep unrelated fixes narrowly scoped.
4. Verify schedule hashes/counts/seat rotation, artifact schema/resume/retry behavior, secret scanning,
   missing/censor behavior, report rendering, and the 52-map × 5-depth provider-free DFS/memory gate.
5. Run dashboard and Godot 4.5 gates exactly as documented, including `build:pages`; do not count a
   platform skip as a pass.
6. Generate and audit a non-live season. Confirm no protected material is durable.
7. Only then configure the cloud secret and run/resume the 120-race pilot. Persist and report its
   projected calls, tokens, elapsed time, and cost without exposing protected data.
8. If the technical gate passes, continue automatically through the 600 baseline and 120 skill study
   as the user requested. Use only the resumable CLI; never hand-edit result/state JSON.
9. Analyze, render, visually inspect, and leak-scan all artifacts. Use the safe `export` command for a
   curated tracked report; never force-add `runs/`.
10. Commit only approved Labyrinth code/tests/tooling and curated aggregate/report artifacts. Publish
    to `WorldEval2/main` only with the handover's fast-forward-only procedure; never force-push.

Use sub-agents if available, with these boundaries:

- Sub-agent A — read-only benchmark integrity/security audit of spec/artifacts/runner/analysis/report.
- Sub-agent B — runtime/provider-free DFS and bounded-memory tests; no live provider access.
- Sub-agent C — dashboard + Godot 4.5 verification and narrowly scoped fixes.
- Sub-agent D — read-only statistics/report numerical and visual review.
- Keep live credentials and the single season runner with the primary coordinator. Never allow two
  agents to mutate the same season or overlapping files concurrently.

Require each sub-agent to return: files inspected/changed, exact commands run, exact results, remaining
risks, and whether it touched external state. The primary coordinator must review all diffs and is the
only agent allowed to stage, commit, export, or push.

Keep the user updated during long work with concise numeric progress. At completion, report the exact
race/model-episode counts, calls, input/output/total tokens, elapsed time, infrastructure voids,
provider/model failures, selected skill depth, paired effects and 95% intervals, skilled-Luna
comparison, hashes, safe artifact paths, test/build outcomes, and final commit/push. Do not provide
private model chain-of-thought; describe only observable action traces and high-level strategy.
