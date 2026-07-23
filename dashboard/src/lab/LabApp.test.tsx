import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"
import type { CachedMazeShowcaseView } from "@/api"
import { LabApp, PublicLabApp } from "./LabApp"
import {
  cloneLabRun,
  getPublicGameBenchmark,
  getPublicGameReplays,
  getPublicLabGame,
  getLabRun,
  getLabRunProjection,
  getLabSandboxManifest,
  launchGenericGame,
  launchLabRunDraft,
  launchLabyrinth,
  publishLabRun,
  sealLabRun,
  submitLabRunToBenchmark,
} from "./lab-api"

const showcase = {
  showcaseId: "trio-maze-race-v0",
  taskId: "trio-maze-race-v0",
  label: "WorldArena: Labyrinth Run",
  tagline: "Three agents. One maze.",
  status: "ready",
  cached: true,
  video: {
    durationSeconds: 180,
    fps: 30,
    height: 1080,
    mimeType: "video/mp4",
    sha256: "a".repeat(64),
    width: 1920,
  },
  entrants: [
    {
      participantId: "participant_0",
      entrantId: "sol",
      displayName: "Sol",
      model: "demo-sol-v1",
      color: "#fbbf24",
      lane: "gold",
      style: "search",
    },
    {
      participantId: "participant_1",
      entrantId: "luna",
      displayName: "Luna",
      model: "demo-luna-v1",
      color: "#a78bfa",
      lane: "purple",
      style: "search",
    },
    {
      participantId: "participant_2",
      entrantId: "terra",
      displayName: "Terra",
      model: "demo-terra-v1",
      color: "#34d399",
      lane: "green",
      style: "search",
    },
  ],
  winner: { participantId: "participant_0", displayName: "Sol" },
  result: {
    winnerId: "participant_0",
    winner: "Sol",
    finishOrder: ["participant_0", "participant_2", "participant_1"],
    completionTick: 584,
    reason: "all_racers_finished",
    explanation: "Sol finished first.",
  },
  timeline: [
    {
      atSeconds: 12,
      participantId: "participant_0",
      kind: "junction",
      label: "First junction",
    },
  ],
} as unknown as CachedMazeShowcaseView

vi.mock("@/api", () => ({
  cachedMazeVideoUrl: () => "/api/embodiment/showcases/trio-maze-race-v0/video",
  getCachedMazeShowcase: () => Promise.resolve(showcase),
}))

const catalogueCategories = [
  {
    id: "sandbox-primitives",
    label: "Sandbox Primitives",
    order: 10,
    description: "Godot-owned building blocks.",
  },
  {
    id: "solo-agent-tasks",
    label: "Solo Agent Tasks",
    order: 20,
    description: "Single-agent control tasks.",
  },
  {
    id: "two-agent-games",
    label: "Two-Agent Games",
    order: 30,
    description: "Paired games.",
  },
  {
    id: "multi-agent-games",
    label: "Multi-Agent Games",
    order: 40,
    description: "Games for three or more participants.",
  },
  {
    id: "strategy-worlds",
    label: "Strategy Worlds",
    order: 50,
    description: "Long-horizon strategy environments.",
  },
]

function gameFixture({
  categoryId,
  demo,
  id,
  interactionKind,
  liveLaunch,
  participants,
  readiness = "demo_replay_ready",
  title,
}: {
  categoryId: string
  demo?: boolean
  id: string
  interactionKind: string
  liveLaunch?: boolean
  participants: [number, number]
  readiness?: string
  title: string
}) {
  const primaryCategory = catalogueCategories.find(
    (category) => category.id === categoryId
  )
  const live = readiness === "live_ready"
  const admitsLiveLaunch = liveLaunch ?? live
  const admitsDemo = demo ?? !live
  return {
    id,
    title,
    readiness,
    readiness_note: live
      ? "Live provider race is available."
      : "A deterministic replay is available.",
    primary_category: primaryCategory,
    secondary_tags: ["evaluation", "planning"],
    participants: { minimum: participants[0], maximum: participants[1] },
    interaction_kind: interactionKind,
    capabilities: {
      live_launch: admitsLiveLaunch,
      demo: admitsDemo,
      replay: true,
      spectator: true,
      benchmark: admitsLiveLaunch,
      checkpoint: admitsLiveLaunch,
    },
    task_ids: [`${id}-v1`],
    capability_statements: ["Spatial reasoning"],
    agent_interface: {
      observation: "Visible passages.",
      actions: "Choose a passage.",
      memory: "Private memory.",
    },
    scoring: {
      summary: "Finish efficiently.",
      metrics: [
        {
          id: "completion",
          label: "Completion",
          description: "Reach the exit.",
        },
      ],
    },
    configuration_controls: [
      {
        id: "vision",
        label: "Vision",
        control_type: "select",
        description: "Sightline range.",
        options: ["1", "4"],
      },
    ],
    failure_modes: ["Dead ends."],
    safety: {
      public_view: "Safe replay.",
      private_agent_state: "Private memory stays private.",
    },
    supported_modes: [
      {
        id: live ? "live_provider_race" : "cached_replay",
        label: live ? "Live" : "Replay",
        description: live
          ? "Fresh authority race."
          : "Deterministic authority replay.",
      },
    ],
  }
}

const catalogueGames = [
  gameFixture({
    categoryId: "two-agent-games",
    id: "checkpoint-race",
    interactionKind: "competitive",
    participants: [2, 2],
    title: "Checkpoint Race",
  }),
  gameFixture({
    categoryId: "multi-agent-games",
    id: "labyrinth-run",
    interactionKind: "competitive",
    participants: [3, 3],
    readiness: "live_ready",
    title: "Labyrinth Run",
  }),
  gameFixture({
    categoryId: "strategy-worlds",
    id: "mini-rts",
    interactionKind: "competitive",
    participants: [3, 3],
    title: "Mini RTS",
  }),
  gameFixture({
    categoryId: "solo-agent-tasks",
    demo: true,
    id: "movement-maze",
    interactionKind: "solo",
    liveLaunch: true,
    participants: [1, 1],
    readiness: "live_ready",
    title: "Movement Maze",
  }),
]

const gamesResponse = {
  schema_version: "worldeval/lab-game-catalog/2",
  categories: catalogueCategories.map((category) => ({
    ...category,
    game_ids: catalogueGames
      .filter((game) => game.primary_category?.id === category.id)
      .map((game) => game.id),
  })),
  games: catalogueGames,
}

const sourceContractSha256 = "a".repeat(64)
const cloneContractSha256 = "b".repeat(64)
const liveContractSha256 = "d".repeat(64)

function labRunRecord({
  contractSha256,
  id,
  lifecycle,
  lineageDiff = {},
  mode = "exploratory",
  parentContractSha256 = null,
}: {
  contractSha256: string
  id: string
  lifecycle: string
  lineageDiff?: Record<string, unknown>
  mode?: "exploratory" | "sealed_benchmark"
  parentContractSha256?: string | null
}) {
  return {
    run_id: id,
    replay_available: lifecycle === "completed",
    resume_supported: false,
    video_available: false,
    contract: {
      contract_sha256: contractSha256,
      entrants: [
        { entrant_id: "entrant_0", provider: "openai" },
        { entrant_id: "entrant_1", provider: "openai" },
        { entrant_id: "entrant_2", provider: "openai" },
      ],
      game_id: "labyrinth-run",
      game_version: "trio-maze-race-v1",
      lineage_diff: lineageDiff,
      mode,
      parent_contract_sha256: parentContractSha256,
    },
    state: { status: lifecycle },
  }
}

function mazeProjection(
  runId: string,
  lifecycle: string,
  contractSha256 = cloneContractSha256
) {
  const racers = ["Sol", "Terra", "Luna"].map((display_name, index) => ({
    participant_id: `participant_${index}`,
    entrant_id: `entrant_${index}`,
    display_name,
    color: ["#fbbf24", "#fa755e", "#36c2bd"][index],
    position: [1, 1],
    path: [[1, 1]],
    visible_cells: [[1, 1]],
    provider_calls: 0,
    finished: false,
  }))
  return {
    run_id: runId,
    contract_sha256: contractSha256,
    sequence: 0,
    status: lifecycle,
    snapshot: {
      arena: {
        tick: 0,
        provider_calls: 0,
        map: {
          rows: ["###", "#.#", "###"],
          start: [1, 1],
          exit: [1, 1],
        },
        racers,
      },
    },
  }
}

function genericRunRecord({
  contractSha256 = "c".repeat(64),
  gameId,
  id,
  lifecycle,
  mode,
  models,
  parentContractSha256 = null,
}: {
  contractSha256?: string
  gameId: string
  id: string
  lifecycle: string
  mode: "demo" | "exploratory"
  models: string[]
  parentContractSha256?: string | null
}) {
  const provider = mode === "demo" ? "authority" : "openai"
  return {
    run_id: id,
    authority_available: true,
    replay_available: lifecycle === "completed",
    resume_supported: false,
    video_available: false,
    contract: {
      contract_sha256: contractSha256,
      entrants: models.map((model, index) => ({
        entrant_id: `entrant_${index}`,
        display_name:
          ["Alpha", "Bravo", "Charlie"][index] ?? `Seat ${index + 1}`,
        model_id: model,
        provider,
      })),
      game_id: gameId,
      game_version: `${gameId}-v1`,
      lineage_diff: {},
      mode,
      parent_contract_sha256: parentContractSha256,
    },
    state: { status: lifecycle },
  }
}

function genericProjection({
  contractSha256 = "c".repeat(64),
  gameId,
  lifecycle,
  mode = "demo",
  models,
  runId,
  sequence = 4,
}: {
  contractSha256?: string
  gameId: string
  lifecycle: string
  mode?: "demo" | "exploratory"
  models: string[]
  runId: string
  sequence?: number
}) {
  const provider = mode === "demo" ? "authority" : "openai"
  return {
    run_id: runId,
    contract_sha256: contractSha256,
    status: lifecycle,
    sequence,
    snapshot: {
      game: {
        game_id: gameId,
        game_version: `${gameId}-v1`,
        mode,
        scenario_id: null,
      },
      entrants: models.map((model, index) => ({
        entrant_id: `entrant_${index}`,
        display_name:
          ["Alpha", "Bravo", "Charlie"][index] ?? `Seat ${index + 1}`,
        model_id: model,
        provider,
      })),
      configuration: { seed: 7 },
      authority: {
        state: lifecycle,
        progress: {
          authority_tick: 12,
          decision_sequence: sequence,
        },
        replay_state: lifecycle === "completed" ? "ready" : "pending",
      },
    },
    events: [],
  }
}

