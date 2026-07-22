export type Provider = "openai" | "anthropic" | "gemini"
export type OpponentProvider = Provider | "scripted"
export type ControllerMode = "scripted_demo" | "live_provider"
export type MazeVisionRange = 1 | 2 | 4 | 8 | "infinite"
export const DEMO_SCENARIOS = {
  "orientation-v0": {
    authorityTaskId: "orientation-v0",
    displayLabel: "Stage A Orientation",
    evaluationProfileId: "solo-orientation-v1",
    maximumEpisodeTicks: 600,
    providerModel: "orientation-demo-v1",
  },
  "interaction-v0": {
    authorityTaskId: "interaction-v0",
    displayLabel: "Stage B Interaction",
    evaluationProfileId: "solo-interaction-v1",
    maximumEpisodeTicks: 600,
    providerModel: "interaction-demo-v1",
  },
  "construction-v0": {
    authorityTaskId: "construction-v0",
    displayLabel: "Stage C Construction",
    evaluationProfileId: "solo-construction-v1",
    maximumEpisodeTicks: 600,
    providerModel: "construction-demo-v1",
  },
  "neutral-encounter-v0": {
    authorityTaskId: "neutral-encounter-v0",
    displayLabel: "Stage D Neutral Encounter",
    evaluationProfileId: "solo-neutral-encounter-v1",
    maximumEpisodeTicks: 600,
    providerModel: "neutral-encounter-demo-v1",
  },
  "multi-action-demo-v0": {
    authorityTaskId: "construction-v0",
    displayLabel: "Multi-action solo showcase",
    evaluationProfileId: "solo-multi-action-showcase-v1",
    maximumEpisodeTicks: 1300,
    providerModel: "construction-demo-v1",
  },
  "movement-maze-v0": {
    authorityTaskId: "movement-maze-v0",
    displayLabel: "Movement maze",
    evaluationProfileId: "solo-movement-maze-v1",
    maximumEpisodeTicks: 200,
    providerModel: "movement-maze-demo-v1",
  },
  "operator-action-course-v0": {
    authorityTaskId: "operator-action-course-v0",
    displayLabel: "Operator action course",
    evaluationProfileId: "solo-operator-action-course-v1",
    maximumEpisodeTicks: 300,
    providerModel: "operator-action-course-demo-v1",
  },
} as const
export type DemoScenarioId = keyof typeof DEMO_SCENARIOS
export const SCRIPTED_DEMO_MODELS = {
  "orientation-v0": "orientation-demo-v1",
  "interaction-v0": "interaction-demo-v1",
  "construction-v0": "construction-demo-v1",
  "neutral-encounter-v0": "neutral-encounter-demo-v1",
  "movement-maze-v0": "movement-maze-demo-v1",
  "operator-action-course-v0": "operator-action-course-demo-v1",
} as const
export type ScriptedDemoTaskId = keyof typeof SCRIPTED_DEMO_MODELS
export const DEMO_DUO_GAMES = {
  "rts-skirmish-v0": {
    displayLabel: "RTS Skirmish · featured MVP",
    description: "A two-base RTS battle: harvest wood and ore, bank materials, raise buildings, train units, hold the centre, and break the rival town hall.",
    models: ["rts-harvester-alpha-v1", "rts-commander-bravo-v1"],
  },
  "central-relay-v0": {
    displayLabel: "Central Relay",
    description: "Full relay-hold-or-knockout duel.",
    models: ["duelist-alpha-v1", "duelist-bravo-v1"],
  },
  "duo-checkpoint-race-v0": {
    displayLabel: "Checkpoint Race",
    description: "Race through visible ordered checkpoints.",
    models: ["checkpoint-racer-alpha-v1", "checkpoint-racer-bravo-v1"],
  },
  "duo-relay-control-v0": {
    displayLabel: "Relay Control",
    description: "Compete for a visible relay hold target.",
    models: ["relay-controller-alpha-v1", "relay-controller-bravo-v1"],
  },
  "duo-spar-v0": {
    displayLabel: "Sparring",
    description: "A simple visible-only guard, strike, and knockout match.",
    models: ["sparring-alpha-v1", "sparring-bravo-v1"],
  },
  "duo-resource-relay-v0": {
    displayLabel: "Frontier Resource Relay",
    description: "Frontier battlefield: gather material, deposit it, build a barricade, defend your relay, and contest the rival in combat.",
    models: ["resource-relay-alpha-v1", "resource-relay-bravo-v1"],
  },
} as const
export type DemoDuoGameId = keyof typeof DEMO_DUO_GAMES
export function isDemoDuoGameId(value: unknown): value is DemoDuoGameId {
  return typeof value === "string" && Object.hasOwn(DEMO_DUO_GAMES, value)
}
export function isRtsSkirmishTask(value: unknown): boolean {
  return value === "rts-skirmish-v0"
}
export const DEMO_TRIO_GAMES = {
  "trio-relay-v0": {
    displayLabel: "Trio Relay",
    description: "Three agents compete to hold the visible central relay.",
  },
  "trio-free-for-all-v0": {
    displayLabel: "Trio Free-for-All",
    description: "Three agents fight until one remains or the fixed horizon ends.",
  },
} as const
export type DemoTrioGameId = keyof typeof DEMO_TRIO_GAMES
export const DEMO_TRIO_ENTRANTS = [
  { displayName: "Sol", model: "demo-sol-v1" },
  { displayName: "Luna", model: "demo-luna-v1" },
  { displayName: "Terra", model: "demo-terra-v1" },
] as const
export type ParticipantId = "participant_0" | "participant_1" | "participant_2"
/** A small, already-public roster projection. It deliberately excludes credentials and policy internals. */
export type PublicEntrant = {
  entrantId: string
  displayName: string
  model: string
}
export type EpisodeSetup = {
  controllerMode: ControllerMode
  mode: "solo" | "duel" | "trio"
  provider: Provider
  model: string
  apiKey: string
  opponentProvider: OpponentProvider
  opponentModel: string
  opponentApiKey: string
  thirdModel?: string
  mazeVisionRange?: MazeVisionRange
  scenarioId: string
  taskId: string
  duoTaskId?: DemoDuoGameId
  trioTaskId?: DemoTrioGameId
  seed: number
}
export type TimelineRow = {
  window: number
  intent: string
  receipt: string
  ticks: string
  latencyMs: number | null
}

/**
 * This is intentionally a small public projection of a locally archived replay.  It contains
 * only data that is safe to show beside participant-visible video; authority replays,
 * observations, prompts, provider output, and credentials never cross this API boundary.
 */
export type SavedReplaySummary = {
  replayId: string
  episodeId: string
  scenarioId: string
  evaluationProfileId: string
  taskId: string
  label: string
  outcome: string
  authorityTicks: number
  durationSeconds: number
  video: {
    available: boolean
    mimeType: "video/mp4"
    width: number
    height: number
    fps: number
  }
}

export type SavedReplayAvailability = {
  state: "saving" | "ready" | "unavailable"
  replayId?: string
}

export type EvaluationMetric =
  | { state: "supported"; value: unknown }
  | { state: "unavailable"; reason: string }

export type EvaluationProjectionView = {
  schemaVersion: "llm-controller/evaluation-projection/1.0.0"
  projectionSha256: string
  state: "supported"
  scope: "solo" | "paired_duel_leg"
  run: {
    episodeId: string
    taskId: string
    scenarioId?: string
    evaluationProfileId?: string
    certificationEligible: boolean
  }
  result: {
    outcome: string
    finalStateHash: string
    windows: number
    providerFailures: number
  }
  metrics: Record<string, EvaluationMetric>
}

export type SeriesEvaluationView = {
  seriesId: string
  certificationEligible: boolean
  certificationReason: string | null
  legs: EvaluationProjectionView[]
}

export type TrioEvaluationView = {
  seriesId: string
  taskId: DemoTrioGameId
  planSha256: string
  legs: Array<{
    legIndex: number
    terminalReason: string
    completionTick: number | null
    placements: Array<{ placement: number; entrantIds: string[]; tied: boolean }>
  }>
  entrants: Record<string, {
    displayName: string
    objectivePoints: number
    reliability: { fallbackWindows: number; stoppedCallsAfterElimination: number }
  }>
  eachEntrantUsesEachSeatOnce: boolean
}

export type SeriesArchiveView = {
  evidenceState: "pending" | "saving" | "ready" | "unavailable"
  nativeReplayState: "saving" | "ready" | "unavailable"
  nativeReplayReason?: string
  nativeArtifacts: SeriesNativeReplayArtifact[]
}

export type SeriesNativeReplayArtifact = {
  legIndex: 0 | 1 | 2
  participantId: ParticipantId
  sha256: string
  width: 1280
  height: 720
  fps: 30
}

export type EpisodeView = {
  kind: "episode" | "series" | "trio"
  episodeId: string
  status: "queued" | "running" | "success" | "failure" | "cancelled"
  observationSeq: number
  tick: number
  taskId?: string
  taskLabel: string
  entrants?: PublicEntrant[]
  scenarioId?: string
  evaluationProfileId?: string
  failureCode?: string | null
  replay?: SavedReplayAvailability
  /** Present only for a live Labyrinth Run; the race has an MP4 rather than a player camera. */
  liveMazeVideoState?: "saving" | "ready" | "unavailable"
  timeline: TimelineRow[]
  result?: {
    finalStateHash: string
    providerFailures: number
    outcome: string
    windows: number
    trioLegs?: Array<{
      legIndex: number
      terminalReason: string
      placements: Array<{ place: number; participantIds: ParticipantId[]; tied: boolean }>
    }>
  }
  seriesArchive?: SeriesArchiveView
}

export type ParticipantFrameView = {
  blob: Blob | null
  observationSeq: number | null
  sha256: string | null
  state: "loading" | "live" | "finished"
}

export type SeriesParticipantFrameView = ParticipantFrameView & {
  participantId: ParticipantId
  legIndex: number | null
}

export type ReadinessGate = {
  id: string
  label: string
  passed: boolean
  code: string | null
}

export type ReadinessView = {
  format: "llm-controller/embodiment-readiness-view/1.1.0"
  gates: ReadinessGate[]
  ready_for_promotion: boolean
  report_available: boolean
  runtime_capabilities: { passed: boolean; code: string | null }
  source_fingerprint: string | null
}

/** Safe, cached RTS broadcast metadata. This deliberately has no replay URL or authority state. */
export type CachedRtsShowcaseView = {
  showcaseId: "rts-skirmish-v0"
  taskId: "rts-skirmish-v0"
  label: string
  status: "ready"
  cached: true
  video: {
    durationSeconds: number
    fps: number
    height: number
    mimeType: "video/mp4"
    sha256: string
    width: number
  }
  winner: { team: string }
  completion: { outcome: string; reason: string; tick: number }
  casualties: Array<{ atTick: number; team: string; unitId: string }>
  highlights: Array<{ atSeconds: number; label: string }>
}

export type CachedRtsEvaluationView = {
  showcaseId: "rts-skirmish-v0"
  taskId: "rts-skirmish-v0"
  completion: { outcome: string; reason: string; tick: number }
  metrics: Array<{ id: string; label: string; value: string }>
  verification: {
    manifestSha256: string
    replaySha256: string
    videoSha256: string
    finalStateSha256: string
    state: "verified"
  }
}

export type CachedMazeEntrant = {
  participantId: ParticipantId
  entrantId: "sol" | "luna" | "terra"
  displayName: "Sol" | "Luna" | "Terra"
  model: string
  color: string
  lane: string
  style: string
}

