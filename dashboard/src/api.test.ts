import { describe, expect, it, vi } from "vitest"
import {
  DEMO_DUO_GAMES,
  DEMO_TRIO_ENTRANTS,
  bundleUrl,
  cachedCrossroadsVideoUrl,
  cachedRtsVideoUrl,
  cachedSoloVideoUrl,
  cancelRun,
  createEpisode,
  createRun,
  getEpisode,
  getCachedCrossroadsEvaluation,
  getCachedCrossroadsShowcase,
  getCachedRtsEvaluation,
  getCachedRtsShowcase,
  getCachedSoloShowcase,
  getEpisodeEvaluation,
  getEpisodeTimeline,
  getParticipantFrame,
  getSeriesParticipantFrame,
  getSeriesEvaluation,
  getTrioEvaluation,
  getReadiness,
  getSavedReplay,
  getSavedReplayEvaluation,
  getSavedReplays,
  savedReplayVideoUrl,
  seriesParticipantVideoUrl,
} from "@/api"

function response(value: unknown) {
  return { ok: true, json: async () => value } as Response
}

const scriptedDemoSetup = {
  controllerMode: "scripted_demo" as const,
  mode: "solo" as const,
  provider: "openai" as const,
  model: "gpt-5.6-sol",
  apiKey: "",
  opponentProvider: "scripted" as const,
  opponentModel: "balanced-v1",
  opponentApiKey: "",
  scenarioId: "orientation-v0",
  taskId: "orientation-v0",
  seed: 20240520,
}

describe("public replay routes", () => {
  it("routes solo episodes and paired series to their sealed public evidence", () => {
    expect(bundleUrl("ep_stage_c")).toBe(
      "/api/embodiment/episodes/ep_stage_c/replay"
    )
    expect(bundleUrl("series_two_leg")).toBe(
      "/api/embodiment/series/series_two_leg/replay"
    )
    expect(bundleUrl("trio_three_leg")).toBe(
      "/api/embodiment/trio-series/trio_three_leg/replay"
    )
  })
})

