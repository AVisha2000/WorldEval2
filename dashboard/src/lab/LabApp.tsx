import { useCallback, useEffect, useMemo, useState } from "react"
import type { CachedMazeShowcaseView } from "@/api"
import { getCachedMazeShowcase } from "@/api"
import {
  cloneLabRun,
  connectLocalLabSession,
  getLabAuthMode,
  getLabGames,
  getLabRun,
  getLabRunProjection,
  getLabRunSpectator,
  getLabRuns,
  getLabSession,
  getLabyrinthBenchmark,
  getPublicGameBenchmark,
  getPublicLabGames,
  getPublicLabGame,
  launchLabRunDraft,
  launchLabyrinth,
  requestLabMagicLink,
} from "./lab-api"
import {
  BenchmarksPanel,
  GameGuide,
  LabNavigation,
  ModelsPanel,
  PublicGamePage,
  RunComposer,
  RunsPanel,
  SimulationStage,
  type LabView,
} from "./lab-content"
import type {
  LabBenchmark,
  LabAuthMode,
  LabGame,
  LabLaunchInput,
  LabProjection,
  LabRun,
  LabSession,
  LabSpectatorFeed,
  RemoteState,
} from "./types"
import "./lab.css"

const loading = <T,>(): RemoteState<T> => ({ kind: "loading", data: null })
const offline = <T,>(): RemoteState<T> => ({ kind: "offline", data: null })
const ready = <T,>(data: T): RemoteState<T> => ({ kind: "ready", data })

type ActiveLabRun = {
  run: LabRun
  projection: LabProjection
}

function mergeSpectatorFeed(
  current: LabSpectatorFeed | null,
  incoming: LabSpectatorFeed
): LabSpectatorFeed {
  const retained =
    !incoming.resetRequired && current?.runId === incoming.runId
      ? current.frames
      : []
  const frames = new Map(retained.map((frame) => [frame.sequence, frame]))
  for (const frame of incoming.frames) frames.set(frame.sequence, frame)
  return {
    ...incoming,
    frames: [...frames.values()].sort((first, second) => first.sequence - second.sequence).slice(-128),
  }
}