export type CachedMazeShowcaseView = {
  showcaseId: "trio-maze-race-v0"
  taskId: "trio-maze-race-v0"
  label: string
  tagline: string
  status: "ready"
  cached: true
  video: CachedRtsShowcaseView["video"]
  entrants: CachedMazeEntrant[]
  winner: { participantId: ParticipantId; displayName: string }
  result: {
    winnerId: ParticipantId
    winner: string
    finishOrder: ParticipantId[]
    completionTick: number
    reason: string
    explanation: string
  }
  timeline: Array<{
    atSeconds: number
    participantId: ParticipantId | "broadcast"
    kind: string
    label: string
  }>
}

export type CachedMazeEvaluationView = {
  showcaseId: "trio-maze-race-v0"
  taskId: "trio-maze-race-v0"
  scope: "trio_maze_race"
  summary: string
  participants: Array<{
    participantId: ParticipantId
    displayName: string
    model: string
    color: string
    place: number
    finishTick: number
    completionSeconds: string
    distanceCells: number
    shortestPathCells: number
    pathEfficiencyBasisPoints: number
    uniqueCorridorCells: number
    repeatedCorridorCells: number
    passagesExplored: number
    deadEndsEntered: number
    successfulBacktracks: number
    collisions: number
    invalidDecisions: number
    idleThinkingTicks: number
  }>
  verification: {
    state: "verified"
    deterministic: true
    mapSha256: string
    finalStateSha256: string
    manifestSha256: string
    replaySha256: string
    videoSha256: string
  }
}

export type CachedSoloShowcaseView = {
  showcaseId: "solo-multi-action-v0"
  taskId: "construction-v0"
  scenarioId: "multi-action-demo-v0"
  label: string
  tagline: string
  status: "ready"
  cached: true
  participant: { participantId: "participant_0"; displayName: string; model: string }
  video: CachedRtsShowcaseView["video"]
  highlights: Array<{ atSeconds: number; label: string }>
  verification: {
    state: "verified"
    renderer: "godot-movie-maker+ffmpeg"
    releaseProfile: "worldarena-participant-1080p30-v1"
    evidenceSha256: string
    replaySha256: string
    finalStateSha256: string
    protocolPackageSha256: string
    protocolVersion: "llm-controller/0.1.0"
    authorityTicks: number
  }
}

export type CrossroadsFactionId = "sol" | "luna" | "terra"

export type CachedCrossroadsEntrant = {
  entrantId: ParticipantId
  factionId: CrossroadsFactionId
  displayName: "Sol" | "Luna" | "Terra"
  glyph: "△" | "○" | "□"
  color: "#fbbf24" | "#a78bfa" | "#34d399"
  policyId: "crossroads-conquest-demo-v1"
}

export type CachedCrossroadsPlacement = {
  placement: 1 | 2 | 3
  entrantId: ParticipantId
  factionId: CrossroadsFactionId
  displayName: "Sol" | "Luna" | "Terra"
}

export type CachedCrossroadsElimination = {
  order: 1 | 2
  factionId: "terra" | "sol"
  eliminatedBy: "sol" | "luna"
  round: number
  eventId: string
}

export type CachedCrossroadsShowcaseView = {
  showcaseId: "crossroads-conquest-v0"
  taskId: "crossroads-conquest-v0"
  title: string
  tagline: string
  status: "ready"
  cached: true
  verified: true
  entrants: CachedCrossroadsEntrant[]
  winner: { entrantId: "participant_1"; factionId: "luna"; displayName: "Luna" }
  placements: CachedCrossroadsPlacement[]
  eliminationOrder: CachedCrossroadsElimination[]
  timeline: Array<{
    beatId: string
    atSeconds: number
    round: number
    frameIndex: number
    eventId: string
    kind: string
    editorial: boolean
    label: string
  }>
  video: CachedRtsShowcaseView["video"]
  authority: {
    protocol: string
    seed: 424242
    policyId: "crossroads-conquest-demo-v1"
    mapId: "tri_13_v1"
    rulesId: "arena-v0.4"
  }
}

export type CachedCrossroadsEvaluationView = {
  showcaseId: "crossroads-conquest-v0"
  taskId: "crossroads-conquest-v0"
  scope: "crossroads_conquest"
  outcome: {
    winner: CachedCrossroadsShowcaseView["winner"]
    placements: CachedCrossroadsPlacement[]
    eliminationOrder: CachedCrossroadsElimination[]
  }
  verification: {
    state: "verified"
    deterministic: true
    deterministicRuns: 2
    orderRejections: 0
    lunaFirstHostileRound: number
    manifestSha256: string
    replaySha256: string
    evaluationSha256: string
    videoSha256: string
    normalizedTraceSha256: string
    finalStateSha256: string
  }
  factions: Array<{
    factionId: CrossroadsFactionId
    placement: number
    coreHp: number
    eliminatedRound: number | null
    eliminatedBy: CrossroadsFactionId | null
    strongholdsDestroyed: number
  }>
}

async function checked<T>(response: Response): Promise<T> {
  if (!response.ok)
    throw new Error(`Episode service returned ${response.status}`)
  return response.json() as Promise<T>
}

export async function createEpisode(setup: EpisodeSetup): Promise<EpisodeView> {
  const isScriptedDemo = setup.controllerMode === "scripted_demo"
  const scenario = isScriptedDemo ? demoScenario(setup.scenarioId) : null
  let controller:
    | { provider: "demo"; model: string }
    | { provider: Provider; model: string; api_key: string }
  if (isScriptedDemo) {
    controller = {
      provider: "demo",
      model: scenario!.providerModel,
    }
  } else {
    controller = {
      provider: setup.provider,
      model: setup.model,
      api_key: setup.apiKey,
    }
  }
  const raw = await checked<Record<string, unknown>>(
    await fetch("/api/embodiment/episodes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({
        ...controller,
        ...(scenario ? { scenario_id: setup.scenarioId } : {}),
        task_id: scenario?.authorityTaskId ?? setup.taskId,
        seed: setup.seed,
        observation_profile: "hybrid-visible-v1",
        maximum_episode_ticks: scenario?.maximumEpisodeTicks ?? 600,
      }),
    })
  )
  return normalizeEpisode(raw, [])
}

export function isScriptedDemoTask(
  taskId: string
): taskId is ScriptedDemoTaskId {
  return Object.hasOwn(SCRIPTED_DEMO_MODELS, taskId)
}

export function isDemoScenario(
  scenarioId: string
): scenarioId is DemoScenarioId {
  return Object.hasOwn(DEMO_SCENARIOS, scenarioId)
}

export function isDemoTrioGameId(value: unknown): value is DemoTrioGameId {
  return typeof value === "string" && Object.hasOwn(DEMO_TRIO_GAMES, value)
}

export function demoScenario(scenarioId: string) {
  if (!isDemoScenario(scenarioId))
    throw new Error("Demo provider scenario is invalid")
  return DEMO_SCENARIOS[scenarioId]
}

export async function createRun(setup: EpisodeSetup): Promise<EpisodeView> {
  if (setup.mode === "solo") return createEpisode(setup)
  if (setup.mode === "trio") {
    if (setup.controllerMode !== "scripted_demo") {
      const raw = await checked<Record<string, unknown>>(
        await fetch("/api/embodiment/maze-races", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          cache: "no-store",
          body: JSON.stringify({
            provider: setup.provider,
            api_key: setup.apiKey,
            max_provider_calls: 450,
            vision_range_cells: setup.mazeVisionRange ?? 4,
            entrants: [
              { display_name: "Sol", model: setup.model },
              { display_name: "Terra", model: setup.opponentModel },
              { display_name: "Luna", model: setup.thirdModel ?? "gpt-5.6-luna" },
            ],
          }),
        })
      )
      return normalizeLiveMaze(raw)
    }
    const taskId = setup.trioTaskId ?? "trio-relay-v0"
    const raw = await checked<Record<string, unknown>>(
      await fetch("/api/embodiment/trio-series", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          seed: setup.seed,
          max_provider_calls: 1080,
          task_id: taskId,
          entrants: DEMO_TRIO_ENTRANTS.map((entrant) => ({
            provider: "demo",
            model: entrant.model,
          })),
        }),
      })
    )
    return normalizeTrioSeries(raw)
  }
  if (setup.controllerMode === "live_provider" && setup.mode === "duel") {
    const raw = await checked<Record<string, unknown>>(
      await fetch("/api/embodiment/series", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          seed: setup.seed,
          max_live_provider_calls: 720,
          task_id: "rts-skirmish-v1",
          entrants: [
            { provider: setup.provider, model: setup.model, api_key: setup.apiKey },
            { provider: setup.provider, model: setup.opponentModel, api_key: setup.apiKey },
          ],
        }),
      })
    )
    return normalizeSeries(raw)
  }
  const isDemoDuel = setup.controllerMode === "scripted_demo"
  if (isDemoDuel) {
    const taskId = setup.duoTaskId ?? "central-relay-v0"
    const game = DEMO_DUO_GAMES[taskId]
    const raw = await checked<Record<string, unknown>>(
      await fetch("/api/embodiment/series", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          seed: setup.seed,
          max_live_provider_calls: 2160,
          task_id: taskId,
          entrants: [
            { provider: "demo", model: game.models[0] },
            { provider: "demo", model: game.models[1] },
          ],
        }),
      })
    )
    return normalizeSeries(raw)
  }
  const opponent =
    setup.opponentProvider === "scripted"
      ? { provider: "scripted", model: setup.opponentModel }
      : {
          provider: setup.opponentProvider,
          model: setup.opponentModel,
          api_key: setup.opponentApiKey,
        }
  const raw = await checked<Record<string, unknown>>(
    await fetch("/api/embodiment/series", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({
        seed: setup.seed,
        max_live_provider_calls:
          setup.opponentProvider === "scripted" ? 360 : 720,
        entrants: [
          {
            provider: setup.provider,
            model: setup.model,
            api_key: setup.apiKey,
          },
          opponent,
        ],
      }),
    })
  )
  return normalizeSeries(raw)
}

function normalizeLiveMaze(raw: Record<string, unknown>): EpisodeView {
  const state = String(raw.state ?? "queued")
  const replay = raw.replay as Record<string, unknown> | undefined
  const raceResult = replay?.result as Record<string, unknown> | undefined
  const video = raw.video as Record<string, unknown> | undefined
  return {
    kind: "episode",
    episodeId: String(raw.episode_id),
    status: state === "completed" ? "success" : state === "failed" ? "failure" : state === "cancelled" ? "cancelled" : state === "running" ? "running" : "queued",
    observationSeq: typeof replay?.provider_calls === "number" ? replay.provider_calls : 0,
    tick: typeof replay?.elapsed_ticks === "number" ? replay.elapsed_ticks : 0,
    taskId: "trio-maze-race-v1",
    taskLabel: "Labyrinth Run · live",
    entrants: Array.isArray(raw.entrants) ? raw.entrants.map((value) => {
      const entrant = value as Record<string, unknown>
      return { entrantId: String(entrant.entrant_id), displayName: String(entrant.display_name), model: String(entrant.model) }
    }) : undefined,
    failureCode: typeof raw.failure === "string" ? raw.failure : null,
    liveMazeVideoState: video?.state === "saving" || video?.state === "ready" || video?.state === "unavailable"
      ? video.state
      : "unavailable",
    replay: { state: "unavailable" },
    result: raceResult ? {
      finalStateHash: typeof replay?.final_state_sha256 === "string" ? replay.final_state_sha256 : "",
      providerFailures: Array.isArray(replay?.racers)
        ? replay.racers.reduce((total, value) => total + (typeof (value as Record<string, unknown>).invalid_decisions === "number" ? (value as Record<string, unknown>).invalid_decisions as number : 0), 0)
        : 0,
      outcome: typeof raceResult.winner_id === "string" ? "success" : String(raceResult.reason ?? "completed"),
      windows: typeof replay?.provider_calls === "number" ? replay.provider_calls : 0,
    } : undefined,
    timeline: [],
  }
}

