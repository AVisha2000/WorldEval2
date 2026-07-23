import type {
  LabBenchmark,
  LabAuthMode,
  LabCell,
  LabControl,
  LabGame,
  LabGameCapabilities,
  LabGameCategory,
  LabGameCategoryId,
  LabGenericLaunchInput,
  LabGenericProjectionSummary,
  LabInteractionKind,
  LabLaunchInput,
  LabLeaderboardRow,
  LabMazeFrame,
  LabMazeRacer,
  LabMetric,
  LabProvider,
  LabProjection,
  LabPublicReplay,
  LabReadiness,
  LabRun,
  LabRunEvidenceResult,
  LabSandboxManifest,
  LabSandboxPrimitive,
  LabSandboxRecipe,
  LabSession,
  LabSpectatorFeed,
  LabSpectatorFrame,
} from "./types"
import { isOpenAiLabRunMode, LAB_GAME_CATEGORIES } from "./types"

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

function runMode(value: unknown): LabRun["mode"] {
  return value === "demo" ||
    value === "exploratory" ||
    value === "sealed_benchmark"
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

const categoryById = new Map(
  LAB_GAME_CATEGORIES.map((category) => [category.id, category])
)
const safeIdentifierPattern = /^[a-z0-9][a-z0-9_-]{0,95}$/
const runLifecycles = new Set([
  "draft",
  "queued",
  "running",
  "checkpointed",
  "completed",
  "failed",
  "cancelled",
  "sealed",
  "verified",
])
const capabilityKeys = [
  "live_launch",
  "demo",
  "replay",
  "spectator",
  "benchmark",
  "checkpoint",
] as const

type ParsedCatalogueCategory = {
  category: LabGameCategory
  gameIds: Set<string>
}

function safeText(value: unknown, maximumLength: number): string | null {
  const parsed = string(value)
  return parsed &&
    parsed.length <= maximumLength &&
    !parsed.includes("\0") &&
    parsed === parsed.trim()
    ? parsed
    : null
}

function safeIdentifier(value: unknown): string | null {
  const parsed = string(value)
  return parsed && safeIdentifierPattern.test(parsed) ? parsed : null
}

function modelIdentifier(value: unknown): string | null {
  const parsed = safeText(value, 128)
  return parsed && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(parsed)
    ? parsed
    : null
}

function categoryId(value: unknown): LabGameCategoryId | null {
  const parsed = safeIdentifier(value)
  return parsed && categoryById.has(parsed as LabGameCategoryId)
    ? (parsed as LabGameCategoryId)
    : null
}

function category(value: unknown): LabGameCategory | null {
  const item = record(value)
  const id = categoryId(item?.id)
  if (!item || !id) return null
  const fallback = categoryById.get(id)
  if (!fallback) return null
  const label = safeText(item.label, 96)
  const description = safeText(item.description, 320)
  const order = item.order
  return label &&
    description &&
    typeof order === "number" &&
    Number.isInteger(order) &&
    order === fallback.order
    ? { id, label, description, order }
    : fallback
}

function categoryProjection(value: unknown): ParsedCatalogueCategory[] {
  const seen = new Set<LabGameCategoryId>()
  const parsed: ParsedCatalogueCategory[] = []
  for (const item of records(value)) {
    const parsedCategory = category(item)
    if (!parsedCategory || seen.has(parsedCategory.id)) continue
    const rawGameIds = item.game_ids
    if (!Array.isArray(rawGameIds) || rawGameIds.length > 500) continue
    const gameIds = rawGameIds.map(safeIdentifier)
    if (
      gameIds.some((gameId) => gameId === null) ||
      new Set(gameIds).size !== gameIds.length
    ) {
      continue
    }
    seen.add(parsedCategory.id)
    parsed.push({
      category: parsedCategory,
      gameIds: new Set(gameIds as string[]),
    })
  }
  return parsed
}

function participantRange(
  value: unknown
): { minimum: number; maximum: number } | null {
  const item = record(value)
  const minimum = item?.minimum
  const maximum = item?.maximum
  return item &&
    typeof minimum === "number" &&
    Number.isInteger(minimum) &&
    minimum >= 1 &&
    minimum <= 64 &&
    typeof maximum === "number" &&
    Number.isInteger(maximum) &&
    maximum >= minimum &&
    maximum <= 64
    ? { minimum, maximum }
    : null
}

function interactionKind(
  value: unknown,
  maximumParticipants: number
): LabInteractionKind {
  if (
    value === "solo" ||
    value === "cooperative" ||
    value === "competitive" ||
    value === "mixed"
  ) {
    if (
      (value === "solo" && maximumParticipants === 1) ||
      (value !== "solo" && maximumParticipants >= 2)
    ) {
      return value
    }
  }
  return maximumParticipants === 1 ? "solo" : "mixed"
}

function capabilities(value: unknown): LabGameCapabilities {
  const item = record(value)
  const values = Object.fromEntries(
    capabilityKeys.map((key) => [
      key,
      item?.[key] === true || item?.[key] === false ? item[key] : false,
    ])
  ) as Record<(typeof capabilityKeys)[number], boolean>
  return {
    liveLaunch: values.live_launch,
    demo: values.demo,
    replay: values.replay,
    spectator: values.spectator,
    benchmark: values.benchmark && values.replay,
    checkpoint: values.checkpoint,
  }
}

function secondaryTags(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return [
    ...new Set(
      value
        .slice(0, 24)
        .map(safeIdentifier)
        .filter((tag): tag is string => tag !== null)
    ),
  ].sort()
}

function inferredCategory(maximumParticipants: number): LabGameCategory {
  const id: LabGameCategoryId =
    maximumParticipants === 1
      ? "solo-agent-tasks"
      : maximumParticipants === 2
        ? "two-agent-games"
        : "multi-agent-games"
  return categoryById.get(id) as LabGameCategory
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

function game(
  value: JsonRecord,
  catalogueCategories: ParsedCatalogueCategory[] = []
): LabGame | null {
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
  const participants =
    participantRange(value.participants) ??
    ({ minimum: 1, maximum: 1 } as const)
  const inlineCategory = category(value.primary_category)
  const projectedCategories = catalogueCategories.filter((candidate) =>
    candidate.gameIds.has(id)
  )
  const projectedCategory =
    projectedCategories.length === 1 ? projectedCategories[0].category : null
  const primaryCategory =
    inlineCategory && projectedCategory
      ? inlineCategory.id === projectedCategory.id
        ? projectedCategory
        : inferredCategory(participants.maximum)
      : (inlineCategory ??
        projectedCategory ??
        inferredCategory(participants.maximum))

  return {
    id,
    title,
    readiness: readiness(value.readiness),
    readinessNote:
      string(value.readiness_note) ?? "No readiness note is published.",
    primaryCategory,
    secondaryTags: secondaryTags(value.secondary_tags),
    participants,
    interactionKind: interactionKind(
      value.interaction_kind,
      participants.maximum
    ),
    capabilities: capabilities(value.capabilities),
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
  const categories = categoryProjection(root?.categories)
  return records(candidates)
    .flatMap((item) => {
      const parsed = game(item, categories)
      return parsed ? [parsed] : []
    })
    .sort((first, second) => first.title.localeCompare(second.title))
}

export async function getPublicLabGames(): Promise<LabGame[]> {
  const raw = await json("/api/public/games")
  const root = record(raw)
  const candidates = Array.isArray(raw) ? raw : root?.games
  const categories = categoryProjection(root?.categories)
  return records(candidates)
    .flatMap((item) => {
      const parsed = game(item, categories)
      return parsed ? [parsed] : []
    })
    .sort((first, second) => first.title.localeCompare(second.title))
}

export async function getPublicLabGame(gameId: string): Promise<LabGame> {
  const expectedGameId = safeIdentifier(gameId)
  if (!expectedGameId) throw new Error("public game id is invalid")
  const raw = await json(
    `/api/public/games/${encodeURIComponent(expectedGameId)}`
  )
  const parsed = record(raw)
  const result = parsed ? game(parsed) : null
  if (!result || result.id !== expectedGameId) {
    throw new Error("public game payload is invalid")
  }
  return result
}

function safeIdentifierArray(
  value: unknown,
  { allowEmpty, maximumLength }: { maximumLength: number; allowEmpty: boolean }
): string[] | null {
  if (!Array.isArray(value) || value.length > maximumLength) return null
  const parsed = value.map(safeIdentifier)
  if (
    (!allowEmpty && parsed.length === 0) ||
    parsed.some((item) => item === null) ||
    new Set(parsed).size !== parsed.length
  ) {
    return null
  }
  return parsed as string[]
}

function sandboxPrimitive(value: JsonRecord): LabSandboxPrimitive | null {
  const id = safeIdentifier(value.id)
  const title = safeText(value.title, 96)
  const summary = safeText(value.summary, 480)
  const compositionRank = integer(value.composition_rank)
  const dependencies = safeIdentifierArray(value.dependencies, {
    maximumLength: 10,
    allowEmpty: true,
  })
  const primitiveCapabilities = safeIdentifierArray(value.capabilities, {
    maximumLength: 24,
    allowEmpty: false,
  })
  if (
    !id ||
    !title ||
    !summary ||
    compositionRank === null ||
    compositionRank < 0 ||
    compositionRank > 10_000 ||
    !dependencies ||
    !primitiveCapabilities ||
    !sha256(value.spec_sha256) ||
    value.authority_owner !== "godot" ||
    value.schema_version !== "worldeval/sandbox-primitive/1"
  ) {
    return null
  }
  return {
    id,
    title,
    summary,
    compositionRank,
    dependencies,
    capabilities: primitiveCapabilities,
  }
}

function sandboxRecipe(value: JsonRecord): LabSandboxRecipe | null {
  const id = safeIdentifier(value.id)
  const title = safeText(value.title, 120)
  const summary = safeText(value.summary, 600)
  const lifecycle = value.lifecycle
  const executable = value.executable
  const primitiveIds = safeIdentifierArray(value.primitive_ids, {
    maximumLength: 32,
    allowEmpty: false,
  })
  const compositionOrder = safeIdentifierArray(value.composition_order, {
    maximumLength: 32,
    allowEmpty: false,
  })
  const authorityBinding = record(value.authority_binding)
  const authorityOwner = authorityBinding
    ? safeIdentifier(authorityBinding.authority_owner)
    : null
  const taskId = authorityBinding
    ? safeIdentifier(authorityBinding.task_id)
    : null
  const protocolVersion = authorityBinding
    ? safeText(authorityBinding.protocol_version, 128)
    : null
  if (
    !id ||
    !title ||
    !summary ||
    (lifecycle !== "canonical" && lifecycle !== "draft") ||
    typeof executable !== "boolean" ||
    !primitiveIds ||
    !compositionOrder ||
    !sha256(value.recipe_sha256) ||
    value.schema_version !== "worldeval/sandbox-recipe/1" ||
    (executable
      ? lifecycle !== "canonical" ||
        id !== "operator-action-course-sandbox-v1" ||
        authorityOwner !== "godot" ||
        taskId !== "operator-action-course-v0" ||
        protocolVersion !== "llm-controller/0.2.0"
      : lifecycle !== "draft" || authorityBinding !== null)
  ) {
    return null
  }
  return {
    id,
    title,
    summary,
    lifecycle,
    executable,
    primitiveIds,
    compositionOrder,
    authorityOwner: authorityOwner === "godot" ? "godot" : null,
    taskId,
    protocolVersion,
  }
}

export async function getLabSandboxManifest(): Promise<LabSandboxManifest> {
  const root = record(await json("/api/lab/sandbox"))
  const rawPrimitives = root ? records(root.primitives) : []
  const rawRecipes = root ? records(root.recipes) : []
  if (
    !root ||
    root.schema_version !== "worldeval/sandbox-manifest/1" ||
    root.authority_owner !== "godot" ||
    !sha256(root.manifest_sha256) ||
    rawPrimitives.length !== 10 ||
    rawRecipes.length !== 1
  ) {
    throw new Error("Lab sandbox manifest is invalid")
  }
  const primitives = rawPrimitives.flatMap((item) => {
    const parsed = sandboxPrimitive(item)
    return parsed ? [parsed] : []
  })
  const recipes = rawRecipes.flatMap((item) => {
    const parsed = sandboxRecipe(item)
    return parsed ? [parsed] : []
  })
  const primitiveIds = new Set(primitives.map((primitive) => primitive.id))
  const ranks = new Set(
    primitives.map((primitive) => primitive.compositionRank)
  )
  const orderedPrimitiveIds = [...primitives]
    .sort((first, second) => first.compositionRank - second.compositionRank)
    .map((primitive) => primitive.id)
  const executableRecipes = recipes.filter((recipe) => recipe.executable)
  if (
    primitives.length !== rawPrimitives.length ||
    recipes.length !== rawRecipes.length ||
    primitiveIds.size !== primitives.length ||
    ranks.size !== primitives.length ||
    primitives.some(
      (primitive) =>
        primitive.dependencies.includes(primitive.id) ||
        primitive.dependencies.some(
          (dependency) => !primitiveIds.has(dependency)
        )
    ) ||
    executableRecipes.length !== 1 ||
    recipes.some(
      (recipe) =>
        recipe.primitiveIds.length !== primitiveIds.size ||
        recipe.primitiveIds.some((id) => !primitiveIds.has(id)) ||
        recipe.compositionOrder.join("\0") !== orderedPrimitiveIds.join("\0")
    )
  ) {
    throw new Error("Lab sandbox manifest is invalid")
  }
  return {
    authorityOwner: "godot",
    manifestSha256: sha256(root.manifest_sha256) as string,
    primitives: [...primitives].sort(
      (first, second) => first.compositionRank - second.compositionRank
    ),
    recipes,
  }
}

function run(value: JsonRecord): LabRun | null {
  const contract = record(value.contract)
  const state = record(value.state)
  const id = string(value.run_id) ?? string(value.id)
  const gameId = string(value.game_id) ?? string(contract?.game_id)
  const lifecycle =
    string(value.lifecycle) ?? string(state?.status) ?? string(state?.lifecycle)
  if (!id || !gameId || !lifecycle || !runLifecycles.has(lifecycle)) {
    return null
  }

  const lineageDiff = record(contract?.lineage_diff)
  const entrants = records(contract?.entrants)
  const providers = entrants.map((entrant) => provider(entrant.provider))
  const contractProvider =
    providers.length &&
    providers[0] &&
    providers.every((item) => item === providers[0])
      ? providers[0]
      : null

  return {
    id,
    gameId,
    gameVersion: string(value.game_version) ?? string(contract?.game_version),
    lifecycle,
    mode: runMode(value.mode) ?? runMode(contract?.mode),
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

function genericProjectionSummary(
  root: JsonRecord,
  snapshot: JsonRecord
): LabGenericProjectionSummary | null {
  const game = record(snapshot.game)
  const gameId = safeIdentifier(game?.game_id)
  const gameVersion = safeText(game?.game_version, 160)
  const mode = safeIdentifier(game?.mode)
  const scenarioId =
    game?.scenario_id === null
      ? null
      : (safeIdentifier(game?.scenario_id) ?? null)
  if (!gameId || !gameVersion || !mode) return null

  const rawEntrants = records(snapshot.entrants)
  if (rawEntrants.length < 1 || rawEntrants.length > 3) return null
  const entrants = rawEntrants.flatMap((item) => {
    const entrantId = safeIdentifier(item.entrant_id)
    const modelId = safeText(item.model_id, 200)
    const providerId = safeIdentifier(item.provider)
    const displayName = safeText(item.display_name, 120)
    return entrantId && modelId && providerId && displayName
      ? [{ entrantId, modelId, provider: providerId, displayName }]
      : []
  })
  if (
    entrants.length !== rawEntrants.length ||
    new Set(entrants.map((entrant) => entrant.entrantId)).size !==
      entrants.length
  ) {
    return null
  }

  const authorityRecord = record(snapshot.authority)
  let authority: LabGenericProjectionSummary["authority"] = null
  if (authorityRecord) {
    const state = safeIdentifier(authorityRecord.state)
    if (
      !state ||
      ![
        "queued",
        "running",
        "checkpointed",
        "completed",
        "failed",
        "cancelled",
      ].includes(state)
    ) {
      return null
    }
    const progress = record(authorityRecord.progress)
    const authorityTick = integer(progress?.authority_tick)
    const decisionSequence = integer(progress?.decision_sequence)
    const failureCode =
      authorityRecord.failure_code === undefined
        ? null
        : safeIdentifier(authorityRecord.failure_code)
    const replayState =
      authorityRecord.replay_state === undefined
        ? null
        : safeIdentifier(authorityRecord.replay_state)
    if (
      (authorityTick !== null && authorityTick < 0) ||
      (decisionSequence !== null && decisionSequence < 0) ||
      (authorityTick === null) !== (decisionSequence === null) ||
      (authorityRecord.failure_code !== undefined && !failureCode) ||
      (authorityRecord.replay_state !== undefined &&
        (!replayState ||
          !["pending", "saving", "ready", "unavailable"].includes(replayState)))
    ) {
      return null
    }
    authority = {
      state,
      failureCode,
      authorityTick,
      decisionSequence,
      replayState,
    }
  }

  const eventCount = Array.isArray(root.events) ? root.events.length : 0
  if (eventCount > 4_096) return null
  return {
    gameId,
    gameVersion,
    mode,
    scenarioId,
    entrants,
    authority,
    eventCount,
    terminalAvailable: record(snapshot.terminal) !== null,
  }
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
  if (!result || result.id !== runId)
    throw new Error("Lab run payload is invalid")
  return result
}

function parseRunMutation(
  raw: unknown,
  expectedRunId: string,
  expectedGameId: string,
  expectedContractSha256: string
): LabRun {
  const parsed = record(raw)
  const result = parsed ? run(parsed) : null
  if (
    !safeIdentifier(expectedGameId) ||
    !sha256(expectedContractSha256) ||
    !result ||
    result.id !== expectedRunId ||
    result.gameId !== expectedGameId ||
    result.contractSha256 !== expectedContractSha256
  ) {
    throw new Error("Lab run mutation payload is invalid")
  }
  return result
}

async function mutateRunLifecycle(
  runId: string,
  expectedGameId: string,
  expectedContractSha256: string,
  action: "seal" | "verify",
  csrfToken: string
): Promise<LabRun> {
  const raw = await json(
    `/api/lab/runs/${encodeURIComponent(runId)}/${action}`,
    {
      method: "POST",
      headers: { "X-WorldEval-CSRF": csrfToken },
    }
  )
  const result = parseRunMutation(
    raw,
    runId,
    expectedGameId,
    expectedContractSha256
  )
  if (
    (action === "seal" &&
      result.lifecycle !== "sealed" &&
      result.lifecycle !== "verified") ||
    (action === "verify" && result.lifecycle !== "verified")
  ) {
    throw new Error("Lab run mutation lifecycle is invalid")
  }
  return result
}

export function sealLabRun(
  runId: string,
  expectedGameId: string,
  expectedContractSha256: string,
  csrfToken: string
): Promise<LabRun> {
  return mutateRunLifecycle(
    runId,
    expectedGameId,
    expectedContractSha256,
    "seal",
    csrfToken
  )
}

export function verifyLabRun(
  runId: string,
  expectedGameId: string,
  expectedContractSha256: string,
  csrfToken: string
): Promise<LabRun> {
  return mutateRunLifecycle(
    runId,
    expectedGameId,
    expectedContractSha256,
    "verify",
    csrfToken
  )
}

export async function cancelLabRun(
  runId: string,
  expectedGameId: string,
  expectedContractSha256: string,
  csrfToken: string
): Promise<LabRun> {
  const raw = await json(`/api/lab/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
    headers: { "X-WorldEval-CSRF": csrfToken },
  })
  const result = parseRunMutation(
    raw,
    runId,
    expectedGameId,
    expectedContractSha256
  )
  if (result.lifecycle !== "cancelled") {
    throw new Error("Lab run cancellation payload is invalid")
  }
  return result
}

export async function publishLabRun(
  runId: string,
  expectedGameId: string,
  expectedContractSha256: string,
  csrfToken: string
): Promise<LabRunEvidenceResult> {
  if (!sha256(expectedContractSha256)) {
    throw new Error("Lab replay publication contract is invalid")
  }
  const raw = await json(`/api/lab/runs/${encodeURIComponent(runId)}/publish`, {
    method: "POST",
    headers: { "X-WorldEval-CSRF": csrfToken },
  })
  const root = record(raw)
  const publication = root
    ? publicReplay(root, expectedGameId, expectedContractSha256)
    : null
  if (!publication) {
    throw new Error("Lab replay publication payload is invalid")
  }
  const publicPath = `/share/games/${encodeURIComponent(publication.gameId)}?replay=${encodeURIComponent(publication.publicationSlug)}`
  return {
    run: null,
    notice: "Safe unlisted replay published.",
    publicPath,
  }
}

export async function submitLabRunToBenchmark(
  runId: string,
  expectedContractSha256: string,
  csrfToken: string
): Promise<LabRunEvidenceResult> {
  if (!sha256(expectedContractSha256)) {
    throw new Error("Lab benchmark contract is invalid")
  }
  const raw = await json(
    `/api/lab/runs/${encodeURIComponent(runId)}/benchmark`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-WorldEval-CSRF": csrfToken,
      },
      body: JSON.stringify({ recipe_id: "labyrinth-interactive-v1" }),
    }
  )
  const root = record(raw)
  const fields = new Set([
    "authority_result_sha256",
    "contract_sha256",
    "participants",
    "recipe_id",
    "recipe_sha256",
    "replay_projection_sha256",
    "run_id",
    "schema_version",
    "verified_result_sha256",
  ])
  const participants = records(root?.participants)
  const metricFields = new Set([
    "budget_charged_calls",
    "completion_basis_points",
    "input_tokens",
    "invalid_action_rate_basis_points",
    "latency_ms",
    "output_tokens",
    "path_efficiency_basis_points",
    "recovery_rate_basis_points",
  ])
  const basisPointFields = [
    "completion_basis_points",
    "invalid_action_rate_basis_points",
    "path_efficiency_basis_points",
    "recovery_rate_basis_points",
  ] as const
  if (
    !root ||
    Object.keys(root).length !== fields.size ||
    Object.keys(root).some((field) => !fields.has(field)) ||
    root.schema_version !== "worldeval/lab-verified-benchmark-result/1" ||
    root.recipe_id !== "labyrinth-interactive-v1" ||
    root.run_id !== runId ||
    !sha256(root.authority_result_sha256) ||
    root.contract_sha256 !== expectedContractSha256 ||
    !sha256(root.recipe_sha256) ||
    !sha256(root.replay_projection_sha256) ||
    !sha256(root.verified_result_sha256) ||
    !Array.isArray(root.participants) ||
    participants.length !== root.participants.length ||
    participants.length !== 3 ||
    new Set(participants.map((participant) => participant.entrant_id)).size !==
      participants.length ||
    participants.some((participant) => {
      const participantFields = new Set([
        "entrant_id",
        "metrics",
        "model_id",
        "provider",
      ])
      const metrics = record(participant.metrics)
      return (
        Object.keys(participant).length !== participantFields.size ||
        Object.keys(participant).some(
          (field) => !participantFields.has(field)
        ) ||
        !safeIdentifier(participant.entrant_id) ||
        !safeText(participant.model_id, 200) ||
        !safeIdentifier(participant.provider) ||
        !metrics ||
        Object.keys(metrics).length !== metricFields.size ||
        Object.keys(metrics).some((field) => !metricFields.has(field)) ||
        Object.values(metrics).some(
          (value) =>
            typeof value !== "number" || !Number.isInteger(value) || value < 0
        ) ||
        basisPointFields.some(
          (field) =>
            typeof metrics[field] !== "number" || metrics[field] > 10_000
        )
      )
    })
  ) {
    throw new Error("Lab benchmark admission payload is invalid")
  }
  return {
    run: null,
    notice: "Verified evidence admitted to the Labyrinth leaderboard.",
    publicPath: null,
  }
}

/**
 * Create an exact, separately persisted frozen clone. The empty configuration
 * patch is intentional: the service copies the parent configuration verbatim
 * while recording parent-contract lineage on the new draft.
 */
export async function cloneLabRun(
  source: LabRun,
  csrfToken: string
): Promise<LabRun> {
  if (!source.contractSha256) {
    throw new Error("Lab clone source contract is invalid")
  }
  const raw = await json(
    `/api/lab/runs/${encodeURIComponent(source.id)}/clone`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-WorldEval-CSRF": csrfToken,
      },
      body: JSON.stringify({ changes: { configuration: {} } }),
    }
  )
  const parsed = record(raw)
  const result = parsed ? run(parsed) : null
  if (
    !result ||
    result.lifecycle !== "draft" ||
    result.id === source.id ||
    result.gameId !== source.gameId ||
    result.gameVersion !== source.gameVersion ||
    result.mode !== source.mode ||
    result.provider !== source.provider ||
    !(
      (result.mode === "demo" && result.provider === null) ||
      (isOpenAiLabRunMode(result.mode) && result.provider === "openai")
    ) ||
    !result.contractSha256 ||
    result.contractSha256 === source.contractSha256 ||
    result.parentContractSha256 !== source.contractSha256
  ) {
    throw new Error("Lab clone draft payload is invalid")
  }
  return result
}

/**
 * Start an existing frozen clone. Demo drafts submit an empty object; live
 * OpenAI drafts accept only a fresh session key. All game configuration remains
 * in the authority-owned draft contract.
 */
export async function launchLabRunDraft(
  draft: LabRun,
  apiKey: string,
  csrfToken: string
): Promise<LabRun> {
  const demo = draft.mode === "demo"
  if (
    !demo &&
    (!isOpenAiLabRunMode(draft.mode) || !apiKey || draft.provider !== "openai")
  ) {
    throw new Error("Lab draft launch credential is invalid")
  }
  const raw = await json(
    `/api/lab/runs/${encodeURIComponent(draft.id)}/launch`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-WorldEval-CSRF": csrfToken,
      },
      body: JSON.stringify(demo ? {} : { api_key: apiKey }),
    }
  )
  const parsed = record(raw)
  const result = parsed ? run(parsed) : null
  if (
    !result ||
    result.id !== draft.id ||
    result.gameId !== draft.gameId ||
    result.gameVersion !== draft.gameVersion ||
    result.mode !== draft.mode ||
    result.provider !== draft.provider ||
    result.contractSha256 !== draft.contractSha256 ||
    result.parentContractSha256 !== draft.parentContractSha256 ||
    !["queued", "running", "checkpointed", "completed", "failed"].includes(
      result.lifecycle
    ) ||
    (demo
      ? result.mode !== "demo" || result.provider !== null
      : !isOpenAiLabRunMode(result.mode) || result.provider !== "openai")
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
  const sequence = root ? integer(root.sequence) : null
  const contractSha256 = root ? sha256(root.contract_sha256) : null
  if (
    !root ||
    !snapshot ||
    runIdFromPayload !== runId ||
    !contractSha256 ||
    !lifecycle ||
    sequence === null ||
    sequence < 0
  )
    throw new Error("Lab replay projection is invalid")
  const frame = mazeFrame(record(snapshot.arena)) ?? mazeFrame(snapshot)
  const summary = genericProjectionSummary(root, snapshot)
  if (!frame && !summary) throw new Error("Lab replay projection is invalid")
  return {
    runId: runIdFromPayload,
    contractSha256,
    lifecycle,
    sequence,
    frame,
    summary,
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
  const metrics = record(value.metrics)
  const asDisplayValue = (input: unknown): string | null => {
    const text = string(input)
    if (text) return text
    return typeof input === "number" && Number.isFinite(input)
      ? String(input)
      : null
  }
  const basisPoints = (input: unknown): string | null => {
    const points = integer(input)
    if (points === null || points < 0 || points > 10_000) return null
    const percent = points / 100
    return `${Number.isInteger(percent) ? percent.toFixed(0) : percent.toFixed(2)}%`
  }
  const sampleCount = integer(value.sample_count)
  return {
    model,
    completion:
      asDisplayValue(value.completion) ??
      asDisplayValue(value.completion_rate) ??
      basisPoints(metrics?.completion_basis_points),
    calls:
      asDisplayValue(value.calls) ??
      asDisplayValue(value.budget_charged_calls) ??
      asDisplayValue(metrics?.budget_charged_calls),
    pathEfficiency:
      asDisplayValue(value.path_efficiency) ??
      basisPoints(metrics?.path_efficiency_basis_points),
    cost: asDisplayValue(value.cost),
    evidence:
      asDisplayValue(value.evidence) ??
      (sampleCount === null
        ? null
        : `${sampleCount} verified sample${sampleCount === 1 ? "" : "s"}`),
  }
}

export async function getLabyrinthBenchmark(): Promise<LabBenchmark> {
  const raw = await json("/api/lab/benchmarks/labyrinth-run")
  return parseBenchmark(raw, "labyrinth-run")
}

export async function getPublicGameBenchmark(
  gameId: string
): Promise<LabBenchmark> {
  const expectedGameId = safeIdentifier(gameId)
  if (!expectedGameId) throw new Error("public benchmark game id is invalid")
  const raw = await json(
    `/api/public/games/${encodeURIComponent(expectedGameId)}/benchmark`
  )
  return parseBenchmark(raw, expectedGameId)
}

const publicReplayFields = new Set([
  "cartridge_sha256",
  "contract_sha256",
  "game_id",
  "game_version",
  "lifecycle_status",
  "method_label",
  "projection_sha256",
  "publication_sha256",
  "publication_slug",
  "published_at_epoch_ms",
  "replay",
  "result_sha256",
  "schema_version",
  "state_sha256",
])

function publicReplay(
  value: JsonRecord,
  expectedGameId: string,
  expectedContractSha256?: string
): LabPublicReplay | null {
  if (
    Object.keys(value).length !== publicReplayFields.size ||
    Object.keys(value).some((key) => !publicReplayFields.has(key)) ||
    value.schema_version !== "worldeval/public-replay-publication/1" ||
    value.game_id !== expectedGameId ||
    (expectedContractSha256 !== undefined &&
      value.contract_sha256 !== expectedContractSha256) ||
    !sha256(value.cartridge_sha256) ||
    !sha256(value.contract_sha256) ||
    !sha256(value.projection_sha256) ||
    !sha256(value.publication_sha256) ||
    !sha256(value.result_sha256) ||
    !sha256(value.state_sha256)
  ) {
    return null
  }
  const publicationSlug = safeText(value.publication_slug, 40)
  const gameVersion = safeText(value.game_version, 160)
  const methodLabel = safeText(value.method_label, 80)
  const lifecycle = value.lifecycle_status
  const publishedAt = epochToIso(value.published_at_epoch_ms)
  const replay = record(value.replay)
  const snapshot = record(replay?.snapshot)
  const sequence = integer(replay?.sequence)
  const events = replay?.events
  if (
    !publicationSlug ||
    !/^pub_[A-Za-z0-9_-]{32}$/.test(publicationSlug) ||
    !gameVersion ||
    !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(gameVersion) ||
    !methodLabel ||
    !/^[a-z][a-z0-9._-]{0,79}$/.test(methodLabel) ||
    (lifecycle !== "completed" &&
      lifecycle !== "sealed" &&
      lifecycle !== "verified") ||
    !publishedAt ||
    !replay ||
    Object.keys(replay).length !== 3 ||
    !snapshot ||
    sequence === null ||
    sequence < 0 ||
    !Array.isArray(events) ||
    events.length > 4_096 ||
    events.some((event) => !record(event))
  ) {
    return null
  }
  const frame = mazeFrame(record(snapshot.arena)) ?? mazeFrame(snapshot)
  const summary = genericProjectionSummary(replay, snapshot)
  if (
    expectedGameId === "labyrinth-run"
      ? !frame || summary !== null
      : frame !== null ||
        !summary ||
        summary.gameId !== expectedGameId ||
        summary.gameVersion !== gameVersion
  ) {
    return null
  }
  return {
    publicationSlug,
    gameId: expectedGameId,
    gameVersion,
    lifecycle,
    publishedAt,
    sequence,
    eventCount: events.length,
    frame,
    summary,
  }
}

export async function getPublicGameReplays(
  gameId: string
): Promise<LabPublicReplay[]> {
  const expectedGameId = safeIdentifier(gameId)
  if (!expectedGameId) throw new Error("public replay game id is invalid")
  const root = record(
    await json(
      `/api/public/games/${encodeURIComponent(expectedGameId)}/replays`
    )
  )
  const rawPublicationsValue = root?.publications
  const rawPublications = Array.isArray(rawPublicationsValue)
    ? rawPublicationsValue.map(record)
    : []
  if (
    !root ||
    Object.keys(root).length !== 3 ||
    !["game_id", "publications", "schema_version"].every((field) =>
      Object.hasOwn(root, field)
    ) ||
    root.schema_version !== "worldeval/public-game-replays/1" ||
    root.game_id !== expectedGameId ||
    !Array.isArray(rawPublicationsValue) ||
    rawPublications.some((publication) => publication === null) ||
    rawPublications.length > 500
  ) {
    throw new Error("public replay list payload is invalid")
  }
  const publications = (rawPublications as JsonRecord[]).flatMap((item) => {
    const parsed = publicReplay(item, expectedGameId)
    return parsed ? [parsed] : []
  })
  if (publications.length !== rawPublications.length) {
    throw new Error("public replay list payload is invalid")
  }
  return publications
}

function parseBenchmark(raw: unknown, expectedGameId: string): LabBenchmark {
  const root = record(raw)
  if (!root || root.game_id !== expectedGameId) {
    throw new Error("benchmark payload is not bound to the requested game")
  }
  const nestedLeaderboard = record(root.leaderboard)
  const publishedLeaderboards = records(root.leaderboards)
  const firstPublishedLeaderboard = record(publishedLeaderboards[0])
  const firstRecipe = record(records(root.recipes)[0])
  const firstRecipeLeaderboard = record(firstRecipe?.leaderboard)
  const candidates = Array.isArray(root.verified_results)
    ? root.verified_results
    : Array.isArray(root.leaderboard)
      ? root.leaderboard
      : (nestedLeaderboard?.rows ??
        nestedLeaderboard?.models ??
        firstPublishedLeaderboard?.rows ??
        firstRecipeLeaderboard?.rows)
  return {
    gameId: expectedGameId,
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
): Promise<string> {
  if (
    !input.apiKey ||
    input.provider !== "openai" ||
    Object.values(input.models).some((model) => modelIdentifier(model) === null)
  ) {
    throw new Error("Labyrinth launch input is invalid")
  }
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
  const parsed = record(raw)
  const result = parsed ? run(parsed) : null
  if (
    !result ||
    !safeIdentifier(result.id) ||
    !result.id.startsWith("run_labyrinth_") ||
    result.gameId !== "labyrinth-run" ||
    !result.contractSha256 ||
    result.provider !== "openai" ||
    result.mode !== input.mode ||
    !["queued", "running", "checkpointed", "completed", "failed"].includes(
      result.lifecycle
    )
  ) {
    throw new Error("Labyrinth launch response is invalid")
  }
  return result.id
}

export async function launchGenericGame(
  input: LabGenericLaunchInput,
  csrfToken: string
): Promise<LabRun> {
  const gameId = safeIdentifier(input.gameId)
  if (
    !gameId ||
    (input.mode !== "demo" && input.mode !== "live") ||
    !Number.isInteger(input.seed) ||
    input.seed < 0 ||
    input.seed > 2_147_483_647 ||
    input.models.length < 1 ||
    input.models.length > 3 ||
    (input.mode === "live" &&
      (!input.apiKey ||
        input.models.some((model) => modelIdentifier(model) === null)))
  ) {
    throw new Error("Lab game launch input is invalid")
  }
  const payload =
    input.mode === "demo"
      ? { mode: "demo", seed: input.seed }
      : {
          mode: "live",
          provider: "openai",
          api_key: input.apiKey,
          models: input.models,
          seed: input.seed,
        }
  const raw = await json(`/api/lab/runs/games/${encodeURIComponent(gameId)}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-WorldEval-CSRF": csrfToken,
    },
    body: JSON.stringify(payload),
  })
  const parsed = record(raw)
  const result = parsed ? run(parsed) : null
  if (
    !result ||
    !result.id.startsWith("run_game_") ||
    result.gameId !== gameId ||
    !result.contractSha256 ||
    result.mode !== (input.mode === "demo" ? "demo" : "exploratory") ||
    !["queued", "running", "checkpointed", "completed", "failed"].includes(
      result.lifecycle
    ) ||
    (input.mode === "live"
      ? result.provider !== "openai"
      : result.provider !== null)
  ) {
    throw new Error("Lab game launch response is invalid")
  }
  return result
}

export function labRunFrameUrl(
  runId: string,
  participantId: string,
  revision: number
): string {
  if (
    !runId.startsWith("run_game_") ||
    !/^participant_[0-2]$/.test(participantId) ||
    !Number.isInteger(revision) ||
    revision < 0
  ) {
    throw new Error("Lab game frame URL input is invalid")
  }
  return `/api/lab/runs/${encodeURIComponent(runId)}/frame?participant=${encodeURIComponent(
    participantId
  )}&v=${revision}`
}