describe("cached RTS showcase routes", () => {
  const showcase = {
    showcase_id: "rts-skirmish-v0", task_id: "rts-skirmish-v0", label: "Mini RTS",
    status: "ready", cached: true,
    video: { duration_seconds: 150, fps: 30, height: 1080, mime_type: "video/mp4", sha256: "a".repeat(64), width: 1920 },
    winner: { team: "Terra" },
    completion: { outcome: "win", reason: "town_hall_destroyed", tick: 1190 },
    casualties: [{ at_tick: 850, team: "Terra", unit_id: "terra_agent_1" }],
    highlights: [{ at_seconds: 0, label: "Workers deploy" }],
    raw_output: "must not survive",
  }
  const evaluation = {
    showcase_id: "rts-skirmish-v0", task_id: "rts-skirmish-v0",
    completion: showcase.completion,
    metrics: [{ id: "economy", label: "Economy", value: "Three workers" }],
    verification: {
      manifest_sha256: "b".repeat(64), replay_sha256: "c".repeat(64),
      video_sha256: "d".repeat(64), final_state_sha256: "e".repeat(64), state: "verified",
    },
    prompt: "must not survive",
  }

  it("reads only safe cached metadata and has no browser replay route", async () => {
    const fetch = vi.fn<
      (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
    >(async (input) => {
      const route = String(input)
      return response(route.endsWith("/evaluation") ? evaluation : showcase)
    })
    vi.stubGlobal("fetch", fetch)
    await expect(getCachedRtsShowcase()).resolves.toMatchObject({
      label: "Mini RTS", video: { durationSeconds: 150 }, casualties: [{ unitId: "terra_agent_1" }],
    })
    await expect(getCachedRtsEvaluation()).resolves.toMatchObject({
      metrics: [{ id: "economy" }], verification: { state: "verified" },
    })
    expect(cachedRtsVideoUrl()).toBe("/api/embodiment/showcases/rts-skirmish-v0/video")
    expect(fetch.mock.calls.map(([route]) => String(route))).toEqual([
      "/api/embodiment/showcases/rts-skirmish-v0",
      "/api/embodiment/showcases/rts-skirmish-v0/evaluation",
    ])
    expect(fetch.mock.calls.map(([, init]) => init)).toEqual([
      { cache: "no-store" },
      { cache: "no-store" },
    ])
  })

  it.each(["raw_output", "api_key", "API key", "Bearer token", "client secret"])(
    "rejects private text variant %s",
    async (privateText) => {
      vi.stubGlobal("fetch", vi.fn(async () => response({ ...showcase, label: privateText })))
      await expect(getCachedRtsShowcase()).rejects.toThrow("Cached RTS showcase is invalid")
    },
  )
})

describe("cached solo showcase route", () => {
  const showcase = {
    showcase_id: "solo-multi-action-v0",
    task_id: "construction-v0",
    scenario_id: "multi-action-demo-v0",
    label: "Solo Multi-Action Construction",
    tagline: "Turn, walk, gather, carry, deposit, build, and celebrate in one sealed run.",
    status: "ready",
    cached: true,
    participant: {
      participant_id: "participant_0",
      display_name: "Demo Builder",
      model: "construction-demo-v1",
    },
    video: {
      duration_seconds: 121.9,
      fps: 30,
      height: 1080,
      mime_type: "video/mp4",
      sha256: "1".repeat(64),
      width: 1920,
    },
    highlights: [
      { at_seconds: 0, label: "Orient toward the visible worksite" },
      { at_seconds: 94, label: "Build the visible barricade" },
    ],
    verification: {
      state: "verified",
      renderer: "godot-movie-maker+ffmpeg",
      release_profile: "worldarena-participant-1080p30-v1",
      evidence_sha256: "2".repeat(64),
      replay_sha256: "3".repeat(64),
      final_state_sha256: "4".repeat(64),
      protocol_package_sha256: "5".repeat(64),
      protocol_version: "llm-controller/0.1.0",
      authority_ticks: 1194,
    },
  }

  it("loads the checked-in solo highlight and exposes its stable video route", async () => {
    const fetch = vi.fn(async () => response(showcase))
    vi.stubGlobal("fetch", fetch)

    await expect(getCachedSoloShowcase()).resolves.toMatchObject({
      showcaseId: "solo-multi-action-v0",
      video: { durationSeconds: 121.9, width: 1920, height: 1080 },
      verification: { state: "verified", authorityTicks: 1194 },
    })
    expect(cachedSoloVideoUrl()).toBe(
      "/api/embodiment/showcases/solo-multi-action-v0/video"
    )
    expect(fetch).toHaveBeenCalledWith(
      "/api/embodiment/showcases/solo-multi-action-v0",
      { cache: "no-store" }
    )
  })
})

describe("cached Crossroads Conquest routes", () => {
  const beatIds = [
    "opening_reveal", "sol_introduction", "terra_introduction", "luna_introduction",
    "terra_claims_crossroads", "sol_prepares_assault", "luna_observes", "crossroads_clash",
    "sol_takes_crossroads", "two_front_march", "terra_counterpunch", "sol_breaches_terra",
    "terra_eliminated", "exposed_sol_overview", "luna_strikes", "sol_eliminated",
    "verified_result",
  ]
  const starts = [0, 12, 19, 26, 33, 44, 55, 65, 78, 90, 102, 114, 126, 137, 146, 158, 169]
  const eventIds = [
    "", "", "", "", "event_4", "event_5", "event_6", "event_7", "event_8",
    "event_9", "event_10", "event_11", "terra_falls", "terra_falls", "event_14",
    "sol_falls", "sol_falls",
  ]
  const placements = [
    { placement: 1, entrant_id: "participant_1", faction_id: "luna", display_name: "Luna" },
    { placement: 2, entrant_id: "participant_0", faction_id: "sol", display_name: "Sol" },
    { placement: 3, entrant_id: "participant_2", faction_id: "terra", display_name: "Terra" },
  ]
  const eliminationOrder = [
    { order: 1, faction_id: "terra", eliminated_by: "sol", round: 25, event_id: "terra_eliminated" },
    { order: 2, faction_id: "sol", eliminated_by: "luna", round: 29, event_id: "sol_eliminated" },
  ]
  const showcase = {
    showcase_id: "crossroads-conquest-v0", task_id: "crossroads-conquest-v0",
    title: "WorldArena: Crossroads Conquest",
    tagline: "Three strongholds. One crossroads. Last faction standing.",
    status: "ready", cached: true, verified: true,
    entrants: [
      { entrant_id: "participant_0", faction_id: "sol", display_name: "Sol", glyph: "△", color: "#fbbf24", policy_id: "crossroads-conquest-demo-v1" },
      { entrant_id: "participant_1", faction_id: "luna", display_name: "Luna", glyph: "○", color: "#a78bfa", policy_id: "crossroads-conquest-demo-v1" },
      { entrant_id: "participant_2", faction_id: "terra", display_name: "Terra", glyph: "□", color: "#34d399", policy_id: "crossroads-conquest-demo-v1" },
    ],
    winner: { entrant_id: "participant_1", faction_id: "luna", display_name: "Luna" },
    placements,
    elimination_order: eliminationOrder,
    timeline: beatIds.map((beat_id, index) => ({
      beat_id, at_seconds: starts[index], round: Math.min(29, index * 2),
      frame_index: Math.max(0, index - 3), event_id: eventIds[index],
      kind: index < 4 ? "editorial" : "authority_event", editorial: index < 4,
      label: `Public beat ${index}`,
    })),
    video: { duration_seconds: 180, fps: 30, height: 1080, mime_type: "video/mp4", sha256: "1".repeat(64), width: 1920 },
    authority: { protocol: "world-arena/0.4", seed: 424242, policy_id: "crossroads-conquest-demo-v1", map_id: "tri_13_v1", rules_id: "arena-v0.4" },
    raw_output: "must not survive",
  }
  const evaluation = {
    showcase_id: "crossroads-conquest-v0", task_id: "crossroads-conquest-v0",
    scope: "crossroads_conquest",
    outcome: { winner: showcase.winner, placements, elimination_order: eliminationOrder },
    verification: {
      state: "verified", deterministic: true, deterministic_runs: 2, order_rejections: 0,
      luna_first_hostile_round: 26, manifest_sha256: "2".repeat(64),
      replay_sha256: "3".repeat(64), evaluation_sha256: "4".repeat(64),
      video_sha256: "5".repeat(64), normalized_trace_sha256: "6".repeat(64),
      final_state_sha256: "7".repeat(64),
    },
    factions: [
      { faction_id: "sol", placement: 2, core_hp: 0, eliminated_round: 29, eliminated_by: "luna", strongholds_destroyed: 1 },
      { faction_id: "luna", placement: 1, core_hp: 1000, eliminated_round: null, eliminated_by: null, strongholds_destroyed: 1 },
      { faction_id: "terra", placement: 3, core_hp: 0, eliminated_round: 25, eliminated_by: "sol", strongholds_destroyed: 0 },
    ],
  }

  it("parses only the cached public projections and exposes no replay helper", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => response(
      String(input).endsWith("/evaluation") ? evaluation : showcase
    ))
    vi.stubGlobal("fetch", fetch)

    await expect(getCachedCrossroadsShowcase()).resolves.toMatchObject({
      title: "WorldArena: Crossroads Conquest", winner: { displayName: "Luna" },
      eliminationOrder: [{ factionId: "terra", eliminatedBy: "sol" }, { factionId: "sol", eliminatedBy: "luna" }],
    })
    await expect(getCachedCrossroadsEvaluation()).resolves.toMatchObject({
      verification: { deterministicRuns: 2, orderRejections: 0, lunaFirstHostileRound: 26 },
    })
    expect(cachedCrossroadsVideoUrl()).toBe("/api/embodiment/showcases/crossroads-conquest-v0/video")
    expect(fetch.mock.calls.map(([route]) => String(route))).toEqual([
      "/api/embodiment/showcases/crossroads-conquest-v0",
      "/api/embodiment/showcases/crossroads-conquest-v0/evaluation",
    ])
    expect(JSON.stringify(await getCachedCrossroadsShowcase())).not.toContain("raw_output")
  })

  it("rejects a malformed editorial timeline", async () => {
    const malformed = { ...showcase, timeline: [...showcase.timeline].reverse() }
    vi.stubGlobal("fetch", vi.fn(async () => response(malformed)))
    await expect(getCachedCrossroadsShowcase()).rejects.toThrow("invalid")
  })
})