export function liveMazeVideoUrl(episodeId: string): string {
  return `/api/embodiment/maze-races/${encodeURIComponent(episodeId)}/video`
}

export async function cancelRun(episodeId: string): Promise<void> {
  const route = episodeId.startsWith("ep_live_labyrinth_")
    ? `/api/embodiment/maze-races/${episodeId}/cancel`
    : episodeId.startsWith("trio_")
    ? `/api/embodiment/trio-series/${episodeId}/cancel`
    : episodeId.startsWith("series_")
    ? `/api/embodiment/series/${episodeId}/cancel`
    : `/api/embodiment/episodes/${episodeId}/cancel`
  await checked<Record<string, unknown>>(
    await fetch(route, { method: "POST", cache: "no-store" })
  )
}

export async function getEpisode(episodeId: string): Promise<EpisodeView> {
  if (episodeId.startsWith("ep_live_labyrinth_")) {
    const status = await checked<Record<string, unknown>>(
      await fetch(`/api/embodiment/maze-races/${episodeId}`, { cache: "no-store" })
    )
    if (status.state === "completed") {
      const replay = await checked<Record<string, unknown>>(
        await fetch(`/api/embodiment/maze-races/${episodeId}/replay`, { cache: "no-store" })
      )
      return normalizeLiveMaze({ ...status, replay })
    }
    return normalizeLiveMaze(status)
  }
  if (episodeId.startsWith("trio_")) return getTrioSeries(episodeId)
  if (episodeId.startsWith("series_")) return getSeries(episodeId)
  const status = await checked<Record<string, unknown>>(
    await fetch(`/api/embodiment/episodes/${episodeId}`, { cache: "no-store" })
  )
  const state = String(status.state ?? "queued")
  if (["completed", "failed", "cancelled"].includes(state)) {
    const result = await checked<Record<string, unknown>>(
      await fetch(`/api/embodiment/episodes/${episodeId}/result`, {
        cache: "no-store",
      })
    )
    // Lifecycle status owns the export state while the sealed result owns final authority
    // evidence. Keep the small public replay projection when the result is read separately.
    return normalizeEpisode({ ...result, replay: result.replay ?? status.replay }, [])
  }
  return normalizeEpisode(status, [])
}

export async function getEpisodeTimeline(
  episodeId: string
): Promise<TimelineRow[]> {
  if (episodeId.startsWith("series_")) {
    const timeline = await checked<{ legs?: unknown }>(
      await fetch(`/api/embodiment/series/${episodeId}/timeline`, {
        cache: "no-store",
      })
    )
    return seriesTimelineRows(timeline.legs)
  }
  if (episodeId.startsWith("trio_")) {
    const timeline = await checked<{ events?: unknown; legs?: unknown }>(
      await fetch(`/api/embodiment/trio-series/${episodeId}/timeline`, {
        cache: "no-store",
      })
    )
    return trioReceiptTimelineRows(timeline.legs, timeline.events)
  }
  const timeline = await checked<{ events: unknown[] }>(
    await fetch(`/api/embodiment/episodes/${episodeId}/timeline`, {
      cache: "no-store",
    })
  )
  return timelineRows(timeline.events)
}

export async function getSavedReplays(): Promise<SavedReplaySummary[]> {
  const raw = await checked<{ replays?: unknown }>(
    await fetch("/api/embodiment/replays", { cache: "no-store" })
  )
  if (!Array.isArray(raw.replays))
    throw new Error("Saved replay list is invalid")
  return raw.replays.map(parseSavedReplay)
}

/**
 * A replay archive is intentionally absent while native rendering is still underway.  A 404 is
 * therefore a normal, non-sensitive pending state rather than an episode-service failure.
 */
export async function getSavedReplay(
  episodeId: string
): Promise<SavedReplaySummary | null> {
  const response = await fetch(
    `/api/embodiment/replays/${encodeURIComponent(episodeId)}`,
    {
      cache: "no-store",
    }
  )
  if (response.status === 404) return null
  return parseSavedReplay(await checked<unknown>(response))
}

export async function getEpisodeEvaluation(
  episodeId: string
): Promise<EvaluationProjectionView | null> {
  const response = await fetch(
    `/api/embodiment/episodes/${encodeURIComponent(episodeId)}/evaluation`,
    { cache: "no-store" }
  )
  if (response.status === 409) return null
  return parseEvaluation(await checked<unknown>(response))
}

export async function getSeriesEvaluation(
  seriesId: string
): Promise<SeriesEvaluationView | null> {
  const response = await fetch(
    `/api/embodiment/series/${encodeURIComponent(seriesId)}/evaluation`,
    { cache: "no-store" }
  )
  if (response.status === 409) return null
  const raw = await checked<unknown>(response)
  if (!raw || typeof raw !== "object") throw new Error("Series evaluation is invalid")
  const value = raw as Record<string, unknown>
  const certification = value.certification as Record<string, unknown> | undefined
  if (
    value.series_id !== seriesId ||
    !certification ||
    typeof certification.eligible !== "boolean" ||
    !Array.isArray(value.legs) ||
    value.legs.length !== 2
  ) throw new Error("Series evaluation is invalid")
  return {
    seriesId,
    certificationEligible: certification.eligible,
    certificationReason:
      typeof certification.reason === "string" ? certification.reason : null,
    legs: value.legs.map(parseEvaluation),
  }
}

export async function getTrioEvaluation(
  seriesId: string
): Promise<TrioEvaluationView | null> {
  const response = await fetch(
    `/api/embodiment/trio-series/${encodeURIComponent(seriesId)}/evaluation`,
    { cache: "no-store" }
  )
  if (response.status === 409) return null
  const raw = await checked<unknown>(response)
  if (!raw || typeof raw !== "object") throw new Error("Trio evaluation is invalid")
  const value = raw as Record<string, unknown>
  const series = value.series as Record<string, unknown> | undefined
  const cyclic = value.cyclic_normalization as Record<string, unknown> | undefined
  const legs = value.legs
  const entrants = value.entrants
  if (
    value.scope !== "trio_game_series" ||
    value.protocol_version !== "llm-controller/0.3.0" ||
    !isDemoTrioGameId(value.task_id) ||
    !series || series.leg_count !== 3 ||
    typeof series.plan_sha256 !== "string" ||
    !series.plan_sha256.match(/^[0-9a-f]{64}$/) ||
    !Array.isArray(legs) || legs.length !== 3 ||
    !entrants || typeof entrants !== "object" ||
    !cyclic || cyclic.each_entrant_uses_each_seat_once !== true
  ) throw new Error("Trio evaluation is invalid")
  const parsedEntrants: TrioEvaluationView["entrants"] = {}
  for (const entrantId of ["sol", "luna", "terra"]) {
    const entrant = (entrants as Record<string, unknown>)[entrantId]
    if (!entrant || typeof entrant !== "object") throw new Error("Trio entrant evaluation is invalid")
    const item = entrant as Record<string, unknown>
    const reliability = item.reliability as Record<string, unknown> | undefined
    if (
      typeof item.display_name !== "string" ||
      !Number.isSafeInteger(item.objective_points) ||
      !reliability ||
      !Number.isSafeInteger(reliability.fallback_windows) ||
      !Number.isSafeInteger(reliability.stopped_calls_after_elimination)
    ) throw new Error("Trio entrant evaluation is invalid")
    parsedEntrants[entrantId] = {
      displayName: item.display_name,
      objectivePoints: item.objective_points as number,
      reliability: {
        fallbackWindows: reliability.fallback_windows as number,
        stoppedCallsAfterElimination: reliability.stopped_calls_after_elimination as number,
      },
    }
  }
  return {
    seriesId,
    taskId: value.task_id,
    planSha256: series.plan_sha256,
    legs: legs.map(parseTrioEvaluationLeg),
    entrants: parsedEntrants,
    eachEntrantUsesEachSeatOnce: true,
  }
}

export async function getSavedReplayEvaluation(
  replayId: string
): Promise<EvaluationProjectionView | null> {
  const response = await fetch(
    `/api/embodiment/replays/${encodeURIComponent(replayId)}/evaluation`,
    { cache: "no-store" }
  )
  if (response.status === 409) return null
  return parseEvaluation(await checked<unknown>(response))
}

export function savedReplayVideoUrl(replayId: string): string {
  return `/api/embodiment/replays/${encodeURIComponent(replayId)}/video`
}

export function seriesParticipantVideoUrl(
  seriesId: string,
  legIndex: 0 | 1 | 2,
  participantId: ParticipantId
): string {
  const collection = seriesId.startsWith("trio_") ? "trio-series" : "series"
  return `/api/embodiment/${collection}/${encodeURIComponent(seriesId)}/legs/${legIndex}/participants/${participantId}/video`
}

export async function getParticipantFrame(
  episodeId: string
): Promise<ParticipantFrameView> {
  const response = await fetch(`/api/embodiment/episodes/${episodeId}/frame`, {
    cache: "no-store",
  })
  const state = response.headers.get("X-Frame-State")
  if (
    !(["loading", "live", "finished"] as const).includes(
      state as ParticipantFrameView["state"]
    )
  ) {
    throw new Error("Participant frame state is invalid")
  }
  if (response.status === 204) {
    return {
      blob: null,
      observationSeq: null,
      sha256: null,
      state: state as ParticipantFrameView["state"],
    }
  }
  if (
    !response.ok ||
    response.headers.get("Content-Type")?.split(";", 1)[0] !== "image/png"
  ) {
    throw new Error(`Participant frame service returned ${response.status}`)
  }
  const observationSeq = Number(response.headers.get("X-Observation-Seq"))
  const sha256 = response.headers.get("X-Content-SHA256")
  if (
    !Number.isSafeInteger(observationSeq) ||
    observationSeq < 0 ||
    !sha256?.match(/^[0-9a-f]{64}$/)
  ) {
    throw new Error("Participant frame metadata is invalid")
  }
  return {
    blob: await response.blob(),
    observationSeq,
    sha256,
    state: state as ParticipantFrameView["state"],
  }
}

