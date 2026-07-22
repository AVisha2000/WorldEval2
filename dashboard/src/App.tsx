import { useRef, useState } from "react"
import type { CSSProperties } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import type {
  CachedMazeEvaluationView,
  CachedMazeShowcaseView,
  CachedRtsEvaluationView,
  CachedRtsShowcaseView,
  CachedSoloShowcaseView,
  EpisodeSetup,
} from "@/api"
import {
  getCachedCrossroadsEvaluation,
  getCachedCrossroadsShowcase,
  cachedMazeVideoUrl,
  cachedRtsVideoUrl,
  cancelRun,
  createRun,
  getCachedMazeEvaluation,
  getCachedMazeShowcase,
  getCachedRtsEvaluation,
  getCachedRtsShowcase,
  getCachedSoloShowcase,
  getEpisode,
  getEpisodeTimeline,
} from "@/api"
import worldArenaThumbnail from "@/assets/worldarena-build-things-thumbnail.jpg"
import { CrossroadsWorkspace } from "@/components/crossroads-workspace"
import { EpisodeWorkspace, ReplaySpeedControls } from "@/components/episode-workspace"
import { SetupPanel } from "@/components/setup-panel"
import { SoloShowcaseWorkspace } from "@/components/solo-showcase-workspace"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"

const INITIAL_SETUP: EpisodeSetup = {
  controllerMode: "scripted_demo",
  mode: "solo",
  provider: "openai",
  model: "gpt-5.6-sol",
  apiKey: "",
  opponentProvider: "scripted",
  opponentModel: "balanced-v1",
  opponentApiKey: "",
  mazeVisionRange: 4,
  scenarioId: "construction-v0",
  taskId: "construction-v0",
  seed: 20240520,
}

type CachedMazeWorkspaceProps = {
  evaluation?: CachedMazeEvaluationView
  evaluationError: boolean
  showcase?: CachedMazeShowcaseView
  showcaseError: boolean
  view: string
}