describe("Demo trio series", () => {
  it.each(["trio-relay-v0", "trio-free-for-all-v0"] as const)(
    "posts exactly Sol, Luna, and Terra without credentials for %s",
    async (taskId) => {
      const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        void input
        void init
        return response({ series_id: "trio_demo", task_id: taskId, state: "queued" })
      })
      vi.stubGlobal("fetch", fetch)
      const run = await createRun({
        ...scriptedDemoSetup,
        mode: "trio",
        trioTaskId: taskId,
      })
      expect(fetch.mock.calls[0]?.[0]).toBe("/api/embodiment/trio-series")
      const payload = JSON.parse(String(
        (fetch.mock.calls[0] as unknown as [unknown, RequestInit])[1].body
      ))
      expect(payload).toMatchObject({ task_id: taskId, max_provider_calls: 1080 })
      expect(payload.entrants).toEqual(DEMO_TRIO_ENTRANTS.map((entrant) => ({
        provider: "demo",
        model: entrant.model,
      })))
      expect(JSON.stringify(payload)).not.toMatch(/api_key|prompt|credential/)
      expect(run.kind).toBe("trio")
    }
  )

  it("keeps a completed trio successful while its native replay is preparing", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const route = String(input)
      if (route.endsWith("/result")) {
        return response({
          series_id: "trio_demo", task_id: "trio-relay-v0", legs: [],
        })
      }
      if (route.endsWith("/archive")) {
        return response({
          evidence: { state: "saving" },
          native_replay: { state: "saving" },
        })
      }
      return response({
        series_id: "trio_demo", task_id: "trio-relay-v0", state: "completed",
        archive: {
          evidence: { state: "saving" },
          native_replay: { state: "saving" },
        },
      })
    })
    vi.stubGlobal("fetch", fetch)
    const run = await getEpisode("trio_demo")
    expect(run).toMatchObject({
      kind: "trio", status: "success",
      seriesArchive: { evidenceState: "saving", nativeReplayState: "saving" },
    })
    expect(JSON.stringify(run)).not.toMatch(/prompt|raw_output|credential|spectator/)
  })

  it("accepts participant 2 pixels only from the scoped trio frame route", async () => {
    const fetch = vi.fn(async () => new Response(new Uint8Array([137, 80, 78, 71]), {
      headers: {
        "Content-Type": "image/png",
        "X-Content-SHA256": "d".repeat(64),
        "X-Frame-State": "finished",
        "X-Leg-Index": "2",
        "X-Observation-Seq": "19",
        "X-Participant-ID": "participant_2",
      },
    }))
    vi.stubGlobal("fetch", fetch)
    await expect(getSeriesParticipantFrame("trio_demo", "participant_2")).resolves.toMatchObject({
      participantId: "participant_2", legIndex: 2, observationSeq: 19, state: "finished",
    })
    expect(fetch).toHaveBeenCalledWith(
      "/api/embodiment/trio-series/trio_demo/participants/participant_2/frame",
      { cache: "no-store" },
    )
    expect(seriesParticipantVideoUrl("trio_demo", 2, "participant_2")).toBe(
      "/api/embodiment/trio-series/trio_demo/legs/2/participants/participant_2/video"
    )
  })

  it("parses only allow-listed cyclic evaluation fields", async () => {
    const participant = {
      placement: 1, objective_points: 20, damage_dealt: 0, damage_taken: 0,
      decision_windows: 2, fallback_windows: 0, provider_calls: 2,
      suppressed_eliminated_calls: 0, eliminated: false, eliminated_tick: null,
      reliability_per_mille: 1000, entrant_id: "sol",
    }
    const legs = [0, 1, 2].map((leg_index) => ({
      leg_index, completion_tick: 20, terminal_reason: "relay_hold",
      placements: [{ placement: 1, entrant_ids: ["sol"], tied: false }],
      participants: { participant_0: participant, participant_1: participant, participant_2: participant },
    }))
    const entrants = Object.fromEntries(["sol", "luna", "terra"].map((id) => [id, {
      display_name: id[0].toUpperCase() + id.slice(1), objective_points: 60,
      damage: { dealt: 0, taken: 0 }, normalized_per_leg: {},
      reliability: { decision_windows: 6, fallback_ratio_per_mille: 0,
        fallback_windows: 0, provider_calls: 6, stopped_calls_after_elimination: 0 },
    }]))
    vi.stubGlobal("fetch", vi.fn(async () => response({
      schema_version: "trio-game-series-evaluation/1", scope: "trio_game_series",
      protocol_version: "llm-controller/0.3.0", task_id: "trio-relay-v0",
      series: { leg_count: 3, plan_sha256: "e".repeat(64), seat_rotations_complete: true },
      legs, entrants,
      cyclic_normalization: { each_entrant_uses_each_seat_once: true, seat_totals: {}, seat_aggregate_ranges: {} },
      raw_output: "must not survive",
    })))
    const value = await getTrioEvaluation("trio_demo")
    expect(value?.legs).toHaveLength(3)
    expect(value?.entrants.terra.displayName).toBe("Terra")
    expect(value).not.toHaveProperty("raw_output")
  })

  it("projects three participant receipts without protected latency or output", async () => {
    const receipt = (participant: number) => ({
      action_id: `action_${participant}`, disposition: "accepted",
      applied_ticks: 10, codes: ["applied"],
    })
    vi.stubGlobal("fetch", vi.fn(async () => response({
      series_id: "trio_demo",
      events: [],
      legs: [{ leg_index: 0, receipts: [{ observation_seq: 0, participants: {
        participant_0: receipt(0), participant_1: receipt(1), participant_2: receipt(2),
      } }] }],
    })))
    const rows = await getEpisodeTimeline("trio_demo")
    expect(rows).toEqual([expect.objectContaining({
      intent: expect.stringContaining("participant_2: action_2"),
      receipt: expect.stringContaining("participant_2: accepted"),
      ticks: "0–10",
      latencyMs: null,
    })])
    expect(JSON.stringify(rows)).not.toMatch(/prompt|raw_output|credential/)
  })
})

