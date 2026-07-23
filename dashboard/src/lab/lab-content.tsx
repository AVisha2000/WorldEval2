import { useEffect, useRef, useState } from "react"
import type { CSSProperties, FormEvent, RefObject } from "react"
import {
  ArrowRight,
  BrainCircuit,
  Check,
  ChevronRight,
  Compass,
  FileClock,
  Gamepad2,
  KeyRound,
  ListTree,
  LoaderCircle,
  Pause,
  Play,
  Route,
  ShieldCheck,
  Sparkles,
  Telescope,
  Trophy,
  Video,
  X,
} from "lucide-react"
import type { CachedMazeShowcaseView } from "@/api"
import { cachedMazeVideoUrl } from "@/api"
import { labRunVideoUrl } from "./lab-api"
import type {
  LabBenchmark,
  LabAuthMode,
  LabGame,
  LabLaunchInput,
  LabMazeFrame,
  LabReadiness,
  LabRun,
  LabSession,
  LabProjection,
  LabSpectatorFeed,
  RemoteState,
} from "./types"

export type LabView = "lab" | "games" | "runs" | "benchmarks" | "models"

const EMPTY_SPECTATOR_FRAMES: LabSpectatorFeed["frames"] = []

type NavigationProps = {
  activeView: LabView
  onSelect: (view: LabView) => void
}

const navigation: Array<{
  id: LabView
  label: string
  shortLabel: string
  icon: typeof Telescope
}> = [
  { id: "lab", label: "Lab", shortLabel: "Lab", icon: Telescope },
  { id: "games", label: "Games", shortLabel: "Games", icon: Gamepad2 },
  { id: "runs", label: "Runs", shortLabel: "Runs", icon: ListTree },
  {
    id: "benchmarks",
    label: "Benchmarks",
    shortLabel: "Board",
    icon: Trophy,
  },
  { id: "models", label: "Models", shortLabel: "Models", icon: BrainCircuit },
]

export function LabNavigation({ activeView, onSelect }: NavigationProps) {
  return (
    <aside className="lab-navigation" aria-label="WorldEval Lab navigation">
      <div className="lab-brand" aria-label="WorldEval">
        <span className="lab-brand-mark" aria-hidden="true">
          W
        </span>
        <span>WorldEval</span>
      </div>
      <nav className="lab-navigation-list">
        {navigation.map((item) => {
          const Icon = item.icon
          const active = item.id === activeView
          return (
            <button
              aria-label={item.label}
              className={["lab-navigation-item", active && "is-active"]
                .filter(Boolean)
                .join(" ")}
              key={item.id}
              onClick={() => onSelect(item.id)}
              type="button"
            >
              <Icon aria-hidden="true" />
              <span className="lab-navigation-label">
                <span className="lab-navigation-label-long">{item.label}</span>
                <span aria-hidden="true" className="lab-navigation-label-short">
                  {item.shortLabel}
                </span>
              </span>
            </button>
          )
        })}
      </nav>
      <p className="lab-navigation-note">
        <ShieldCheck aria-hidden="true" />
        Evidence first.
        <br />
        Play fair.
      </p>
    </aside>
  )
}

function RemoteIndicator({
  label,
  state,
}: {
  label: string
  state: RemoteState<unknown>
}) {
  if (state.kind === "loading") {
    return (
      <span className="lab-remote-state is-loading">
        <LoaderCircle aria-hidden="true" />
        {label} loading
      </span>
    )
  }
  if (state.kind === "offline") {
    return <span className="lab-remote-state is-offline">{label} offline</span>
  }
  return (
    <span className="lab-remote-state is-ready">
      <Check aria-hidden="true" />
      {label} connected
    </span>
  )
}

function MazeTimeline({
  showcase,
  videoRef,
}: {
  showcase: CachedMazeShowcaseView | null
  videoRef: RefObject<HTMLVideoElement | null>
}) {
  const events = showcase?.timeline ?? []
  if (!events.length) {
    return (
      <p className="lab-empty-inline">
        The cached race timeline is not available yet.
      </p>
    )
  }
  return (
    <ol className="lab-timeline" aria-label="Safe Labyrinth Run timeline">
      {events.map((event) => (
        <li key={`${event.atSeconds}-${event.kind}-${event.label}`}>
          <button
            onClick={() => {
              if (videoRef.current) {
                videoRef.current.currentTime = event.atSeconds
                void videoRef.current.play()
              }
            }}
            type="button"
          >
            <i aria-hidden="true" />
            <span>{event.label}</span>
            <time>{formatSeconds(event.atSeconds)}</time>
          </button>
        </li>
      ))}
    </ol>
  )
}

function formatSeconds(value: number): string {
  const minutes = Math.floor(value / 60)
  return `${minutes}:${String(value % 60).padStart(2, "0")}`
}