export async function getSeriesParticipantFrame(
  seriesId: string,
  participantId: ParticipantId
): Promise<SeriesParticipantFrameView> {
  const collection = seriesId.startsWith("trio_") ? "trio-series" : "series"
  const response = await fetch(
    `/api/embodiment/${collection}/${encodeURIComponent(seriesId)}/participants/${participantId}/frame`,
    { cache: "no-store" }
  )
  const state = response.headers.get("X-Frame-State")
  if (state !== "loading" && state !== "live" && state !== "finished" && state !== "unavailable")
    throw new Error("Series participant frame state is invalid")
  if (response.status === 204) return {
    blob: null,
    observationSeq: null,
    sha256: null,
    state: state === "unavailable" ? "finished" : state,
    participantId,
    legIndex: null,
  }
  if (!response.ok || response.headers.get("Content-Type")?.split(";", 1)[0] !== "image/png")
    throw new Error(`Series participant frame service returned ${response.status}`)
  const observationSeq = Number(response.headers.get("X-Observation-Seq"))
  const legIndex = Number(response.headers.get("X-Leg-Index"))
  const returnedParticipant = response.headers.get("X-Participant-ID")
  const sha256 = response.headers.get("X-Content-SHA256")
  if (
    !Number.isSafeInteger(observationSeq) || observationSeq < 0 ||
    !Number.isSafeInteger(legIndex) ||
    !(seriesId.startsWith("trio_") ? [0, 1, 2] : [0, 1]).includes(legIndex) ||
    returnedParticipant !== participantId ||
    !sha256?.match(/^[0-9a-f]{64}$/)
  ) throw new Error("Series participant frame metadata is invalid")
  return {
    blob: await response.blob(), observationSeq, sha256,
    state: state as "live" | "finished", participantId, legIndex,
  }
}

export async function getReadiness(): Promise<ReadinessView> {
  return checked<ReadinessView>(
    await fetch("/api/embodiment/certification/readiness", {
      cache: "no-store",
    })
  )
}

export async function getCachedRtsShowcase(): Promise<CachedRtsShowcaseView> {
  return parseCachedRtsShowcase(await checked<unknown>(
    await fetch("/api/embodiment/showcases/rts-skirmish-v0", { cache: "no-store" })
  ))
}

export async function getCachedRtsEvaluation(): Promise<CachedRtsEvaluationView> {
  return parseCachedRtsEvaluation(await checked<unknown>(
    await fetch("/api/embodiment/showcases/rts-skirmish-v0/evaluation", { cache: "no-store" })
  ))
}

export function cachedRtsVideoUrl(): string {
  return "/api/embodiment/showcases/rts-skirmish-v0/video"
}

export async function getCachedMazeShowcase(): Promise<CachedMazeShowcaseView> {
  return parseCachedMazeShowcase(await checked<unknown>(
    await fetch("/api/embodiment/showcases/trio-maze-race-v0", { cache: "force-cache" })
  ))
}

export async function getCachedMazeEvaluation(): Promise<CachedMazeEvaluationView> {
  return parseCachedMazeEvaluation(await checked<unknown>(
    await fetch("/api/embodiment/showcases/trio-maze-race-v0/evaluation", { cache: "force-cache" })
  ))
}

export function cachedMazeVideoUrl(): string {
  return "/api/embodiment/showcases/trio-maze-race-v0/video"
}

export async function getCachedSoloShowcase(): Promise<CachedSoloShowcaseView> {
  return parseCachedSoloShowcase(await checked<unknown>(
    // The local API may be restarted while the dashboard stays open.  Do not retain a previous
    // 404 for the primary solo preset, otherwise a healthy restarted backend remains stuck in
    // the "unavailable" view until the browser cache is manually cleared.
    await fetch("/api/embodiment/showcases/solo-multi-action-v0", { cache: "no-store" })
  ))
}

export function cachedSoloVideoUrl(): string {
  return "/api/embodiment/showcases/solo-multi-action-v0/video"
}

export async function getCachedCrossroadsShowcase(): Promise<CachedCrossroadsShowcaseView> {
  return parseCachedCrossroadsShowcase(await checked<unknown>(
    await fetch("/api/embodiment/showcases/crossroads-conquest-v0", { cache: "force-cache" })
  ))
}

export async function getCachedCrossroadsEvaluation(): Promise<CachedCrossroadsEvaluationView> {
  return parseCachedCrossroadsEvaluation(await checked<unknown>(
    await fetch("/api/embodiment/showcases/crossroads-conquest-v0/evaluation", { cache: "force-cache" })
  ))
}

export function cachedCrossroadsVideoUrl(): string {
  return "/api/embodiment/showcases/crossroads-conquest-v0/video"
}

async function getSeries(seriesId: string): Promise<EpisodeView> {
  const status = await checked<Record<string, unknown>>(
    await fetch(`/api/embodiment/series/${seriesId}`, { cache: "no-store" })
  )
  if (["completed", "failed", "cancelled"].includes(String(status.state))) {
    const result = await checked<Record<string, unknown>>(
      await fetch(`/api/embodiment/series/${seriesId}/result`, {
        cache: "no-store",
      })
    )
    // The result route owns sealed authority data, while status owns the independent archive
    // lifecycle. Preserve both so a completed match can truthfully say that replay rendering is
    // still in progress instead of dropping the archive projection during the result refresh.
    return normalizeSeries({ ...status, ...result, archive: status.archive ?? result.archive })
  }
  return normalizeSeries(status)
}

async function getTrioSeries(seriesId: string): Promise<EpisodeView> {
  const status = await checked<Record<string, unknown>>(
    await fetch(`/api/embodiment/trio-series/${seriesId}`, { cache: "no-store" })
  )
  if (["completed", "failed", "cancelled"].includes(String(status.state))) {
    if (String(status.state) !== "completed") return normalizeTrioSeries(status)
    const [result, archive] = await Promise.all([
      checked<Record<string, unknown>>(
        await fetch(`/api/embodiment/trio-series/${seriesId}/result`, {
          cache: "no-store",
        })
      ),
      checked<Record<string, unknown>>(
        await fetch(`/api/embodiment/trio-series/${seriesId}/archive`, {
          cache: "no-store",
        })
      ),
    ])
    return normalizeTrioSeries({ ...status, result, archive })
  }
  return normalizeTrioSeries(status)
}

export function bundleUrl(episodeId: string) {
  if (episodeId.startsWith("trio_")) {
    return `/api/embodiment/trio-series/${episodeId}/replay`
  }
  if (episodeId.startsWith("series_")) {
    return `/api/embodiment/series/${episodeId}/replay`
  }
  return `/api/embodiment/episodes/${episodeId}/replay`
}

function normalizeEpisode(
  raw: Record<string, unknown>,
  events: unknown[]
): EpisodeView {
  const config = (raw.config ?? {}) as Record<string, unknown>
  const state = String(raw.state ?? "queued")
  const status =
    state === "completed"
      ? "success"
      : state === "failed"
        ? "failure"
        : state === "cancelled"
          ? "cancelled"
          : state === "running"
            ? "running"
            : "queued"
  const timeline = timelineRows(events)
  const value = raw.result as Record<string, unknown> | null | undefined
  const terminal = value?.terminal as Record<string, unknown> | undefined
  const progress = raw.progress as Record<string, unknown> | null | undefined
  const progressObservationSeq = safeNonNegativeInteger(
    progress?.observation_seq
  )
  const progressTick = safeNonNegativeInteger(progress?.authority_tick)
  const resultWindows = safeNonNegativeInteger(value?.windows)
  const replay = normalizeSavedReplayAvailability(raw.replay ?? value?.replay)
  const scenarioId = nonEmptyString(config.scenario_id)
  const evaluationProfileId = nonEmptyString(config.evaluation_profile_id)
  const scenario = scenarioId && isDemoScenario(scenarioId)
    ? DEMO_SCENARIOS[scenarioId]
    : null
  return {
    kind: "episode",
    episodeId: String(raw.episode_id),
    status,
    // The lifecycle route exposes only monotonic public timing.  It intentionally carries no
    // player observation fields, pixels, prompts, model output, or authority state.
    observationSeq: Math.max(
      timeline.length,
      progressObservationSeq,
      resultWindows
    ),
    tick: Math.max(lastTick(timeline), progressTick),
    taskId: String(config.task_id ?? ""),
    taskLabel: scenario?.displayLabel ?? String(config.task_id ?? "Live episode"),
    ...(scenarioId ? { scenarioId } : {}),
    ...(evaluationProfileId ? { evaluationProfileId } : {}),
    failureCode: typeof raw.failure === "string" ? raw.failure : null,
    replay,
    timeline,
    result: value
      ? {
          finalStateHash: String(value.final_state_hash ?? ""),
          providerFailures: Number(value.provider_failures ?? 0),
          outcome: String(terminal?.outcome ?? status),
          windows: Number(value.windows ?? timeline.length),
        }
      : undefined,
  }
}

function normalizeSeries(raw: Record<string, unknown>): EpisodeView {
  const state = String(raw.state ?? "queued")
  const status =
    state === "completed"
      ? "success"
      : state === "failed"
        ? "failure"
        : state === "cancelled"
          ? "cancelled"
          : state === "running"
            ? "running"
            : "queued"
  const value = raw.result as Record<string, unknown> | null | undefined
  const legs = Array.isArray(value?.legs)
    ? (value.legs as Record<string, unknown>[])
    : []
  const archive = raw.archive as Record<string, unknown> | undefined
  const evidence = archive?.evidence as Record<string, unknown> | undefined
  const nativeReplay = archive?.native_replay as Record<string, unknown> | undefined
  const evidenceState = evidence?.state
  const nativeReplayReason = nonEmptyString(nativeReplay?.reason)
  const nativeArtifacts = parseSeriesNativeArtifacts(nativeReplay?.artifacts)
  const entrants = parsePublicEntrants(
    (raw.config as Record<string, unknown> | undefined)?.entrants ?? raw.entrants,
    2,
  )
  const taskId = typeof raw.task_id === "string"
    ? raw.task_id
    : typeof (raw.config as Record<string, unknown> | undefined)?.task_id === "string"
      ? String((raw.config as Record<string, unknown>).task_id)
      : "central-relay-v0"
  const seriesArchive =
    (evidenceState === "pending" || evidenceState === "saving" ||
      evidenceState === "ready" || evidenceState === "unavailable") &&
    (nativeReplay?.state === "saving" ||
      (nativeReplay?.state === "unavailable" && nativeReplayReason) ||
      (nativeReplay?.state === "ready" && nativeArtifacts.length === 4))
      ? {
          evidenceState: evidenceState as SeriesArchiveView["evidenceState"],
          nativeReplayState: nativeReplay.state as SeriesArchiveView["nativeReplayState"],
          ...(nativeReplayReason ? { nativeReplayReason } : {}),
          nativeArtifacts,
        }
      : undefined
  return {
    kind: "series",
    episodeId: String(raw.series_id),
    status,
    observationSeq: legs.reduce(
      (sum, leg) => sum + Number(leg.windows ?? 0),
      0
    ),
    tick: legs.reduce((sum, leg) => sum + Number(leg.windows ?? 0) * 10, 0),
    taskId,
    taskLabel: taskId in DEMO_DUO_GAMES
      ? `${DEMO_DUO_GAMES[taskId as DemoDuoGameId].displayLabel} · two-leg 1v1`
      : "Symmetric two-leg 1v1",
    ...(entrants ? { entrants } : {}),
    timeline: [],
    ...(seriesArchive ? { seriesArchive } : {}),
    result: value
      ? {
          finalStateHash: String(value.plan_sha256 ?? ""),
          providerFailures: legs.reduce(
            (sum, leg) => sum + Number(leg.provider_failures ?? 0),
            0
          ),
          outcome: String(value.winner_entrant_id ?? value.status ?? status),
          windows: legs.reduce((sum, leg) => sum + Number(leg.windows ?? 0), 0),
        }
      : undefined,
  }
}