describe("Live Labyrinth Run", () => {
  it("reserves the full 150-turn budget for each private racer", async () => {
    const fetch = vi.fn(async () =>
      response({
        episode_id: "ep_live_labyrinth_test",
        task_id: "trio-maze-race-v1",
        state: "queued",
        entrants: [],
        video: { state: "unavailable" },
      })
    )
    vi.stubGlobal("fetch", fetch)

    await createRun({
      ...scriptedDemoSetup,
      controllerMode: "live_provider",
      mode: "trio",
      apiKey: "session-only-test-key",
      opponentProvider: "openai",
      opponentModel: "gpt-5.6-terra",
      thirdModel: "gpt-5.6-luna",
    })

    expect(fetch.mock.calls[0]?.[0]).toBe("/api/embodiment/maze-races")
    const payload = JSON.parse(
      String((fetch.mock.calls[0] as unknown as [unknown, RequestInit])[1].body)
    )
    expect(payload.max_provider_calls).toBe(450)
    expect(payload.entrants).toEqual([
      { display_name: "Sol", model: "gpt-5.6-sol" },
      { display_name: "Terra", model: "gpt-5.6-terra" },
      { display_name: "Luna", model: "gpt-5.6-luna" },
    ])
  })
})

describe("Demo paired series", () => {
  it("keeps a completed series successful while its native replay is preparing", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const route = String(input)
      if (route.endsWith("/result")) {
        return response({
          series_id: "series_demo", state: "completed", result: { legs: [] },
        })
      }
      return response({
        series_id: "series_demo", state: "completed",
        archive: {
          evidence: { state: "saving" },
          native_replay: { state: "saving" },
        },
      })
    })
    vi.stubGlobal("fetch", fetch)
    const run = await getEpisode("series_demo")
    expect(run).toMatchObject({
      kind: "series", status: "success",
      seriesArchive: { evidenceState: "saving", nativeReplayState: "saving" },
    })
    expect(JSON.stringify(run)).not.toMatch(/prompt|raw_output|credential|spectator/)
  })

  it("posts exactly two keyless deterministic entrants", async () => {
    const fetch = vi.fn(async () => response({
      series_id: "series_demo",
      state: "queued",
      archive: {
        evidence: { state: "pending" },
        native_replay: { state: "unavailable", reason: "participant_video_not_recorded" },
      },
    }))
    vi.stubGlobal("fetch", fetch)
    await createRun({ ...scriptedDemoSetup, mode: "duel" })
    const call = fetch.mock.calls[0] as unknown as [RequestInfo | URL, RequestInit]
    const payload = JSON.parse(String(call[1].body))
    expect(payload.entrants).toEqual([
      { provider: "demo", model: "duelist-alpha-v1" },
      { provider: "demo", model: "duelist-bravo-v1" },
    ])
    expect(JSON.stringify(payload)).not.toContain("api_key")
  })

  it.each([
    ["duo-checkpoint-race-v0", "checkpoint-racer-alpha-v1", "checkpoint-racer-bravo-v1"],
    ["duo-relay-control-v0", "relay-controller-alpha-v1", "relay-controller-bravo-v1"],
    ["duo-spar-v0", "sparring-alpha-v1", "sparring-bravo-v1"],
    ["duo-resource-relay-v0", "resource-relay-alpha-v1", "resource-relay-bravo-v1"],
  ] as const)("posts the selected keyless two-leg game %s", async (taskId, alpha, bravo) => {
    const fetch = vi.fn(async () => response({
      series_id: "series_demo_game",
      task_id: taskId,
      state: "queued",
      archive: {
        evidence: { state: "pending" },
        native_replay: { state: "unavailable", reason: "participant_video_not_recorded" },
      },
    }))
    vi.stubGlobal("fetch", fetch)
    const run = await createRun({ ...scriptedDemoSetup, mode: "duel", duoTaskId: taskId })
    const payload = JSON.parse(String((fetch.mock.calls[0] as unknown as [unknown, RequestInit])[1].body))
    expect(payload.task_id).toBe(taskId)
    expect(payload.entrants).toEqual([
      { provider: "demo", model: alpha },
      { provider: "demo", model: bravo },
    ])
    expect(JSON.stringify(payload)).not.toContain("api_key")
    expect(run.taskLabel).toContain(DEMO_DUO_GAMES[taskId].displayLabel)
  })

  it("accepts only the selected participant's typed PNG frame", async () => {
    const pixels = new Uint8Array([137, 80, 78, 71])
    vi.stubGlobal("fetch", vi.fn(async () => new Response(pixels, {
      headers: {
        "Content-Type": "image/png",
        "X-Content-SHA256": "f".repeat(64),
        "X-Frame-State": "live",
        "X-Leg-Index": "1",
        "X-Observation-Seq": "7",
        "X-Participant-ID": "participant_1",
      },
    })))
    await expect(getSeriesParticipantFrame("series_demo", "participant_1")).resolves.toMatchObject({
      participantId: "participant_1",
      legIndex: 1,
      observationSeq: 7,
      state: "live",
    })
  })

  it("loads exactly two safe authority-derived leg evaluations", async () => {
    const leg = (index: number) => ({
      schema_version: "llm-controller/evaluation-projection/1.0.0",
      projection_sha256: String(index + 1).repeat(64),
      state: "supported",
      scope: "paired_duel_leg",
      run: { episode_id: `ep_leg_${index}`, task_id: "central-relay-v0", certification_eligible: false },
      result: { final_state_hash: "a".repeat(64), provider_failures: 0, windows: 2, terminal: { outcome: "success" } },
      evaluation: { metrics: { task_success: { state: "supported", value: true } } },
      raw_output: "must not survive parsing",
    })
    vi.stubGlobal("fetch", vi.fn(async () => response({
      series_id: "series_demo",
      certification: { eligible: false, reason: "demo_provider" },
      legs: [leg(0), leg(1)],
    })))
    const value = await getSeriesEvaluation("series_demo")
    expect(value?.legs).toHaveLength(2)
    expect(value?.certificationEligible).toBe(false)
    expect(value?.legs[0]).not.toHaveProperty("raw_output")
  })
})

