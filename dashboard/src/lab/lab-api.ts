import type {
  LabBenchmark,
  LabAuthMode,
  LabCell,
  LabControl,
  LabGame,
  LabLaunchInput,
  LabLeaderboardRow,
  LabMazeFrame,
  LabMazeRacer,
  LabMetric,
  LabProvider,
  LabProjection,
  LabReadiness,
  LabRun,
  LabSession,
  LabSpectatorFeed,
  LabSpectatorFrame,
} from "./types"

type JsonRecord = Record<string, unknown>

function record(value: unknown): JsonRecord | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonRecord)
    : null
}

function string(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null
}

function sha256(value: unknown): string | null {
  const parsed = string(value)
  return parsed && /^[a-f0-9]{64}$/i.test(parsed) ? parsed.toLowerCase() : null
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.flatMap((item) => {
        const parsed = string(item)
        return parsed ? [parsed] : []
      })
    : []
}

function provider(value: unknown): LabProvider {
  return value === "openai" || value === "anthropic" || value === "gemini"
    ? value
    : null
}

function records(value: unknown): JsonRecord[] {
  return Array.isArray(value)
    ? value.flatMap((item) => {
        const parsed = record(item)
        return parsed ? [parsed] : []
      })
    : []
}

async function json(endpoint: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(endpoint, {
    cache: "no-store",
    credentials: "same-origin",
    ...init,
  })
  if (!response.ok) throw new Error(`${endpoint} returned ${response.status}`)
  return response.json() as Promise<unknown>
}

export async function getLabSession(): Promise<LabSession | null> {
  const response = await fetch("/api/auth/me", {
    cache: "no-store",
    credentials: "same-origin",
  })
  if (response.status === 401) return null
  if (!response.ok) throw new Error(`/api/auth/me returned ${response.status}`)
  const root = record(await response.json())
  const operatorRecord = root ? record(root.operator) : null
  const operator = root
    ? (string(root.operator) ??
      string(operatorRecord?.email) ??
      string(operatorRecord?.member_id))
    : null
  const csrfToken = root ? string(root.csrf_token) : null
  return operator && csrfToken ? { operator, csrfToken } : null
}

export async function getLabAuthMode(): Promise<LabAuthMode> {
  const root = record(await json("/api/auth/configuration"))
  const mode = root ? string(root.mode) : null
  if (mode === "local" || mode === "magic_link") return mode
  throw new Error("Lab authentication configuration is invalid")
}

export async function connectLocalLabSession(): Promise<LabSession> {
  const response = await fetch("/api/auth/local-login", {
    method: "POST",
    cache: "no-store",
    credentials: "same-origin",
  })
  if (!response.ok)
    throw new Error(`/api/auth/local-login returned ${response.status}`)
  const session = await getLabSession()
  if (!session) throw new Error("local session was not established")
  return session
}

export async function requestLabMagicLink(email: string): Promise<void> {
  const response = await fetch("/api/auth/magic-link", {
    method: "POST",
    cache: "no-store",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  })
  if (!response.ok)
    throw new Error(`/api/auth/magic-link returned ${response.status}`)
}

function readiness(value: unknown): LabReadiness {
  if (
    value === "live_ready" ||
    value === "demo_replay_ready" ||
    value === "experimental"
  ) {
    return value
  }
  return "experimental"
}

function metric(value: JsonRecord): LabMetric | null {
  const id = string(value.id)
  const label = string(value.label)
  const description = string(value.description)
  return id && label && description ? { id, label, description } : null
}

function control(value: JsonRecord): LabControl | null {
  const id = string(value.id)
  const label = string(value.label)
  const controlType = string(value.control_type) ?? "fixed"
  const description = string(value.description) ?? ""
  return id && label
    ? { id, label, controlType, description, options: strings(value.options) }
    : null
}