export function LabApp() {
  const [view, setView] = useState<LabView>("lab")
  const [selectedGameId, setSelectedGameId] = useState("labyrinth-run")
  const [games, setGames] = useState<RemoteState<LabGame[]>>(loading)
  const [runs, setRuns] = useState<RemoteState<LabRun[]>>(loading)
  const [benchmark, setBenchmark] = useState<RemoteState<LabBenchmark>>(loading)
  const [showcase, setShowcase] =
    useState<RemoteState<CachedMazeShowcaseView>>(loading)
  const [session, setSession] =
    useState<RemoteState<LabSession | null>>(loading)
  const [authMode, setAuthMode] = useState<RemoteState<LabAuthMode>>(loading)
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const [activeRun, setActiveRun] = useState<RemoteState<ActiveLabRun | null>>(
    ready(null)
  )
  const [spectator, setSpectator] = useState<
    RemoteState<LabSpectatorFeed | null>
  >(ready(null))
  const [draftLaunch, setDraftLaunch] = useState<LabRun | null>(null)
  const activeLifecycle =
    activeRun.kind === "ready" && activeRun.data
      ? activeRun.data.run.lifecycle
      : null
  const activeAuthorityUnavailable =
    activeRun.kind === "ready" && activeRun.data
      ? activeRun.data.run.authorityAvailable === false
      : false
  const selectedGame = useMemo(
    () =>
      games.kind === "ready"
        ? (games.data.find((game) => game.id === selectedGameId) ?? null)
        : null,
    [games, selectedGameId]
  )

  const refresh = useCallback(async () => {
    const [
      gameResult,
      runResult,
      benchmarkResult,
      showcaseResult,
      sessionResult,
      authModeResult,
    ] = await Promise.allSettled([
      getLabGames().catch(() => getPublicLabGames()),
      getLabRuns(),
      getLabyrinthBenchmark().catch(() =>
        getPublicGameBenchmark("labyrinth-run")
      ),
      getCachedMazeShowcase(),
      getLabSession(),
      getLabAuthMode(),
    ])
    setGames(
      gameResult.status === "fulfilled" ? ready(gameResult.value) : offline()
    )
    setRuns(
      runResult.status === "fulfilled" ? ready(runResult.value) : offline()
    )
    setBenchmark(
      benchmarkResult.status === "fulfilled"
        ? ready(benchmarkResult.value)
        : offline()
    )
    setShowcase(
      showcaseResult.status === "fulfilled"
        ? ready(showcaseResult.value)
        : offline()
    )
    setSession(
      sessionResult.status === "fulfilled"
        ? ready(sessionResult.value)
        : offline()
    )
    setAuthMode(
      authModeResult.status === "fulfilled"
        ? ready(authModeResult.value)
        : offline()
    )
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refresh()
    }, 0)
    return () => window.clearTimeout(timer)
  }, [refresh])

  useEffect(() => {
    if (!activeRunId) {
      return
    }
    let cancelled = false
    let timer: number | undefined
    let completedVideoChecks = 0

    const poll = async () => {
      try {
        const [run, projection] = await Promise.all([
          getLabRun(activeRunId),
          getLabRunProjection(activeRunId),
        ])
        if (cancelled) return
        setActiveRun(ready({ run, projection }))
        setRuns((current) => {
          if (current.kind !== "ready") return current
          const found = current.data.some((item) => item.id === run.id)
          return ready(
            found
              ? current.data.map((item) => (item.id === run.id ? run : item))
              : [run, ...current.data]
          )
        })
        const inFlight =
          (run.lifecycle === "queued" || run.lifecycle === "running") &&
          run.authorityAvailable !== false
        const waitingForVideo =
          run.lifecycle === "completed" &&
          !run.videoAvailable &&
          completedVideoChecks++ < 120
        if (inFlight || waitingForVideo) {
          timer = window.setTimeout(() => void poll(), 800)
        }
      } catch {
        if (!cancelled) setActiveRun(offline())
      }
    }
    void poll()
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [activeRunId])

  useEffect(() => {
    if (!activeRunId || activeAuthorityUnavailable) return
    let cancelled = false
    let timer: number | undefined
    let cursor = 0
    let terminalEmptyPolls = 0
    const terminal = ["completed", "failed", "cancelled"].includes(
      activeLifecycle ?? ""
    )
    const poll = async () => {
      try {
        const feed = await getLabRunSpectator(activeRunId, cursor)
        if (cancelled) return
        cursor = feed.cursor
        terminalEmptyPolls = feed.frames.length ? 0 : terminalEmptyPolls + 1
        setSpectator((current) =>
          ready(mergeSpectatorFeed(current.kind === "ready" ? current.data : null, feed))
        )
      } catch {
        if (!cancelled) setSpectator(offline())
      }
      if (!cancelled && (!terminal || terminalEmptyPolls < 2)) {
        timer = window.setTimeout(() => void poll(), 400)
      }
    }
    void poll()
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [activeAuthorityUnavailable, activeLifecycle, activeRunId])

  function showGame(gameId: string) {
    setSelectedGameId(gameId)
    setView("games")
  }

  async function startLabyrinth(input: LabLaunchInput): Promise<string | null> {
    if (session.kind !== "ready" || !session.data)
      throw new Error("No local Lab session")
    const runId = await launchLabyrinth(input, session.data.csrfToken)
    if (runId) {
      setDraftLaunch(null)
      setActiveRun(loading())
      setSpectator(loading())
      setActiveRunId(runId)
      setView("lab")
    }
    await refresh()
    return runId
  }

  async function startDraftLabyrinth(
    draft: LabRun,
    apiKey: string
  ): Promise<string | null> {
    if (
      session.kind !== "ready" ||
      !session.data ||
      draft.lifecycle !== "draft" ||
      draft.provider !== "openai" ||
      !draft.contractSha256 ||
      !draft.parentContractSha256
    ) {
      throw new Error("Frozen clone is not launchable")
    }

    // Check the authority-owned draft immediately before sending a new key. This
    // stops a stale tab from turning an already-launched clone into another run.
    const current = await getLabRun(draft.id)
    if (
      current.lifecycle !== "draft" ||
      current.provider !== "openai" ||
      current.contractSha256 !== draft.contractSha256 ||
      !current.parentContractSha256
    ) {
      throw new Error("Frozen clone is no longer launchable")
    }

    const launched = await launchLabRunDraft(
      draft.id,
      apiKey,
      session.data.csrfToken
    )
    setDraftLaunch(null)
    setActiveRun(loading())
    setSpectator(loading())
    setActiveRunId(launched.id)
    setView("lab")
    setRuns((existing) => {
      if (existing.kind !== "ready") return existing
      return ready(
        existing.data.map((run) => (run.id === launched.id ? launched : run))
      )
    })
    return launched.id
  }

  async function connectSession(): Promise<void> {
    setSession(loading())
    try {
      setSession(ready(await connectLocalLabSession()))
      await refresh()
    } catch (error) {
      setSession(offline())
      throw error
    }
  }

  async function requestMagicLink(email: string): Promise<void> {
    await requestLabMagicLink(email)
  }

  function openDraft(run: LabRun) {
    setActiveRunId(null)
    setActiveRun(ready(null))
    setSpectator(ready(null))
    setDraftLaunch(run)
    setView("lab")
  }

  async function cloneRun(source: LabRun): Promise<void> {
    if (session.kind !== "ready" || !session.data)
      throw new Error("No local Lab session")
    const draft = await cloneLabRun(source.id, session.data.csrfToken)
    setRuns((existing) => {
      if (existing.kind !== "ready") return existing
      return ready([draft, ...existing.data.filter((run) => run.id !== draft.id)])
    })
    openDraft(draft)
  }

  function openRun(run: LabRun) {
    if (run.lifecycle === "draft") {
      openDraft(run)
      return
    }
    setActiveRun(loading())
    setSpectator(loading())
    setDraftLaunch(null)
    setActiveRunId(run.id)
    setView("lab")
  }

  return (
    <div className="lab-app">
      <LabNavigation activeView={view} onSelect={setView} />
      <main className="lab-main">
        <header className="lab-topbar">
          <div>
            <span>WorldEval Lab</span>
            <p>Simulation workspace</p>
          </div>
          <div className="lab-topbar-session">
            <span>
              {session.kind === "ready" && session.data
                ? session.data.operator
                : "No Lab session"}
            </span>
            <i
              className={
                session.kind === "ready" && session.data ? "is-connected" : ""
              }
              aria-hidden="true"
            />
          </div>
        </header>
        <div className="lab-main-scroll">
          {view === "lab" ? (
            <SimulationStage
              activeRun={activeRun}
              games={games}
              onSelectGame={showGame}
              showcase={showcase.kind === "ready" ? showcase.data : null}
              showcaseState={showcase}
              spectator={spectator}
            />
          ) : null}
          {view === "games" ? (
            <GameGuide
              game={selectedGame}
              games={games}
              onSelectGame={showGame}
            />
          ) : null}
          {view === "runs" ? (
            <RunsPanel
              cloneEnabled={session.kind === "ready" && session.data !== null}
              onClone={cloneRun}
              onOpen={openRun}
              runs={runs}
            />
          ) : null}
          {view === "benchmarks" ? (
            <BenchmarksPanel benchmark={benchmark} />
          ) : null}
          {view === "models" ? <ModelsPanel benchmark={benchmark} /> : null}
        </div>
      </main>
      <RunComposer
        authMode={authMode}
        draft={draftLaunch}
        key={draftLaunch?.id ?? "fresh-labyrinth-run"}
        onConnect={connectSession}
        onLaunchDraft={startDraftLabyrinth}
        onLaunch={startLabyrinth}
        onRequestMagicLink={requestMagicLink}
        session={session}
      />
    </div>
  )
}

export function PublicLabApp({ gameId }: { gameId: string }) {
  const [game, setGame] = useState<RemoteState<LabGame>>(loading)
  const [benchmark, setBenchmark] = useState<RemoteState<LabBenchmark>>(loading)

  useEffect(() => {
    let active = true
    void Promise.allSettled([
      getPublicLabGame(gameId),
      getPublicGameBenchmark(gameId),
    ]).then(([gameResult, benchmarkResult]) => {
      if (!active) return
      setGame(
        gameResult.status === "fulfilled" ? ready(gameResult.value) : offline()
      )
      setBenchmark(
        benchmarkResult.status === "fulfilled"
          ? ready(benchmarkResult.value)
          : offline()
      )
    })
    return () => {
      active = false
    }
  }, [gameId])

  return (
    <div className="lab-public-shell">
      <header className="lab-public-topbar">
        <a href="/share">WorldEval</a>
        <span>Unlisted game guide</span>
      </header>
      <main>
        <PublicGamePage benchmark={benchmark} game={game} />
      </main>
    </div>
  )
}

export function PublicShareLanding() {
  return (
    <div className="lab-public-shell">
      <header className="lab-public-topbar">
        <span className="lab-public-wordmark">WorldEval</span>
        <span>Public game guides</span>
      </header>
      <main className="lab-public-landing">
        <p>Evidence-led game field guides</p>
        <h1>See what an agent is actually being tested on.</h1>
        <span>
          These unlisted pages are read-only. They can contain published guide
          material, safe replays, and verified benchmark evidence—but never API
          keys, live controls, raw model output, private observations, or agent
          memory.
        </span>
        <a href="/share/games/labyrinth-run">Open Labyrinth Run</a>
      </main>
    </div>
  )
}
