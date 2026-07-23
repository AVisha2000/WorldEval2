export type LabReadiness = "live_ready" | "demo_replay_ready" | "experimental"

export type LabProvider = "openai" | "anthropic" | "gemini" | null

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
  mode: string | null
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
  mode: "exploratory"
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
  lifecycle: string
  frame: LabMazeFrame | null
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