function game(value: JsonRecord): LabGame | null {
  const id = string(value.id)
  const title = string(value.title)
  const agentInterface = record(value.agent_interface)
  const scoring = record(value.scoring)
  const safety = record(value.safety)
  if (!id || !title || !agentInterface || !scoring || !safety) return null

  const observation = string(agentInterface.observation)
  const actions = string(agentInterface.actions)
  const memory = string(agentInterface.memory)
  const scoringSummary = string(scoring.summary)
  const publicView = string(safety.public_view)
  const privateAgentState = string(safety.private_agent_state)
  if (
    !observation ||
    !actions ||
    !memory ||
    !scoringSummary ||
    !publicView ||
    !privateAgentState
  ) {
    return null
  }

  const metrics = records(scoring.metrics).flatMap((item) => {
    const parsed = metric(item)
    return parsed ? [parsed] : []
  })
  const configurationControls = records(value.configuration_controls).flatMap(
    (item) => {
      const parsed = control(item)
      return parsed ? [parsed] : []
    }
  )
  const supportedModes = records(value.supported_modes).flatMap((item) => {
    const modeId = string(item.id)
    const modeLabel = string(item.label)
    const modeDescription = string(item.description)
    return modeId && modeLabel && modeDescription
      ? [{ id: modeId, label: modeLabel, description: modeDescription }]
      : []
  })

  return {
    id,
    title,
    readiness: readiness(value.readiness),
    readinessNote:
      string(value.readiness_note) ?? "No readiness note is published.",
    taskIds: strings(value.task_ids),
    capabilityStatements: strings(value.capability_statements),
    agentInterface: { observation, actions, memory },
    scoring: { summary: scoringSummary, metrics },
    configurationControls,
    failureModes: strings(value.failure_modes),
    safety: { publicView, privateAgentState },
    supportedModes,
  }
}

export async function getLabGames(): Promise<LabGame[]> {
  const raw = await json("/api/lab/games")
  const root = record(raw)
  const candidates = Array.isArray(raw) ? raw : root?.games
  return records(candidates)
    .flatMap((item) => {
      const parsed = game(item)
      return parsed ? [parsed] : []
    })
    .sort((first, second) => first.title.localeCompare(second.title))
}

export async function getPublicLabGames(): Promise<LabGame[]> {
  const raw = await json("/api/public/games")
  const root = record(raw)
  const candidates = Array.isArray(raw) ? raw : root?.games
  return records(candidates)
    .flatMap((item) => {
      const parsed = game(item)
      return parsed ? [parsed] : []
    })
    .sort((first, second) => first.title.localeCompare(second.title))
}

export async function getPublicLabGame(gameId: string): Promise<LabGame> {
  const raw = await json(`/api/public/games/${encodeURIComponent(gameId)}`)
  const parsed = record(raw)
  const result = parsed ? game(parsed) : null
  if (!result) throw new Error("public game payload is invalid")
  return result
}

function run(value: JsonRecord): LabRun | null {
  const contract = record(value.contract)
  const state = record(value.state)
  const id = string(value.run_id) ?? string(value.id)
  const gameId = string(value.game_id) ?? string(contract?.game_id)
  const lifecycle =
    string(value.lifecycle) ?? string(state?.status) ?? string(state?.lifecycle)
  if (!id || !gameId || !lifecycle) return null

  const lineageDiff = record(contract?.lineage_diff)
  const entrants = records(contract?.entrants)
  const providers = entrants.map((entrant) => provider(entrant.provider))
  const contractProvider =
    providers.length && providers[0] && providers.every((item) => item === providers[0])
      ? providers[0]
      : null

  return {
    id,
    gameId,
    gameVersion: string(value.game_version) ?? string(contract?.game_version),
    lifecycle,
    mode: string(value.mode) ?? string(contract?.mode),
    provider: contractProvider,
    authorityAvailable:
      typeof value.authority_available === "boolean"
        ? value.authority_available
        : null,
    contractSha256: sha256(contract?.contract_sha256),
    parentContractSha256: sha256(contract?.parent_contract_sha256),
    lineageChangeCount: lineageDiff ? Object.keys(lineageDiff).length : 0,
    replayAvailable: value.replay_available === true,
    videoAvailable: value.video_available === true,
    resumeSupported: value.resume_supported === true,
    createdAt:
      string(value.created_at) ?? epochToIso(value.created_at_epoch_ms),
  }
}

function integer(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) ? value : null
}

function cell(value: unknown): LabCell | null {
  if (!Array.isArray(value) || value.length !== 2) return null
  const x = integer(value[0])
  const y = integer(value[1])
  return x === null || y === null ? null : [x, y]
}

function cells(value: unknown): LabCell[] | null {
  if (!Array.isArray(value)) return null
  const parsed = value.map(cell)
  return parsed.every((item): item is LabCell => item !== null) ? parsed : null
}