function normalizeTrioSeries(raw: Record<string, unknown>): EpisodeView {
  const state = String(raw.state ?? "queued")
  const status = state === "completed" ? "success"
    : state === "failed" ? "failure"
    : state === "cancelled" ? "cancelled"
    : state === "running" ? "running" : "queued"
  const value = raw.result as Record<string, unknown> | null | undefined
  const legs = Array.isArray(value?.legs)
    ? value.legs as Record<string, unknown>[]
    : []
  const archive = raw.archive as Record<string, unknown> | undefined
  const evidence = archive?.evidence as Record<string, unknown> | undefined
  const nativeReplay = archive?.native_replay as Record<string, unknown> | undefined
  const nativeReplayReason = nonEmptyString(nativeReplay?.reason)
  const nativeArtifacts = parseSeriesNativeArtifacts(nativeReplay?.artifacts, true)
  const entrants = parsePublicEntrants(
    (raw.config as Record<string, unknown> | undefined)?.entrants ?? raw.entrants,
    3,
  )
  const evidenceState = evidence?.state
  const seriesArchive =
    (evidenceState === "pending" || evidenceState === "saving" ||
      evidenceState === "ready" || evidenceState === "unavailable") &&
    (nativeReplay?.state === "saving" ||
      (nativeReplay?.state === "unavailable" && nativeReplayReason) ||
      (nativeReplay?.state === "ready" && nativeArtifacts.length === 9))
      ? {
          evidenceState: evidenceState as SeriesArchiveView["evidenceState"],
          nativeReplayState: nativeReplay.state as SeriesArchiveView["nativeReplayState"],
          ...(nativeReplayReason ? { nativeReplayReason } : {}),
          nativeArtifacts,
        }
      : undefined
  const taskId = typeof raw.task_id === "string" ? raw.task_id
    : typeof (raw.config as Record<string, unknown> | undefined)?.task_id === "string"
      ? String((raw.config as Record<string, unknown>).task_id)
      : "trio-relay-v0"
  const windows = legs.reduce(
    (sum, leg) => sum + Number(leg.decision_windows ?? 0), 0
  )
  return {
    kind: "trio",
    episodeId: String(raw.series_id),
    status,
    observationSeq: windows,
    tick: windows * 10,
    taskId,
    taskLabel: isDemoTrioGameId(taskId)
      ? `${DEMO_TRIO_GAMES[taskId].displayLabel} · three cyclic legs`
      : "Three-agent cyclic series",
    ...(entrants ? { entrants } : {}),
    timeline: [],
    failureCode: typeof raw.failure === "string" ? raw.failure : null,
    ...(seriesArchive ? { seriesArchive } : {}),
    result: value ? {
      finalStateHash: String(value.plan_sha256 ?? ""),
      providerFailures: legs.reduce(
        (sum, leg) => sum + Number(leg.fallback_windows ?? 0), 0
      ),
      outcome: "Three verified cyclic legs",
      windows,
      trioLegs: legs.map((leg, legIndex) => {
        const terminal = leg.terminal as Record<string, unknown> | undefined
        const placements = Array.isArray(leg.placements) ? leg.placements : []
        return {
          legIndex,
          terminalReason: String(terminal?.reason ?? "verified"),
          placements: placements.map((child) => {
            if (!child || typeof child !== "object")
              throw new Error("Trio result placement is invalid")
            const placement = child as Record<string, unknown>
            if (
              !Number.isSafeInteger(placement.place) ||
              !Array.isArray(placement.participant_ids) ||
              !placement.participant_ids.every((id) =>
                ["participant_0", "participant_1", "participant_2"].includes(String(id))
              ) ||
              typeof placement.tie !== "boolean"
            ) throw new Error("Trio result placement is invalid")
            return {
              place: placement.place as number,
              participantIds: placement.participant_ids as ParticipantId[],
              tied: placement.tie,
            }
          }),
        }
      }),
    } : undefined,
  }
}

function parseSeriesNativeArtifacts(
  raw: unknown,
  trio = false
): SeriesNativeReplayArtifact[] {
  if (raw === undefined) return []
  if (!Array.isArray(raw)) throw new Error("Series native replay artifacts are invalid")
  const artifacts = raw.map((child) => {
    if (!child || typeof child !== "object") throw new Error("Series native replay artifact is invalid")
    const value = child as Record<string, unknown>
    const legIndex = value.leg_index
    const participantId = value.participant_id
    const sha256 = nonEmptyString(value.sha256)
    if (
      !(trio ? [0, 1, 2] : [0, 1]).includes(Number(legIndex)) ||
      !(trio
        ? ["participant_0", "participant_1", "participant_2"]
        : ["participant_0", "participant_1"]
      ).includes(String(participantId)) ||
      value.width !== 1280 || value.height !== 720 || value.fps !== 30 ||
      value.mime_type !== "video/mp4" || !sha256?.match(/^[0-9a-f]{64}$/)
    ) throw new Error("Series native replay artifact is invalid")
    return {
      legIndex: legIndex as 0 | 1 | 2,
      participantId: participantId as ParticipantId,
      sha256,
      width: 1280 as const,
      height: 720 as const,
      fps: 30 as const,
    }
  })
  if (new Set(artifacts.map((value) => `${value.legIndex}:${value.participantId}`)).size !== artifacts.length)
    throw new Error("Series native replay artifacts are duplicated")
  return artifacts
}

function parsePublicEntrants(raw: unknown, expectedCount: 2 | 3): PublicEntrant[] | undefined {
  if (raw === undefined) return undefined
  if (!Array.isArray(raw) || raw.length !== expectedCount)
    throw new Error("Public entrant roster is invalid")
  const entrants = raw.map((child) => {
    if (!child || typeof child !== "object")
      throw new Error("Public entrant roster is invalid")
    const value = child as Record<string, unknown>
    const entrantId = nonEmptyString(value.entrant_id)
    const model = nonEmptyString(value.model)
    const displayNameValue = nonEmptyString(value.display_name)
    if (
      !entrantId || !model || entrantId.length > 128 || model.length > 200 ||
      (displayNameValue !== null && displayNameValue.length > 128)
    ) throw new Error("Public entrant roster is invalid")
    const displayName = displayNameValue ?? model
    return { entrantId, displayName, model }
  })
  if (new Set(entrants.map((entrant) => entrant.entrantId)).size !== expectedCount)
    throw new Error("Public entrant roster contains duplicates")
  return entrants
}

function seriesTimelineRows(raw: unknown): TimelineRow[] {
  if (!Array.isArray(raw)) throw new Error("Series timeline is invalid")
  const rows: TimelineRow[] = []
  for (const [legIndex, legValue] of raw.entries()) {
    if (!legValue || typeof legValue !== "object")
      throw new Error("Series timeline is invalid")
    const receipts = (legValue as Record<string, unknown>).receipts
    if (!Array.isArray(receipts)) throw new Error("Series timeline is invalid")
    for (const windowValue of receipts) {
      if (!windowValue || typeof windowValue !== "object")
        throw new Error("Series timeline is invalid")
      const window = windowValue as Record<string, unknown>
      const participants = window.participants as Record<string, unknown> | undefined
      if (!participants) throw new Error("Series timeline is invalid")
      const summaries = ["participant_0", "participant_1"].map((participantId) => {
        const receipt = participants[participantId] as Record<string, unknown> | undefined
        if (!receipt) throw new Error("Series timeline is invalid")
        return {
          action: `${participantId}: ${String(receipt.action_id ?? "neutral input")}`,
          receipt: `${participantId}: ${String(receipt.disposition ?? "recorded")}`,
          ticks: safeNonNegativeInteger(receipt.applied_ticks),
        }
      })
      const appliedTicks = Math.max(...summaries.map((summary) => summary.ticks))
      const observationSeq = safeNonNegativeInteger(window.observation_seq)
      const start = observationSeq * 10
      rows.push({
        window: rows.length,
        intent: `Leg ${legIndex + 1} · ${summaries.map((value) => value.action).join(" / ")}`,
        receipt: summaries.map((value) => value.receipt).join(" / "),
        ticks: `${start}–${start + appliedTicks}`,
        latencyMs: null,
      })
    }
  }
  return rows
}

function trioTimelineRows(raw: unknown): TimelineRow[] {
  if (!Array.isArray(raw)) throw new Error("Trio timeline is invalid")
  return raw.map((child, index) => {
    if (!child || typeof child !== "object") throw new Error("Trio timeline is invalid")
    const value = child as Record<string, unknown>
    const legIndex = safeNonNegativeInteger(value.leg_index)
    const tick = safeNonNegativeInteger(value.tick)
    if (legIndex > 2 || typeof value.kind !== "string" || typeof value.summary !== "string")
      throw new Error("Trio timeline is invalid")
    return {
      window: index,
      intent: `Leg ${legIndex + 1} · ${value.kind}`,
      receipt: value.summary,
      ticks: String(tick),
      latencyMs: null,
    }
  })
}

function trioReceiptTimelineRows(legsRaw: unknown, eventsRaw: unknown): TimelineRow[] {
  if (!Array.isArray(legsRaw)) return trioTimelineRows(eventsRaw)
  const rows: TimelineRow[] = []
  for (const legValue of legsRaw) {
    if (!legValue || typeof legValue !== "object")
      throw new Error("Trio receipt timeline is invalid")
    const leg = legValue as Record<string, unknown>
    const legIndex = safeNonNegativeInteger(leg.leg_index)
    if (legIndex > 2 || !Array.isArray(leg.receipts))
      throw new Error("Trio receipt timeline is invalid")
    for (const windowValue of leg.receipts) {
      if (!windowValue || typeof windowValue !== "object")
        throw new Error("Trio receipt timeline is invalid")
      const window = windowValue as Record<string, unknown>
      const participants = window.participants as Record<string, unknown> | undefined
      if (!participants) throw new Error("Trio receipt timeline is invalid")
      const summaries = (["participant_0", "participant_1", "participant_2"] as const)
        .map((participantId) => {
          const receipt = participants[participantId] as Record<string, unknown> | undefined
          if (!receipt) throw new Error("Trio receipt timeline is invalid")
          return {
            action: `${participantId}: ${String(receipt.action_id ?? "neutral input")}`,
            disposition: `${participantId}: ${String(receipt.disposition ?? "recorded")}`,
            ticks: safeNonNegativeInteger(receipt.applied_ticks),
          }
        })
      const observationSeq = safeNonNegativeInteger(window.observation_seq)
      const appliedTicks = Math.max(...summaries.map((value) => value.ticks))
      const start = observationSeq * 10
      rows.push({
        window: rows.length,
        intent: `Leg ${legIndex + 1} · ${summaries.map((value) => value.action).join(" / ")}`,
        receipt: summaries.map((value) => value.disposition).join(" / "),
        ticks: `${start}–${start + appliedTicks}`,
        latencyMs: null,
      })
    }
  }
  return rows
}

function parseTrioEvaluationLeg(raw: unknown): TrioEvaluationView["legs"][number] {
  if (!raw || typeof raw !== "object") throw new Error("Trio evaluation leg is invalid")
  const value = raw as Record<string, unknown>
  const legIndex = safeNonNegativeInteger(value.leg_index)
  const completionTick = value.completion_tick
  if (
    legIndex > 2 ||
    (completionTick !== null && !Number.isSafeInteger(completionTick)) ||
    typeof value.terminal_reason !== "string" ||
    !Array.isArray(value.placements)
  ) throw new Error("Trio evaluation leg is invalid")
  const placements = value.placements.map((child) => {
    if (!child || typeof child !== "object") throw new Error("Trio placement is invalid")
    const placement = child as Record<string, unknown>
    if (
      !Number.isSafeInteger(placement.placement) ||
      !Array.isArray(placement.entrant_ids) ||
      !placement.entrant_ids.every((entrant) => typeof entrant === "string") ||
      typeof placement.tied !== "boolean"
    ) throw new Error("Trio placement is invalid")
    return {
      placement: placement.placement as number,
      entrantIds: placement.entrant_ids as string[],
      tied: placement.tied,
    }
  })
  return {
    legIndex,
    terminalReason: value.terminal_reason,
    completionTick: completionTick as number | null,
    placements,
  }
}

