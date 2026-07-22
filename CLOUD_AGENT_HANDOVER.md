# WorldEval2 Labyrinth Benchmark — Cloud Agent Handover

Last updated: 2026-07-22 (Europe/London)

## 1. Read this first

This repository is the continuation repository for work that happened after a closed submission
deadline. Work only in **WorldEval2**. Do not commit, push, rebase, or otherwise modify the original
`AVisha2000/WorldEval` repository.

- Active local repository: `/Users/arlind/Documents/WorldEval2`
- Active GitHub repository: `https://github.com/AVisha2000/WorldEval2`
- Handoff branch: `codex/labyrinth-benchmark-handoff`
- Base commit on `WorldEval2/main`: `b6b0ae67b2b0cd83eef02cc7beea2ecc15866521`
- Original submission cutoff used for the audit: 2026-07-22 01:00 Europe/London
- Last verified pre-cutoff original commit: `244acf44f587899070d1a9dc793ee63cfd6f9caa`
- Original GitHub `main` was restored to that commit. GitHub's audit history will still honestly show
  the accidental post-cutoff push and the later corrective force-push; do not claim otherwise.
- The active original local path was replaced with a clean checkout at `244acf44...`, with its push
  URL disabled. The previous dirty local checkout was preserved at
  `/Users/arlind/Documents/WorldEval-postcutoff-local-archive-20260722-1425` for recovery only; its
  push URL is also disabled. Do not use that archive as a working repository.

The benchmark implementation is a work in progress. The live 120 + 600 + 120 race programme has
**not** been run. Do not merge this branch to `main` or publish benchmark claims until every gate in
section 12 passes.

A cloud agent should begin with:

```bash
git clone https://github.com/AVisha2000/WorldEval2.git
cd WorldEval2
git switch --track origin/codex/labyrinth-benchmark-handoff
git status --short --branch
git rev-parse HEAD
test "$(git remote get-url origin)" = "https://github.com/AVisha2000/WorldEval2.git"
test "$(git remote get-url --push origin)" = "https://github.com/AVisha2000/WorldEval2.git"
```

The person handing off the task must provide the pushed branch-tip SHA separately. Verify that SHA
before doing any work; do not assume `main` contains this snapshot.

`CLOUD_AGENT_PROMPT.md` contains a shorter ready-to-paste orchestration prompt. This document remains
the detailed source of truth if the two ever appear to differ.

## 2. User's requested outcome

Build and run a reproducible Labyrinth capability benchmark:

1. A 120-race technical pilot: 4 difficulties × 3 pilot maps × 5 vision depths × 2 repetitions.
2. A 600-race unskilled baseline: 4 difficulties × 10 main maps × 5 depths × 3 repetitions.
3. Select the best vision depth objectively from baseline results.
4. A 120-race skill-on study: the same 40 main maps × 3 repetitions at that depth.
5. Three OpenAI model snapshots race concurrently within each race:
   `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`, all at low reasoning effort.
6. Races themselves run sequentially to avoid cross-race latency and rate-limit interference.
7. Produce verified JSON/CSV artifacts, SVG/PNG plots, and a self-contained HTML report with
   deterministic 95% hierarchical-bootstrap intervals (10,000 replicates).
8. Compare whether skilled Luna matches or exceeds unskilled Sol or Terra without treating a
   participant-seat tie-break as a model win.
9. After the complete live run and all verification, curate only safe aggregate/report artifacts,
   commit the Labyrinth scope, and push `WorldEval2/main`.

## 3. Non-negotiable credential and evidence rules

- Never paste, print, log, commit, or copy an API key into an artifact. A key pasted earlier in chat
  must be treated as compromised and must not be reused.
- The desktop workspace currently has an ignored `.env`; its contents are deliberately not recorded
  here. A cloud runner must configure `OPENAI_API_KEY` through its secret manager or process
  environment.