function mazeRacer(value: JsonRecord): LabMazeRacer | null {
  const participantId = string(value.participant_id)
  const entrantId = string(value.entrant_id)
  const displayName = string(value.display_name)
  const color = string(value.color)
  const position = cell(value.position) ?? cell(value.final_cell)
  const path = cells(value.path)
  const visibleCells = cells(value.visible_cells) ?? []
  const providerCalls = integer(value.provider_calls) ?? 0
  const finished = value.finished === true
  if (
    !participantId ||
    !entrantId ||
    !displayName ||
    !color ||
    !position ||
    !path?.length ||
    path.at(-1)?.[0] !== position[0] ||
    path.at(-1)?.[1] !== position[1] ||
    providerCalls < 0
  ) {
    return null
  }
  return {
    participantId,
    entrantId,
    displayName,
    color,
    position,
    path,
    visibleCells,
    providerCalls,
    finished,
  }
}

function mazeFrame(value: JsonRecord | null): LabMazeFrame | null {
  if (!value) return null
  const map = record(value.map)
  const racers = records(value.racers).flatMap((item) => {
    const parsed = mazeRacer(item)
    return parsed ? [parsed] : []
  })
  const rows = map ? strings(map.rows) : []
  const start = map ? cell(map.start) : null
  const exit = map ? cell(map.exit) : null
  const tick = integer(value.tick) ?? integer(value.elapsed_ticks)
  const providerCalls = integer(value.provider_calls)
  if (
    !rows.length ||
    !start ||
    !exit ||
    tick === null ||
    providerCalls === null ||
    providerCalls < 0 ||
    racers.length !== 3
  ) {
    return null
  }
  return { tick, providerCalls, map: { rows, start, exit }, racers }
}

function epochToIso(value: unknown): string | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? new Date(value).toISOString()
    : null
}

export async function getLabRuns(): Promise<LabRun[]> {
  const raw = await json("/api/lab/runs")
  const root = record(raw)
  const candidates = Array.isArray(raw) ? raw : root?.runs
  return records(candidates).flatMap((item) => {
    const parsed = run(item)
    return parsed ? [parsed] : []
  })
}

export async function getLabRun(runId: string): Promise<LabRun> {
  const raw = await json(`/api/lab/runs/${encodeURIComponent(runId)}`)
  const parsed = record(raw)
  const result = parsed ? run(parsed) : null
  if (!result) throw new Error("Lab run payload is invalid")
  return result
}

/**
 * Create an exact, separately persisted frozen clone. The empty configuration
 * patch is intentional: the service copies the parent configuration verbatim
 * while recording parent-contract lineage on the new draft.
 */
export async function cloneLabRun(
  runId: string,
  csrfToken: string
): Promise<LabRun> {
  const raw = await json(`/api/lab/runs/${encodeURIComponent(runId)}/clone`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-WorldEval-CSRF": csrfToken,
    },
    body: JSON.stringify({ changes: { configuration: {} } }),
  })
  const parsed = record(raw)
  const result = parsed ? run(parsed) : null
  if (
    !result ||
    result.lifecycle !== "draft" ||
    result.provider !== "openai" ||
    !result.contractSha256 ||
    !result.parentContractSha256
  ) {
    throw new Error("Lab clone draft payload is invalid")
  }
  return result
}

/**
 * Start an existing frozen clone. Deliberately accepts only the new
 * session-only API key: all game configuration remains in the draft contract.
 */
export async function launchLabRunDraft(
  runId: string,
  apiKey: string,
  csrfToken: string
): Promise<LabRun> {
  const raw = await json(`/api/lab/runs/${encodeURIComponent(runId)}/launch`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-WorldEval-CSRF": csrfToken,
    },
    body: JSON.stringify({ api_key: apiKey }),
  })
  const parsed = record(raw)
  const result = parsed ? run(parsed) : null
  if (
    !result ||
    result.id !== runId ||
    result.lifecycle === "draft" ||
    result.provider !== "openai"
  ) {
    throw new Error("Lab draft launch payload is invalid")
  }
  return result
}

export async function getLabRunProjection(
  runId: string
): Promise<LabProjection> {
  const raw = await json(
    `/api/lab/runs/${encodeURIComponent(runId)}/projection`
  )
  const root = record(raw)
  const snapshot = root ? record(root.snapshot) : null
  const runIdFromPayload = root ? string(root.run_id) : null
  const lifecycle = root ? string(root.status) : null
  if (!root || !snapshot || !runIdFromPayload || !lifecycle)
    throw new Error("Lab replay projection is invalid")
  return {
    runId: runIdFromPayload,
    lifecycle,
    frame: mazeFrame(record(snapshot.arena)) ?? mazeFrame(snapshot),
  }
}