describe("evaluation routes", () => {
  const projection = {
    schema_version: "llm-controller/evaluation-projection/1.0.0",
    projection_sha256: "a".repeat(64),
    state: "supported",
    scope: "solo",
    run: {
      episode_id: "ep_evaluation",
      task_id: "construction-v0",
      scenario_id: "multi-action-demo-v0",
      evaluation_profile_id: "solo-multi-action-showcase-v1",
      certification_eligible: false,
    },
    result: {
      final_state_hash: "b".repeat(64),
      provider_failures: 0,
      windows: 4,
      terminal: { outcome: "success" },
    },
    evaluation: {
      metrics: {
        task_success: { state: "supported", value: true },
        path_efficiency: { state: "unavailable", reason: "not_recorded" },
      },
    },
    prompts: ["must not be projected"],
  }

  it("loads current and saved hash-bound projections without reflecting extra fields", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response(projection)))
    await expect(getEpisodeEvaluation("ep_evaluation")).resolves.toMatchObject({
      projectionSha256: "a".repeat(64),
      run: { scenarioId: "multi-action-demo-v0" },
      metrics: { task_success: { state: "supported", value: true } },
    })
    const saved = await getSavedReplayEvaluation("ep_evaluation")
    expect(saved).not.toHaveProperty("prompts")
  })

  it("treats unsealed current and legacy saved evaluations as unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 409 })))
    await expect(getEpisodeEvaluation("ep_waiting")).resolves.toBeNull()
    await expect(getSavedReplayEvaluation("ep_legacy")).resolves.toBeNull()
  })
})