- Both `.env` and the user-mentioned `local.env` are ignored in WorldEval2. The duplicate `.env` in
  the archived old checkout was removed after byte-for-byte verification against WorldEval2; the
  retained WorldEval2 file is only the current local credential source and must never be committed.
  A cloud runner must use its own newly issued secret; never copy or reuse a key from chat or this
  local machine.
- The CLI reads only `OPENAI_API_KEY`. It does not accept a credential argument.
- OpenAI Responses calls use `store: false`, no `previous_response_id`, no Conversations, no response
  chaining, low reasoning effort, and structured output.
- Never persist prompts, observations, raw provider responses, scratchpads, chain-of-thought,
  `navigation_memory`, provider requests, or credentials.
- Durable race results may contain only allow-listed numeric/public evidence: validated actions,
  physical paths, completion, calls, tokens, latency, stop reasons, provider-failure enums, peak
  memory bytes, and memory compaction-event counts.
- `runs/` is git-ignored and is the only place for raw season state. Before publishing anything,
  run the artifact tree audit and a separate repository secret scan.
- Do not ask models for private chain-of-thought. Safe decision traces describe submitted actions and
  outcomes only.

## 4. Implemented architecture

### Dynamic maps

`backend/genesis_arena/embodiment/maze_maps.py` defines a hash-bound `MazeMapSpec` and deterministic
recursive-backtracker generator.

- Easy: 15×15, tree, 5 landmarks.
- Medium: 21×21, tree, 4 landmarks.
- Hard: 31×31, 12 deterministic extra connections, 2 landmarks.
- Memory stress: 41×41, 32 deterministic extra connections, no landmarks.
- Start/exit are diameter endpoints before deterministic braiding.
- Metrics include dimensions, walkable cells, edge count, shortest path, dead ends, junctions, cycle
  rank, connectivity, and the deterministic DFS upper bound `2 × undirected edges`.
- Per-racer provider-call budget equals `2E`; maximum authority ticks equal four times that budget.
- Graphs are cached as immutable mappings. This was essential for the exhaustive map gate.

Frozen suites are checked by
`backend/genesis_arena/embodiment/labyrinth_benchmark/frozen-map-index.json`.

- Pilot manifest SHA-256:
  `a0bc832dadae7e386ccdce5996b750e247c839a8e092509fd1f33b88173d1a07`
- Main manifest SHA-256:
  `0f878f5c7671f9bd8d0299b227c102e0f2481d0e69299932244b282847c2f7d6`

Never regenerate or update these hashes casually. A generator change creates a new benchmark version.

### Agent observation, vision, and movement

`backend/genesis_arena/embodiment/live_labyrinth.py` remains the authority.

- Vision depths are `1`, `2`, `4`, `8`, and `infinite`.
- Vision is 360-degree straight-line sight, wall-occluded, and never looks around corners.
- The observer may see the whole maze in the UI, but the model receives only its player-scoped
  observation.
- The model must explicitly choose a relative passage and movement mode.
- `single_cell` moves exactly one cell.
- `follow_corridor` is still model-authorized control; it advances naturally cell by cell and stops
  before a junction, dead end, exit, episode origin, previously traversed cell, or the submitted
  cell limit. The environment does not choose a route for the model.
- Public replay paths and Godot presentation show natural movement, visible-region highlighting, and
  visited trails.

### Backend-managed memory

Each participant owns an isolated `maze-nav/2` memory, serialized canonically through the reusable
`EpisodeMemory` core with a hard 2,048-byte limit.

It records only state derived from visible observations and accepted moves:

- local pose and heading;
- current untried relative passages and required backtrack passage;
- known/traversed passage masks and visit counts;
- run-length-encoded route/backtrack state;
- unresolved junctions and recent landmarks;
- previous choice/outcome.

Resolved/off-route detail is deterministically compacted. Numeric `memory_evictions` means
**compaction events**, with at most one event per model decision; it is not a count of every omitted
row and not evidence that the model forgot an equal number of cells. Mutable memory and scratchpads
are best-effort zeroed or cleared and closed at episode end; Python cannot guarantee cryptographic
secure erasure from a managed runtime.