function timelineRows(events: unknown[]): TimelineRow[] {
  const rows: TimelineRow[] = []
  for (const event of events) {
    if (!event || typeof event !== "object") continue
    const item = event as Record<string, unknown>
    if (
      item.kind !== "action_receipts" ||
      !item.value ||
      typeof item.value !== "object"
    )
      continue
    const record = item.value as Record<string, unknown>
    const participants = record.participants as
      Record<string, unknown> | undefined
    const receipt = participants?.participant_0 as
      Record<string, unknown> | undefined
    if (!receipt) continue
    const codes = Array.isArray(receipt.codes) ? receipt.codes.map(String) : []
    rows.push({
      window: Number(record.observation_seq ?? rows.length),
      intent: String(receipt.action_id ?? "neutral input"),
      receipt: String(receipt.disposition ?? codes[0] ?? "recorded"),
      ticks: `${Number(receipt.start_tick ?? 0)}–${Number(receipt.end_tick ?? 0)}`,
      latencyMs: null,
    })
  }
  return rows
}

function lastTick(rows: TimelineRow[]): number {
  const final = rows.at(-1)?.ticks.split("–").at(-1)
  return final ? Number(final) : 0
}

function safeNonNegativeInteger(value: unknown): number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : 0
}

function parseSavedReplay(raw: unknown): SavedReplaySummary {
  if (!raw || typeof raw !== "object")
    throw new Error("Saved replay is invalid")
  const value = raw as Record<string, unknown>
  const video = value.video
  if (!video || typeof video !== "object")
    throw new Error("Saved replay video is invalid")
  const videoValue = video as Record<string, unknown>
  const replayId = nonEmptyString(value.replay_id)
  const episodeId = nonEmptyString(value.episode_id)
  const taskId = nonEmptyString(value.task_id)
  const scenarioId = nonEmptyString(value.scenario_id)
  const evaluationProfileId = nonEmptyString(value.evaluation_profile_id)
  const label = nonEmptyString(value.label)
  const outcome = nonEmptyString(value.outcome)
  if (
    !replayId ||
    !episodeId ||
    !taskId ||
    !scenarioId ||
    !evaluationProfileId ||
    !isDemoScenario(scenarioId) ||
    DEMO_SCENARIOS[scenarioId].evaluationProfileId !== evaluationProfileId ||
    !label ||
    !outcome ||
    typeof videoValue.available !== "boolean" ||
    videoValue.mime_type !== "video/mp4"
  ) {
    throw new Error("Saved replay is invalid")
  }
  return {
    replayId,
    episodeId,
    scenarioId,
    evaluationProfileId,
    taskId,
    label,
    outcome,
    authorityTicks: safeNonNegativeInteger(value.authority_ticks),
    durationSeconds: safeNonNegativeNumber(value.duration_seconds),
    video: {
      available: videoValue.available,
      mimeType: "video/mp4",
      width: safePositiveInteger(videoValue.width),
      height: safePositiveInteger(videoValue.height),
      fps: safePositiveInteger(videoValue.fps),
    },
  }
}

function parseEvaluation(raw: unknown): EvaluationProjectionView {
  if (!raw || typeof raw !== "object") throw new Error("Evaluation is invalid")
  const value = raw as Record<string, unknown>
  const run = value.run as Record<string, unknown> | undefined
  const result = value.result as Record<string, unknown> | undefined
  const evaluation = value.evaluation as Record<string, unknown> | undefined
  const metrics = evaluation?.metrics
  const projectionSha256 = nonEmptyString(value.projection_sha256)
  if (
    value.schema_version !== "llm-controller/evaluation-projection/1.0.0" ||
    value.state !== "supported" ||
    (value.scope !== "solo" && value.scope !== "paired_duel_leg") ||
    !projectionSha256?.match(/^[0-9a-f]{64}$/) ||
    !run || !result || !metrics || typeof metrics !== "object"
  ) throw new Error("Evaluation is invalid")
  const episodeId = nonEmptyString(run.episode_id)
  const taskId = nonEmptyString(run.task_id)
  const outcome = nonEmptyString((result.terminal as Record<string, unknown> | undefined)?.outcome)
  const finalStateHash = nonEmptyString(result.final_state_hash)
  if (!episodeId || !taskId || !outcome || !finalStateHash?.match(/^[0-9a-f]{64}$/))
    throw new Error("Evaluation is invalid")
  const safeMetrics: Record<string, EvaluationMetric> = {}
  for (const [name, metric] of Object.entries(metrics)) {
    if (!metric || typeof metric !== "object" || !/^[a-z][a-z0-9_]{0,63}$/.test(name))
      throw new Error("Evaluation is invalid")
    const item = metric as Record<string, unknown>
    if (item.state === "supported") safeMetrics[name] = { state: "supported", value: item.value }
    else if (item.state === "unavailable" && typeof item.reason === "string")
      safeMetrics[name] = { state: "unavailable", reason: item.reason }
    else throw new Error("Evaluation is invalid")
  }
  return {
    schemaVersion: value.schema_version,
    projectionSha256,
    state: "supported",
    scope: value.scope,
    run: {
      episodeId,
      taskId,
      ...(typeof run.scenario_id === "string" ? { scenarioId: run.scenario_id } : {}),
      ...(typeof run.evaluation_profile_id === "string"
        ? { evaluationProfileId: run.evaluation_profile_id } : {}),
      certificationEligible: run.certification_eligible === true,
    },
    result: {
      outcome,
      finalStateHash,
      windows: safeNonNegativeInteger(result.windows),
      providerFailures: safeNonNegativeInteger(result.provider_failures),
    },
    metrics: safeMetrics,
  }
}

function parseCachedRtsShowcase(raw: unknown): CachedRtsShowcaseView {
  if (!raw || typeof raw !== "object") throw new Error("Cached RTS showcase is invalid")
  const value = raw as Record<string, unknown>
  const video = objectValue(value.video, "Cached RTS showcase video")
  const winner = objectValue(value.winner, "Cached RTS showcase winner")
  const completion = parseCachedRtsCompletion(value.completion)
  const label = safePublicText(value.label, 180)
  const team = safePublicText(winner.team, 72)
  if (
    value.showcase_id !== "rts-skirmish-v0" || value.task_id !== "rts-skirmish-v0" ||
    value.status !== "ready" || value.cached !== true || !label || !team ||
    video.mime_type !== "video/mp4" || !sha256Value(video.sha256)
  ) throw new Error("Cached RTS showcase is invalid")
  const durationSeconds = publicPositiveInteger(video.duration_seconds)
  const fps = publicPositiveInteger(video.fps)
  const width = publicPositiveInteger(video.width)
  const height = publicPositiveInteger(video.height)
  if (!durationSeconds || !fps || !width || !height || !Array.isArray(value.highlights) || !Array.isArray(value.casualties))
    throw new Error("Cached RTS showcase is invalid")
  let lastSecond = -1
  const highlights = value.highlights.map((child) => {
    const item = objectValue(child, "Cached RTS highlight")
    const atSeconds = publicNonNegativeInteger(item.at_seconds)
    const itemLabel = safePublicText(item.label, 180)
    if (atSeconds === null || !itemLabel || atSeconds <= lastSecond || atSeconds > durationSeconds)
      throw new Error("Cached RTS showcase is invalid")
    lastSecond = atSeconds
    return { atSeconds, label: itemLabel }
  })
  let lastTick = -1
  const casualties = value.casualties.map((child) => {
    const item = objectValue(child, "Cached RTS casualty")
    const atTick = publicNonNegativeInteger(item.at_tick)
    const casualtyTeam = safePublicText(item.team, 72)
    const unitId = safePublicText(item.unit_id, 72)
    if (atTick === null || !casualtyTeam || !unitId || atTick <= lastTick)
      throw new Error("Cached RTS showcase is invalid")
    lastTick = atTick
    return { atTick, team: casualtyTeam, unitId }
  })
  return {
    showcaseId: "rts-skirmish-v0", taskId: "rts-skirmish-v0", label,
    status: "ready", cached: true,
    video: {
      durationSeconds,
      fps,
      height,
      mimeType: "video/mp4",
      sha256: video.sha256 as string,
      width,
    },
    winner: { team }, completion, casualties, highlights,
  }
}

function parseCachedSoloShowcase(raw: unknown): CachedSoloShowcaseView {
  const value = objectValue(raw, "Cached solo showcase")
  const participant = objectValue(value.participant, "Cached solo participant")
  const video = objectValue(value.video, "Cached solo video")
  const verification = objectValue(value.verification, "Cached solo verification")
  const label = safePublicText(value.label, 180)
  const tagline = safePublicText(value.tagline, 240)
  const displayName = safePublicText(participant.display_name, 72)
  const model = safePublicText(participant.model, 72)
  const durationSeconds = typeof video.duration_seconds === "number" &&
    Number.isFinite(video.duration_seconds) && video.duration_seconds > 0
    ? video.duration_seconds
    : null
  const fps = publicPositiveInteger(video.fps)
  const width = publicPositiveInteger(video.width)
  const height = publicPositiveInteger(video.height)
  const authorityTicks = publicPositiveInteger(verification.authority_ticks)
  if (
    value.showcase_id !== "solo-multi-action-v0" || value.task_id !== "construction-v0" ||
    value.scenario_id !== "multi-action-demo-v0" || value.status !== "ready" ||
    value.cached !== true || participant.participant_id !== "participant_0" ||
    !label || !tagline || !displayName || !model || !durationSeconds || !fps || !width ||
    !height || video.mime_type !== "video/mp4" || !sha256Value(video.sha256) ||
    verification.state !== "verified" || verification.renderer !== "godot-movie-maker+ffmpeg" ||
    verification.release_profile !== "worldarena-participant-1080p30-v1" ||
    verification.protocol_version !== "llm-controller/0.1.0" || !authorityTicks ||
    !sha256Value(verification.evidence_sha256) || !sha256Value(verification.replay_sha256) ||
    !sha256Value(verification.final_state_sha256) ||
    !sha256Value(verification.protocol_package_sha256) || !Array.isArray(value.highlights)
  ) throw new Error("Cached solo showcase is invalid")
  let previousSecond = -1
  const highlights = value.highlights.map((child) => {
    const item = objectValue(child, "Cached solo highlight")
    const atSeconds = publicNonNegativeInteger(item.at_seconds)
    const itemLabel = safePublicText(item.label, 180)
    if (atSeconds === null || atSeconds <= previousSecond || atSeconds > durationSeconds || !itemLabel)
      throw new Error("Cached solo showcase is invalid")
    previousSecond = atSeconds
    return { atSeconds, label: itemLabel }
  })
  if (!highlights.length) throw new Error("Cached solo showcase is invalid")
  return {
    showcaseId: "solo-multi-action-v0",
    taskId: "construction-v0",
    scenarioId: "multi-action-demo-v0",
    label,
    tagline,
    status: "ready",
    cached: true,
    participant: { participantId: "participant_0", displayName, model },
    video: {
      durationSeconds,
      fps,
      height,
      mimeType: "video/mp4",
      sha256: video.sha256 as string,
      width,
    },
    highlights,
    verification: {
      state: "verified",
      renderer: "godot-movie-maker+ffmpeg",
      releaseProfile: "worldarena-participant-1080p30-v1",
      evidenceSha256: verification.evidence_sha256 as string,
      replaySha256: verification.replay_sha256 as string,
      finalStateSha256: verification.final_state_sha256 as string,
      protocolPackageSha256: verification.protocol_package_sha256 as string,
      protocolVersion: "llm-controller/0.1.0",
      authorityTicks,
    },
  }
}

