import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"
import type { CachedMazeShowcaseView } from "@/api"
import { LabApp } from "./LabApp"

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

const gamesResponse = {
  games: [
    {
      id: "labyrinth-run",
      title: "Labyrinth Run",
      readiness: "live_ready",
      readiness_note: "Live provider race is available.",
      task_ids: ["trio-maze-race-v1"],
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
          id: "live_provider_race",
          label: "Live",
          description: "Fresh authority race.",
        },
      ],
    },
  ],
}

const sourceContractSha256 = "a".repeat(64)
const cloneContractSha256 = "b".repeat(64)

function labRunRecord({
  contractSha256,
  id,
  lifecycle,
  lineageDiff = {},
  parentContractSha256 = null,
}: {
  contractSha256: string
  id: string
  lifecycle: string
  lineageDiff?: Record<string, unknown>
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
      parent_contract_sha256: parentContractSha256,
    },
    state: { status: lifecycle },
  }
}

function mazeProjection(runId: string, lifecycle: string) {
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

describe("LabApp", () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
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
          return new Response(JSON.stringify({ run_id: "run_labyrinth_1" }), {
            status: 202,
          })
        }
        if (url === "/api/lab/runs/run_labyrinth_1") {
          return new Response(
            JSON.stringify({
              run_id: "run_labyrinth_1",
              replay_available: false,
              video_available: false,
              resume_supported: false,
              contract: {
                game_id: "labyrinth-run",
                game_version: "trio-maze-race-v1",
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
        if (
          url ===
          "/api/lab/runs/run_labyrinth_1/spectator?after=0"
        ) {
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
        if (
          url ===
          "/api/lab/runs/run_labyrinth_1/spectator?after=2"
        ) {
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
    expect(await screen.findByRole("button", { name: "Live now" })).toHaveAttribute(
      "aria-pressed",
      "true"
    )
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
                  operator: { member_id: "local", email: "local@worldeval.test" },
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
          return new Response(JSON.stringify({ runs: [source] }), { status: 200 })
        if (url === "/api/lab/benchmarks/labyrinth-run")
          return new Response(
            JSON.stringify({
              game_id: "labyrinth-run",
              season_state: "not_run",
              verified_results: [],
            }),
            { status: 200 }
          )
        if (url === "/api/lab/runs/run_source_1/clone" && init?.method === "POST") {
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

    expect(await screen.findByText("Unlaunched frozen draft")).toBeInTheDocument()
    expect(screen.getByText(/exact frozen copy/i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Launch frozen clone" })).toBeInTheDocument()
    expect(screen.queryByText("Model roster")).not.toBeInTheDocument()

    await user.type(screen.getByLabelText("OpenAI API key"), "new-session-key")
    await user.click(
      screen.getByRole("checkbox", {
        name: "I reviewed this frozen-clone launch. It starts a fresh race and does not resume the parent.",
      })
    )
    await user.click(screen.getByRole("button", { name: "Launch frozen clone" }))

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
                  operator: { member_id: "local", email: "local@worldeval.test" },
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
          return new Response(JSON.stringify({ runs: [source] }), { status: 200 })
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
    await user.click(screen.getByRole("button", { name: "Launch frozen clone" }))

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