function CachedMazeWorkspace({
  evaluation,
  evaluationError,
  showcase,
  showcaseError,
  view,
}: CachedMazeWorkspaceProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const winner = showcase?.winner.displayName ?? "Sol"
  const participantById = new Map(showcase?.entrants.map((entrant) => [entrant.participantId, entrant]))
  return (
    <section className="episode-workspace rts-showcase maze-showcase" aria-label="Cached Trio Maze Race showcase">
      <div className="episode-workspace-header">
        <div>
          <p className="eyebrow">Second featured highlight · cached deterministic replay</p>
          <h2>WorldArena: Labyrinth Run</h2>
          <p>{showcase?.tagline ?? "Three agents. The same maze. No shared vision. One memory-limited race."}</p>
        </div>
        <span className="status-badge success">{winner} wins</span>
      </div>
      <div className="maze-model-key" aria-label="Model colour key">
        <strong>Model key</strong>
        {showcase?.entrants.map((entrant) => (
          <span key={entrant.participantId} style={{ "--entrant-color": entrant.color } as CSSProperties}>
            <i aria-hidden="true" /> <b>{entrant.displayName}</b> · {entrant.model}
          </span>
        )) ?? <span>Loading Sol, Luna, and Terra…</span>}
      </div>
      {showcaseError ? <p role="alert" className="error-banner">The saved Labyrinth Run package is unavailable or failed verification.</p> : null}
      {view === "run" ? <>
        <video ref={videoRef} className="rts-showcase-video" controls autoPlay muted playsInline src={cachedMazeVideoUrl()}>
          Your browser cannot play the saved Labyrinth Run video.
        </video>
        <ReplaySpeedControls videoRef={videoRef} />
        <p className="muted-copy">{showcase
          ? `${showcase.video.durationSeconds}-second native Godot broadcast · ${showcase.video.width}×${showcase.video.height} · ${showcase.video.fps} FPS`
          : "Loading verified video metadata…"}</p>
      </> : null}
      {view === "timeline" ? <div className="rts-showcase-panel" aria-label="Labyrinth Run timeline">
        <h3>Safe race timeline</h3>
        <p>Only public junction, dead-end, backtrack, and finish summaries are shown.</p>
        {showcase?.timeline.map((event) => {
          const entrant = event.participantId === "broadcast" ? undefined : participantById.get(event.participantId)
          return <div className="rts-showcase-row" key={event.atSeconds}>
            <time>{formatTime(event.atSeconds)}</time>
            <span>{entrant ? <b style={{ color: entrant.color }}>{entrant.displayName} · </b> : null}{event.label}</span>
          </div>
        }) ?? <p className="muted-copy">Loading safe race events…</p>}
      </div> : null}
      {view === "result" ? <div className="rts-showcase-panel maze-winner-card" aria-label="Labyrinth Run result">
        <p className="eyebrow">Winner calling card</p>
        <h3>{winner} wins the Labyrinth Run</h3>
        <p>{showcase?.result.explanation ?? "Loading the verified finish rationale…"}</p>
        <div className="maze-podium">
          {evaluation?.participants.map((participant) => <article key={participant.participantId} style={{ "--entrant-color": participant.color } as CSSProperties}>
            <span>#{participant.place}</span><strong>{participant.displayName}</strong>
            <small>{participant.completionSeconds}s · {participant.distanceCells} cells</small>
          </article>) ?? <p className="muted-copy">Open Evaluation once to load the detailed podium metrics.</p>}
        </div>
      </div> : null}
      {view === "evaluation" ? <div className="rts-showcase-panel" aria-label="Labyrinth Run evaluation">
        <h3>Spatial-reasoning evaluation</h3>
        <p>{evaluation?.summary ?? "Loading authority-derived metrics…"}</p>
        {evaluationError ? <p role="alert" className="error-banner">The public maze evaluation is unavailable.</p> : null}
        <div className="maze-metrics-grid">
          {evaluation?.participants.map((participant) => <article key={participant.participantId} style={{ "--entrant-color": participant.color } as CSSProperties}>
            <header><span>#{participant.place}</span><div><strong>{participant.displayName}</strong><small>{participant.model}</small></div></header>
            <dl>
              <div><dt>Finish</dt><dd>{participant.completionSeconds}s</dd></div>
              <div><dt>Path efficiency</dt><dd>{(participant.pathEfficiencyBasisPoints / 100).toFixed(2)}%</dd></div>
              <div><dt>Distance</dt><dd>{participant.distanceCells} / {participant.shortestPathCells} cells</dd></div>
              <div><dt>Unique / repeated</dt><dd>{participant.uniqueCorridorCells} / {participant.repeatedCorridorCells}</dd></div>
              <div><dt>Passages / dead ends</dt><dd>{participant.passagesExplored} / {participant.deadEndsEntered}</dd></div>
              <div><dt>Backtracks</dt><dd>{participant.successfulBacktracks}</dd></div>
              <div><dt>Collisions / invalid</dt><dd>{participant.collisions} / {participant.invalidDecisions}</dd></div>
              <div><dt>Thinking ticks</dt><dd>{participant.idleThinkingTicks}</dd></div>
            </dl>
          </article>) ?? null}
        </div>
      </div> : null}
      {view === "replay" ? <div className="rts-showcase-panel" aria-label="Labyrinth Run replay verification">
        <h3>Deterministic replay identity</h3>
        <p>The private scratchpads and raw provider decisions stay server-side. The public video is bound to the fixed map, replay, final state, and evaluation hashes.</p>
        {evaluation?.verification.state === "verified" ? <>
          <div className="rts-showcase-metric"><strong>Verification</strong><span>Python package and Godot authority agree on the podium and metrics</span></div>
          <div className="rts-showcase-metric"><strong>Map identity</strong><span className="code-block">{evaluation.verification.mapSha256}</span></div>
          <div className="rts-showcase-metric"><strong>Final state</strong><span className="code-block">{evaluation.verification.finalStateSha256}</span></div>
        </> : <p className="muted-copy">Loading replay verification status…</p>}
      </div> : null}
    </section>
  )
}

/** A reliable, participant-visible first run. It deliberately stays on the no-key Demo path. */
const QUICK_START_SETUP: EpisodeSetup = {
  ...INITIAL_SETUP,
  controllerMode: "scripted_demo",
  mode: "solo",
  scenarioId: "multi-action-demo-v0",
  taskId: "construction-v0",
}