describe("saved native replay routes", () => {
  const savedReplay = {
    replay_id: "replay_construction_demo",
    episode_id: "ep_construction_demo",
    scenario_id: "construction-v0",
    evaluation_profile_id: "solo-construction-v1",
    task_id: "construction-v0",
    label: "Construction V0 scripted demo",
    outcome: "success",
    authority_ticks: 1194,
    duration_seconds: 39,
    video: {
      available: true,
      mime_type: "video/mp4",
      sha256: "e".repeat(64),
      width: 1280,
      height: 720,
      fps: 30,
    },
    prompts: ["must never reach the dashboard"],
  }

  it("loads only the public saved-replay projection and generates an encoded video route", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url === "/api/embodiment/replays")
        return response({ replays: [savedReplay] })
      return response(savedReplay)
    })
    vi.stubGlobal("fetch", fetch)

    await expect(getSavedReplays()).resolves.toEqual([
      {
        replayId: "replay_construction_demo",
        episodeId: "ep_construction_demo",
        scenarioId: "construction-v0",
        evaluationProfileId: "solo-construction-v1",
        taskId: "construction-v0",
        label: "Construction V0 scripted demo",
        outcome: "success",
        authorityTicks: 1194,
        durationSeconds: 39,
        video: {
          available: true,
          mimeType: "video/mp4",
          width: 1280,
          height: 720,
          fps: 30,
        },
      },
    ])
    await expect(getSavedReplay("ep_construction_demo")).resolves.toMatchObject(
      {
        replayId: "replay_construction_demo",
        video: { available: true, mimeType: "video/mp4" },
      }
    )
    expect(fetch).toHaveBeenNthCalledWith(1, "/api/embodiment/replays", {
      cache: "no-store",
    })
    expect(fetch).toHaveBeenNthCalledWith(
      2,
      "/api/embodiment/replays/ep_construction_demo",
      { cache: "no-store" }
    )
    expect(savedReplayVideoUrl("replay construction/demo")).toBe(
      "/api/embodiment/replays/replay%20construction%2Fdemo/video"
    )
  })

  it("treats a missing archive as a normal pending state while native export is running", async () => {
    const fetch = vi.fn(async () => new Response(null, { status: 404 }))
    vi.stubGlobal("fetch", fetch)

    await expect(getSavedReplay("ep_still_saving")).resolves.toBeNull()
    expect(fetch).toHaveBeenCalledWith(
      "/api/embodiment/replays/ep_still_saving",
      { cache: "no-store" }
    )
  })

  it.each([
    { scenario_id: "unknown-v0" },
    { evaluation_profile_id: "solo-orientation-v1" },
  ])("rejects a saved replay with mismatched safe identity: %o", async (override) => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ ...savedReplay, ...override })))
    await expect(getSavedReplay("ep_construction_demo")).rejects.toThrow(
      "Saved replay is invalid"
    )
  })
})