export async function getLabRunSpectator(
  runId: string,
  afterSequence: number
): Promise<LabSpectatorFeed> {
  if (!Number.isInteger(afterSequence) || afterSequence < 0) {
    throw new Error("Lab spectator cursor is invalid")
  }
  const raw = await json(
    `/api/lab/runs/${encodeURIComponent(runId)}/spectator?after=${afterSequence}`
  )
  const root = record(raw)
  const schemaVersion = root ? string(root.schema_version) : null
  const runIdFromPayload = root ? string(root.run_id) : null
  const cursor = root ? integer(root.cursor) : null
  const resetRequired = root?.reset_required
  if (
    !root ||
    schemaVersion !== "worldeval/lab-live-spectator-feed/1" ||
    !runIdFromPayload ||
    runIdFromPayload !== runId ||
    cursor === null ||
    cursor < 0 ||
    typeof resetRequired !== "boolean"
  ) {
    throw new Error("Lab spectator feed is invalid")
  }
  const frames: LabSpectatorFrame[] = []
  let priorSequence = 0
  for (const item of records(root.frames)) {
    const sequence = integer(item.sequence)
    const frame = mazeFrame(record(item.frame))
    if (
      sequence === null ||
      sequence <= priorSequence ||
      sequence > cursor ||
      !frame
    ) {
      throw new Error("Lab spectator feed is invalid")
    }
    frames.push({ sequence, frame })
    priorSequence = sequence
  }
  return { runId: runIdFromPayload, cursor, resetRequired, frames }
}

export function labRunVideoUrl(runId: string): string {
  return `/api/lab/runs/${encodeURIComponent(runId)}/video`
}

function leaderboardRow(value: JsonRecord): LabLeaderboardRow | null {
  const model =
    string(value.model) ?? string(value.model_id) ?? string(value.label)
  if (!model) return null
  const asDisplayValue = (input: unknown): string | null => {
    const text = string(input)
    if (text) return text
    return typeof input === "number" && Number.isFinite(input)
      ? String(input)
      : null
  }
  return {
    model,
    completion:
      asDisplayValue(value.completion) ?? asDisplayValue(value.completion_rate),
    calls:
      asDisplayValue(value.calls) ?? asDisplayValue(value.budget_charged_calls),
    pathEfficiency: asDisplayValue(value.path_efficiency),
    cost: asDisplayValue(value.cost),
    evidence:
      asDisplayValue(value.evidence) ?? asDisplayValue(value.sample_count),
  }
}

export async function getLabyrinthBenchmark(): Promise<LabBenchmark> {
  const raw = await json("/api/lab/benchmarks/labyrinth-run")
  return parseBenchmark(raw)
}

export async function getPublicGameBenchmark(
  gameId: string
): Promise<LabBenchmark> {
  const raw = await json(
    `/api/public/games/${encodeURIComponent(gameId)}/benchmark`
  )
  return parseBenchmark(raw)
}

function parseBenchmark(raw: unknown): LabBenchmark {
  const root = record(raw)
  if (!root) throw new Error("benchmark payload is not an object")
  const nestedLeaderboard = record(root.leaderboard)
  const candidates = Array.isArray(root.verified_results)
    ? root.verified_results
    : Array.isArray(root.leaderboard)
      ? root.leaderboard
      : (nestedLeaderboard?.rows ?? nestedLeaderboard?.models)
  return {
    gameId: string(root.game_id) ?? "labyrinth-run",
    seasonState: string(root.season_state) ?? "unknown",
    leaderboard: records(candidates).flatMap((item) => {
      const parsed = leaderboardRow(item)
      return parsed ? [parsed] : []
    }),
    recipeCount: Array.isArray(root.recipes) ? root.recipes.length : 0,
    message: string(root.message),
  }
}

export async function launchLabyrinth(
  input: LabLaunchInput,
  csrfToken: string
): Promise<string | null> {
  const raw = await json("/api/lab/runs/labyrinth", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-WorldEval-CSRF": csrfToken,
    },
    body: JSON.stringify({
      api_key: input.apiKey,
      provider: input.provider,
      entrants: [
        { display_name: "Sol", model: input.models.sol },
        { display_name: "Terra", model: input.models.terra },
        { display_name: "Luna", model: input.models.luna },
      ],
      vision_range_cells:
        input.visionRangeCells === "infinite"
          ? "infinite"
          : Number(input.visionRangeCells),
      skill_mode: input.skillMode,
      mode: input.mode,
    }),
  })
  const root = record(raw)
  return root ? (string(root.run_id) ?? string(root.episode_id)) : null
}