/** The Central Relay scene has the established, highly visible Alpha/Bravo entrant palette. */
const TEAM_QUICK_START_SETUP: EpisodeSetup = {
  ...INITIAL_SETUP,
  controllerMode: "scripted_demo",
  mode: "duel",
  duoTaskId: "central-relay-v0",
}

type CachedRtsWorkspaceProps = {
  evaluation?: CachedRtsEvaluationView
  evaluationError: boolean
  showcase?: CachedRtsShowcaseView
  showcaseError: boolean
  view: string
}

function CachedRtsWorkspace({
  evaluation,
  evaluationError,
  showcase,
  showcaseError,
  view,
}: CachedRtsWorkspaceProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const winner = showcase?.winner.team ?? "Terra"
  const completion = showcase?.completion
  return (
    <section className="episode-workspace rts-showcase" aria-label="Cached RTS skirmish showcase">
      <div className="episode-workspace-header">
        <div>
          <p className="eyebrow">Cached deterministic replay</p>
          <h2>{winner} vs Luna</h2>
          <p>
            {showcase?.label ?? "WorldArena: Mini RTS"} is a saved authority-verified demo.
            It never starts a provider call or simulation in this dashboard.
          </p>
        </div>
        <span className="status-badge success">{completion?.reason.replaceAll("_", " ") ?? "Loading result"}</span>
      </div>
      {showcaseError ? <p role="alert" className="error-banner">The saved RTS package is unavailable or failed verification.</p> : null}
      {view === "run" ? (
        <>
          <video ref={videoRef} className="rts-showcase-video" controls autoPlay muted playsInline src={cachedRtsVideoUrl()}>
            Your browser cannot play the saved RTS demo video.
          </video>
          <ReplaySpeedControls videoRef={videoRef} />
          <p className="muted-copy">
            {showcase
              ? `${showcase.video.durationSeconds}-second cached broadcast · ${showcase.video.width}×${showcase.video.height} · ${showcase.video.fps} FPS`
              : "Loading verified video metadata…"}
          </p>
          <p className="muted-copy">
            Terra and Luna are credential-free deterministic Demo agents using the structured task path for later LLM controllers. On-screen labels are safe task summaries, not hidden chain-of-thought, prompts, or raw output.
          </p>
        </>
      ) : null}
      {view === "timeline" ? (
        <div className="rts-showcase-panel" aria-label="Cached RTS cinematic timeline">
          <h3>Cinematic timeline</h3>
          {showcase?.highlights.map((highlight) => (
            <div className="rts-showcase-row" key={highlight.atSeconds}>
              <time>{formatTime(highlight.atSeconds)}</time><span>{highlight.label}</span>
            </div>
          )) ?? <p className="muted-copy">Loading the saved story beats…</p>}
        </div>
      ) : null}
      {view === "result" ? (
        <div className="rts-showcase-panel" aria-label="Cached RTS result">
          <h3>{winner} wins</h3>
          <p>{completion ? `${formatReason(completion.reason)} at authority tick ${completion.tick}.` : "Loading the sealed match result…"}</p>
          <h4>Casualty sequence</h4>
          {showcase?.casualties.length ? showcase.casualties.map((casualty) => (
            <div className="rts-showcase-row" key={casualty.unitId}>
              <time>Tick {casualty.atTick}</time><span>{casualty.team} · {casualty.unitId}</span>
            </div>
          )) : <p className="muted-copy">No public casualty milestones are available in this cached package.</p>}
        </div>
      ) : null}
      {view === "evaluation" ? (
        <div className="rts-showcase-panel" aria-label="Cached RTS evaluation">
          <h3>Verified evaluation</h3>
          {evaluationError ? <p role="alert" className="error-banner">The public RTS evaluation is unavailable.</p> : null}
          {evaluation?.metrics.map((metric) => (
            <div className="rts-showcase-metric" key={metric.id}>
              <strong>{metric.label}</strong><span>{metric.value}</span>
            </div>
          )) ?? <p className="muted-copy">Loading pre-approved evaluation metrics…</p>}
        </div>
      ) : null}
      {view === "replay" ? (
        <div className="rts-showcase-panel" aria-label="Cached RTS replay verification">
          <h3>Replay identity</h3>
          <p>The authority replay remains server-side. This public broadcast is bound to the verified cached package.</p>
          {evaluation?.verification.state === "verified" ? (
            <div className="rts-showcase-metric">
              <strong>Verification</strong><span>Manifest, replay, final state, and video hashes verified</span>
            </div>
          ) : <p className="muted-copy">Loading replay verification status…</p>}
        </div>
      ) : null}
    </section>
  )
}