function parseCachedRtsEvaluation(raw: unknown): CachedRtsEvaluationView {
  if (!raw || typeof raw !== "object") throw new Error("Cached RTS evaluation is invalid")
  const value = raw as Record<string, unknown>
  const completion = parseCachedRtsCompletion(value.completion)
  const verification = objectValue(value.verification, "Cached RTS verification")
  if (
    value.showcase_id !== "rts-skirmish-v0" || value.task_id !== "rts-skirmish-v0" ||
    verification.state !== "verified" || !Array.isArray(value.metrics) ||
    !sha256Value(verification.manifest_sha256) || !sha256Value(verification.replay_sha256) ||
    !sha256Value(verification.video_sha256) || !sha256Value(verification.final_state_sha256)
  ) throw new Error("Cached RTS evaluation is invalid")
  const ids = new Set<string>()
  const metrics = value.metrics.map((child) => {
    const item = objectValue(child, "Cached RTS metric")
    const id = safePublicText(item.id, 48)
    const label = safePublicText(item.label, 72)
    const metricValue = safePublicText(item.value, 180)
    if (!id || !label || !metricValue || ids.has(id))
      throw new Error("Cached RTS evaluation is invalid")
    ids.add(id)
    return { id, label, value: metricValue }
  })
  if (!metrics.length || metrics.length > 12) throw new Error("Cached RTS evaluation is invalid")
  return {
    showcaseId: "rts-skirmish-v0", taskId: "rts-skirmish-v0", completion, metrics,
    verification: {
      manifestSha256: verification.manifest_sha256 as string,
      replaySha256: verification.replay_sha256 as string,
      videoSha256: verification.video_sha256 as string,
      finalStateSha256: verification.final_state_sha256 as string,
      state: "verified",
    },
  }
}

