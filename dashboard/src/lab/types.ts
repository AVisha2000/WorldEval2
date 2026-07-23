export type LabReadiness = "live_ready" | "demo_replay_ready" | "experimental"

export type LabProvider = "openai" | "anthropic" | "gemini" | null

export type LabGameCategoryId =
  | "sandbox-primitives"
  | "solo-agent-tasks"
  | "two-agent-games"
  | "multi-agent-games"
  | "strategy-worlds"

export type LabGameCategory = {
  id: LabGameCategoryId
  label: string
  order: number
  description: string
}

/**
 * Canonical category identities remain usable if a catalogue response is old or
 * malformed. Safe, validated server labels can replace the presentation text,
 * but cannot introduce a new category or reorder the product taxonomy.
 */
export const LAB_GAME_CATEGORIES: readonly LabGameCategory[] = [
  {
    id: "sandbox-primitives",
    label: "Sandbox Primitives",
    order: 10,
    description:
      "Godot-owned building blocks for movement, visibility, resources, interaction, scoring, and termination.",
  },
  {
    id: "solo-agent-tasks",
    label: "Solo Agent Tasks",
    order: 20,
    description:
      "Single-agent environments focused on control, planning, and execution.",
  },
  {
    id: "two-agent-games",
    label: "Two-Agent Games",
    order: 30,
    description:
      "Paired competitive or cooperative environments with isolated agent state.",
  },
  {
    id: "multi-agent-games",
    label: "Multi-Agent Games",
    order: 40,
    description:
      "Environments with three or more independently controlled participants.",
  },
  {
    id: "strategy-worlds",
    label: "Strategy Worlds",
    order: 50,
    description:
      "Long-horizon worlds centred on economy, tactics, territorial control, and adaptation.",
  },
] as const

export type LabInteractionKind =
  "solo" | "cooperative" | "competitive" | "mixed"

export type LabGameCapabilities = {
  liveLaunch: boolean
  demo: boolean
  replay: boolean
  spectator: boolean
  benchmark: boolean
  checkpoint: boolean
}

export type LabMetric = {
  id: string
  label: string
  description: string
}

export type LabControl = {
  id: string
  label: string
  controlType: string
  description: string
  options: string[]
}

export type LabGame = {
  id: string
  title: string
  readiness: LabReadiness
  readinessNote: string
  primaryCategory: LabGameCategory
  secondaryTags: string[]
  participants: {
    minimum: number
    maximum: number
  }
  interactionKind: LabInteractionKind
  capabilities: LabGameCapabilities
  taskIds: string[]
  capabilityStatements: string[]
  agentInterface: {
    observation: string
    actions: string
    memory: string
  }
  scoring: {
    summary: string
    metrics: LabMetric[]
  }
  configurationControls: LabControl[]
  failureModes: string[]
  safety: {
    publicView: string
    privateAgentState: string
  }
  supportedModes: Array<{
    id: string
    label: string
    description: string
  }>
}

export type LabRun = {
  id: string
  gameId: string
  gameVersion: string | null
  lifecycle: string
  mode: "demo" | "exploratory" | "sealed_benchmark" | null
  provider: LabProvider
  /** Whether this process can still communicate with the live game authority. */
  authorityAvailable: boolean | null
  contractSha256: string | null
  parentContractSha256: string | null
  lineageChangeCount: number
  replayAvailable: boolean
  videoAvailable: boolean
  resumeSupported: boolean
  createdAt: string | null
}

export function isOpenAiLabRunMode(
  mode: LabRun["mode"]
): mode is "exploratory" | "sealed_benchmark" {
  return mode === "exploratory" || mode === "sealed_benchmark"
}

export type LabLeaderboardRow = {
  model: string
  completion: string | null
  calls: string | null
  pathEfficiency: string | null
  cost: string | null
  evidence: string | null
}

export type LabBenchmark = {
  gameId: string
  seasonState: string
  leaderboard: LabLeaderboardRow[]
  recipeCount: number
  message: string | null
}

export type RemoteState<T> =
  | { kind: "loading"; data: null }
  | { kind: "ready"; data: T }
  | { kind: "offline"; data: null }

export type LabLaunchInput = {
  apiKey: string
  provider: "openai"
  models: {
    sol: string
    terra: string
    luna: string
  }
  visionRangeCells: "1" | "2" | "4" | "8" | "infinite"
  skillMode: "none" | "maze-navigation-v1"
  mode: "exploratory" | "sealed_benchmark"
}

export type LabGenericLaunchInput = {
  gameId: string
  mode: "demo" | "live"
  seed: number
  apiKey: string
  models: string[]
}

export type LabSession = {
  operator: string
  csrfToken: string
}

export type LabAuthMode = "local" | "magic_link"

export type LabCell = [number, number]

export type LabMazeRacer = {
  participantId: string
  entrantId: string
  displayName: string
  color: string
  position: LabCell
  path: LabCell[]
  visibleCells: LabCell[]
  providerCalls: number
  finished: boolean
}

export type LabMazeFrame = {
  tick: number
  providerCalls: number
  map: {
    rows: string[]
    start: LabCell
    exit: LabCell
  }
  racers: LabMazeRacer[]
}

export type LabProjection = {
  runId: string
  contractSha256: string
  lifecycle: string
  sequence: number
  frame: LabMazeFrame | null
  summary: LabGenericProjectionSummary | null
}

export type LabGenericProjectionSummary = {
  gameId: string
  gameVersion: string
  mode: string
  scenarioId: string | null
  entrants: Array<{
    entrantId: string
    displayName: string
    modelId: string
    provider: string
  }>
  authority: {
    state: string
    failureCode: string | null
    authorityTick: number | null
    decisionSequence: number | null
    replayState: string | null
  } | null
  eventCount: number
  terminalAvailable: boolean
}

export type LabPublicReplay = {
  publicationSlug: string
  gameId: string
  gameVersion: string
  lifecycle: "completed" | "sealed" | "verified"
  publishedAt: string
  sequence: number
  eventCount: number
  frame: LabMazeFrame | null
  summary: LabGenericProjectionSummary | null
}

export type LabRunEvidenceAction = "seal" | "verify" | "publish" | "benchmark"

export type LabRunEvidenceResult = {
  run: LabRun | null
  notice: string
  publicPath: string | null
}

/** A transient, authenticated-only frame from the live Lab spectator buffer. */
export type LabSpectatorFrame = {
  sequence: number
  frame: LabMazeFrame
}

export type LabSpectatorFeed = {
  runId: string
  cursor: number
  resetRequired: boolean
  frames: LabSpectatorFrame[]
}

export type LabSandboxPrimitive = {
  id: string
  title: string
  summary: string
  compositionRank: number
  dependencies: string[]
  capabilities: string[]
}

export type LabSandboxRecipe = {
  id: string
  title: string
  summary: string
  lifecycle: "canonical" | "draft"
  executable: boolean
  primitiveIds: string[]
  compositionOrder: string[]
  authorityOwner: "godot" | null
  taskId: string | null
  protocolVersion: string | null
}

export type LabSandboxManifest = {
  authorityOwner: "godot"
  manifestSha256: string
  primitives: LabSandboxPrimitive[]
  recipes: LabSandboxRecipe[]
}