function sandboxManifestFixture() {
  const primitives = Array.from({ length: 10 }, (_, index) => ({
    schema_version: "worldeval/sandbox-primitive/1",
    id: `primitive-${index + 1}`,
    title: `Primitive ${index + 1}`,
    summary: `Reusable authority capability ${index + 1}.`,
    composition_rank: index,
    dependencies: index === 0 ? [] : [`primitive-${index}`],
    capabilities: [`capability-${index + 1}`],
    spec_sha256: String(index + 1)
      .repeat(64)
      .slice(0, 64),
    authority_owner: "godot",
  }))
  const primitiveIds = primitives.map((primitive) => primitive.id)
  return {
    schema_version: "worldeval/sandbox-manifest/1",
    authority_owner: "godot",
    manifest_sha256: "f".repeat(64),
    primitives,
    recipes: [
      {
        schema_version: "worldeval/sandbox-recipe/1",
        id: "operator-action-course-sandbox-v1",
        title: "Operator Action Course",
        summary: "A canonical executable composition of all ten primitives.",
        lifecycle: "canonical",
        executable: true,
        primitive_ids: primitiveIds,
        composition_order: primitiveIds,
        authority_binding: {
          authority_owner: "godot",
          task_id: "operator-action-course-v0",
          protocol_version: "llm-controller/0.2.0",
        },
        recipe_sha256: "e".repeat(64),
      },
    ],
  }
}

function catalogueFetch(catalogue: unknown = gamesResponse) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url === "/api/auth/me") return new Response(null, { status: 401 })
    if (url === "/api/auth/configuration") {
      return new Response(JSON.stringify({ mode: "local" }), { status: 200 })
    }
    if (url === "/api/lab/games") {
      return new Response(JSON.stringify(catalogue), { status: 200 })
    }
    if (url === "/api/lab/runs") {
      return new Response(JSON.stringify({ runs: [] }), { status: 200 })
    }
    if (url === "/api/lab/benchmarks/labyrinth-run") {
      return new Response(
        JSON.stringify({
          game_id: "labyrinth-run",
          season_state: "not_run",
          verified_results: [],
        }),
        { status: 200 }
      )
    }
    throw new Error(`Unexpected fetch ${url}`)
  })
}

function connectedFetch({
  benchmark = {
    game_id: "labyrinth-run",
    season_state: "not_run",
    verified_results: [],
  },
  catalogue = gamesResponse,
  onRequest,
  runs = [],
  sandbox = sandboxManifestFixture(),
}: {
  benchmark?: unknown
  catalogue?: unknown
  onRequest?: (
    url: string,
    init: RequestInit | undefined
  ) => Promise<Response | undefined> | Response | undefined
  runs?: unknown[]
  sandbox?: unknown
} = {}) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url === "/api/auth/me") {
      return new Response(
        JSON.stringify({
          operator: { member_id: "local", email: "local@worldeval.test" },
          csrf_token: "csrf",
        }),
        { status: 200 }
      )
    }
    if (url === "/api/auth/configuration") {
      return new Response(JSON.stringify({ mode: "local" }), { status: 200 })
    }
    if (url === "/api/lab/games") {
      return new Response(JSON.stringify(catalogue), { status: 200 })
    }
    if (url === "/api/lab/runs") {
      return new Response(JSON.stringify({ runs }), { status: 200 })
    }
    if (url === "/api/lab/sandbox") {
      return new Response(JSON.stringify(sandbox), { status: 200 })
    }
    if (url === "/api/lab/benchmarks/labyrinth-run") {
      return new Response(JSON.stringify(benchmark), { status: 200 })
    }
    const response = await onRequest?.(url, init)
    if (response) return response
    throw new Error(`Unexpected fetch ${url}`)
  })
}