function parseCachedMazeShowcase(raw: unknown): CachedMazeShowcaseView {
  const value = objectValue(raw, "Cached maze showcase")
  const video = objectValue(value.video, "Cached maze video")
  const winner = objectValue(value.winner, "Cached maze winner")
  const result = objectValue(value.result, "Cached maze result")
  if (
    value.showcase_id !== "trio-maze-race-v0" || value.task_id !== "trio-maze-race-v0" ||
    value.status !== "ready" || value.cached !== true || video.mime_type !== "video/mp4" ||
    !sha256Value(video.sha256) || !Array.isArray(value.entrants) || value.entrants.length !== 3 ||
    !Array.isArray(value.timeline)
  ) throw new Error("Cached maze showcase is invalid")
  const label = safePublicText(value.label, 180)
  const tagline = boundedDisplayText(value.tagline, 180)
  const durationSeconds = publicPositiveInteger(video.duration_seconds)
  const fps = publicPositiveInteger(video.fps)
  const width = publicPositiveInteger(video.width)
  const height = publicPositiveInteger(video.height)
  if (!label || !tagline || !durationSeconds || !fps || !width || !height)
    throw new Error("Cached maze showcase is invalid")
  const expectedEntrants = ["sol", "luna", "terra"]
  const entrants = value.entrants.map((child, index) => {
    const item = objectValue(child, "Cached maze entrant")
    const participantId = participantIdValue(item.participant_id)
    const entrantId = safePublicText(item.entrant_id, 24)
    const displayName = safePublicText(item.display_name, 40)
    const model = safePublicText(item.model, 72)
    const color = safePublicText(item.color, 16)
    const lane = safePublicText(item.lane, 24)
    const style = safePublicText(item.style, 120)
    if (!participantId || entrantId !== expectedEntrants[index] ||
      !["Sol", "Luna", "Terra"].includes(displayName ?? "") || !model ||
      !color || !/^#[0-9a-f]{6}$/i.test(color) || !lane || !style)
      throw new Error("Cached maze showcase is invalid")
    return { participantId, entrantId, displayName, model, color, lane, style } as CachedMazeEntrant
  })
  const winnerId = participantIdValue(result.winner_id)
  const finishOrder = Array.isArray(result.finish_order)
    ? result.finish_order.map(participantIdValue)
    : []
  const winnerName = safePublicText(result.winner, 40)
  const winnerParticipantId = participantIdValue(winner.participant_id)
  const winnerDisplayName = safePublicText(winner.display_name, 40)
  const completionTick = publicNonNegativeInteger(result.completion_tick)
  const reason = safePublicText(result.reason, 72)
  const explanation = safePublicText(result.explanation, 360)
  if (!winnerId || finishOrder.some((item) => item === null) || finishOrder.length !== 3 ||
    !winnerName || !winnerParticipantId || !winnerDisplayName || completionTick === null ||
    !reason || !explanation)
    throw new Error("Cached maze showcase is invalid")
  let previousSecond = -1
  const timeline = value.timeline.map((child) => {
    const item = objectValue(child, "Cached maze highlight")
    const atSeconds = publicNonNegativeInteger(item.at_seconds)
    const participantId = item.participant_id === "broadcast"
      ? "broadcast" as const
      : participantIdValue(item.participant_id)
    const kind = safePublicText(item.kind, 40)
    const itemLabel = safePublicText(item.label, 180)
    if (atSeconds === null || atSeconds <= previousSecond || atSeconds > durationSeconds ||
      !participantId || !kind || !itemLabel) throw new Error("Cached maze showcase is invalid")
    previousSecond = atSeconds
    return { atSeconds, participantId, kind, label: itemLabel }
  })
  return {
    showcaseId: "trio-maze-race-v0", taskId: "trio-maze-race-v0", label, tagline,
    status: "ready", cached: true,
    video: { durationSeconds, fps, height, mimeType: "video/mp4", sha256: video.sha256 as string, width },
    entrants,
    winner: { participantId: winnerParticipantId, displayName: winnerDisplayName },
    result: {
      winnerId, winner: winnerName, finishOrder: finishOrder as ParticipantId[], completionTick,
      reason, explanation,
    },
    timeline,
  }
}

function parseCachedMazeEvaluation(raw: unknown): CachedMazeEvaluationView {
  const value = objectValue(raw, "Cached maze evaluation")
  const verification = objectValue(value.verification, "Cached maze verification")
  if (
    value.showcase_id !== "trio-maze-race-v0" || value.task_id !== "trio-maze-race-v0" ||
    value.scope !== "trio_maze_race" || !Array.isArray(value.participants) ||
    value.participants.length !== 3 || verification.state !== "verified" ||
    verification.deterministic !== true
  ) throw new Error("Cached maze evaluation is invalid")
  const summary = safePublicText(value.summary, 360)
  if (!summary) throw new Error("Cached maze evaluation is invalid")
  const hashes = ["map_sha256", "final_state_sha256", "manifest_sha256", "replay_sha256", "video_sha256"] as const
  if (hashes.some((key) => !sha256Value(verification[key])))
    throw new Error("Cached maze evaluation is invalid")
  const participants = value.participants.map((child, index) => {
    const item = objectValue(child, "Cached maze participant evaluation")
    const participantId = participantIdValue(item.participant_id)
    const displayName = safePublicText(item.display_name, 40)
    const model = safePublicText(item.model, 72)
    const color = safePublicText(item.color, 16)
    const completionSeconds = safePublicText(item.completion_seconds, 16)
    const integer = (key: string) => publicNonNegativeInteger(item[key])
    const place = integer("place")
    if (!participantId || !displayName || !model || !color || !completionSeconds || place !== index + 1)
      throw new Error("Cached maze evaluation is invalid")
    const metrics = [
      "finish_tick", "distance_cells", "shortest_path_cells", "path_efficiency_basis_points",
      "unique_corridor_cells", "repeated_corridor_cells", "passages_explored", "dead_ends_entered",
      "successful_backtracks", "collisions", "invalid_decisions", "idle_thinking_ticks",
    ] as const
    const values = Object.fromEntries(metrics.map((key) => [key, integer(key)]))
    if (Object.values(values).some((metric) => metric === null))
      throw new Error("Cached maze evaluation is invalid")
    return {
      participantId, displayName, model, color, place, completionSeconds,
      finishTick: values.finish_tick!, distanceCells: values.distance_cells!,
      shortestPathCells: values.shortest_path_cells!,
      pathEfficiencyBasisPoints: values.path_efficiency_basis_points!,
      uniqueCorridorCells: values.unique_corridor_cells!, repeatedCorridorCells: values.repeated_corridor_cells!,
      passagesExplored: values.passages_explored!, deadEndsEntered: values.dead_ends_entered!,
      successfulBacktracks: values.successful_backtracks!, collisions: values.collisions!,
      invalidDecisions: values.invalid_decisions!, idleThinkingTicks: values.idle_thinking_ticks!,
    }
  })
  return {
    showcaseId: "trio-maze-race-v0", taskId: "trio-maze-race-v0", scope: "trio_maze_race",
    summary, participants,
    verification: {
      state: "verified", deterministic: true,
      mapSha256: verification.map_sha256 as string,
      finalStateSha256: verification.final_state_sha256 as string,
      manifestSha256: verification.manifest_sha256 as string,
      replaySha256: verification.replay_sha256 as string,
      videoSha256: verification.video_sha256 as string,
    },
  }
}

function parseCachedCrossroadsShowcase(raw: unknown): CachedCrossroadsShowcaseView {
  const value = objectValue(raw, "Cached Crossroads showcase")
  const video = objectValue(value.video, "Cached Crossroads video")
  const authority = objectValue(value.authority, "Cached Crossroads authority")
  const title = safePublicText(value.title, 180)
  const tagline = safePublicText(value.tagline, 240)
  if (
    value.showcase_id !== "crossroads-conquest-v0" || value.task_id !== "crossroads-conquest-v0" ||
    value.status !== "ready" || value.cached !== true || value.verified !== true ||
    !title || !tagline || !Array.isArray(value.entrants) || value.entrants.length !== 3 ||
    !Array.isArray(value.timeline) || value.timeline.length !== 17 ||
    video.duration_seconds !== 180 || video.fps !== 30 || video.width !== 1920 ||
    video.height !== 1080 || video.mime_type !== "video/mp4" || !sha256Value(video.sha256) ||
    authority.protocol !== "world-arena/0.4" || authority.seed !== 424242 ||
    authority.policy_id !== "crossroads-conquest-demo-v1" || authority.map_id !== "tri_13_v1" ||
    authority.rules_id !== "arena-v0.4"
  ) throw new Error("Cached Crossroads showcase is invalid")

  const expectedEntrants = [
    ["participant_0", "sol", "Sol", "△", "#fbbf24"],
    ["participant_1", "luna", "Luna", "○", "#a78bfa"],
    ["participant_2", "terra", "Terra", "□", "#34d399"],
  ] as const
  const entrants = value.entrants.map((child, index) => {
    const item = objectValue(child, "Cached Crossroads entrant")
    const expected = expectedEntrants[index]
    if (
      item.entrant_id !== expected[0] || item.faction_id !== expected[1] ||
      item.display_name !== expected[2] || item.glyph !== expected[3] ||
      item.color !== expected[4] || item.policy_id !== "crossroads-conquest-demo-v1"
    ) throw new Error("Cached Crossroads showcase is invalid")
    return {
      entrantId: expected[0], factionId: expected[1], displayName: expected[2],
      glyph: expected[3], color: expected[4], policyId: "crossroads-conquest-demo-v1" as const,
    }
  })
  const winner = parseCrossroadsWinner(value.winner)
  const placements = parseCrossroadsPlacements(value.placements)
  const eliminationOrder = parseCrossroadsEliminations(value.elimination_order)
  const timelineWindows = [
    ["opening_reveal", 0, 12, true], ["sol_introduction", 12, 19, true],
    ["terra_introduction", 19, 26, true], ["luna_introduction", 26, 33, true],
    ["terra_claims_crossroads", 33, 44, false], ["sol_prepares_assault", 44, 55, false],
    ["luna_observes", 55, 65, false], ["crossroads_clash", 65, 78, false],
    ["sol_takes_crossroads", 78, 90, false], ["two_front_march", 90, 102, false],
    ["terra_counterpunch", 102, 114, false], ["sol_breaches_terra", 114, 126, false],
    ["terra_eliminated", 126, 137, false], ["exposed_sol_overview", 137, 146, false],
    ["luna_strikes", 146, 158, false], ["sol_eliminated", 158, 169, false],
    ["verified_result", 169, 180, false],
  ] as const
  let previousSecond = -1
  const timeline = value.timeline.map((child, index) => {
    const item = objectValue(child, "Cached Crossroads timeline event")
    const [expectedBeatId, windowStart, windowEnd, expectedEditorial] = timelineWindows[index]
    const beatId = safePublicText(item.beat_id, 80)
    const atSeconds = publicNonNegativeNumber(item.at_seconds)
    const round = publicNonNegativeInteger(item.round)
    const frameIndex = publicNonNegativeInteger(item.frame_index)
    const eventId = item.event_id === "" ? "" : safePublicText(item.event_id, 80)
    const kind = safePublicText(item.kind, 48)
    const label = safePublicText(item.label, 240)
    if (
      beatId !== expectedBeatId || eventId === null || atSeconds === null || atSeconds < windowStart ||
      atSeconds >= windowEnd || atSeconds < previousSecond || round === null || round > 29 ||
      frameIndex === null || !kind || !label || item.editorial !== expectedEditorial ||
      (expectedEditorial ? eventId !== "" : !eventId)
    ) throw new Error("Cached Crossroads showcase is invalid")
    previousSecond = atSeconds
    return { beatId, atSeconds, round, frameIndex, eventId, kind, editorial: expectedEditorial, label }
  })
  return {
    showcaseId: "crossroads-conquest-v0",
    taskId: "crossroads-conquest-v0",
    title,
    tagline,
    status: "ready",
    cached: true,
    verified: true,
    entrants,
    winner,
    placements,
    eliminationOrder,
    timeline,
    video: {
      durationSeconds: 180,
      fps: 30,
      height: 1080,
      mimeType: "video/mp4",
      sha256: video.sha256 as string,
      width: 1920,
    },
    authority: {
      protocol: "world-arena/0.4",
      seed: 424242,
      policyId: "crossroads-conquest-demo-v1",
      mapId: "tri_13_v1",
      rulesId: "arena-v0.4",
    },
  }
}

function parseCachedCrossroadsEvaluation(raw: unknown): CachedCrossroadsEvaluationView {
  const value = objectValue(raw, "Cached Crossroads evaluation")
  const outcome = objectValue(value.outcome, "Cached Crossroads outcome")
  const verification = objectValue(value.verification, "Cached Crossroads verification")
  if (
    value.showcase_id !== "crossroads-conquest-v0" || value.task_id !== "crossroads-conquest-v0" ||
    value.scope !== "crossroads_conquest" || !Array.isArray(value.factions) ||
    value.factions.length !== 3 || verification.state !== "verified" ||
    verification.deterministic !== true || verification.deterministic_runs !== 2 ||
    verification.order_rejections !== 0
  ) throw new Error("Cached Crossroads evaluation is invalid")
  const lunaFirstHostileRound = publicNonNegativeInteger(verification.luna_first_hostile_round)
  const hashKeys = [
    "manifest_sha256", "replay_sha256", "evaluation_sha256", "video_sha256",
    "normalized_trace_sha256", "final_state_sha256",
  ] as const
  if (lunaFirstHostileRound === null || hashKeys.some((key) => !sha256Value(verification[key])))
    throw new Error("Cached Crossroads evaluation is invalid")
  const winner = parseCrossroadsWinner(outcome.winner)
  const placements = parseCrossroadsPlacements(outcome.placements)
  const eliminationOrder = parseCrossroadsEliminations(outcome.elimination_order)
  if (lunaFirstHostileRound !== eliminationOrder[0].round + 1)
    throw new Error("Cached Crossroads evaluation is invalid")
  const factions = value.factions.map((child) => {
    const item = objectValue(child, "Cached Crossroads faction evaluation")
    const factionId = crossroadsFactionIdValue(item.faction_id)
    const placement = publicPositiveInteger(item.placement)
    const coreHp = publicNonNegativeInteger(item.core_hp)
    const eliminatedRound = item.eliminated_round === null
      ? null
      : publicNonNegativeInteger(item.eliminated_round)
    const eliminatedBy = item.eliminated_by === null
      ? null
      : crossroadsFactionIdValue(item.eliminated_by)
    const strongholdsDestroyed = publicNonNegativeInteger(item.strongholds_destroyed)
    if (
      !factionId || !placement || coreHp === null ||
      (item.eliminated_round !== null && eliminatedRound === null) ||
      (item.eliminated_by !== null && eliminatedBy === null) || strongholdsDestroyed === null
    ) throw new Error("Cached Crossroads evaluation is invalid")
    return { factionId, placement, coreHp, eliminatedRound, eliminatedBy, strongholdsDestroyed }
  })
  const byFaction = new Map(factions.map((faction) => [faction.factionId, faction]))
  if (
    byFaction.size !== 3 || byFaction.get("luna")?.placement !== 1 ||
    byFaction.get("sol")?.placement !== 2 || byFaction.get("terra")?.placement !== 3
  ) throw new Error("Cached Crossroads evaluation is invalid")
  return {
    showcaseId: "crossroads-conquest-v0",
    taskId: "crossroads-conquest-v0",
    scope: "crossroads_conquest",
    outcome: { winner, placements, eliminationOrder },
    verification: {
      state: "verified",
      deterministic: true,
      deterministicRuns: 2,
      orderRejections: 0,
      lunaFirstHostileRound,
      manifestSha256: verification.manifest_sha256 as string,
      replaySha256: verification.replay_sha256 as string,
      evaluationSha256: verification.evaluation_sha256 as string,
      videoSha256: verification.video_sha256 as string,
      normalizedTraceSha256: verification.normalized_trace_sha256 as string,
      finalStateSha256: verification.final_state_sha256 as string,
    },
    factions,
  }
}

function parseCrossroadsWinner(raw: unknown): CachedCrossroadsShowcaseView["winner"] {
  const value = objectValue(raw, "Cached Crossroads winner")
  if (
    value.entrant_id !== "participant_1" || value.faction_id !== "luna" ||
    value.display_name !== "Luna"
  ) throw new Error("Cached Crossroads outcome is invalid")
  return { entrantId: "participant_1", factionId: "luna", displayName: "Luna" }
}

function parseCrossroadsPlacements(raw: unknown): CachedCrossroadsPlacement[] {
  if (!Array.isArray(raw) || raw.length !== 3)
    throw new Error("Cached Crossroads placements are invalid")
  const expected = [
    [1, "participant_1", "luna", "Luna"],
    [2, "participant_0", "sol", "Sol"],
    [3, "participant_2", "terra", "Terra"],
  ] as const
  return raw.map((child, index) => {
    const item = objectValue(child, "Cached Crossroads placement")
    const target = expected[index]
    if (
      item.placement !== target[0] || item.entrant_id !== target[1] ||
      item.faction_id !== target[2] || item.display_name !== target[3]
    ) throw new Error("Cached Crossroads placements are invalid")
    return {
      placement: target[0], entrantId: target[1], factionId: target[2], displayName: target[3],
    }
  })
}

function parseCrossroadsEliminations(raw: unknown): CachedCrossroadsElimination[] {
  if (!Array.isArray(raw) || raw.length !== 2)
    throw new Error("Cached Crossroads elimination order is invalid")
  const expected = [[1, "terra", "sol"], [2, "sol", "luna"]] as const
  const values = raw.map((child, index) => {
    const item = objectValue(child, "Cached Crossroads elimination")
    const target = expected[index]
    const round = publicPositiveInteger(item.round)
    const eventId = safePublicText(item.event_id, 80)
    if (
      item.order !== target[0] || item.faction_id !== target[1] ||
      item.eliminated_by !== target[2] || !round || round > 29 || !eventId
    ) throw new Error("Cached Crossroads elimination order is invalid")
    return {
      order: target[0], factionId: target[1], eliminatedBy: target[2], round, eventId,
    }
  })
  if (values[0].round >= values[1].round)
    throw new Error("Cached Crossroads elimination order is invalid")
  return values
}

function parseCachedRtsCompletion(raw: unknown): CachedRtsShowcaseView["completion"] {
  const completion = objectValue(raw, "Cached RTS completion")
  const outcome = safePublicText(completion.outcome, 40)
  const reason = safePublicText(completion.reason, 72)
  const tick = publicNonNegativeInteger(completion.tick)
  if (!outcome || !reason || tick === null) throw new Error("Cached RTS completion is invalid")
  return { outcome, reason, tick }
}

function objectValue(value: unknown, name: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${name} is invalid`)
  return value as Record<string, unknown>
}

function participantIdValue(value: unknown): ParticipantId | null {
  return value === "participant_0" || value === "participant_1" || value === "participant_2"
    ? value
    : null
}

function crossroadsFactionIdValue(value: unknown): CrossroadsFactionId | null {
  return value === "sol" || value === "luna" || value === "terra" ? value : null
}

function boundedDisplayText(value: unknown, maxLength: number): string | null {
  return typeof value === "string" && value.length > 0 && value.length <= maxLength && !value.includes("\u0000")
    ? value
    : null
}

function sha256Value(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value)
}

function publicNonNegativeInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null
}

function publicNonNegativeNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null
}

function publicPositiveInteger(value: unknown): number | null {
  const parsed = publicNonNegativeInteger(value)
  return parsed !== null && parsed > 0 ? parsed : null
}

function safePublicText(value: unknown, maxLength: number): string | null {
  if (typeof value !== "string" || !value || value.length > maxLength || value.includes("\u0000")) return null
  if (/api[ _]key|authorization|bearer|chain of thought|credential|memory|observation|prompt|raw[ _]output|secret|spectator|token/i.test(value)) return null
  return value
}

function normalizeSavedReplayAvailability(
  value: unknown
): SavedReplayAvailability | undefined {
  if (!value || typeof value !== "object") return undefined
  const replay = value as Record<string, unknown>
  const state = replay.state
  if (state !== "saving" && state !== "ready" && state !== "unavailable")
    return undefined
  const replayId = nonEmptyString(replay.replay_id)
  return replayId ? { state, replayId } : { state }
}

function safePositiveInteger(value: unknown): number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0
    ? value
    : 0
}

function safeNonNegativeNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : 0
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null
}