Cycle/reconvergence handling forces a safe return without allowing a non-tree edge to overwrite the
DFS parent. The full frozen-map gate proves the generic controller finishes every map/depth within
the `2E` movement bound and 2,048 bytes.

### Protocol and optional skill

The always-on protocol prompt explains observation fields, `maze-nav/2`, legal actions, vision, and
corridor semantics. It contains no DFS strategy. The optional generic skill is:

`backend/genesis_arena/embodiment/skills/maze-navigation-v1/SKILL.md`

It gives generic DFS/backtracking/corridor/recovery/scratchpad discipline. Validation rejects seeds,
map IDs, coordinates, grid rows, explicit start/exit locations, and route sequences. The skill is
included exactly once and hash-bound only in skill-on requests. `ProviderRequest` itself is unchanged.

### Provider behavior

OpenAI adapter changes are in:

- `backend/genesis_arena/embodiment/providers/contracts.py`
- `backend/genesis_arena/embodiment/providers/openai_adapter.py`

The benchmark sets service tier `default`. Credential/quota failures stop a season. A unanimous
three-racer transport/rate-limit outage voids the race and allows one durable retry after 60 seconds;
a repeated outage stops with resumable state. Timeouts, refusals, malformed output, and illegal
actions remain model outcomes. Verify the durable retry logic and its interruption tests before live
execution.

### Benchmark package and CLI

Package: `backend/genesis_arena/embodiment/labyrinth_benchmark/`

- `spec.py`: frozen spec, model metadata, exact schedule construction, deterministic seat rotation,
  phase-specific deterministic shuffle, hashes, and strict 120/600/120 factorial validation.
- `artifacts.py`: canonical/atomic persistence, strict resume binding, public-safe validation, result
  schema/path checks, and artifact audit.
- `runner.py`: OpenAI executor, opaque model-facing episode IDs, sequential race orchestration,
  concurrent calls within a race, infrastructure retry/stop behavior, pilot gate, and cost projection.
- `analysis.py`: flattening, objective depth selection, deterministic hierarchical bootstrap,
  aggregates, paired skill effects, missing/censored counts, model comparisons, and CSV output.
- `report.py`: accessible deterministic SVG/PNG figures and self-contained HTML report; optional
  Matplotlib improves one publication raster while Pillow/SVG remain deterministic fallbacks.
- `cli.py`: `generate`, `pilot`, `run`, `analyze`, `report`, `acknowledge-stop`, and safe curated
  `export` commands.

Declared console entry point (rerun the editable install after checkout so the executable exists):

```bash
genesis-labyrinth-benchmark --season-id <season-id> <command>
```

Schedules rotate Sol/Terra/Luna across `participant_0..2`. Model-facing episode IDs are opaque hashes,
so map difficulty, seed, index, depth, and repetition are not leaked through the request identity.

### UI and Godot

Dashboard changes add the five vision choices and describe the independent dynamic per-racer budget.
The API keeps its compatibility input while the live runtime derives the actual map budget.

Godot now consumes replay-provided geometry and scales lanes/camera through 41×41. It renders
participant sight regions and path trails while retaining the full spectator maze. The historical
cached `trio-maze-race-v0` showcase is intentionally unchanged.

## 5. Exact experiment and statistics

Official schedules contain three concurrent model episodes per race:

| Phase | Maps | Depths | Repetitions | Races | Model episodes |
|---|---:|---:|---:|---:|---:|
| Pilot | 12 | 5 | 2 | 120 | 360 |
| Baseline | 40 | 5 | 3 | 600 | 1,800 |
| Skill | same 40 | selected 1 | 3 | 120 | 360 |
| Total | — | — | — | 840 | 2,520 |

Best depth selection order is frozen:

1. highest completion rate;
2. lowest median budget-charged calls (unfinished episodes receive the full budget);
3. lowest median total tokens;
4. shallower order: 1, 2, 4, 8, infinite.