function formatTime(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`
}

function formatReason(reason: string): string {
  return reason.replaceAll("_", " ")
}

export function App() {
  const [setup, setSetup] = useState(INITIAL_SETUP)
  const submittedSetup = useRef<EpisodeSetup | null>(null)
  const [episodeId, setEpisodeId] = useState<string | null>(null)
  const [showcase, setShowcase] = useState<"solo" | "rts" | "maze" | "crossroads" | null>(null)
  const [selected, setSelected] = useState(0)
  const [view, setView] = useState("run")
  const create = useMutation({
    mutationFn: () => {
      const submitted = submittedSetup.current
      if (submitted === null) throw new Error("Episode setup is unavailable")
      return createRun(submitted)
    },
    onSuccess: (episode) => {
      setEpisodeId(episode.episodeId)
      setView("run")
    },
    onSettled: () => {
      submittedSetup.current = null
      setSetup((current) => ({ ...current, apiKey: "", opponentApiKey: "" }))
    },
  })
  const start = (next: EpisodeSetup) => {
    if (create.isPending) return
    setSetup(next)
    setShowcase(null)
    submittedSetup.current = next
    create.mutate()
  }
  const status = useQuery({
    queryKey: ["episode", episodeId],
    queryFn: () => getEpisode(episodeId!),
    enabled: episodeId !== null,
    refetchInterval: (query) => query.state.data === undefined
      || ["queued", "running"].includes(query.state.data.status)
      ? 1000
      : false,
    refetchIntervalInBackground: true,
  })
  const timeline = useQuery({
    queryKey: ["episode-timeline", episodeId],
    queryFn: () => getEpisodeTimeline(episodeId!),
    enabled: episodeId !== null && view === "timeline",
    refetchInterval: () => status.data === undefined || ["queued", "running"].includes(status.data.status)
      ? 1000
      : false,
    refetchIntervalInBackground: true,
  })
  const cancel = useMutation({
    mutationFn: () => {
      if (episodeId === null) throw new Error("Run identifier is unavailable")
      return cancelRun(episodeId)
    },
    onSuccess: () => status.refetch(),
  })
  const cachedShowcase = useQuery({
    queryKey: ["cached-rts-showcase"],
    queryFn: getCachedRtsShowcase,
    enabled: showcase === "rts",
    staleTime: 60 * 60 * 1000,
    retry: false,
  })
  const cachedEvaluation = useQuery({
    queryKey: ["cached-rts-evaluation"],
    queryFn: getCachedRtsEvaluation,
    enabled: showcase === "rts" && (view === "evaluation" || view === "replay"),
    staleTime: 60 * 60 * 1000,
    retry: false,
  })
  const cachedSoloShowcase = useQuery<CachedSoloShowcaseView>({
    queryKey: ["cached-solo-showcase"],
    queryFn: getCachedSoloShowcase,
    enabled: showcase === "solo",
    staleTime: 60 * 60 * 1000,
    retry: false,
  })
  const cachedMazeShowcase = useQuery({
    queryKey: ["cached-maze-showcase"],
    queryFn: getCachedMazeShowcase,
    enabled: showcase === "maze",
    staleTime: 60 * 60 * 1000,
    retry: false,
  })
  const cachedMazeEvaluation = useQuery({
    queryKey: ["cached-maze-evaluation"],
    queryFn: getCachedMazeEvaluation,
    enabled: showcase === "maze" && ["result", "evaluation", "replay"].includes(view),
    staleTime: 60 * 60 * 1000,
    retry: false,
  })
  const cachedCrossroadsShowcase = useQuery({
    queryKey: ["cached-crossroads-showcase"],
    queryFn: getCachedCrossroadsShowcase,
    enabled: showcase === "crossroads",
    staleTime: 60 * 60 * 1000,
    retry: false,
  })
  const cachedCrossroadsEvaluation = useQuery({
    queryKey: ["cached-crossroads-evaluation"],
    queryFn: getCachedCrossroadsEvaluation,
    enabled: showcase === "crossroads" && (view === "evaluation" || view === "replay"),
    staleTime: 60 * 60 * 1000,
    retry: false,
  })
  const statusEpisode = status.data ?? create.data
  const episode = statusEpisode && timeline.data !== undefined
    ? { ...statusEpisode, timeline: timeline.data }
    : statusEpisode
  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header-brand">
          <h1>WorldArea</h1>
          <img
            alt="WorldArena agents building on a shared world"
            className="app-header-thumbnail"
            height="68"
            src={worldArenaThumbnail}
            width="120"
          />
        </div>
        <Tabs value={view} onValueChange={setView}>
          <TabsList variant="line">
            <TabsTrigger value="run">Run</TabsTrigger>
            <TabsTrigger value="timeline">Timeline</TabsTrigger>
            <TabsTrigger value="result">Result</TabsTrigger>
            <TabsTrigger value="evaluation">Evaluation</TabsTrigger>
            <TabsTrigger value="replay">Replay</TabsTrigger>
          </TabsList>
        </Tabs>
      </header>
      <div className="app-body">
        <SetupPanel
          setup={setup}
          pending={create.isPending}
          onChange={setSetup}
          onSubmit={() => start(setup)}
          onQuickStart={() => start(QUICK_START_SETUP)}
          onTeamQuickStart={() => start(TEAM_QUICK_START_SETUP)}
          onRtsQuickStart={() => {
            setShowcase("rts")
            setEpisodeId(null)
            setView("run")
          }}
          onSoloQuickStart={() => {
            setShowcase("solo")
            setEpisodeId(null)
            setView("run")
          }}
          onMazeQuickStart={() => {
            setShowcase("maze")
            setEpisodeId(null)
            setView("run")
          }}
          onCrossroadsQuickStart={() => {
            setShowcase("crossroads")
            setEpisodeId(null)
            setView("run")
          }}
        />
        {showcase === "solo" ? (
          <SoloShowcaseWorkspace
            showcase={cachedSoloShowcase.data}
            showcaseError={cachedSoloShowcase.isError}
            view={view}
          />
        ) : showcase === "rts" ? (
          <CachedRtsWorkspace
            evaluation={cachedEvaluation.data}
            evaluationError={cachedEvaluation.isError}
            showcase={cachedShowcase.data}
            showcaseError={cachedShowcase.isError}
            view={view}
          />
        ) : showcase === "maze" ? (
          <CachedMazeWorkspace
            evaluation={cachedMazeEvaluation.data}
            evaluationError={cachedMazeEvaluation.isError}
            showcase={cachedMazeShowcase.data}
            showcaseError={cachedMazeShowcase.isError}
            view={view}
          />
        ) : showcase === "crossroads" ? (
          <CrossroadsWorkspace
            evaluation={cachedCrossroadsEvaluation.data}
            evaluationError={cachedCrossroadsEvaluation.isError}
            showcase={cachedCrossroadsShowcase.data}
            showcaseError={cachedCrossroadsShowcase.isError}
            view={view}
          />
        ) : (
          <EpisodeWorkspace
            key={episode?.episodeId ?? "empty"}
            episode={episode}
            controllerMode={setup.controllerMode}
            mode={setup.mode}
            view={view}
            selected={selected}
            onSelect={setSelected}
            cancelling={cancel.isPending}
            onCancel={() => cancel.mutate()}
          />
        )}
      </div>
      {create.isError || status.isError ? (
        <div role="alert" className="error-banner">
          The episode is no longer available. Refresh the page and start a new run; provider keys
          are session-only and are never retained by the browser.
        </div>
      ) : null}
    </div>
  )
}

export default App