describe("certification readiness", () => {
  it("requests the no-store public readiness projection", async () => {
    const fetch = vi.fn(
      async () =>
        ({
          ok: true,
          json: async () => ({
            format: "llm-controller/embodiment-readiness-view/1.1.0",
          }),
        }) as Response
    )
    vi.stubGlobal("fetch", fetch)
    await getReadiness()
    expect(fetch).toHaveBeenCalledWith(
      "/api/embodiment/certification/readiness",
      { cache: "no-store" }
    )
  })
})

describe("participant frame route", () => {
  it("loads only a no-store image response and its player-visible sequence", async () => {
    const png = new Uint8Array([137, 80, 78, 71])
    const fetch = vi.fn(async () => new Response(png, {
      headers: {
        "Content-Type": "image/png",
        "X-Content-SHA256": "a".repeat(64),
        "X-Frame-State": "live",
        "X-Observation-Seq": "3",
      },
    }))
    vi.stubGlobal("fetch", fetch)

    const frame = await getParticipantFrame("ep_visible")

    expect(fetch).toHaveBeenCalledWith(
      "/api/embodiment/episodes/ep_visible/frame",
      { cache: "no-store" }
    )
    expect(frame).toMatchObject({
      observationSeq: 3,
      sha256: "a".repeat(64),
      state: "live",
    })
    expect(frame.blob?.type).toBe("image/png")
  })
})

describe("run cancellation", () => {
  it("uses the scoped no-store cancellation route for episodes and series", async () => {
    const fetch = vi.fn(
      async () =>
        ({
          ok: true,
          json: async () => ({ state: "cancelled" }),
        }) as Response
    )
    vi.stubGlobal("fetch", fetch)

    await cancelRun("ep_live")
    await cancelRun("series_live")
    await cancelRun("trio_live")

    expect(fetch).toHaveBeenNthCalledWith(
      1,
      "/api/embodiment/episodes/ep_live/cancel",
      {
        method: "POST",
        cache: "no-store",
      }
    )
    expect(fetch).toHaveBeenNthCalledWith(
      2,
      "/api/embodiment/series/series_live/cancel",
      {
        method: "POST",
        cache: "no-store",
      }
    )
    expect(fetch).toHaveBeenNthCalledWith(
      3,
      "/api/embodiment/trio-series/trio_live/cancel",
      { method: "POST", cache: "no-store" }
    )
  })
})