The bootstrap resamples map seeds first, then repetitions inside each sampled map, using 10,000
deterministic replicates. Missing token telemetry is excluded and counted, never converted to zero.
Path efficiency is successful-run-only. The current runner persists zero for incomplete episodes and
analysis excludes those rows from successful-run aggregates. Keep tests for both behaviors so a short
incomplete path can never be reported as greater than 100% efficient.

Required reported metrics include completion, charged calls, successful-run path efficiency, cells
per call, corridor-command share, invalid/wait/repeated/recovery rates, separate input/output and total
tokens, latency, peak memory bytes, compaction events, provider outcomes, and infrastructure voids.

## 6. Known test evidence at handoff time

The runtime owner reported these passing in `WorldEval2`:

- Ruff for map/runtime files and three focused test files: pass.
- `test_live_labyrinth.py`, `test_live_labyrinth_media.py`, and `test_maze_maps.py`: 30 passed in
  about 54 seconds.
- Exhaustive 52 frozen maps × 5 depths provider-free DFS/memory test: pass in about 47 seconds.
- CamelCase/separator protected-key leak regressions: pass.
- Skill, timeout, fail-fast, and desynchronized-rate-limit subset: 6 passed.
- Dashboard Vitest suite earlier in this task: 10 files / 108 tests passed.
- Headless Godot audit earlier in this task: legacy plus 21×21, 31×31, and 41×41 geometry,
  visibility, trails, camera, and dynamic-tick scenarios passed.
- A native 41×41 MP4 smoke render passed earlier in this task.

These are not a substitute for rerunning the full gates from the handoff commit. Benchmark-tooling
tests now pass in focused runs: 23 benchmark/OpenAI tests passed in 7.1 seconds during independent
review, and the implementation owner later reported 45 benchmark/provider tests, 30 episode/API
tests, 9 live-Labyrinth tests, 2 media tests, 18 fast maze-map tests, plus the exhaustive frozen-map
test passing separately in 44.7 seconds. Ruff was clean.

The broad Pytest gate is **not green**. It collected 1,047 tests and completed 495 before teardown was
interrupted: 490 passed and 5 failed. Exact evidence:

- `tests/arena/test_benchmark_contract.py::test_benchmark_contract_hashes_match_frozen_sources`
  reproducibly fails because the pre-existing frozen `arena_rules.gd` digest expects `515dd825...`
  while the current unmodified file hashes to `f47cd46c...`.
- `tests/duel/test_duel_managed_process_launcher.py::test_launcher_uses_only_stdin_and_scrubs_one_use_material`
  failed in the broad run but passed immediately in isolation; treat it as a transient and rerun it.
- Both managed-demo parameterizations and the invalid-output recovery case in
  `tests/embodiment/test_control_game_product_integration.py` reproducibly return
  `embodiment_session_reset_failed` instead of `completed`.
- The next duo-game managed E2E case had already returned `duel_series_execution_failed` and then
  hung in Uvicorn teardown before it could be included in the completed count.

The three control-game failures still reproduced after terminating a stale two-hour Labyrinth Godot
process, so do not describe them as resolved. No live provider was involved. Frontend
lint/type-check/build and final report visual QA remain for the cloud continuation. See section 12.

## 7. Workspace setup

Use commands from the root of the cloud clone (the `WorldEval2` directory created in section 1).

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,benchmark]'
cd dashboard
pnpm install --frozen-lockfile
cd ..
```

Desktop-bundled Node tools on the current Mac are:

```text
/Users/arlind/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback/pnpm
/Users/arlind/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin
```

In another cloud environment, use its normal supported Node/pnpm installation and the lockfiles.
Do not commit dependency directories.

## 8. Generate and inspect a non-live season first

Choose a stable safe identifier, for example `labyrinth-2026-07-22-v1`.

```bash
.venv/bin/genesis-labyrinth-benchmark \
  --season-id labyrinth-2026-07-22-v1 generate