describe("LabApp", () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    window.history.replaceState(null, "", "/")
  })

  it("rejects mismatched run identities and non-Godot sandbox bindings", async () => {
    const mismatchedRun = genericRunRecord({
      gameId: "checkpoint-race",
      id: "run_game_wrong",
      lifecycle: "completed",
      mode: "demo",
      models: ["authority-alpha", "authority-bravo"],
    })
    const mismatchedProjection = genericProjection({
      gameId: "checkpoint-race",
      lifecycle: "completed",
      models: ["authority-alpha", "authority-bravo"],
      runId: "run_game_wrong",
    })
    const untrustedSandbox = sandboxManifestFixture()
    untrustedSandbox.recipes[0].authority_binding.authority_owner = "browser"
    const unknownLifecycle = genericRunRecord({
      gameId: "checkpoint-race",
      id: "run_game_unknown_lifecycle",
      lifecycle: "resuming",
      mode: "demo",
      models: ["authority-alpha", "authority-bravo"],
    })
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === "/api/lab/runs/run_game_expected") {
          return new Response(JSON.stringify(mismatchedRun), { status: 200 })
        }
        if (url === "/api/lab/runs/run_game_expected/projection") {
          return new Response(JSON.stringify(mismatchedProjection), {
            status: 200,
          })
        }
        if (url === "/api/lab/sandbox") {
          return new Response(JSON.stringify(untrustedSandbox), { status: 200 })
        }
        if (url === "/api/lab/runs/run_game_unknown_lifecycle") {
          return new Response(JSON.stringify(unknownLifecycle), { status: 200 })
        }
        throw new Error(`Unexpected fetch ${url}`)
      })
    )

    await expect(getLabRun("run_game_expected")).rejects.toThrow(
      "Lab run payload is invalid"
    )
    await expect(getLabRunProjection("run_game_expected")).rejects.toThrow(
      "Lab replay projection is invalid"
    )
    await expect(getLabSandboxManifest()).rejects.toThrow(
      "Lab sandbox manifest is invalid"
    )
    await expect(getLabRun("run_game_unknown_lifecycle")).rejects.toThrow(
      "Lab run payload is invalid"
    )
  })

  it("accepts only the exact safe public replay projection", async () => {
    const projection = genericProjection({
      gameId: "checkpoint-race",
      lifecycle: "completed",
      models: ["authority-alpha", "authority-bravo"],
      runId: "private-run-id-is-not-published",
    })
    const publication = {
      cartridge_sha256: "a".repeat(64),
      contract_sha256: "b".repeat(64),
      game_id: "checkpoint-race",
      game_version: "checkpoint-race-v1",
      lifecycle_status: "verified",
      method_label: "explicit_unlisted_link",
      projection_sha256: "c".repeat(64),
      publication_sha256: "d".repeat(64),
      publication_slug: `pub_${"A".repeat(32)}`,
      published_at_epoch_ms: 1_784_800_000_000,
      replay: {
        events: projection.events,
        sequence: projection.sequence,
        snapshot: projection.snapshot,
      },
      result_sha256: "e".repeat(64),
      schema_version: "worldeval/public-replay-publication/1",
      state_sha256: "f".repeat(64),
    }
    let tainted = false
    let rootTainted = false
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        const value = tainted
          ? { ...publication, raw_response: "private model material" }
          : publication
        const root = {
          game_id: "checkpoint-race",
          publications: [value],
          schema_version: "worldeval/public-game-replays/1",
        }
        return new Response(
          JSON.stringify(
            rootTainted ? { ...root, api_key: "must-not-pass" } : root
          ),
          { status: 200 }
        )
      })
    )

    await expect(getPublicGameReplays("checkpoint-race")).resolves.toEqual([
      expect.objectContaining({
        eventCount: 0,
        gameId: "checkpoint-race",
        lifecycle: "verified",
        publicationSlug: `pub_${"A".repeat(32)}`,
        sequence: 4,
      }),
    ])

    tainted = true
    await expect(getPublicGameReplays("checkpoint-race")).rejects.toThrow(
      "public replay list payload is invalid"
    )

    tainted = false
    rootTainted = true
    await expect(getPublicGameReplays("checkpoint-race")).rejects.toThrow(
      "public replay list payload is invalid"
    )
  })

  it("rejects public replays whose inner authority differs from the receipt", async () => {
    const checkpointProjection = genericProjection({
      gameId: "checkpoint-race",
      lifecycle: "completed",
      models: ["authority-alpha", "authority-bravo"],
      runId: "private-run-id",
    })
    const movementProjection = genericProjection({
      gameId: "movement-maze",
      lifecycle: "completed",
      models: ["authority-alpha"],
      runId: "private-run-id",
    })
    const maze = mazeProjection("private-run-id", "completed", "b".repeat(64))
    let outerGameId = "checkpoint-race"
    let outerGameVersion = "checkpoint-race-v1"
    let innerReplay: unknown = {
      events: movementProjection.events,
      sequence: movementProjection.sequence,
      snapshot: movementProjection.snapshot,
    }
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        const publication = {
          cartridge_sha256: "a".repeat(64),
          contract_sha256: "b".repeat(64),
          game_id: outerGameId,
          game_version: outerGameVersion,
          lifecycle_status: "verified",
          method_label: "explicit_unlisted_link",
          projection_sha256: "c".repeat(64),
          publication_sha256: "d".repeat(64),
          publication_slug: `pub_${"D".repeat(32)}`,
          published_at_epoch_ms: 1_784_800_000_000,
          replay: innerReplay,
          result_sha256: "e".repeat(64),
          schema_version: "worldeval/public-replay-publication/1",
          state_sha256: "f".repeat(64),
        }
        return new Response(
          JSON.stringify({
            game_id: outerGameId,
            publications: [publication],
            schema_version: "worldeval/public-game-replays/1",
          }),
          { status: 200 }
        )
      })
    )

    await expect(getPublicGameReplays("checkpoint-race")).rejects.toThrow(
      "public replay list payload is invalid"
    )

    innerReplay = {
      events: [],
      sequence: maze.sequence,
      snapshot: maze.snapshot,
    }
    await expect(getPublicGameReplays("checkpoint-race")).rejects.toThrow(
      "public replay list payload is invalid"
    )

    outerGameId = "labyrinth-run"
    outerGameVersion = "trio-maze-race-v1"
    innerReplay = {
      events: checkpointProjection.events,
      sequence: checkpointProjection.sequence,
      snapshot: checkpointProjection.snapshot,
    }
    await expect(getPublicGameReplays("labyrinth-run")).rejects.toThrow(
      "public replay list payload is invalid"
    )
  })

  it("keeps the selected public replay in the share URL", async () => {
    const game = gameFixture({
      categoryId: "two-agent-games",
      id: "checkpoint-race",
      interactionKind: "paired",
      participants: [2, 2],
      title: "Checkpoint Race",
    })
    const projection = genericProjection({
      gameId: "checkpoint-race",
      lifecycle: "completed",
      models: ["authority-alpha", "authority-bravo"],
      runId: "private-run-id",
    })
    const firstSlug = `pub_${"E".repeat(32)}`
    const secondSlug = `pub_${"F".repeat(32)}`
    const publication = (slug: string, digest: string) => ({
      cartridge_sha256: digest.repeat(64),
      contract_sha256: "a".repeat(64),
      game_id: "checkpoint-race",
      game_version: "checkpoint-race-v1",
      lifecycle_status: "verified",
      method_label: "explicit_unlisted_link",
      projection_sha256: "b".repeat(64),
      publication_sha256: "c".repeat(64),
      publication_slug: slug,
      published_at_epoch_ms: 1_784_800_000_000,
      replay: {
        events: projection.events,
        sequence: projection.sequence,
        snapshot: projection.snapshot,
      },
      result_sha256: "d".repeat(64),
      schema_version: "worldeval/public-replay-publication/1",
      state_sha256: "e".repeat(64),
    })
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === "/api/public/games/checkpoint-race") {
          return new Response(JSON.stringify(game), { status: 200 })
        }
        if (url === "/api/public/games/checkpoint-race/benchmark") {
          return new Response(
            JSON.stringify({
              game_id: "checkpoint-race",
              season_state: "not_run",
              verified_results: [],
            }),
            { status: 200 }
          )
        }
        if (url === "/api/public/games/checkpoint-race/replays") {
          return new Response(
            JSON.stringify({
              game_id: "checkpoint-race",
              publications: [
                publication(firstSlug, "1"),
                publication(secondSlug, "2"),
              ],
              schema_version: "worldeval/public-game-replays/1",
            }),
            { status: 200 }
          )
        }
        throw new Error(`Unexpected fetch ${url}`)
      })
    )
    window.history.replaceState(
      null,
      "",
      `/share/games/checkpoint-race?replay=${firstSlug}`
    )
    const user = userEvent.setup()
    render(<PublicLabApp gameId="checkpoint-race" />)

    const secondReplay = await screen.findByRole("button", {
      name: /Replay 2/,
    })
    await user.click(secondReplay)

    expect(new URLSearchParams(window.location.search).get("replay")).toBe(
      secondSlug
    )
    expect(secondReplay).toHaveAttribute("aria-pressed", "true")
  })

  it("binds public guides and benchmarks to the requested game route", async () => {
    const labyrinth = gameFixture({
      categoryId: "multi-agent-games",
      id: "labyrinth-run",
      interactionKind: "multi",
      participants: [3, 3],
      readiness: "live_ready",
      title: "Labyrinth Run",
    })
    let benchmarkHasGameId = true
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === "/api/public/games/checkpoint-race") {
          return new Response(JSON.stringify(labyrinth), { status: 200 })
        }
        if (url === "/api/public/games/checkpoint-race/benchmark") {
          return new Response(
            JSON.stringify({
              ...(benchmarkHasGameId ? { game_id: "labyrinth-run" } : {}),
              season_state: "not_run",
              verified_results: [],
            }),
            { status: 200 }
          )
        }
        throw new Error(`Unexpected fetch ${url}`)
      })
    )

    await expect(getPublicLabGame("checkpoint-race")).rejects.toThrow(
      "public game payload is invalid"
    )
    await expect(getPublicGameBenchmark("checkpoint-race")).rejects.toThrow(
      "benchmark payload is not bound to the requested game"
    )
    benchmarkHasGameId = false
    await expect(getPublicGameBenchmark("checkpoint-race")).rejects.toThrow(
      "benchmark payload is not bound to the requested game"
    )
  })

  it("binds a published replay to the selected run contract", async () => {
    const projection = genericProjection({
      gameId: "checkpoint-race",
      lifecycle: "verified",
      models: ["authority-alpha", "authority-bravo"],
      runId: "private-run-id-is-not-published",
    })
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        return new Response(
          JSON.stringify({
            cartridge_sha256: "1".repeat(64),
            contract_sha256: "2".repeat(64),
            game_id: "checkpoint-race",
            game_version: "checkpoint-race-v1",
            lifecycle_status: "verified",
            method_label: "explicit_unlisted_link",
            projection_sha256: "3".repeat(64),
            publication_sha256: "4".repeat(64),
            publication_slug: `pub_${"C".repeat(32)}`,
            published_at_epoch_ms: 1_784_800_000_000,
            replay: {
              events: projection.events,
              sequence: projection.sequence,
              snapshot: projection.snapshot,
            },
            result_sha256: "5".repeat(64),
            schema_version: "worldeval/public-replay-publication/1",
            state_sha256: "6".repeat(64),
          }),
          { status: 201 }
        )
      })
    )

    await expect(
      publishLabRun("run_expected", "checkpoint-race", "7".repeat(64), "csrf")
    ).rejects.toThrow("Lab replay publication payload is invalid")
  })

  it("rejects a malformed Labyrinth launch receipt", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response(JSON.stringify({}), { status: 202 })
    })
    vi.stubGlobal("fetch", fetchMock)

    await expect(
      launchLabyrinth(
        {
          apiKey: "session-key",
          provider: "openai",
          models: {
            sol: "gpt-5.6-sol",
            terra: "gpt-5.6-terra",
            luna: "gpt-5.6-luna",
          },
          visionRangeCells: "4",
          skillMode: "none",
          mode: "exploratory",
        },
        "csrf"
      )
    ).rejects.toThrow("Labyrinth launch response is invalid")
  })

  it("rejects a generic launch receipt in an impossible lifecycle", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        return new Response(
          JSON.stringify(
            genericRunRecord({
              gameId: "movement-maze",
              id: "run_game_impossible_draft",
              lifecycle: "draft",
              mode: "demo",
              models: ["authority-alpha"],
            })
          ),
          { status: 202 }
        )
      })
    )

    await expect(
      launchGenericGame(
        {
          gameId: "movement-maze",
          mode: "demo",
          seed: 7,
          apiKey: "",
          models: ["authority-alpha"],
        },
        "csrf"
      )
    ).rejects.toThrow("Lab game launch response is invalid")
  })

  it("binds evidence mutation receipts to their requested run and lifecycle", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith("/seal")) {
          return new Response(
            JSON.stringify(
              labRunRecord({
                contractSha256: "7".repeat(64),
                id: "run_expected",
                lifecycle: "completed",
              })
            ),
            { status: 200 }
          )
        }
        return new Response(
          JSON.stringify({
            authority_result_sha256: "1".repeat(64),
            contract_sha256: "2".repeat(64),
            participants: [
              {
                entrant_id: "entrant_0",
                metrics: {
                  budget_charged_calls: 1,
                  completion_basis_points: 10_000,
                  input_tokens: 1,
                  invalid_action_rate_basis_points: 0,
                  latency_ms: 1,
                  output_tokens: 1,
                  path_efficiency_basis_points: 10_000,
                  recovery_rate_basis_points: 0,
                },
                model_id: "gpt-5.6-sol",
                provider: "openai",
              },
            ],
            recipe_id: "labyrinth-interactive-v1",
            recipe_sha256: "3".repeat(64),
            replay_projection_sha256: "4".repeat(64),
            run_id: "run_expected",
            schema_version: "worldeval/lab-verified-benchmark-result/1",
            verified_result_sha256: "5".repeat(64),
          }),
          { status: 201 }
        )
      })
    )

    await expect(
      sealLabRun("run_expected", "labyrinth-run", "7".repeat(64), "csrf")
    ).rejects.toThrow("Lab run mutation lifecycle is invalid")
    await expect(
      submitLabRunToBenchmark("run_expected", "6".repeat(64), "csrf")
    ).rejects.toThrow("Lab benchmark admission payload is invalid")
  })

  it("rejects a lifecycle mutation for a different contract", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        return new Response(
          JSON.stringify(
            labRunRecord({
              contractSha256: "8".repeat(64),
              id: "run_expected",
              lifecycle: "sealed",
            })
          ),
          { status: 200 }
        )
      })
    )

    await expect(
      sealLabRun("run_expected", "labyrinth-run", "7".repeat(64), "csrf")
    ).rejects.toThrow("Lab run mutation payload is invalid")
  })

  it("binds clone creation and launch to the frozen source lineage", async () => {
    const source = labRunRecord({
      contractSha256: "1".repeat(64),
      id: "run_source_binding",
      lifecycle: "completed",
    })
    const wrongParentDraft = labRunRecord({
      contractSha256: "2".repeat(64),
      id: "run_clone_wrong_parent",
      lifecycle: "draft",
      parentContractSha256: "3".repeat(64),
    })
    const frozenDraft = labRunRecord({
      contractSha256: "4".repeat(64),
      id: "run_clone_frozen",
      lifecycle: "draft",
      parentContractSha256: "1".repeat(64),
    })
    const changedLaunch = labRunRecord({
      contractSha256: "5".repeat(64),
      id: "run_clone_frozen",
      lifecycle: "queued",
      parentContractSha256: "1".repeat(64),
    })
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === "/api/lab/runs/run_source_binding") {
          return new Response(JSON.stringify(source), { status: 200 })
        }
        if (url === "/api/lab/runs/run_source_binding/clone") {
          return new Response(JSON.stringify(wrongParentDraft), {
            status: 201,
          })
        }
        if (url === "/api/lab/runs/run_clone_frozen") {
          return new Response(JSON.stringify(frozenDraft), { status: 200 })
        }
        if (url === "/api/lab/runs/run_clone_frozen/launch") {
          return new Response(JSON.stringify(changedLaunch), { status: 202 })
        }
        throw new Error(`Unexpected fetch ${url}`)
      })
    )

    const parsedSource = await getLabRun("run_source_binding")
    await expect(cloneLabRun(parsedSource, "csrf")).rejects.toThrow(
      "Lab clone draft payload is invalid"
    )
    const parsedDraft = await getLabRun("run_clone_frozen")
    await expect(
      launchLabRunDraft(parsedDraft, "session-key", "csrf")
    ).rejects.toThrow("Lab draft launch payload is invalid")
  })

  it("groups every game category, keeps the empty sandbox visible, and selects across groups", async () => {
    vi.stubGlobal("fetch", catalogueFetch())
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Games" }))

    const picker = screen.getByRole("combobox", { name: "Game catalogue" })
    const sandbox = screen.getByRole("group", {
      name: "Sandbox Primitives · 0 games",
    })
    const solo = screen.getByRole("group", {
      name: "Solo Agent Tasks · 1 game",
    })
    const duo = screen.getByRole("group", {
      name: "Two-Agent Games · 1 game",
    })
    const multi = screen.getByRole("group", {
      name: "Multi-Agent Games · 1 game",
    })
    const strategy = screen.getByRole("group", {
      name: "Strategy Worlds · 1 game",
    })

    expect(
      within(sandbox).getByRole("option", { name: "No admitted games yet" })
    ).toBeDisabled()
    expect(
      within(solo).getByRole("option", { name: /Movement Maze.*1 agent/ })
    ).toBeInTheDocument()
    expect(
      within(duo).getByRole("option", { name: /Checkpoint Race.*2 agents/ })
    ).toBeInTheDocument()
    expect(
      within(multi).getByRole("option", { name: /Labyrinth Run.*3 agents/ })
    ).toBeInTheDocument()
    expect(
      within(strategy).getByRole("option", { name: /Mini RTS.*3 agents/ })
    ).toBeInTheDocument()

    await user.selectOptions(picker, "movement-maze")

    expect(
      (await screen.findAllByRole("heading", { name: "Movement Maze" })).length
    ).toBeGreaterThan(0)
    expect(screen.getByText("1 agent")).toBeInTheDocument()
    expect(screen.getByText("solo")).toBeInTheDocument()
    expect(
      screen.getByText(
        "Live · Demo · Replay · Spectator · Benchmark · Checkpoint"
      )
    ).toBeInTheDocument()
  })

  it("renders verified recipe leaderboards from the evidence-store projection", async () => {
    vi.stubGlobal(
      "fetch",
      connectedFetch({
        benchmark: {
          game_id: "labyrinth-run",
          season_state: "results_available",
          recipes: [{ recipe: { recipe_id: "labyrinth-interactive-v1" } }],
          leaderboards: [
            {
              rows: [
                {
                  model_id: "gpt-5.6-sol",
                  provider: "openai",
                  sample_count: 1,
                  metrics: {
                    budget_charged_calls: 100,
                    completion_basis_points: 9000,
                    path_efficiency_basis_points: 8125,
                  },
                },
              ],
            },
          ],
        },
      })
    )
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Benchmarks" }))

    expect(
      screen.getByRole("heading", { name: "Labyrinth benchmark" })
    ).toBeInTheDocument()
    expect(screen.getByText("gpt-5.6-sol")).toBeInTheDocument()
    expect(screen.getByText("90%")).toBeInTheDocument()
    expect(screen.getByText("81.25%")).toBeInTheDocument()
    expect(screen.getByText("1 verified sample")).toBeInTheDocument()
  })

  it("advances a benchmark cartridge through seal, verify, publish, and admission", async () => {
    const id = "run_benchmark_candidate"
    const contractSha256 = "9".repeat(64)
    const completed = labRunRecord({
      contractSha256,
      id,
      lifecycle: "completed",
      mode: "sealed_benchmark",
    })
    const sealed = labRunRecord({
      contractSha256,
      id,
      lifecycle: "sealed",
      mode: "sealed_benchmark",
    })
    const verified = labRunRecord({
      contractSha256,
      id,
      lifecycle: "verified",
      mode: "sealed_benchmark",
    })
    const fetchMock = connectedFetch({
      runs: [completed],
      onRequest: (url, init) => {
        if (url === `/api/lab/runs/${id}/seal` && init?.method === "POST") {
          expect(init.headers).toMatchObject({ "X-WorldEval-CSRF": "csrf" })
          return new Response(JSON.stringify(sealed), { status: 200 })
        }
        if (url === `/api/lab/runs/${id}/verify` && init?.method === "POST") {
          expect(init.headers).toMatchObject({ "X-WorldEval-CSRF": "csrf" })
          return new Response(JSON.stringify(verified), { status: 200 })
        }
        if (url === `/api/lab/runs/${id}/publish` && init?.method === "POST") {
          expect(init.headers).toMatchObject({ "X-WorldEval-CSRF": "csrf" })
          const projection = mazeProjection(id, "verified", contractSha256)
          return new Response(
            JSON.stringify({
              cartridge_sha256: "5".repeat(64),
              contract_sha256: contractSha256,
              game_id: "labyrinth-run",
              game_version: "trio-maze-race-v1",
              lifecycle_status: "verified",
              method_label: "explicit_unlisted_link",
              projection_sha256: "6".repeat(64),
              publication_sha256: "8".repeat(64),
              publication_slug: `pub_${"B".repeat(32)}`,
              published_at_epoch_ms: 1_784_800_000_000,
              replay: {
                events: [],
                sequence: projection.sequence,
                snapshot: projection.snapshot,
              },
              result_sha256: "7".repeat(64),
              schema_version: "worldeval/public-replay-publication/1",
              state_sha256: "9".repeat(64),
            }),
            { status: 201 }
          )
        }
        if (
          url === `/api/lab/runs/${id}/benchmark` &&
          init?.method === "POST"
        ) {
          expect(init.headers).toMatchObject({ "X-WorldEval-CSRF": "csrf" })
          expect(JSON.parse(String(init.body))).toEqual({
            recipe_id: "labyrinth-interactive-v1",
          })
          return new Response(
            JSON.stringify({
              authority_result_sha256: "1".repeat(64),
              contract_sha256: contractSha256,
              participants: ["sol", "terra", "luna"].map((model, index) => ({
                entrant_id: `entrant_${index}`,
                metrics: {
                  budget_charged_calls: 10 + index,
                  completion_basis_points: 10_000,
                  input_tokens: 100,
                  invalid_action_rate_basis_points: 0,
                  latency_ms: 250,
                  output_tokens: 20,
                  path_efficiency_basis_points: 8_500,
                  recovery_rate_basis_points: 100,
                },
                model_id: `gpt-5.6-${model}`,
                provider: "openai",
              })),
              recipe_id: "labyrinth-interactive-v1",
              recipe_sha256: "2".repeat(64),
              replay_projection_sha256: "3".repeat(64),
              run_id: id,
              schema_version: "worldeval/lab-verified-benchmark-result/1",
              verified_result_sha256: "4".repeat(64),
            }),
            { status: 201 }
          )
        }
        return undefined
      },
    })
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Runs" }))
    await user.click(
      screen.getByRole("button", {
        name: `Seal completed cartridge for ${id}`,
      })
    )
    await screen.findByText("Run cartridge sealed.")
    await user.click(
      screen.getByRole("button", {
        name: `Verify durable evidence for ${id}`,
      })
    )
    await screen.findByText("Durable run evidence verified.")
    await user.click(
      screen.getByRole("button", {
        name: `Publish safe replay for ${id}`,
      })
    )

    expect(
      await screen.findByRole("link", { name: "Open unlisted replay" })
    ).toHaveAttribute(
      "href",
      `/share/games/labyrinth-run?replay=pub_${"B".repeat(32)}`
    )

    await user.click(
      screen.getByRole("button", {
        name: `Submit verified benchmark evidence for ${id}`,
      })
    )
    await screen.findByText(
      "Verified evidence admitted to the Labyrinth leaderboard."
    )
  })

  it("locks and submits the frozen Labyrinth benchmark recipe", async () => {
    const fetchMock = connectedFetch({
      onRequest: (url, init) => {
        if (url === "/api/lab/runs/labyrinth" && init?.method === "POST") {
          expect(JSON.parse(String(init.body))).toMatchObject({
            mode: "sealed_benchmark",
            skill_mode: "none",
            vision_range_cells: 4,
          })
          return new Response(
            JSON.stringify(
              labRunRecord({
                contractSha256: "8".repeat(64),
                id: "run_labyrinth_benchmark",
                lifecycle: "queued",
                mode: "sealed_benchmark",
              })
            ),
            { status: 202 }
          )
        }
        return undefined
      },
    })
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.selectOptions(
      await screen.findByLabelText("Experiment intent"),
      "sealed_benchmark"
    )
    expect(screen.getByLabelText("Vision depth")).toBeDisabled()
    expect(
      screen.getByRole("checkbox", { name: "Use maze navigation skill" })
    ).toBeDisabled()
    await user.type(
      screen.getByLabelText("OpenAI API key"),
      "benchmark-session-key"
    )
    await user.click(
      screen.getByRole("checkbox", {
        name: "I reviewed this sealed benchmark candidate contract.",
      })
    )
    await user.click(screen.getByRole("button", { name: "Launch experiment" }))

    await screen.findByText("Run run_labyrinth_benchmark was accepted.")
    expect(screen.getByLabelText("OpenAI API key")).toHaveValue("")
  })

  it("cancels an in-flight saved run only after an authority receipt", async () => {
    const running = genericRunRecord({
      gameId: "checkpoint-race",
      id: "run_game_cancel_me",
      lifecycle: "running",
      mode: "demo",
      models: ["authority-alpha", "authority-bravo"],
    })
    const cancelled = genericRunRecord({
      gameId: "checkpoint-race",
      id: "run_game_cancel_me",
      lifecycle: "cancelled",
      mode: "demo",
      models: ["authority-alpha", "authority-bravo"],
    })
    const fetchMock = connectedFetch({
      runs: [running],
      onRequest: (url, init) => {
        if (
          url === "/api/lab/runs/run_game_cancel_me/cancel" &&
          init?.method === "POST"
        ) {
          expect(init.headers).toMatchObject({ "X-WorldEval-CSRF": "csrf" })
          return new Response(JSON.stringify(cancelled), { status: 200 })
        }
        return undefined
      },
    })
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Runs" }))
    await user.click(
      screen.getByRole("button", {
        name: "Cancel active run run_game_cancel_me",
      })
    )

    await screen.findByText("Run cancelled by the authority.")
    expect(screen.getByText("cancelled")).toBeInTheDocument()
  })

  it("keeps an authoritative cancellation when projection refresh is offline", async () => {
    const id = "run_game_cancel_projection_offline"
    const models = ["authority-alpha", "authority-bravo"]
    const running = genericRunRecord({
      gameId: "checkpoint-race",
      id,
      lifecycle: "running",
      mode: "demo",
      models,
    })
    const cancelled = genericRunRecord({
      gameId: "checkpoint-race",
      id,
      lifecycle: "cancelled",
      mode: "demo",
      models,
    })
    let projectionReads = 0
    const fetchMock = connectedFetch({
      runs: [running],
      onRequest: (url, init) => {
        if (url === `/api/lab/runs/${id}`) {
          return new Response(JSON.stringify(running), { status: 200 })
        }
        if (url === `/api/lab/runs/${id}/projection`) {
          projectionReads += 1
          return projectionReads === 1
            ? new Response(
                JSON.stringify(
                  genericProjection({
                    gameId: "checkpoint-race",
                    lifecycle: "running",
                    models,
                    runId: id,
                  })
                ),
                { status: 200 }
              )
            : new Response(null, { status: 503 })
        }
        if (url === `/api/lab/runs/${id}/cancel` && init?.method === "POST") {
          return new Response(JSON.stringify(cancelled), { status: 200 })
        }
        return undefined
      },
    })
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Runs" }))
    await user.click(screen.getByRole("button", { name: "Open arena" }))
    await screen.findByRole("region", {
      name: "Checkpoint Race authority spectator",
    })
    await user.click(screen.getByRole("button", { name: "Runs" }))
    await user.click(
      screen.getByRole("button", { name: `Cancel active run ${id}` })
    )

    await screen.findByText("Run cancelled by the authority.")
    expect(screen.getByText("cancelled")).toBeInTheDocument()
    expect(projectionReads).toBe(2)
  })

  it("marks an unavailable generic authority interrupted and removes cancellation", async () => {
    const id = "run_game_interrupted"
    const interrupted = {
      ...genericRunRecord({
        gameId: "checkpoint-race",
        id,
        lifecycle: "running",
        mode: "demo",
        models: ["authority-alpha", "authority-bravo"],
      }),
      authority_available: false,
    }
    const fetchMock = connectedFetch({
      runs: [interrupted],
      onRequest: (url) => {
        if (url === `/api/lab/runs/${id}`) {
          return new Response(JSON.stringify(interrupted), { status: 200 })
        }
        if (url === `/api/lab/runs/${id}/projection`) {
          return new Response(
            JSON.stringify(
              genericProjection({
                gameId: "checkpoint-race",
                lifecycle: "running",
                models: ["authority-alpha", "authority-bravo"],
                runId: id,
              })
            ),
            { status: 200 }
          )
        }
        return undefined
      },
    })
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Runs" }))
    expect(screen.getByText("interrupted")).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: `Cancel active run ${id}` })
    ).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Inspect run" }))
    const stage = await screen.findByRole("region", {
      name: "Checkpoint Race authority spectator",
    })
    expect(within(stage).getByText("interrupted")).toBeInTheDocument()
    expect(
      within(stage).getByRole("button", { name: `Cancel active run ${id}` })
    ).toBeDisabled()
  })

  it("keeps a successful seal when projection refresh is offline", async () => {
    const id = "run_game_seal_projection_offline"
    const models = ["authority-alpha", "authority-bravo"]
    const completed = genericRunRecord({
      gameId: "checkpoint-race",
      id,
      lifecycle: "completed",
      mode: "demo",
      models,
    })
    const sealed = genericRunRecord({
      gameId: "checkpoint-race",
      id,
      lifecycle: "sealed",
      mode: "demo",
      models,
    })
    let projectionReads = 0
    const fetchMock = connectedFetch({
      runs: [completed],
      onRequest: (url, init) => {
        if (url === `/api/lab/runs/${id}`) {
          return new Response(JSON.stringify(completed), { status: 200 })
        }
        if (url === `/api/lab/runs/${id}/projection`) {
          projectionReads += 1
          return projectionReads === 1
            ? new Response(
                JSON.stringify(
                  genericProjection({
                    gameId: "checkpoint-race",
                    lifecycle: "completed",
                    models,
                    runId: id,
                  })
                ),
                { status: 200 }
              )
            : new Response(null, { status: 503 })
        }
        if (url === `/api/lab/runs/${id}/seal` && init?.method === "POST") {
          return new Response(JSON.stringify(sealed), { status: 200 })
        }
        return undefined
      },
    })
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Runs" }))
    await user.click(screen.getByRole("button", { name: "Open arena" }))
    await screen.findByRole("region", {
      name: "Checkpoint Race authority spectator",
    })
    await user.click(screen.getByRole("button", { name: "Runs" }))
    await user.click(
      screen.getByRole("button", {
        name: `Seal completed cartridge for ${id}`,
      })
    )

    await screen.findByText("Run cartridge sealed.")
    expect(screen.getByText("sealed")).toBeInTheDocument()
    expect(projectionReads).toBe(2)
  })

  it("continues polling a checkpointed run until the authority completes", async () => {
    const runId = "run_game_checkpoint_resume"
    const models = ["authority-alpha", "authority-bravo"]
    const lifecycles = ["checkpointed", "running", "completed"]
    let runReadCount = 0
    let currentLifecycle = lifecycles[0]
    const servedLifecycles: string[] = []
    const fetchMock = connectedFetch({
      runs: [
        genericRunRecord({
          gameId: "checkpoint-race",
          id: runId,
          lifecycle: "checkpointed",
          mode: "demo",
          models,
        }),
      ],
      onRequest: (url) => {
        if (url === `/api/lab/runs/${runId}`) {
          currentLifecycle =
            lifecycles[Math.min(runReadCount, lifecycles.length - 1)]
          servedLifecycles.push(currentLifecycle)
          runReadCount += 1
          return new Response(
            JSON.stringify(
              genericRunRecord({
                gameId: "checkpoint-race",
                id: runId,
                lifecycle: currentLifecycle,
                mode: "demo",
                models,
              })
            ),
            { status: 200 }
          )
        }
        if (url === `/api/lab/runs/${runId}/projection`) {
          return new Response(
            JSON.stringify(
              genericProjection({
                gameId: "checkpoint-race",
                lifecycle: currentLifecycle,
                models,
                runId,
                sequence: runReadCount,
              })
            ),
            { status: 200 }
          )
        }
        return undefined
      },
    })
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Runs" }))
    await user.click(screen.getByRole("button", { name: "Open arena" }))

    const stage = await screen.findByRole(
      "region",
      {
        name: "Checkpoint Race authority spectator",
      },
      { timeout: 2_500 }
    )
    await waitFor(
      () => expect(within(stage).getAllByText("completed")).toHaveLength(2),
      { timeout: 2_500 }
    )
    expect(servedLifecycles).toEqual(["checkpointed", "running", "completed"])
    expect(runReadCount).toBe(3)
  })

  it("fails malformed passport metadata closed while retaining a safe game guide", async () => {
    const malformedGame = {
      ...gameFixture({
        categoryId: "solo-agent-tasks",
        id: "malformed-course",
        interactionKind: "solo",
        participants: [1, 1],
        title: "Malformed Course",
      }),
      readiness: "definitely-live",
      primary_category: {
        id: "unknown<script>",
        label: "<script>alert(1)</script>",
        order: -1,
        description: "unsafe",
      },
      secondary_tags: ["safe-tag", "<script>", "", 42],
      participants: { minimum: 0, maximum: "everybody" },
      interaction_kind: "telepathic",
      capabilities: {
        live_launch: "yes",
        demo: 1,
        replay: null,
        spectator: {},
        benchmark: "true",
        checkpoint: [],
      },
    }
    const malformedCatalogue = {
      categories: "not-a-category-projection",
      games: [
        catalogueGames.find((game) => game.id === "labyrinth-run"),
        malformedGame,
      ],
    }
    vi.stubGlobal("fetch", catalogueFetch(malformedCatalogue))
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Games" }))
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Game catalogue" }),
      "malformed-course"
    )

    expect(
      (await screen.findAllByRole("heading", { name: "Malformed Course" }))
        .length
    ).toBeGreaterThan(0)
    expect(screen.getByText("Solo Agent Tasks")).toBeInTheDocument()
    expect(screen.getByText("1 agent")).toBeInTheDocument()
    expect(screen.getAllByText("Experimental")).not.toHaveLength(0)
    expect(screen.getByText("Guide only")).toBeInTheDocument()
    expect(screen.queryByText(/script>alert/)).not.toBeInTheDocument()
  })

  it("uses a labelled native grouped selector at mobile width", async () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 390,
    })
    vi.stubGlobal("fetch", catalogueFetch())
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Games" }))

    const picker = screen.getByRole("combobox", { name: "Game catalogue" })
    expect(picker.tagName).toBe("SELECT")
    expect(picker).toHaveAccessibleDescription(
      /3 agents.*competitive.*Live ready.*Live · Replay · Spectator · Benchmark · Checkpoint/
    )
    expect(
      within(screen.getByRole("region", { name: "Browse games" })).getAllByRole(
        "group"
      )
    ).toHaveLength(5)
  })

  it("launches a solo live game with exactly one OpenAI model seat", async () => {
    const launched = genericRunRecord({
      gameId: "movement-maze",
      id: "run_game_solo_live",
      lifecycle: "completed",
      mode: "exploratory",
      models: ["gpt-5.6-sol"],
    })
    const fetchMock = connectedFetch({
      onRequest: (url, init) => {
        if (
          url === "/api/lab/runs/games/movement-maze" &&
          init?.method === "POST"
        ) {
          expect(init.headers).toMatchObject({ "X-WorldEval-CSRF": "csrf" })
          expect(JSON.parse(String(init.body))).toEqual({
            mode: "live",
            provider: "openai",
            api_key: "solo-session-key",
            models: ["gpt-5.6-sol"],
            seed: 7,
          })
          return new Response(JSON.stringify(launched), { status: 202 })
        }
        if (url === "/api/lab/runs/run_game_solo_live") {
          return new Response(JSON.stringify(launched), { status: 200 })
        }
        if (url === "/api/lab/runs/run_game_solo_live/projection") {
          return new Response(
            JSON.stringify(
              genericProjection({
                gameId: "movement-maze",
                lifecycle: "completed",
                mode: "exploratory",
                models: ["gpt-5.6-sol"],
                runId: "run_game_solo_live",
              })
            ),
            { status: 200 }
          )
        }
        return undefined
      },
    })
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Games" }))
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Game catalogue" }),
      "movement-maze"
    )
    await user.selectOptions(screen.getByLabelText("Run mode"), "live")

    expect(screen.getByLabelText("Agent model")).toHaveValue("gpt-5.6-sol")
    expect(screen.queryByLabelText("Alpha model")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Bravo model")).not.toBeInTheDocument()

    await user.type(screen.getByLabelText("OpenAI API key"), "solo-session-key")
    await user.click(
      screen.getByRole("checkbox", {
        name: "I reviewed this exploratory-run envelope.",
      })
    )
    await user.click(screen.getByRole("button", { name: "Launch experiment" }))

    await screen.findByText("Run run_game_solo_live was accepted.")
    expect(screen.getByLabelText("OpenAI API key")).toHaveValue("")
    expect(
      await screen.findByRole("img", { name: "Seat 1 safe authority frame" })
    ).toHaveAttribute(
      "src",
      "/api/lab/runs/run_game_solo_live/frame?participant=participant_0&v=12"
    )
  })

  it("requires a valid model identifier for every visible live seat", async () => {
    vi.stubGlobal("fetch", connectedFetch())
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Games" }))
    const picker = screen.getByRole("combobox", { name: "Game catalogue" })
    await user.selectOptions(picker, "movement-maze")
    await user.selectOptions(screen.getByLabelText("Run mode"), "live")
    await user.type(screen.getByLabelText("OpenAI API key"), "session-key")
    await user.click(
      screen.getByRole("checkbox", {
        name: "I reviewed this exploratory-run envelope.",
      })
    )

    const agentModel = screen.getByLabelText("Agent model")
    await user.clear(agentModel)
    expect(agentModel).toBeRequired()
    expect(agentModel).toHaveAttribute("aria-invalid", "true")
    expect(
      screen.getByRole("button", { name: "Launch experiment" })
    ).toBeDisabled()
    await user.type(agentModel, "   ")
    expect(
      screen.getByRole("button", { name: "Launch experiment" })
    ).toBeDisabled()
    await user.clear(agentModel)
    await user.type(agentModel, "gpt-5.6-sol")
    expect(agentModel).not.toHaveAttribute("aria-invalid")
    expect(
      screen.getByRole("button", { name: "Launch experiment" })
    ).toBeEnabled()

    await user.selectOptions(picker, "labyrinth-run")
    await user.type(screen.getByLabelText("OpenAI API key"), "session-key")
    await user.click(
      screen.getByRole("checkbox", {
        name: "I reviewed this exploratory-run envelope.",
      })
    )
    const terraModel = screen.getByLabelText("Terra model")
    await user.clear(terraModel)
    expect(terraModel).toHaveAttribute("aria-invalid", "true")
    expect(
      screen.getByRole("button", { name: "Launch experiment" })
    ).toBeDisabled()
  })

  it("clears a stale key and confirmation when refreshed capabilities revoke live launch", async () => {
    const demoOnlyGames = catalogueGames.map((game) =>
      game.id === "movement-maze"
        ? {
            ...game,
            capabilities: {
              ...game.capabilities,
              live_launch: false,
              demo: true,
              benchmark: false,
              checkpoint: false,
            },
          }
        : game
    )
    const demoOnlyCatalogue = {
      ...gamesResponse,
      categories: catalogueCategories.map((category) => ({
        ...category,
        game_ids: demoOnlyGames
          .filter((game) => game.primary_category?.id === category.id)
          .map((game) => game.id),
      })),
      games: demoOnlyGames,
    }
    let connected = false
    let gameRequests = 0
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url === "/api/auth/me") {
          return connected
            ? new Response(
                JSON.stringify({
                  operator: {
                    member_id: "local",
                    email: "local@worldeval.test",
                  },
                  csrf_token: "csrf",
                }),
                { status: 200 }
              )
            : new Response(null, { status: 401 })
        }
        if (url === "/api/auth/configuration") {
          return new Response(JSON.stringify({ mode: "local" }), {
            status: 200,
          })
        }
        if (url === "/api/auth/local-login") {
          connected = true
          return new Response("{}", { status: 200 })
        }
        if (url === "/api/lab/games") {
          gameRequests += 1
          return new Response(
            JSON.stringify(
              gameRequests === 1 ? gamesResponse : demoOnlyCatalogue
            ),
            { status: 200 }
          )
        }
        if (url === "/api/lab/runs") {
          return new Response(JSON.stringify({ runs: [] }), { status: 200 })
        }
        if (url === "/api/lab/sandbox") {
          return new Response(JSON.stringify(sandboxManifestFixture()), {
            status: 200,
          })
        }
        if (url === "/api/lab/benchmarks/labyrinth-run") {
          return new Response(
            JSON.stringify({
              game_id: "labyrinth-run",
              season_state: "not_run",
              verified_results: [],
            }),
            { status: 200 }
          )
        }
        throw new Error(`Unexpected fetch ${url}`)
      })
    )
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Games" }))
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Game catalogue" }),
      "movement-maze"
    )
    await user.selectOptions(screen.getByLabelText("Run mode"), "live")
    await user.type(screen.getByLabelText("OpenAI API key"), "discard-me")
    await user.click(
      screen.getByRole("checkbox", {
        name: "I reviewed this exploratory-run envelope.",
      })
    )
    await user.click(
      screen.getByRole("button", { name: "Connect local session" })
    )

    await waitFor(() => {
      expect(screen.getByLabelText("Run mode")).toHaveValue("demo")
      expect(screen.queryByLabelText("OpenAI API key")).not.toBeInTheDocument()
      expect(
        screen.getByRole("checkbox", {
          name: "I reviewed this credential-free Demo contract.",
        })
      ).not.toBeChecked()
    })
  })

  it("launches a two-seat Demo keylessly and switches the safe frame tab", async () => {
    const models = ["authority-alpha", "authority-bravo"]
    const launched = genericRunRecord({
      gameId: "checkpoint-race",
      id: "run_game_duo_demo",
      lifecycle: "completed",
      mode: "demo",
      models,
    })
    const fetchMock = connectedFetch({
      onRequest: (url, init) => {
        if (
          url === "/api/lab/runs/games/checkpoint-race" &&
          init?.method === "POST"
        ) {
          expect(JSON.parse(String(init.body))).toEqual({
            mode: "demo",
            seed: 7,
          })
          expect(String(init.body)).not.toContain("api_key")
          return new Response(JSON.stringify(launched), { status: 202 })
        }
        if (url === "/api/lab/runs/run_game_duo_demo") {
          return new Response(JSON.stringify(launched), { status: 200 })
        }
        if (url === "/api/lab/runs/run_game_duo_demo/projection") {
          return new Response(
            JSON.stringify(
              genericProjection({
                gameId: "checkpoint-race",
                lifecycle: "completed",
                models,
                runId: "run_game_duo_demo",
              })
            ),
            { status: 200 }
          )
        }
        return undefined
      },
    })
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Games" }))
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Game catalogue" }),
      "checkpoint-race"
    )

    expect(screen.getByLabelText("Alpha model")).toBeDisabled()
    expect(screen.getByLabelText("Bravo model")).toBeDisabled()
    expect(screen.queryByLabelText("Charlie model")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("OpenAI API key")).not.toBeInTheDocument()

    await user.click(
      screen.getByRole("checkbox", {
        name: "I reviewed this credential-free Demo contract.",
      })
    )
    await user.click(screen.getByRole("button", { name: "Launch Demo" }))

    const alphaFrame = await screen.findByRole("img", {
      name: "Seat 1 safe authority frame",
    })
    expect(alphaFrame).toHaveAttribute(
      "src",
      "/api/lab/runs/run_game_duo_demo/frame?participant=participant_0&v=12"
    )
    const firstSeat = screen.getByRole("tab", {
      name: "Seat 1 · participant_0",
    })
    await user.click(firstSeat)
    await user.keyboard("{ArrowRight}")
    expect(
      screen.getByRole("tab", { name: "Seat 2 · participant_1" })
    ).toHaveAttribute("aria-selected", "true")
    expect(
      screen.getByRole("img", { name: "Seat 2 safe authority frame" })
    ).toHaveAttribute(
      "src",
      "/api/lab/runs/run_game_duo_demo/frame?participant=participant_1&v=12"
    )
  })

  it("launches a three-seat Demo with the exact authority-owned envelope", async () => {
    const models = ["authority-alpha", "authority-bravo", "authority-charlie"]
    const launched = genericRunRecord({
      gameId: "mini-rts",
      id: "run_game_trio_demo",
      lifecycle: "completed",
      mode: "demo",
      models,
    })
    const fetchMock = connectedFetch({
      onRequest: (url, init) => {
        if (url === "/api/lab/runs/games/mini-rts" && init?.method === "POST") {
          expect(JSON.parse(String(init.body))).toEqual({
            mode: "demo",
            seed: 7,
          })
          return new Response(JSON.stringify(launched), { status: 202 })
        }
        if (url === "/api/lab/runs/run_game_trio_demo") {
          return new Response(JSON.stringify(launched), { status: 200 })
        }
        if (url === "/api/lab/runs/run_game_trio_demo/projection") {
          return new Response(
            JSON.stringify(
              genericProjection({
                gameId: "mini-rts",
                lifecycle: "completed",
                models,
                runId: "run_game_trio_demo",
              })
            ),
            { status: 200 }
          )
        }
        return undefined
      },
    })
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Games" }))
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Game catalogue" }),
      "mini-rts"
    )

    expect(screen.getByLabelText("Alpha model")).toBeDisabled()
    expect(screen.getByLabelText("Bravo model")).toBeDisabled()
    expect(screen.getByLabelText("Charlie model")).toBeDisabled()

    await user.click(
      screen.getByRole("checkbox", {
        name: "I reviewed this credential-free Demo contract.",
      })
    )
    await user.click(screen.getByRole("button", { name: "Launch Demo" }))

    expect(
      await screen.findByRole("tablist", { name: "Participant frame" })
    ).toBeInTheDocument()
    expect(screen.getAllByRole("tab")).toHaveLength(3)
  })

  it("keeps guide-and-replay-only games visibly unlaunchable", async () => {
    const unavailableGame = gameFixture({
      categoryId: "solo-agent-tasks",
      demo: false,
      id: "archived-arena",
      interactionKind: "solo",
      liveLaunch: false,
      participants: [1, 1],
      readiness: "experimental",
      title: "Archived Arena",
    })
    const games = [...catalogueGames, unavailableGame]
    const catalogue = {
      schema_version: "worldeval/lab-game-catalog/2",
      categories: catalogueCategories.map((category) => ({
        ...category,
        game_ids: games
          .filter((game) => game.primary_category?.id === category.id)
          .map((game) => game.id),
      })),
      games,
    }
    vi.stubGlobal("fetch", connectedFetch({ catalogue }))
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Games" }))
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Game catalogue" }),
      "archived-arena"
    )

    expect(
      screen.getByRole("button", { name: "Launch unavailable" })
    ).toBeDisabled()
    expect(
      screen.getByText(
        "Guide and replay only. No launch authority is admitted."
      )
    ).toBeInTheDocument()
    expect(screen.queryByLabelText("Run mode")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("OpenAI API key")).not.toBeInTheDocument()
  })

  it("renders the ten sandbox primitives and their executable recipe", async () => {
    const sandboxGame = gameFixture({
      categoryId: "sandbox-primitives",
      demo: true,
      id: "operator-action-course",
      interactionKind: "solo",
      liveLaunch: true,
      participants: [1, 1],
      readiness: "live_ready",
      title: "Operator Action Course",
    })
    const games = [...catalogueGames, sandboxGame]
    const catalogue = {
      schema_version: "worldeval/lab-game-catalog/2",
      categories: catalogueCategories.map((category) => ({
        ...category,
        game_ids: games
          .filter((game) => game.primary_category?.id === category.id)
          .map((game) => game.id),
      })),
      games,
    }
    vi.stubGlobal("fetch", connectedFetch({ catalogue }))
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Games" }))
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Game catalogue" }),
      "operator-action-course"
    )

    const sandbox = await screen.findByRole("region", {
      name: "Sandbox Primitives",
    })
    expect(within(sandbox).getAllByRole("listitem")).toHaveLength(10)
    expect(
      within(sandbox).getByText("Depends on Primitive 1")
    ).toBeInTheDocument()
    expect(
      within(sandbox).getByRole("heading", {
        name: "Operator Action Course",
      })
    ).toBeInTheDocument()
    expect(within(sandbox).getByText("operator-action-course-v0")).toBeVisible()
    expect(within(sandbox).getByText("llm-controller/0.2.0")).toBeVisible()
  })

  it("renders the cached simulation, server guide, and clears a submitted session key", async () => {
    let connected = false
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url === "/api/auth/me") {
          return connected
            ? new Response(
                JSON.stringify({
                  operator: {
                    member_id: "local",
                    email: "local@worldeval.test",
                  },
                  csrf_token: "csrf",
                }),
                { status: 200 }
              )
            : new Response(null, { status: 401 })
        }
        if (url === "/api/auth/configuration") {
          return new Response(JSON.stringify({ mode: "local" }), {
            status: 200,
          })
        }
        if (url === "/api/auth/local-login") {
          connected = true
          return new Response("{}", { status: 200 })
        }
        if (url === "/api/lab/games")
          return new Response(JSON.stringify(gamesResponse), { status: 200 })
        if (url === "/api/lab/runs")
          return new Response(JSON.stringify({ runs: [] }), { status: 200 })
        if (url === "/api/lab/benchmarks/labyrinth-run") {
          return new Response(
            JSON.stringify({
              game_id: "labyrinth-run",
              season_state: "not_run",
              verified_results: [],
              message: "No season yet.",
            }),
            { status: 200 }
          )
        }
        if (url === "/api/lab/runs/labyrinth" && init?.method === "POST") {
          expect(init.headers).toMatchObject({ "X-WorldEval-CSRF": "csrf" })
          expect(JSON.parse(String(init.body))).toMatchObject({
            api_key: "sk-session-only",
            entrants: [
              { display_name: "Sol", model: "gpt-5.6-sol" },
              { display_name: "Terra", model: "gpt-5.6-terra" },
              { display_name: "Luna", model: "gpt-5.6-luna" },
            ],
          })
          return new Response(
            JSON.stringify(
              labRunRecord({
                contractSha256: liveContractSha256,
                id: "run_labyrinth_1",
                lifecycle: "running",
              })
            ),
            { status: 202 }
          )
        }
        if (url === "/api/lab/runs/run_labyrinth_1") {
          return new Response(
            JSON.stringify({
              run_id: "run_labyrinth_1",
              replay_available: false,
              video_available: false,
              resume_supported: false,
              contract: {
                contract_sha256: liveContractSha256,
                entrants: [
                  { entrant_id: "entrant_0", provider: "openai" },
                  { entrant_id: "entrant_1", provider: "openai" },
                  { entrant_id: "entrant_2", provider: "openai" },
                ],
                game_id: "labyrinth-run",
                game_version: "trio-maze-race-v1",
                mode: "exploratory",
              },
              state: { status: "running" },
            }),
            { status: 200 }
          )
        }
        if (url === "/api/lab/runs/run_labyrinth_1/projection") {
          const racers = ["Sol", "Terra", "Luna"].map(
            (display_name, index) => ({
              participant_id: `participant_${index}`,
              entrant_id: `entrant_${index}`,
              display_name,
              color: ["#fbbf24", "#fa755e", "#36c2bd"][index],
              position: [1, 1],
              path: [[1, 1]],
              visible_cells: [[1, 1]],
              provider_calls: 1,
              finished: false,
            })
          )
          return new Response(
            JSON.stringify({
              run_id: "run_labyrinth_1",
              contract_sha256: liveContractSha256,
              sequence: 4,
              status: "running",
              snapshot: {
                arena: {
                  tick: 4,
                  provider_calls: 3,
                  map: {
                    rows: ["###", "#.#", "###"],
                    start: [1, 1],
                    exit: [1, 1],
                  },
                  racers,
                },
              },
            }),
            { status: 200 }
          )
        }
        if (url === "/api/lab/runs/run_labyrinth_1/spectator?after=0") {
          const racers = ["Sol", "Terra", "Luna"].map(
            (display_name, index) => ({
              participant_id: `participant_${index}`,
              entrant_id: `entrant_${index}`,
              display_name,
              color: ["#fbbf24", "#fa755e", "#36c2bd"][index],
              position: [1, 1],
              path: [[1, 1]],
              visible_cells: [[1, 1]],
              provider_calls: 1,
              finished: false,
            })
          )
          return new Response(
            JSON.stringify({
              schema_version: "worldeval/lab-live-spectator-feed/1",
              run_id: "run_labyrinth_1",
              cursor: 2,
              reset_required: false,
              frames: [
                {
                  sequence: 1,
                  frame: {
                    tick: 0,
                    provider_calls: 0,
                    map: {
                      rows: ["###", "#.#", "###"],
                      start: [1, 1],
                      exit: [1, 1],
                    },
                    racers,
                  },
                },
                {
                  sequence: 2,
                  frame: {
                    tick: 4,
                    provider_calls: 3,
                    map: {
                      rows: ["###", "#.#", "###"],
                      start: [1, 1],
                      exit: [1, 1],
                    },
                    racers,
                  },
                },
              ],
            }),
            { status: 200 }
          )
        }
        if (url === "/api/lab/runs/run_labyrinth_1/spectator?after=2") {
          return new Response(
            JSON.stringify({
              schema_version: "worldeval/lab-live-spectator-feed/1",
              run_id: "run_labyrinth_1",
              cursor: 2,
              reset_required: false,
              frames: [],
            }),
            { status: 200 }
          )
        }
        throw new Error(`Unexpected fetch ${url}`)
      }
    )
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    render(<LabApp />)

    expect(
      (await screen.findAllByRole("heading", { name: "Labyrinth Run" })).length
    ).toBeGreaterThan(0)
    expect(screen.getByText("Saved simulation")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Games" }))
    expect(
      await screen.findByRole("heading", { name: "What this game tests" })
    ).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Lab" }))
    await user.click(
      screen.getByRole("button", { name: "Connect local session" })
    )
    await screen.findByText(
      "Local session connected. You can now submit a run."
    )
    await user.type(screen.getByLabelText("OpenAI API key"), "sk-session-only")
    await user.click(
      screen.getByRole("checkbox", {
        name: "I reviewed this exploratory-run envelope.",
      })
    )
    await user.click(screen.getByRole("button", { name: "Launch experiment" }))

    await screen.findByText("Run run_labyrinth_1 was accepted.")
    expect(
      await screen.findByText("Authority observer map")
    ).toBeInTheDocument()
    expect(
      await screen.findByRole("img", { name: "Authority maze map at tick 4" })
    ).toBeInTheDocument()
    expect(
      await screen.findByRole("button", { name: "Live now" })
    ).toHaveAttribute("aria-pressed", "true")
    expect(
      screen.getByText(
        "Playback interpolates only between accepted authority frames; it never advances the game or issues a model call."
      )
    ).toBeInTheDocument()
    expect(screen.getByLabelText("OpenAI API key")).toHaveValue("")
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/lab/runs/labyrinth",
        expect.objectContaining({ method: "POST" })
      )
    )
  })

  it("does not offer clone launch for an OpenAI run with no explicit mode", async () => {
    const source = labRunRecord({
      contractSha256: sourceContractSha256,
      id: "run_missing_mode",
      lifecycle: "completed",
    })
    const malformed = {
      ...source,
      contract: { ...source.contract, mode: undefined },
    }
    vi.stubGlobal("fetch", connectedFetch({ runs: [malformed] }))
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Runs" }))

    expect(
      screen.getByRole("button", {
        name: "Clone unavailable for run_missing_mode: unsupported launch contract",
      })
    ).toBeDisabled()
  })

  it("relaunches a generic Demo clone with an empty credential envelope", async () => {
    const source = genericRunRecord({
      contractSha256: sourceContractSha256,
      gameId: "checkpoint-race",
      id: "run_game_demo_source",
      lifecycle: "completed",
      mode: "demo",
      models: ["authority-alpha", "authority-bravo"],
    })
    const draft = genericRunRecord({
      contractSha256: cloneContractSha256,
      gameId: "checkpoint-race",
      id: "run_game_demo_clone",
      lifecycle: "draft",
      mode: "demo",
      models: ["authority-alpha", "authority-bravo"],
      parentContractSha256: sourceContractSha256,
    })
    const launched = genericRunRecord({
      contractSha256: cloneContractSha256,
      gameId: "checkpoint-race",
      id: "run_game_demo_clone",
      lifecycle: "queued",
      mode: "demo",
      models: ["authority-alpha", "authority-bravo"],
      parentContractSha256: sourceContractSha256,
    })
    let cloneLaunched = false
    const fetchMock = connectedFetch({
      runs: [source],
      onRequest: (url, init) => {
        if (
          url === "/api/lab/runs/run_game_demo_source/clone" &&
          init?.method === "POST"
        ) {
          expect(JSON.parse(String(init.body))).toEqual({
            changes: { configuration: {} },
          })
          return new Response(JSON.stringify(draft), { status: 201 })
        }
        if (
          url === "/api/lab/runs/run_game_demo_clone/launch" &&
          init?.method === "POST"
        ) {
          expect(init.headers).toMatchObject({ "X-WorldEval-CSRF": "csrf" })
          expect(JSON.parse(String(init.body))).toEqual({})
          cloneLaunched = true
          return new Response(JSON.stringify(launched), { status: 202 })
        }
        if (url === "/api/lab/runs/run_game_demo_clone") {
          return new Response(
            JSON.stringify(cloneLaunched ? launched : draft),
            { status: 200 }
          )
        }
        if (url === "/api/lab/runs/run_game_demo_clone/projection") {
          return new Response(
            JSON.stringify(
              genericProjection({
                contractSha256: cloneContractSha256,
                gameId: "checkpoint-race",
                lifecycle: "queued",
                models: ["authority-alpha", "authority-bravo"],
                runId: "run_game_demo_clone",
              })
            ),
            { status: 200 }
          )
        }
        return undefined
      },
    })
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await user.click(screen.getByRole("button", { name: "Runs" }))
    await user.click(
      screen.getByRole("button", {
        name: "Clone frozen configuration for run_game_demo_source",
      })
    )

    await screen.findByText("Unlaunched frozen draft")
    expect(screen.queryByLabelText("OpenAI API key")).not.toBeInTheDocument()
    expect(
      screen.getByText(
        (_, element) =>
          element?.tagName === "P" &&
          element.textContent.includes("This Demo clone is credential-free.")
      )
    ).toBeInTheDocument()
    await user.click(
      screen.getByRole("checkbox", {
        name: "I reviewed this frozen-clone launch. It starts a fresh run and does not resume the parent.",
      })
    )
    await user.click(
      screen.getByRole("button", { name: "Launch frozen clone" })
    )

    await waitFor(() => expect(cloneLaunched).toBe(true))
    expect(
      await screen.findByRole("img", {
        name: "Seat 1 safe authority frame",
      })
    ).toBeInTheDocument()
  })

  it("clones a saved contract into a frozen draft and launches it with only a newly entered key", async () => {
    let connected = false
    const source = labRunRecord({
      contractSha256: sourceContractSha256,
      id: "run_source_1",
      lifecycle: "completed",
    })
    const draft = labRunRecord({
      contractSha256: cloneContractSha256,
      id: "run_clone_draft_1",
      lifecycle: "draft",
      parentContractSha256: sourceContractSha256,
    })
    const launched = labRunRecord({
      contractSha256: cloneContractSha256,
      id: "run_clone_draft_1",
      lifecycle: "queued",
      parentContractSha256: sourceContractSha256,
    })
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url === "/api/auth/me") {
          return connected
            ? new Response(
                JSON.stringify({
                  operator: {
                    member_id: "local",
                    email: "local@worldeval.test",
                  },
                  csrf_token: "csrf",
                }),
                { status: 200 }
              )
            : new Response(null, { status: 401 })
        }
        if (url === "/api/auth/configuration")
          return new Response(JSON.stringify({ mode: "local" }), {
            status: 200,
          })
        if (url === "/api/auth/local-login") {
          connected = true
          return new Response("{}", { status: 200 })
        }
        if (url === "/api/lab/games")
          return new Response(JSON.stringify(gamesResponse), { status: 200 })
        if (url === "/api/lab/runs")
          return new Response(JSON.stringify({ runs: [source] }), {
            status: 200,
          })
        if (url === "/api/lab/benchmarks/labyrinth-run")
          return new Response(
            JSON.stringify({
              game_id: "labyrinth-run",
              season_state: "not_run",
              verified_results: [],
            }),
            { status: 200 }
          )
        if (
          url === "/api/lab/runs/run_source_1/clone" &&
          init?.method === "POST"
        ) {
          expect(init.headers).toMatchObject({ "X-WorldEval-CSRF": "csrf" })
          expect(JSON.parse(String(init.body))).toEqual({
            changes: { configuration: {} },
          })
          return new Response(JSON.stringify(draft), { status: 201 })
        }
        if (url === "/api/lab/runs/run_clone_draft_1")
          return new Response(JSON.stringify(draft), { status: 200 })
        if (
          url === "/api/lab/runs/run_clone_draft_1/launch" &&
          init?.method === "POST"
        ) {
          expect(init.headers).toMatchObject({ "X-WorldEval-CSRF": "csrf" })
          expect(JSON.parse(String(init.body))).toEqual({
            api_key: "new-session-key",
          })
          return new Response(JSON.stringify(launched), { status: 202 })
        }
        if (url === "/api/lab/runs/run_clone_draft_1/projection")
          return new Response(
            JSON.stringify(mazeProjection("run_clone_draft_1", "queued")),
            { status: 200 }
          )
        if (url === "/api/lab/runs/run_clone_draft_1/spectator?after=0")
          return new Response(
            JSON.stringify({
              schema_version: "worldeval/lab-live-spectator-feed/1",
              run_id: "run_clone_draft_1",
              cursor: 0,
              reset_required: false,
              frames: [],
            }),
            { status: 200 }
          )
        throw new Error(`Unexpected fetch ${url}`)
      }
    )
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await screen.findAllByRole("heading", { name: "Labyrinth Run" })
    await user.click(
      screen.getByRole("button", { name: "Connect local session" })
    )
    await screen.findByText(
      "Local session connected. You can now submit a run."
    )
    await user.click(screen.getByRole("button", { name: "Runs" }))
    await screen.findByText("run_source_1")
    await user.click(
      screen.getByRole("button", {
        name: "Clone frozen configuration for run_source_1",
      })
    )

    expect(
      await screen.findByText("Unlaunched frozen draft")
    ).toBeInTheDocument()
    expect(screen.getByText(/exact frozen copy/i)).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Launch frozen clone" })
    ).toBeInTheDocument()
    expect(screen.queryByText("Model roster")).not.toBeInTheDocument()

    await user.type(screen.getByLabelText("OpenAI API key"), "new-session-key")
    await user.click(
      screen.getByRole("checkbox", {
        name: "I reviewed this frozen-clone launch. It starts a fresh race and does not resume the parent.",
      })
    )
    await user.click(
      screen.getByRole("button", { name: "Launch frozen clone" })
    )

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/lab/runs/run_clone_draft_1/launch",
        expect.objectContaining({ method: "POST" })
      )
    )
    expect(screen.getByLabelText("OpenAI API key")).toHaveValue("")
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) =>
          String(url) === "/api/lab/runs/labyrinth" &&
          (init as RequestInit | undefined)?.method === "POST"
      )
    ).toBe(false)
  })

  it("does not submit a new key when a clone draft is no longer a draft", async () => {
    let connected = false
    const source = labRunRecord({
      contractSha256: sourceContractSha256,
      id: "run_source_stale",
      lifecycle: "completed",
    })
    const draft = labRunRecord({
      contractSha256: cloneContractSha256,
      id: "run_clone_stale",
      lifecycle: "draft",
      parentContractSha256: sourceContractSha256,
    })
    const noLongerDraft = labRunRecord({
      contractSha256: cloneContractSha256,
      id: "run_clone_stale",
      lifecycle: "queued",
      parentContractSha256: sourceContractSha256,
    })
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url === "/api/auth/me") {
          return connected
            ? new Response(
                JSON.stringify({
                  operator: {
                    member_id: "local",
                    email: "local@worldeval.test",
                  },
                  csrf_token: "csrf",
                }),
                { status: 200 }
              )
            : new Response(null, { status: 401 })
        }
        if (url === "/api/auth/configuration")
          return new Response(JSON.stringify({ mode: "local" }), {
            status: 200,
          })
        if (url === "/api/auth/local-login") {
          connected = true
          return new Response("{}", { status: 200 })
        }
        if (url === "/api/lab/games")
          return new Response(JSON.stringify(gamesResponse), { status: 200 })
        if (url === "/api/lab/runs")
          return new Response(JSON.stringify({ runs: [source] }), {
            status: 200,
          })
        if (url === "/api/lab/benchmarks/labyrinth-run")
          return new Response(
            JSON.stringify({
              game_id: "labyrinth-run",
              season_state: "not_run",
              verified_results: [],
            }),
            { status: 200 }
          )
        if (
          url === "/api/lab/runs/run_source_stale/clone" &&
          init?.method === "POST"
        )
          return new Response(JSON.stringify(draft), { status: 201 })
        if (url === "/api/lab/runs/run_clone_stale")
          return new Response(JSON.stringify(noLongerDraft), { status: 200 })
        if (url === "/api/lab/runs/run_clone_stale/launch") {
          throw new Error("stale clone must not be launched")
        }
        throw new Error(`Unexpected fetch ${url}`)
      }
    )
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    render(<LabApp />)

    await screen.findByText("Saved simulation")
    await screen.findAllByRole("heading", { name: "Labyrinth Run" })
    await user.click(
      screen.getByRole("button", { name: "Connect local session" })
    )
    await screen.findByText(
      "Local session connected. You can now submit a run."
    )
    await user.click(screen.getByRole("button", { name: "Runs" }))
    await user.click(
      screen.getByRole("button", {
        name: "Clone frozen configuration for run_source_stale",
      })
    )
    await screen.findByText("Unlaunched frozen draft")
    await user.type(screen.getByLabelText("OpenAI API key"), "new-session-key")
    await user.click(
      screen.getByRole("checkbox", {
        name: "I reviewed this frozen-clone launch. It starts a fresh race and does not resume the parent.",
      })
    )
    await user.click(
      screen.getByRole("button", { name: "Launch frozen clone" })
    )

    await screen.findByText(
      "The Lab did not accept the frozen clone. Your key was cleared."
    )
    expect(screen.getByLabelText("OpenAI API key")).toHaveValue("")
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) =>
          String(url) === "/api/lab/runs/run_clone_stale/launch" &&
          (init as RequestInit | undefined)?.method === "POST"
      )
    ).toBe(false)
  })
})