describe("scripted solo demos", () => {
  it.each([
    ["orientation-v0", "orientation-demo-v1"],
    ["interaction-v0", "interaction-demo-v1"],
    ["construction-v0", "construction-demo-v1"],
    ["neutral-encounter-v0", "neutral-encounter-demo-v1"],
  ] as const)("uses the credential-free scripted controller for %s", async (scenarioId, model) => {
    let requestBody = ""
    const fetch = vi.fn(
      async (_input: RequestInfo | URL, init?: RequestInit) => {
        requestBody = String(init?.body)
        return {
          ok: true,
          json: async () => ({
            config: {},
            episode_id: "ep_demo",
            state: "queued",
          }),
        } as Response
      }
    )
    vi.stubGlobal("fetch", fetch)

    await createEpisode({ ...scriptedDemoSetup, scenarioId })

    const body = JSON.parse(requestBody)
    expect(body).toMatchObject({
      provider: "demo",
      model,
      scenario_id: scenarioId,
      task_id: scenarioId,
      seed: 20240520,
      maximum_episode_ticks: 600,
    })
    expect(body).not.toHaveProperty("api_key")
  })

  it("maps the multi-action scenario to Construction authority without changing its identity", async () => {
    let requestBody = ""
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      requestBody = String(init?.body)
      return response({ config: {}, episode_id: "ep_showcase", state: "queued" })
    }))

    await createEpisode({ ...scriptedDemoSetup, scenarioId: "multi-action-demo-v0" })

    expect(JSON.parse(requestBody)).toMatchObject({
      provider: "demo",
      model: "construction-demo-v1",
      scenario_id: "multi-action-demo-v0",
      task_id: "construction-v0",
      maximum_episode_ticks: 1300,
    })
  })

  it.each(["central-relay-v0", "toString"])("rejects demo scenario %s before making a request", async (scenarioId) => {
    const fetch = vi.fn()
    vi.stubGlobal("fetch", fetch)

    await expect(
      createEpisode({ ...scriptedDemoSetup, scenarioId })
    ).rejects.toThrow("Demo provider scenario is invalid")
    expect(fetch).not.toHaveBeenCalled()
  })
})

describe("episode lifecycle loading", () => {
  it("loads status without repeatedly loading the full public timeline", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/embodiment/episodes/ep_live")
      return response({
        config: {
          task_id: "construction-v0",
          scenario_id: "multi-action-demo-v0",
          evaluation_profile_id: "solo-multi-action-showcase-v1",
        },
        episode_id: "ep_live",
        progress: { authority_tick: 37, observation_seq: 37 },
        state: "running",
      })
    })
    vi.stubGlobal("fetch", fetch)

    const episode = await getEpisode("ep_live")

    expect(episode.timeline).toEqual([])
    expect(episode).toMatchObject({
      observationSeq: 37,
      tick: 37,
      taskLabel: "Multi-action solo showcase",
      scenarioId: "multi-action-demo-v0",
      evaluationProfileId: "solo-multi-action-showcase-v1",
    })
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it("loads and parses public receipts only when the timeline is requested", async () => {
    const fetch = vi.fn(async () =>
      response({
        events: [
          {
            kind: "action_receipts",
            value: {
              observation_seq: 12,
              participants: {
                participant_0: {
                  action_id: "task_build_barricade_12",
                  codes: ["accepted"],
                  disposition: "accepted",
                  start_tick: 12,
                  end_tick: 13,
                },
              },
            },
          },
        ],
      })
    )
    vi.stubGlobal("fetch", fetch)

    const rows = await getEpisodeTimeline("ep_live")

    expect(fetch).toHaveBeenCalledWith(
      "/api/embodiment/episodes/ep_live/timeline",
      { cache: "no-store" }
    )
    expect(rows).toEqual([
      {
        window: 12,
        intent: "task_build_barricade_12",
        receipt: "accepted",
        ticks: "12–13",
        latencyMs: null,
      },
    ])
  })

  it("retains the safe replay-export state from lifecycle status after it loads the sealed result", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith("/result")) {
        return response({
          config: { task_id: "construction-v0" },
          episode_id: "ep_saving_replay",
          result: { windows: 1194, terminal: { outcome: "success" } },
          state: "completed",
        })
      }
      return response({
        config: { task_id: "construction-v0" },
        episode_id: "ep_saving_replay",
        replay: { state: "saving" },
        state: "completed",
      })
    })
    vi.stubGlobal("fetch", fetch)

    await expect(getEpisode("ep_saving_replay")).resolves.toMatchObject({
      replay: { state: "saving" },
      status: "success",
    })
  })
})