```

Expected output root:

```text
runs/labyrinth-benchmarks/labyrinth-2026-07-22-v1/
```

Before any live call, verify:

- pilot schedule count 120;
- baseline schedule count 600;
- no skill schedule until baseline objective selection;
- suite/spec/schedule hashes round-trip;
- all generated JSON is canonical;
- `runs/` remains ignored;
- repository and generated tree secret scans pass;
- no prompt, raw response, observation, scratchpad, or navigation-memory field is durable.

Run the artifact audit explicitly after generation:

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from genesis_arena.embodiment.labyrinth_benchmark.artifacts import BenchmarkArtifactStore

store = BenchmarkArtifactStore(
    Path("runs/labyrinth-benchmarks"), "labyrinth-2026-07-22-v1"
)
inventory = store.audit_public_tree()
print(f"audited {len(inventory['files'])} files")
PY
```

## 9. Live execution procedure

Only start this after section 12's provider-free gates pass and the cloud secret is configured.

Never put a key on the command line. One safe shell pattern is:

```bash
: "${OPENAI_API_KEY:?configure OPENAI_API_KEY in the cloud secret manager}"
.venv/bin/genesis-labyrinth-benchmark \
  --season-id labyrinth-2026-07-22-v1 pilot
```

The pilot must complete all 120 race results, pass artifact verification, produce no memory overflow,
and have real-provider infrastructure failures below 2%. Model failures remain valid outcomes. The
CLI persists and prints projected remaining calls, input/output tokens, elapsed time, and estimated
cost using the official pricing snapshot recorded in the artifact. The projection is pilot-scaled;
it is **not an upper bound** for harder maps.

After reviewing the persisted pilot gate/projection, the user's approved programme says to continue
automatically:

```bash
.venv/bin/genesis-labyrinth-benchmark \
  --season-id labyrinth-2026-07-22-v1 run --phase all
```

The command is resumable. Do not delete partial result files. If a credential/quota stop occurs,
repair the external condition and resume the same season; do not rewrite valid immutable results.
If an all-racer transport/rate-limit void repeats after its one retry, stop and preserve state.

After an operator has actually corrected a credential/quota condition or a repeated infrastructure
outage, explicitly acknowledge the terminal stop before resuming; this is an auditable action and is
not automatic:

```bash
.venv/bin/genesis-labyrinth-benchmark \
  --season-id labyrinth-2026-07-22-v1 acknowledge-stop
.venv/bin/genesis-labyrinth-benchmark \
  --season-id labyrinth-2026-07-22-v1 run --phase all
```

The exact theoretical cap from all frozen per-participant budgets is 2,051,280 model calls, although
corridor control should make observed use much lower. The season may still take many hours and incur
meaningful cost. Keep the user informed with race counts and numeric projections, never secrets or
raw model content.

`run --phase all` performs final analysis and report generation after the skill phase. The explicit
`analyze` and `report` commands below remain useful as idempotent verification/rerender steps.

## 10. Analyze and render

After exactly 120 + 600 + 120 results:

```bash
.venv/bin/genesis-labyrinth-benchmark \
  --season-id labyrinth-2026-07-22-v1 analyze
.venv/bin/genesis-labyrinth-benchmark \
  --season-id labyrinth-2026-07-22-v1 report
```

Inspect at minimum:

- `analysis/analysis.json`
- `analysis/aggregate.csv`
- `analysis/paired-skill.csv`
- `report/index.html`
- `report/manifest.json`
- every SVG and PNG in `report/figures/`

Open the HTML in a browser and visually inspect all plots at desktop and narrow widths. Open the PNGs
directly to catch clipped axes, labels, missing-cell crashes, overlapping annotations, inaccessible
colors, or misleading confidence bands. Confirm every figure/table states sample size, missingness,
censoring, and whether lower/higher is better where relevant.

## 11. Publication and Git scope

Raw season evidence remains under ignored `runs/` and must not be force-added wholesale. Use the
strict export command to create a deliberate tracked export:

```bash
.venv/bin/genesis-labyrinth-benchmark \
  --season-id labyrinth-2026-07-22-v1 export \
  --destination docs/labyrinth-benchmark/labyrinth-2026-07-22-v1
```

The export contains only:

- frozen `specification.json`;
- pilot and main map manifests;
- pilot, baseline, and skill schedules;
- `analysis.json`, aggregate CSV, and paired-skill CSV;
- the self-contained report HTML and report manifest;
- final PNG/SVG figures;
- a hash-bound `curated-manifest.json` that explicitly lists excluded raw categories.

The command does not create prose documentation. Add a short methodology/results README afterward if
desired, then scan it with the rest of the export.

Re-run secret/protected-material scanning on that export. Do not include per-race raw results even if
they pass the public schema unless the user separately approves publishing them.

Include only Labyrinth implementation/tooling/tests and the curated report in the eventual main
commit. Preserve but do not stage these unrelated user changes:

- `remotion/src/Root.tsx`
- `remotion/src/WorldEvalHackathonOpener.tsx`
- `remotion/src/WorldEvalCodexOutro.tsx`
- `docs/hackathon-gallery/`

Do not stage dependency, cache, virtual-environment, render-output, run, or generated `.uid` files.
In particular, remove or leave untracked the unrelated generated file
`godot/tests/embodiment/rts_skirmish_v1_headless_runner.gd.uid`.

Before committing, inspect `git diff --cached --name-status` and `git diff --cached`. The previous
instruction to push `main` applies only after the entire live season, report, and gates succeed.
Use a fast-forward-only publication flow; never force-push either repository:

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git merge --ff-only codex/labyrinth-benchmark-handoff
git push origin main
```

If the fast-forward merge fails, stop and inspect the new commits instead of rebasing or forcing
blindly. Re-run affected verification after any conflict resolution.

## 12. Required continuation checklist

Do these in order. Do not skip a failed step.

1. Read `git status`, this handoff, and all changed Labyrinth modules before editing.
2. Verify the implemented benchmark artifact hardening and fix it only if a gate fails:
   - strict state schema/spec/type validation;
   - result filename must match embedded race ID;
   - durable one-retry state across interruption during the 60-second delay;
   - recompute decision-derived metrics from safe traces/path instead of trusting serialized values;
   - robust camelCase/separator/prefix protected-key rejection;
   - correct incomplete-run path-efficiency handling.
3. Verify the implemented analysis/report robustness and fix it only if a gate fails:
   - aggregate separate input/output tokens and latency with missing counts;
   - allow a model/difficulty/depth cell with no completed paths or no recovery opportunities to
     render as missing rather than crash;
   - make Matplotlib/fallback CLI wording truthful;
   - verify paired completion/calls/tokens/path effects for every model/difficulty.
4. Verify the existing provider-free benchmark tests and add only missing coverage for:
   - exact schedule counts/order/seat rotation/hashes;
   - selected-depth tie-breaks;
   - resume/idempotency and immutable atomic writes;
   - durable void/requeue rules and fatal credential/quota behavior;
   - strict artifact schemas and adversarial secret/protected-field variants;
   - fixed hierarchical-bootstrap fixture and true 2.5/97.5 percentiles;
   - report generation with complete and deliberately missing metric cells;
   - OpenAI payload `store: false`, no chaining, service tier, low reasoning, quota classification,
     and cache-write telemetry behavior.
5. Install dev + benchmark extras and run full Ruff/Pytest.
6. Run dashboard tests, lint, type-check, production build, and `pnpm build:pages` when validating
   parity with the repository's Pages deployment.
7. Use Godot 4.5 for headless scenarios and the dynamic 41×41 presentation smoke. Do not count a
   Mac-path skip as a pass on Linux. Set `GODOT_BIN` to the real Godot 4.5 executable and run at
   minimum:

   ```bash
   "$GODOT_BIN" --headless --audio-driver Dummy --path godot \
     --script res://tests/embodiment/trio_games/trio_maze_race_headless_runner.gd
   ```
8. Generate a throwaway provider-free season and audit every artifact.
9. Configure `OPENAI_API_KEY` through the cloud secret manager; run the 120-race pilot.
10. If the technical gate passes, resume automatically through baseline and skill study.
11. Analyze, render, visually inspect, and leak-scan the completed report.
12. Create the curated tracked export, stage only approved files, commit, and push `WorldEval2/main`.

Suggested backend verification commands (adjust only for the actual project tooling):

```bash
.venv/bin/ruff check backend tests
.venv/bin/pytest
```

Suggested dashboard commands:

```bash
cd dashboard
pnpm test -- --run
pnpm lint
pnpm typecheck
pnpm build
pnpm build:pages
```

Return to the repository root before Git operations.

## 13. Recommended cloud sub-agent delegation

Use sub-agents if the cloud environment supports them. Keep one primary coordinator responsible for
Git, credentials, frozen hashes, and the single live season. Never let two agents run or mutate the
same season concurrently.

Suggested team, in this order:

1. **Benchmark integrity auditor (read-only)**
   - Read `spec.py`, `artifacts.py`, `runner.py`, `analysis.py`, `report.py`, and benchmark tests.
   - Recompute schedule counts/hashes and the 2,051,280 theoretical call cap independently.
   - Probe resume binding, retry durability, secret/protected-field rejection, missing telemetry,
     incomplete-run censoring, and paired-skill matching.
   - Do not edit while another agent edits those files. Return exact findings to the coordinator.
2. **Runtime and provider-free test owner**
   - Own `maze_maps.py`, `live_labyrinth.py`, the skill file, and their focused tests only if fixes are
     required.
   - Run all 52 maps × 5 vision depths with the deterministic DFS fixture and prove `2E`, 2,048-byte,
     isolation, cycle, invalid-response, and close behavior.
   - Do not call a live provider and do not change frozen hashes without coordinator approval.
3. **Dashboard and Godot verifier**
   - Own only dashboard/Godot fixes required by failing gates.
   - Run Vitest/lint/type-check/build/build:pages and Godot 4.5 headless tests.
   - Inspect 15×15 through 41×41 scaling, vision highlighting, trails, camera framing, and natural
     corridor animation. Report Linux/Mac skips explicitly.
4. **Statistics/report reviewer (read-only until baseline completes)**
   - Validate bootstrap percentiles, map-then-repetition resampling, missing/censored counts, selection
     tie-breaks, and paired effects.
   - After the season, inspect every HTML/SVG/PNG/CSV output for numerical and visual truthfulness.
   - It may propose report fixes, but the coordinator should rerun hashes and the export afterward.
5. **Live-run monitor (only after all offline gates pass)**
   - The coordinator should normally retain this role. If delegated, give exactly one agent access to
     the configured `OPENAI_API_KEY` secret and the season command.
   - It may monitor state/progress and resume through the CLI, but must never print the key, raw model
     responses, prompts, scratchpads, or memory contents.
   - It must stop on credential/quota or repeated-infrastructure terminal state and ask the coordinator
     to verify the external fix before `acknowledge-stop`.

Recommended coordination rules:

- Start with read-only audits in parallel; serialize edits to overlapping files.
- Give each writing agent an explicit file ownership list and require a final diff plus exact test
  command/output summary.
- The primary coordinator reviews and integrates every sub-agent change; a passing focused test is not
  permission to skip the full gates.
- Only the primary coordinator stages, commits, exports curated artifacts, or pushes branches.
- No agent may access or modify `AVisha2000/WorldEval` or the archived old local checkout.

## 14. Final report back to the user

When complete, report concise, reproducible facts:

- final race count and model-episode count;
- total and per-model calls, input/output/total tokens, and elapsed time;
- infrastructure voids by phase and model-failure counts;
- selected skill vision depth and the exact selection metrics;
- completion/call/token/path paired skill effects with 95% intervals;
- whether skilled Luna matched/exceeded unskilled Sol and Terra under the frozen rule;
- spec, map-manifest, schedule, analysis, and report hashes;
- paths/links to the safe report and CSVs;
- exact test/build commands and outcomes;
- final commit and push target.

Do not present private model reasoning. If the user asks how agents reasoned, explain observable
strategy from validated action traces and memory state at a high level, not hidden chain-of-thought.