export function SimulationStage({
  activeRun,
  games,
  showcase,
  showcaseState,
  spectator,
  onSelectGame,
}: {
  activeRun: RemoteState<{ run: LabRun; projection: LabProjection } | null>
  games: RemoteState<LabGame[]>
  showcase: CachedMazeShowcaseView | null
  showcaseState: RemoteState<CachedMazeShowcaseView>
  spectator: RemoteState<LabSpectatorFeed | null>
  onSelectGame: (gameId: string) => void
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [playing, setPlaying] = useState(false)
  if (activeRun.kind === "ready" && activeRun.data) {
    return (
      <LiveMazeStage
        games={games}
        onSelectGame={onSelectGame}
        projection={activeRun.data.projection}
        run={activeRun.data.run}
        spectator={spectator}
      />
    )
  }
  const roster = showcase?.entrants ?? []
  return (
    <section className="lab-simulation" aria-label="Labyrinth Run simulation">
      <header className="lab-section-heading">
        <div>
          <h1>Labyrinth Run</h1>
          <p>
            Replay a verified simulation, then configure a fresh exploratory
            race.
          </p>
        </div>
        <RemoteIndicator label="Showcase" state={showcaseState} />
      </header>
      <div className="lab-simulation-frame">
        <video
          className="lab-simulation-video"
          controls
          muted
          onPause={() => setPlaying(false)}
          onPlay={() => setPlaying(true)}
          playsInline
          preload="metadata"
          ref={videoRef}
          src={cachedMazeVideoUrl()}
        >
          Your browser cannot play the cached Labyrinth Run broadcast.
        </video>
        <div className="lab-simulation-overlay" aria-live="polite">
          <span>
            <Video aria-hidden="true" />
            {playing ? "Playing saved simulation" : "Saved simulation"}
          </span>
          {showcase?.winner.displayName ? (
            <span>{showcase.winner.displayName} won this race</span>
          ) : null}
        </div>
      </div>
      <div className="lab-simulation-controls">
        <button
          className="lab-play-control"
          onClick={() => {
            if (videoRef.current?.paused) void videoRef.current.play()
            else videoRef.current?.pause()
          }}
          type="button"
        >
          {playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
          {playing ? "Pause replay" : "Play replay"}
        </button>
        <MazeTimeline showcase={showcase} videoRef={videoRef} />
      </div>
      <div className="lab-simulation-footer">
        <div
          className="lab-roster"
          aria-label="Cached Labyrinth Run entrant roster"
        >
          <span>Racer roster</span>
          {roster.length ? (
            roster.map((entrant) => (
              <span className="lab-roster-item" key={entrant.participantId}>
                <i
                  style={{ "--roster-color": entrant.color } as CSSProperties}
                />
                <b>{entrant.displayName}</b>
                <small>{entrant.model}</small>
              </span>
            ))
          ) : (
            <small>Waiting for cached race metadata.</small>
          )}
        </div>
        <button
          className="lab-text-button"
          onClick={() => onSelectGame("labyrinth-run")}
          type="button"
        >
          Explore the game guide <ArrowRight aria-hidden="true" />
        </button>
      </div>
      <GameRail games={games} onSelectGame={onSelectGame} />
    </section>
  )
}

function LiveMazeStage({
  games,
  onSelectGame,
  projection,
  run,
  spectator,
}: {
  games: RemoteState<LabGame[]>
  onSelectGame: (gameId: string) => void
  projection: LabProjection
  run: LabRun
  spectator: RemoteState<LabSpectatorFeed | null>
}) {
  const lifecycle = run.lifecycle.replaceAll("_", " ")
  const isCompleted = run.lifecycle === "completed"
  const liveAuthorityUnavailable =
    !isCompleted && run.authorityAvailable === false
  return (
    <section
      className="lab-simulation lab-live-simulation"
      aria-label="Live Labyrinth Run arena"
    >
      <header className="lab-section-heading">
        <div>
          <h1>Labyrinth Run</h1>
          <p>
            {isCompleted
              ? "Authority-completed run. Its verified observer map remains available while Godot video is prepared."
              : liveAuthorityUnavailable
                ? "The live authority is unavailable. This interrupted run cannot resume; clone its frozen contract to start a fresh race."
                : "Live authority map, updated after each completed model decision window."}
          </p>
        </div>
        <span className="lab-live-state">
          {liveAuthorityUnavailable ? "interrupted" : lifecycle}
        </span>
      </header>
      {isCompleted && run.videoAvailable ? (
        <CompletedGodotReplay
          fallbackFrame={projection.frame}
          feed={spectator.kind === "ready" ? spectator.data : null}
          feedState={spectator.kind}
          key={run.id}
          run={run}
        />
      ) : projection.frame ? (
        <LiveSpectatorDirector
          fallbackFrame={projection.frame}
          feed={spectator.kind === "ready" ? spectator.data : null}
          feedState={spectator.kind}
          key={run.id}
        />
      ) : (
        <div className="lab-live-waiting" role="status">
          <LoaderCircle aria-hidden="true" />
          <div>
            <b>Preparing the authority map</b>
            <p>
              The race is registered. The first safe observer frame appears
              before any model result is shown.
            </p>
          </div>
        </div>
      )}
      <div className="lab-live-footer">
        <span>Run {run.id}</span>
        <span>
          {projection.frame
            ? `${projection.frame.providerCalls} provider calls · tick ${projection.frame.tick}`
            : "Waiting for observer frame"}
        </span>
        {isCompleted && !run.videoAvailable ? (
          <span>
            Godot video is unavailable; the verified observer map remains.
          </span>
        ) : null}
        {liveAuthorityUnavailable ? (
          <span>Live authority unavailable · clone to launch a fresh race.</span>
        ) : null}
      </div>
      <GameRail games={games} onSelectGame={onSelectGame} />
    </section>
  )
}

function CompletedGodotReplay({
  fallbackFrame,
  feed,
  feedState,
  run,
}: {
  fallbackFrame: LabMazeFrame | null
  feed: LabSpectatorFeed | null
  feedState: RemoteState<unknown>["kind"]
  run: LabRun
}) {
  const [videoFailed, setVideoFailed] = useState(false)
  if (videoFailed) {
    return fallbackFrame ? (
      <div className="lab-video-fallback">
        <p role="status">
          The Godot video could not be played. Showing the verified authority
          map instead.
        </p>
        <LiveSpectatorDirector
          fallbackFrame={fallbackFrame}
          feed={feed}
          feedState={feedState}
        />
      </div>
    ) : (
      <div className="lab-live-waiting" role="status">
        <Video aria-hidden="true" />
        <div>
          <b>Godot video unavailable</b>
          <p>The safe authority map is not available for this saved run.</p>
        </div>
      </div>
    )
  }
  return (
    <div className="lab-simulation-frame">
      <video
        className="lab-simulation-video"
        controls
        muted
        onError={() => setVideoFailed(true)}
        playsInline
        preload="metadata"
        src={labRunVideoUrl(run.id)}
      >
        Your browser cannot play this completed Godot Labyrinth Run broadcast.
      </video>
      <div className="lab-simulation-overlay" aria-live="polite">
        <span>
          <Video aria-hidden="true" />
          Godot broadcast ready
        </span>
        <span>Authority-completed broadcast</span>
      </div>
    </div>
  )
}

function LiveSpectatorDirector({
  fallbackFrame,
  feed,
  feedState,
}: {
  fallbackFrame: LabMazeFrame
  feed: LabSpectatorFeed | null
  feedState: RemoteState<unknown>["kind"]
}) {
  const frames = feed?.frames ?? EMPTY_SPECTATOR_FRAMES
  const latestIndex = frames.length - 1
  const [paused, setPaused] = useState(false)
  const [speed, setSpeed] = useState<1 | 2>(1)
  const [frameSequence, setFrameSequence] = useState<number | null>(null)
  const [followParticipantId, setFollowParticipantId] = useState("observer")
  const followingLive = frameSequence === null
  const selectedFrameIndex = !frames.length
    ? -1
    : followingLive
      ? latestIndex
      : (() => {
          const matchingIndex = frames.findIndex(
            (frame) => frame.sequence === frameSequence
          )
          return matchingIndex >= 0 ? matchingIndex : latestIndex
        })()

  useEffect(() => {
    if (paused || selectedFrameIndex < 0 || selectedFrameIndex >= latestIndex)
      return
    const next = frames[selectedFrameIndex + 1]
    if (!next) return
    const durationMs = spectatorTransitionDuration(
      frames[selectedFrameIndex]?.frame,
      next.frame,
      speed
    )
    const timer = window.setTimeout(
      () => setFrameSequence(next.sequence),
      durationMs
    )
    return () => window.clearTimeout(timer)
  }, [frames, latestIndex, paused, selectedFrameIndex, speed])

  const displayedFrame =
    selectedFrameIndex < 0
      ? fallbackFrame
      : (frames[selectedFrameIndex]?.frame ?? fallbackFrame)
  const previousFrame =
    !followingLive && selectedFrameIndex > 0
      ? frames[selectedFrameIndex - 1]?.frame
      : undefined
  const bufferedFrames =
    selectedFrameIndex < 0
      ? frames.length
      : Math.max(0, latestIndex - selectedFrameIndex)
  const selectedFollowParticipantId = displayedFrame.racers.some(
    (racer) => racer.participantId === followParticipantId
  )
    ? followParticipantId
    : "observer"
  const atLiveEdge = latestIndex >= 0 && selectedFrameIndex === latestIndex

  function togglePause() {
    if (!paused && latestIndex >= 0) {
      // Pin the current authority frame before pausing; a later network frame
      // must not move a spectator who explicitly paused playback.
      setFrameSequence(frames[latestIndex]?.sequence ?? null)
    }
    setPaused((current) => !current)
  }

  return (
    <div className="lab-spectator-director">
      <div className="lab-spectator-controls" aria-label="Live spectator director">
        <div className="lab-spectator-control-group">
          <button
            aria-pressed={atLiveEdge}
            className="lab-spectator-button is-live"
            disabled={!frames.length}
            onClick={() => {
              setPaused(false)
              setFrameSequence(null)
            }}
            type="button"
          >
            <i aria-hidden="true" />
            Live now
          </button>
          <button
            aria-pressed={paused}
            className="lab-spectator-button"
            disabled={!frames.length}
            onClick={togglePause}
            type="button"
          >
            {paused ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />}
            {paused ? "Resume" : "Pause"}
          </button>
        </div>
        <div className="lab-spectator-control-group" aria-label="Playback speed">
          {[1, 2].map((candidate) => (
            <button
              aria-pressed={speed === candidate}
              className="lab-spectator-button is-compact"
              key={candidate}
              onClick={() => setSpeed(candidate as 1 | 2)}
              type="button"
            >
              {candidate}×
            </button>
          ))}
        </div>
        <label className="lab-spectator-follow">
          <span>Camera</span>
          <select
            onChange={(event) => setFollowParticipantId(event.target.value)}
            value={selectedFollowParticipantId}
          >
            <option value="observer">Observer map</option>
            {displayedFrame.racers.map((racer) => (
              <option key={racer.participantId} value={racer.participantId}>
                Follow {racer.displayName}
              </option>
            ))}
          </select>
        </label>
        <span className="lab-spectator-buffer" aria-live="polite">
          {feedState === "loading"
            ? "Connecting spectator feed"
            : atLiveEdge
              ? "At live edge"
              : `${bufferedFrames} buffered frame${bufferedFrames === 1 ? "" : "s"}`}
        </span>
      </div>
      <AuthorityMazeMap
        followParticipantId={
          selectedFollowParticipantId === "observer"
            ? null
            : selectedFollowParticipantId
        }
        frame={displayedFrame}
        motionDurationMs={
          previousFrame
            ? spectatorTransitionDuration(previousFrame, displayedFrame, speed)
            : 0
        }
        previousFrame={previousFrame}
      />
      <p className="lab-spectator-disclosure">
        Playback interpolates only between accepted authority frames; it never
        advances the game or issues a model call.
      </p>
    </div>
  )
}

function AuthorityMazeMap({
  frame,
  previousFrame,
  followParticipantId,
  motionDurationMs = 0,
}: {
  frame: LabMazeFrame
  previousFrame?: LabMazeFrame
  followParticipantId?: string | null
  motionDurationMs?: number
}) {
  const height = frame.map.rows.length
  const width = Math.max(...frame.map.rows.map((row) => row.length))
  const focusRacer = followParticipantId
    ? frame.racers.find((racer) => racer.participantId === followParticipantId)
    : null
  const viewBox = focusRacer
    ? focusedMazeViewBox(width, height, focusRacer.position)
    : `0 0 ${width} ${height}`
  return (
    <div className="lab-authority-map-wrap">
      <div className="lab-authority-map-heading">
        <span>
          Authority observer map
          {focusRacer ? ` · following ${focusRacer.displayName}` : ""}
        </span>
        <small>
          Colored cells show each racer’s wall-occluded field of view.
        </small>
      </div>
      <svg
        aria-label={`Authority maze map at tick ${frame.tick}`}
        className="lab-authority-map"
        role="img"
        viewBox={viewBox}
      >
        <rect fill="#03101f" height={height} width={width} x="0" y="0" />
        {frame.map.rows.map((row, y) =>
          Array.from({ length: width }, (_, x) => (
            <rect
              className={row[x] === "#" ? "is-wall" : "is-passage"}
              height="1"
              key={`cell-${x}-${y}`}
              width="1"
              x={x}
              y={y}
            />
          ))
        )}
        {frame.racers.flatMap((racer) =>
          racer.visibleCells.map(([x, y]) => (
            <rect
              fill={safeRacerColor(racer.color)}
              height="0.76"
              key={`vision-${racer.participantId}-${x}-${y}`}
              opacity="0.16"
              rx="0.08"
              width="0.76"
              x={x + 0.12}
              y={y + 0.12}
            />
          ))
        )}
        <rect
          className="lab-map-start"
          height="0.52"
          rx="0.08"
          width="0.52"
          x={frame.map.start[0] + 0.24}
          y={frame.map.start[1] + 0.24}
        />
        <rect
          className="lab-map-exit"
          height="0.56"
          rx="0.08"
          width="0.56"
          x={frame.map.exit[0] + 0.22}
          y={frame.map.exit[1] + 0.22}
        />
        {frame.racers.map((racer) => (
          <polyline
            fill="none"
            key={`trail-${racer.participantId}`}
            points={racer.path
              .map(([x, y]) => `${x + 0.5},${y + 0.5}`)
              .join(" ")}
            stroke={safeRacerColor(racer.color)}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeOpacity="0.72"
            strokeWidth="0.17"
          />
        ))}
        {frame.racers.map((racer) => (
          <AnimatedMazeRacer
            durationMs={motionDurationMs}
            key={`racer-${racer.participantId}-${frame.tick}`}
            previousFrame={previousFrame}
            racer={racer}
          />
        ))}
      </svg>
      <div className="lab-authority-map-roster" aria-label="Live racer status">
        {frame.racers.map((racer) => (
          <span key={racer.participantId}>
            <i
              style={
                {
                  "--roster-color": safeRacerColor(racer.color),
                } as CSSProperties
              }
            />
            <b>{racer.displayName}</b>
            <small>{racer.providerCalls} calls</small>
            {racer.finished ? <em>finished</em> : null}
          </span>
        ))}
      </div>
    </div>
  )
}

function AnimatedMazeRacer({
  racer,
  previousFrame,
  durationMs,
}: {
  racer: LabMazeFrame["racers"][number]
  previousFrame?: LabMazeFrame
  durationMs: number
}) {
  const previousRacer = previousFrame?.racers.find(
    (candidate) => candidate.participantId === racer.participantId
  )
  const motion = mazeMotionCells(previousRacer?.path ?? [], racer.path)
  const shouldAnimate = durationMs > 0 && motion.length > 1
  const motionPath = shouldAnimate
    ? motion
        .map(([x, y], index) => `${index ? "L" : "M"}${x + 0.5} ${y + 0.5}`)
        .join(" ")
    : null
  if (!motionPath) {
    return (
      <circle
        className="lab-map-racer"
        cx={racer.position[0] + 0.5}
        cy={racer.position[1] + 0.5}
        fill={safeRacerColor(racer.color)}
        r="0.3"
      />
    )
  }
  return (
    <g className="lab-map-racer-motion">
      <animateMotion dur={`${durationMs}ms`} fill="freeze" path={motionPath} />
      <circle className="lab-map-racer" fill={safeRacerColor(racer.color)} r="0.3" />
    </g>
  )
}

function mazeMotionCells(
  previousPath: LabMazeFrame["racers"][number]["path"],
  currentPath: LabMazeFrame["racers"][number]["path"]
) {
  if (!previousPath.length || !currentPath.length) return currentPath
  const previousIsPrefix =
    previousPath.length <= currentPath.length &&
    previousPath.every(
      (cell, index) =>
        cell[0] === currentPath[index]?.[0] && cell[1] === currentPath[index]?.[1]
    )
  if (previousIsPrefix) return currentPath.slice(previousPath.length - 1)
  const currentCell = currentPath.at(-1)
  // A bounded live trail can roll over on very long races.  Do not invent a diagonal shortcut
  // through a wall when its old prefix is no longer present; the next authoritative frame will
  // resume interpolation from the retained trail.
  return currentCell ? [currentCell] : currentPath
}

function spectatorTransitionDuration(
  previousFrame: LabMazeFrame | undefined,
  currentFrame: LabMazeFrame,
  speed: 1 | 2
): number {
  if (!previousFrame) return 360
  const movedCells = currentFrame.racers.reduce((maximum, racer) => {
    const previousRacer = previousFrame.racers.find(
      (candidate) => candidate.participantId === racer.participantId
    )
    return Math.max(
      maximum,
      mazeMotionCells(previousRacer?.path ?? [], racer.path).length - 1
    )
  }, 0)
  return Math.round(Math.min(1_650, Math.max(360, movedCells * 70)) / speed)
}

function focusedMazeViewBox(
  width: number,
  height: number,
  position: LabMazeFrame["map"]["start"]
): string {
  const span = Math.min(13, width, height)
  const x = Math.min(
    Math.max(position[0] + 0.5 - span / 2, 0),
    Math.max(0, width - span)
  )
  const y = Math.min(
    Math.max(position[1] + 0.5 - span / 2, 0),
    Math.max(0, height - span)
  )
  return `${x} ${y} ${span} ${span}`
}

function safeRacerColor(value: string): string {
  return /^#[0-9a-f]{6}$/i.test(value) ? value : "#71a9ff"
}

function GameRail({
  games,
  onSelectGame,
}: {
  games: RemoteState<LabGame[]>
  onSelectGame: (gameId: string) => void
}) {
  if (games.kind !== "ready") {
    return (
      <p className="lab-rail-state">
        Game catalogue {games.kind === "loading" ? "loading" : "is offline"}.
      </p>
    )
  }
  return (
    <section className="lab-game-rail" aria-label="Available games">
      {games.data.map((game) => (
        <button
          className="lab-game-rail-card"
          key={game.id}
          onClick={() => onSelectGame(game.id)}
          type="button"
        >
          <span className="lab-game-rail-icon">
            <Compass aria-hidden="true" />
          </span>
          <span>
            <b>{game.title}</b>
            <small>{readinessLabel(game.readiness)}</small>
          </span>
          <ChevronRight aria-hidden="true" />
        </button>
      ))}
    </section>
  )
}

export function GameGuide({
  game,
  games,
  onSelectGame,
  showSwitcher = true,
}: {
  game: LabGame | null
  games: RemoteState<LabGame[]>
  onSelectGame: (gameId: string) => void
  showSwitcher?: boolean
}) {
  if (games.kind === "loading")
    return <LoadingPanel title="Loading game catalogue" />
  if (games.kind === "offline" || !game) {
    return (
      <EmptyPanel
        title="Game catalogue unavailable"
        description="Reconnect the Lab API to open a server-published game guide."
      />
    )
  }

  return (
    <section className="lab-guide" aria-labelledby="lab-guide-title">
      <header className="lab-guide-header">
        <div>
          <span className={`lab-readiness is-${game.readiness}`}>
            {readinessLabel(game.readiness)}
          </span>
          <h1 id="lab-guide-title">{game.title}</h1>
          <p>{game.readinessNote}</p>
        </div>
        {showSwitcher ? (
          <div className="lab-guide-switcher" aria-label="Choose a game">
            {games.data.map((item) => (
              <button
                className={item.id === game.id ? "is-selected" : ""}
                key={item.id}
                onClick={() => onSelectGame(item.id)}
                type="button"
              >
                {item.title}
              </button>
            ))}
          </div>
        ) : null}
      </header>
      <div className="lab-guide-hero-grid">
        <article className="lab-guide-diagram">
          <div className="lab-guide-diagram-art" aria-hidden="true">
            <Route />
            <span className="lab-guide-diagram-path path-one" />
            <span className="lab-guide-diagram-path path-two" />
            <span className="lab-guide-diagram-beacon" />
          </div>
          <p>
            Only server-published guide metadata is shown here. The live
            simulation remains authoritative elsewhere.
          </p>
        </article>
        <article className="lab-guide-capabilities">
          <h2>What this game tests</h2>
          {game.capabilityStatements.map((statement) => (
            <div key={statement}>
              <BrainCircuit aria-hidden="true" />
              <p>{statement}</p>
            </div>
          ))}
        </article>
      </div>
      <div className="lab-guide-columns">
        <GuideColumn
          icon={<Telescope aria-hidden="true" />}
          title="How agents interact"
        >
          <GuideDefinition
            label="Observation"
            text={game.agentInterface.observation}
          />
          <GuideDefinition label="Actions" text={game.agentInterface.actions} />
          <GuideDefinition label="Memory" text={game.agentInterface.memory} />
        </GuideColumn>
        <GuideColumn
          icon={<Trophy aria-hidden="true" />}
          title="How scoring works"
        >
          <p>{game.scoring.summary}</p>
          <dl className="lab-guide-metrics">
            {game.scoring.metrics.map((metric) => (
              <GuideDefinition
                key={metric.id}
                label={metric.label}
                text={metric.description}
              />
            ))}
          </dl>
        </GuideColumn>
        <GuideColumn icon={<X aria-hidden="true" />} title="Failure modes">
          <ul className="lab-guide-list">
            {game.failureModes.map((mode) => (
              <li key={mode}>{mode}</li>
            ))}
          </ul>
        </GuideColumn>
        <GuideColumn
          icon={<ShieldCheck aria-hidden="true" />}
          title="Safe public view"
        >
          <p>{game.safety.publicView}</p>
          <p className="lab-guide-private">{game.safety.privateAgentState}</p>
        </GuideColumn>
      </div>
      <section
        className="lab-guide-controls"
        aria-labelledby="guide-controls-title"
      >
        <h2 id="guide-controls-title">Supported configuration</h2>
        <div>
          {game.configurationControls.map((control) => (
            <article key={control.id}>
              <span>{control.controlType}</span>
              <h3>{control.label}</h3>
              <p>{control.description}</p>
              {control.options.length ? (
                <small>{control.options.join(" · ")}</small>
              ) : null}
            </article>
          ))}
        </div>
      </section>
    </section>
  )
}

export function PublicGamePage({
  benchmark,
  game,
}: {
  benchmark: RemoteState<LabBenchmark>
  game: RemoteState<LabGame>
}) {
  if (game.kind === "loading")
    return <LoadingPanel title="Loading game guide" />
  if (game.kind === "offline") {
    return (
      <EmptyPanel
        title="This game guide is unavailable"
        description="The unlisted WorldEval game page could not reach its safe public projection."
      />
    )
  }
  const publishedReplay = game.data.id === "labyrinth-run"
  return (
    <div className="lab-public-page">
      <GameGuide
        game={game.data}
        games={{ kind: "ready", data: [game.data] }}
        onSelectGame={() => undefined}
        showSwitcher={false}
      />
      {publishedReplay ? (
        <section
          className="lab-public-replay"
          aria-labelledby="published-replay-title"
        >
          <div>
            <span>Published replay</span>
            <h2 id="published-replay-title">Labyrinth Run broadcast</h2>
            <p>
              This is an explicitly published, authority-verified replay. It
              contains no credentials, private observations, agent memory, or
              model scratchpads.
            </p>
          </div>
          <video
            controls
            muted
            playsInline
            preload="metadata"
            src={cachedMazeVideoUrl()}
          >
            Your browser cannot play this published Labyrinth Run replay.
          </video>
        </section>
      ) : null}
      <section
        className="lab-public-evidence"
        aria-labelledby="public-evidence-title"
      >
        <ShieldCheck aria-hidden="true" />
        <div>
          <h2 id="public-evidence-title">Verified benchmark evidence</h2>
          <p>
            {benchmark.kind === "ready"
              ? (benchmark.data.message ??
                "No verified results are published for this game.")
              : "Verified results are unavailable while the public benchmark projection is offline."}
          </p>
        </div>
      </section>
    </div>
  )
}

function GuideColumn({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode
  title: string
  children: React.ReactNode
}) {
  return (
    <article className="lab-guide-column">
      <header>
        {icon}
        <h2>{title}</h2>
      </header>
      {children}
    </article>
  )
}

function GuideDefinition({ label, text }: { label: string; text: string }) {
  return (
    <div className="lab-guide-definition">
      <dt>{label}</dt>
      <dd>{text}</dd>
    </div>
  )
}

export function RunsPanel({
  cloneEnabled,
  onClone,
  onOpen,
  runs,
}: {
  cloneEnabled: boolean
  onClone: (run: LabRun) => Promise<void>
  onOpen: (run: LabRun) => void
  runs: RemoteState<LabRun[]>
}) {
  const [cloningRunId, setCloningRunId] = useState<string | null>(null)
  const [cloneNotice, setCloneNotice] = useState("")

  async function clone(run: LabRun) {
    if (!cloneEnabled || run.provider !== "openai" || cloningRunId) return
    setCloningRunId(run.id)
    setCloneNotice("")
    try {
      await onClone(run)
    } catch {
      // Never reflect an upstream error here: it can contain private transport detail.
      setCloneNotice(
        "The Lab could not prepare a frozen clone. No run was launched."
      )
    } finally {
      setCloningRunId(null)
    }
  }

  if (runs.kind === "loading")
    return <LoadingPanel title="Loading saved runs" />
  if (runs.kind === "offline")
    return (
      <EmptyPanel
        title="Saved runs are offline"
        description="The Lab API is unavailable, so no local run history can be displayed."
      />
    )
  if (!runs.data.length) {
    return (
      <EmptyPanel
        title="No saved runs yet"
        description="Launch an exploratory Labyrinth Run to create the first authority-owned run record."
      />
    )
  }
  return (
    <section className="lab-list-page" aria-labelledby="runs-title">
      <header>
        <div>
          <h1 id="runs-title">Saved runs</h1>
          <p>
            Authority-owned records are shown exactly as the Lab API publishes
            them. A clone is an unlaunched frozen draft, never a resume of its
            parent race.
          </p>
        </div>
        <span>{runs.data.length} runs</span>
      </header>
      <div className="lab-run-table" role="table" aria-label="Saved Lab runs">
        <div className="lab-run-row lab-run-heading" role="row">
          <span>Run</span>
          <span>Game</span>
          <span>State</span>
          <span>Lineage</span>
          <span>Artifacts</span>
          <span aria-label="Run actions" />
        </div>
        {runs.data.map((run) => (
          <div className="lab-run-row" key={run.id} role="row">
            <span>
              <b>{run.id}</b>
              <small>
                {run.createdAt
                  ? new Date(run.createdAt).toLocaleString()
                  : "Creation time not published"}
              </small>
            </span>
            <span>
              <b>{run.gameId}</b>
              <small>{run.gameVersion ?? "Version not published"}</small>
            </span>
            <span className="lab-run-state">
              {isInterruptedRun(run)
                ? "interrupted"
                : run.lifecycle.replaceAll("_", " ")}
              {run.lifecycle === "draft" ? (
                <small>Frozen configuration</small>
              ) : isInterruptedRun(run) ? (
                <small>Authority unavailable · no resume</small>
              ) : null}
            </span>
            <span className="lab-run-lineage">
              {run.parentContractSha256 ? (
                <>
                  <b>Clone of {shortContractHash(run.parentContractSha256)}</b>
                  <small>
                    {run.lineageChangeCount
                      ? `${run.lineageChangeCount} declared contract ${run.lineageChangeCount === 1 ? "change" : "changes"}`
                      : "Exact frozen copy"}
                  </small>
                </>
              ) : (
                <>
                  <b>Root contract</b>
                  <small>
                    {run.contractSha256
                      ? shortContractHash(run.contractSha256)
                      : "Fingerprint not published"}
                  </small>
                </>
              )}
            </span>
            <span className="lab-artifact-pairs">
              <small>
                {run.replayAvailable ? "Replay available" : "Replay pending"}
              </small>
              <small>
                {run.videoAvailable ? "Video available" : "Video pending"}
              </small>
            </span>
            <span className="lab-run-actions">
              {run.lifecycle === "draft" ? (
                run.provider === "openai" ? (
                  <button
                    className="lab-run-open"
                    onClick={() => onOpen(run)}
                    type="button"
                  >
                    Open draft
                  </button>
                ) : (
                  <small className="lab-run-provider-note">
                    Provider unavailable in this Lab
                  </small>
                )
              ) : (
                <>
                  <button
                    className="lab-run-open"
                    onClick={() => onOpen(run)}
                    type="button"
                  >
                    {isInterruptedRun(run) ? "Inspect run" : "Open arena"}
                  </button>
                  <button
                    aria-label={
                      run.provider === "openai"
                        ? `Clone frozen configuration for ${run.id}`
                        : `Clone unavailable for ${run.id}: OpenAI Lab runs only`
                    }
                    className="lab-run-clone"
                    disabled={
                      !cloneEnabled ||
                      run.provider !== "openai" ||
                      cloningRunId !== null
                    }
                    onClick={() => void clone(run)}
                    title={
                      run.provider === "openai"
                        ? undefined
                        : "The first Lab release launches OpenAI runs only."
                    }
                    type="button"
                  >
                    {run.provider !== "openai"
                      ? "OpenAI only"
                      : cloningRunId === run.id
                        ? "Cloning…"
                        : "Clone"}
                  </button>
                </>
              )}
            </span>
          </div>
        ))}
      </div>
      <p className="lab-run-clone-notice" aria-live="polite">
        {cloneNotice ||
          (cloneEnabled
            ? "Clone a saved contract to launch it later with a newly entered API key."
            : "Connect a Lab session to clone a saved contract.")}
      </p>
    </section>
  )
}

function shortContractHash(hash: string): string {
  return `${hash.slice(0, 8)}…${hash.slice(-6)}`
}

function isInterruptedRun(run: LabRun): boolean {
  return (
    run.authorityAvailable === false &&
    ["queued", "running", "checkpointed"].includes(run.lifecycle)
  )
}

export function BenchmarksPanel({
  benchmark,
}: {
  benchmark: RemoteState<LabBenchmark>
}) {
  if (benchmark.kind === "loading")
    return <LoadingPanel title="Loading Labyrinth benchmark" />
  if (benchmark.kind === "offline")
    return (
      <EmptyPanel
        title="Benchmark service offline"
        description="No benchmark claims are shown while the server-published season is unavailable."
      />
    )
  const data = benchmark.data
  return (
    <section className="lab-benchmark" aria-labelledby="benchmark-title">
      <header className="lab-benchmark-header">
        <div>
          <h1 id="benchmark-title">Labyrinth benchmark</h1>
          <p>Evidence remains specific to this game, recipe, and season.</p>
        </div>
        <span>Season: {data.seasonState.replaceAll("_", " ")}</span>
      </header>
      <div className="lab-benchmark-summary">
        <article>
          <Telescope aria-hidden="true" />
          <span>Published recipes</span>
          <b>{data.recipeCount}</b>
        </article>
        <article>
          <FileClock aria-hidden="true" />
          <span>Leaderboard rows</span>
          <b>{data.leaderboard.length}</b>
        </article>
        <article>
          <ShieldCheck aria-hidden="true" />
          <span>State</span>
          <b>{data.seasonState.replaceAll("_", " ")}</b>
        </article>
      </div>
      {data.leaderboard.length ? (
        <Leaderboard rows={data.leaderboard} />
      ) : (
        <EmptyPanel
          title="No verified benchmark results"
          description={
            data.message ??
            "This season has not published any server-backed leaderboard rows yet."
          }
          compact
        />
      )}
    </section>
  )
}

function Leaderboard({ rows }: { rows: LabBenchmark["leaderboard"] }) {
  return (
    <div
      className="lab-leaderboard"
      role="table"
      aria-label="Server-published Labyrinth benchmark results"
    >
      <div className="lab-leaderboard-row lab-leaderboard-heading" role="row">
        <span>Model</span>
        <span>Completion</span>
        <span>Calls</span>
        <span>Path efficiency</span>
        <span>Cost</span>
        <span>Evidence</span>
      </div>
      {rows.map((row) => (
        <div className="lab-leaderboard-row" key={row.model} role="row">
          <b>{row.model}</b>
          <span>{row.completion ?? "—"}</span>
          <span>{row.calls ?? "—"}</span>
          <span>{row.pathEfficiency ?? "—"}</span>
          <span>{row.cost ?? "—"}</span>
          <span>{row.evidence ?? "—"}</span>
        </div>
      ))}
    </div>
  )
}

export function ModelsPanel({
  benchmark,
}: {
  benchmark: RemoteState<LabBenchmark>
}) {
  if (benchmark.kind !== "ready" || !benchmark.data.leaderboard.length) {
    return (
      <EmptyPanel
        title="No published model profiles"
        description="Model profiles appear only after server-published benchmark evidence is available."
      />
    )
  }
  return (
    <section className="lab-models" aria-labelledby="models-title">
      <header>
        <h1 id="models-title">Models</h1>
        <p>Capability evidence is scoped to the Labyrinth benchmark above.</p>
      </header>
      <Leaderboard rows={benchmark.data.leaderboard} />
    </section>
  )
}

type ComposerProps = {
  authMode: RemoteState<LabAuthMode>
  draft: LabRun | null
  onConnect: () => Promise<void>
  onLaunch: (input: LabLaunchInput) => Promise<string | null>
  onLaunchDraft: (draft: LabRun, apiKey: string) => Promise<string | null>
  onRequestMagicLink: (email: string) => Promise<void>
  session: RemoteState<LabSession | null>
}

export function RunComposer({
  authMode,
  draft,
  onConnect,
  onLaunch,
  onLaunchDraft,
  onRequestMagicLink,
  session,
}: ComposerProps) {
  const [apiKey, setApiKey] = useState("")
  const [sol, setSol] = useState("gpt-5.6-sol")
  const [terra, setTerra] = useState("gpt-5.6-terra")
  const [luna, setLuna] = useState("gpt-5.6-luna")
  const [visionRangeCells, setVisionRangeCells] =
    useState<LabLaunchInput["visionRangeCells"]>("4")
  const [useSkill, setUseSkill] = useState(false)
  const [contractConfirmed, setContractConfirmed] = useState(false)
  const [state, setState] = useState<
    "idle" | "submitting" | "accepted" | "failed"
  >("idle")
  const [connecting, setConnecting] = useState(false)
  const [email, setEmail] = useState("")
  const [requestingLink, setRequestingLink] = useState(false)
  const [notice, setNotice] = useState("")

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (
      !apiKey ||
      state === "submitting" ||
      session.kind !== "ready" ||
      !session.data
    )
      return
    setState("submitting")
    setNotice("")
    try {
      const runId = draft
        ? await onLaunchDraft(draft, apiKey)
        : await onLaunch({
            apiKey,
            provider: "openai",
            models: { sol, terra, luna },
            visionRangeCells,
            skillMode: useSkill ? "maze-navigation-v1" : "none",
            mode: "exploratory",
          })
      setState("accepted")
      setNotice(
        draft
          ? runId
            ? `Frozen clone ${runId} was accepted for a fresh race.`
            : "Your frozen clone was accepted for a fresh race."
          : runId
            ? `Run ${runId} was accepted.`
            : "Your run was accepted."
      )
    } catch {
      setState("failed")
      setNotice(
        draft
          ? "The Lab did not accept the frozen clone. Your key was cleared."
          : "The Lab did not accept the run. Your key was cleared."
      )
    } finally {
      setApiKey("")
      setContractConfirmed(false)
    }
  }

  async function connect() {
    setConnecting(true)
    setNotice("")
    try {
      await onConnect()
      setNotice("Local session connected. You can now submit a run.")
    } catch {
      setNotice("Unable to establish a local session.")
    } finally {
      setConnecting(false)
    }
  }

  async function requestMagicLink() {
    if (!email || requestingLink) return
    setRequestingLink(true)
    setNotice("")
    try {
      await onRequestMagicLink(email)
      setNotice("If this email is invited, an access link is on its way.")
    } catch {
      setNotice("The Lab could not request an access link right now.")
    } finally {
      setRequestingLink(false)
    }
  }

  const sessionReady = session.kind === "ready" && session.data !== null
  const magicLinkMode =
    authMode.kind === "ready" && authMode.data === "magic_link"
  const launchingFrozenClone = draft !== null

  return (
    <aside className="lab-composer" aria-labelledby="composer-title">
      <div className="lab-composer-title">
        <div>
          <span>{launchingFrozenClone ? "Frozen clone" : "Game"}</span>
          <h2 id="composer-title">
            {launchingFrozenClone ? "Launch clone" : "Labyrinth Run"}
          </h2>
        </div>
        <Sparkles aria-hidden="true" />
      </div>
      <form onSubmit={submit}>
        <div
          className={["lab-session-gate", sessionReady && "is-connected"]
            .filter(Boolean)
            .join(" ")}
        >
          <span>
            <ShieldCheck aria-hidden="true" />
            {sessionReady
              ? `Session: ${session.data?.operator ?? "connected"}`
              : magicLinkMode
                ? "An invited email link is required to launch."
                : "A local Lab session is required to launch."}
          </span>
          {!sessionReady ? (
            magicLinkMode ? (
              <div className="lab-magic-link-request">
                <label htmlFor="lab-access-email">
                  <span>Email</span>
                  <input
                    autoComplete="email"
                    id="lab-access-email"
                    onChange={(event) => setEmail(event.target.value)}
                    placeholder="you@team.example"
                    type="email"
                    value={email}
                  />
                </label>
                <button
                  disabled={requestingLink || !email}
                  onClick={() => void requestMagicLink()}
                  type="button"
                >
                  {requestingLink ? "Sending…" : "Send access link"}
                </button>
              </div>
            ) : (
              <button
                disabled={
                  connecting ||
                  session.kind === "loading" ||
                  authMode.kind !== "ready"
                }
                onClick={() => void connect()}
                type="button"
              >
                {connecting ? "Connecting…" : "Connect local session"}
              </button>
            )
          ) : null}
        </div>
        {draft ? (
          <section className="lab-frozen-draft" aria-labelledby="frozen-draft-title">
            <div>
              <FileClock aria-hidden="true" />
              <span id="frozen-draft-title">Unlaunched frozen draft</span>
            </div>
            <p>
              Run {draft.id} keeps its roster, map, budgets, vision, and skill
              condition exactly as cloned. Launching starts a fresh authority
              race; it never resumes the parent. This OpenAI-first Lab asks for
              a fresh session key for the launch.
            </p>
            <small>
              Parent contract{" "}
              {draft.parentContractSha256
                ? shortContractHash(draft.parentContractSha256)
                : "not published"}
              {draft.lineageChangeCount
                ? ` · ${draft.lineageChangeCount} declared ${draft.lineageChangeCount === 1 ? "change" : "changes"}`
                : " · exact frozen copy"}
            </small>
          </section>
        ) : (
          <fieldset>
            <legend>Model roster</legend>
            <ModelField color="blue" label="Sol" value={sol} onChange={setSol} />
            <ModelField
              color="coral"
              label="Terra"
              value={terra}
              onChange={setTerra}
            />
            <ModelField
              color="teal"
              label="Luna"
              value={luna}
              onChange={setLuna}
            />
          </fieldset>
        )}
        <label className="lab-field" htmlFor="lab-openai-api-key">
          <span>OpenAI API key</span>
          <div className="lab-key-field">
            <KeyRound aria-hidden="true" />
            <input
              aria-label="OpenAI API key"
              autoComplete="off"
              id="lab-openai-api-key"
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="Session-only key"
              type="password"
              value={apiKey}
            />
          </div>
          <small>
            Held only in React state for this submission, then cleared.
          </small>
        </label>
        {!draft ? (
          <>
            <label className="lab-field" htmlFor="lab-vision-depth">
              <span>Vision depth</span>
              <select
                id="lab-vision-depth"
                onChange={(event) =>
                  setVisionRangeCells(
                    event.target.value as LabLaunchInput["visionRangeCells"]
                  )
                }
                value={visionRangeCells}
              >
                <option value="1">1 cell</option>
                <option value="2">2 cells</option>
                <option value="4">4 cells</option>
                <option value="8">8 cells</option>
                <option value="infinite">Infinite to next wall</option>
              </select>
            </label>
            <label className="lab-skill-field">
              <span>
                <BrainCircuit aria-hidden="true" />
                Use maze navigation skill
              </span>
              <input
                checked={useSkill}
                onChange={(event) => setUseSkill(event.target.checked)}
                type="checkbox"
              />
            </label>
          </>
        ) : null}
        <div className="lab-contract-preview">
          <span>{draft ? "Frozen clone contract" : "Experiment contract"}</span>
          <p>
            {draft
              ? "This launch submits only a new session key. The saved clone contract supplies its exact game configuration; no configuration controls can change it here."
              : "Up to 192 model calls per racer (576 total) and 768 authority ticks per racer on the current frozen map. Tokens, elapsed time, and price remain provider-account dependent."}
          </p>
        </div>
        <label className="lab-contract-confirm">
          <input
            checked={contractConfirmed}
            onChange={(event) => setContractConfirmed(event.target.checked)}
            type="checkbox"
          />
          <span>
            {draft
              ? "I reviewed this frozen-clone launch. It starts a fresh race and does not resume the parent."
              : "I reviewed this exploratory-run envelope."}
          </span>
        </label>
        <button
          className="lab-launch-button"
          disabled={
            !apiKey ||
            !contractConfirmed ||
            state === "submitting" ||
            !sessionReady
          }
          type="submit"
        >
          <Play aria-hidden="true" />
          {state === "submitting"
            ? "Launching…"
            : draft
              ? "Launch frozen clone"
              : "Launch experiment"}
        </button>
        <p className={`lab-composer-notice is-${state}`} aria-live="polite">
          {notice ||
            (draft
              ? "A new API key is required for this fresh frozen-clone launch."
              : "Exploratory mode. Existing safety budgets remain enforced by the authority.")}
        </p>
      </form>
    </aside>
  )
}

function ModelField({
  color,
  label,
  onChange,
  value,
}: {
  color: string
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <label className="lab-model-field">
      <span>
        <i className={`is-${color}`} />
        {label}
      </span>
      <input
        aria-label={`${label} model`}
        onChange={(event) => onChange(event.target.value)}
        value={value}
      />
    </label>
  )
}

function LoadingPanel({ title }: { title: string }) {
  return (
    <section className="lab-empty-panel is-loading">
      <LoaderCircle aria-hidden="true" />
      <h1>{title}</h1>
      <p>Waiting for a safe server projection.</p>
    </section>
  )
}

function EmptyPanel({
  compact = false,
  description,
  title,
}: {
  compact?: boolean
  description: string
  title: string
}) {
  return (
    <section
      className={["lab-empty-panel", compact && "is-compact"]
        .filter(Boolean)
        .join(" ")}
    >
      <FileClock aria-hidden="true" />
      <h1>{title}</h1>
      <p>{description}</p>
    </section>
  )
}

function readinessLabel(readiness: LabReadiness): string {
  return readiness === "live_ready"
    ? "Live ready"
    : readiness === "demo_replay_ready"
      ? "Demo / replay ready"
      : "Experimental"
}
