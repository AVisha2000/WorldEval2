import { useEffect, useId, useRef, useState } from "react"
import type { CSSProperties, FormEvent, RefObject } from "react"
import {
  ArrowRight,
  BrainCircuit,
  Check,
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
import { labRunFrameUrl, labRunVideoUrl } from "./lab-api"
import { isOpenAiLabRunMode, LAB_GAME_CATEGORIES } from "./types"
import type {
  LabBenchmark,
  LabAuthMode,
  LabGame,
  LabGenericLaunchInput,
  LabGameCapabilities,
  LabGameCategory,
  LabLaunchInput,
  LabMazeFrame,
  LabReadiness,
  LabRun,
  LabSandboxManifest,
  LabSession,
  LabProjection,
  LabPublicReplay,
  LabSpectatorFeed,
  RemoteState,
  LabRunEvidenceAction,
  LabRunEvidenceResult,
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
              aria-current={active ? "page" : undefined}
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
  cancelEnabled,
  games,
  onCancel,
  showcase,
  showcaseState,
  spectator,
  onSelectGame,
}: {
  activeRun: RemoteState<{
    run: LabRun
    projection: LabProjection
    pollRevision: number
  } | null>
  cancelEnabled: boolean
  games: RemoteState<LabGame[]>
  onCancel: (run: LabRun) => Promise<void>
  showcase: CachedMazeShowcaseView | null
  showcaseState: RemoteState<CachedMazeShowcaseView>
  spectator: RemoteState<LabSpectatorFeed | null>
  onSelectGame: (gameId: string) => void
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [playing, setPlaying] = useState(false)
  if (activeRun.kind === "loading") {
    return <LoadingPanel title="Connecting to run authority" />
  }
  if (activeRun.kind === "offline") {
    return (
      <EmptyPanel
        title="Run authority unavailable"
        description="The Lab could not refresh this run's safe authority evidence. No cached game is substituted for the active run."
      />
    )
  }
  if (activeRun.kind === "ready" && activeRun.data) {
    if (activeRun.data.run.gameId !== "labyrinth-run") {
      return (
        <GenericGameStage
          cancelEnabled={cancelEnabled}
          games={games}
          key={activeRun.data.run.id}
          onSelectGame={onSelectGame}
          onCancel={onCancel}
          pollRevision={activeRun.data.pollRevision}
          projection={activeRun.data.projection}
          run={activeRun.data.run}
        />
      )
    }
    return (
      <LiveMazeStage
        cancelEnabled={cancelEnabled}
        games={games}
        onCancel={onCancel}
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
      <GameCataloguePicker games={games} onSelectGame={onSelectGame} />
    </section>
  )
}

function RunStateControl({
  cancelEnabled,
  label,
  onCancel,
  run,
}: {
  cancelEnabled: boolean
  label: string
  onCancel: (run: LabRun) => Promise<void>
  run: LabRun
}) {
  const [state, setState] = useState<"idle" | "cancelling" | "failed">("idle")
  const cancellable = ["queued", "running", "checkpointed"].includes(
    run.lifecycle
  )
  return (
    <div className="lab-run-state-control">
      <span className="lab-live-state">{label}</span>
      {cancellable ? (
        <button
          aria-label={`Cancel active run ${run.id}`}
          disabled={!cancelEnabled || state === "cancelling"}
          onClick={() => {
            setState("cancelling")
            void onCancel(run).catch(() => setState("failed"))
          }}
          type="button"
        >
          {state === "cancelling"
            ? "Cancelling…"
            : state === "failed"
              ? "Cancel rejected"
              : "Cancel run"}
        </button>
      ) : null}
    </div>
  )
}

function LiveMazeStage({
  cancelEnabled,
  games,
  onCancel,
  onSelectGame,
  projection,
  run,
  spectator,
}: {
  cancelEnabled: boolean
  games: RemoteState<LabGame[]>
  onCancel: (run: LabRun) => Promise<void>
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
        <RunStateControl
          cancelEnabled={cancelEnabled && !liveAuthorityUnavailable}
          label={liveAuthorityUnavailable ? "interrupted" : lifecycle}
          onCancel={onCancel}
          run={run}
        />
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
          <span>
            Live authority unavailable · clone to launch a fresh race.
          </span>
        ) : null}
      </div>
      <GameCataloguePicker games={games} onSelectGame={onSelectGame} />
    </section>
  )
}

function GenericGameStage({
  cancelEnabled,
  games,
  onCancel,
  onSelectGame,
  pollRevision,
  projection,
  run,
}: {
  cancelEnabled: boolean
  games: RemoteState<LabGame[]>
  onCancel: (run: LabRun) => Promise<void>
  onSelectGame: (gameId: string) => void
  pollRevision: number
  projection: LabProjection
  run: LabRun
}) {
  const game =
    games.kind === "ready"
      ? (games.data.find((candidate) => candidate.id === run.gameId) ?? null)
      : null
  const summary = projection.summary
  const participantCount = Math.min(
    3,
    Math.max(
      1,
      summary?.entrants.length ??
        (game && game.participants.minimum === game.participants.maximum
          ? game.participants.maximum
          : 1)
    )
  )
  const participants = Array.from(
    { length: participantCount },
    (_, index) => `participant_${index}`
  )
  const tabGroupId = useId()
  const [selectedParticipant, setSelectedParticipant] = useState(
    participants[0]
  )
  const revision = Math.max(
    pollRevision,
    projection.sequence,
    summary?.authority?.authorityTick ?? 0,
    summary?.authority?.decisionSequence ?? 0
  )
  const frameSrc = labRunFrameUrl(run.id, selectedParticipant, revision)
  const [failedFrameSrc, setFailedFrameSrc] = useState<string | null>(null)
  const selectedIndex = Number(selectedParticipant.replace("participant_", ""))
  const interrupted = isInterruptedRun(run)
  const lifecycle = interrupted
    ? "interrupted"
    : run.lifecycle.replaceAll("_", " ")
  const frameUnavailable =
    run.authorityAvailable === false || failedFrameSrc === frameSrc

  return (
    <section
      className="lab-simulation lab-generic-simulation"
      aria-label={`${game?.title ?? run.gameId} authority spectator`}
    >
      <header className="lab-section-heading">
        <div>
          <span className="lab-stage-kicker">
            {game?.primaryCategory.label ?? "Godot authority"}
          </span>
          <h1>{game?.title ?? run.gameId}</h1>
          <p>
            Sanitized participant pixels from the active Godot authority. This
            view cannot issue actions, advance state, or reveal private model
            material.
          </p>
        </div>
        <RunStateControl
          cancelEnabled={cancelEnabled && !interrupted}
          label={lifecycle}
          onCancel={onCancel}
          run={run}
        />
      </header>

      {participantCount > 1 ? (
        <div
          className="lab-participant-tabs"
          role="tablist"
          aria-label="Participant frame"
        >
          {participants.map((participantId, index) => {
            const selected = participantId === selectedParticipant
            return (
              <button
                aria-label={`Seat ${index + 1} · ${participantId}`}
                aria-controls={`${tabGroupId}-panel`}
                aria-selected={selected}
                id={`${tabGroupId}-${participantId}`}
                key={participantId}
                onClick={() => setSelectedParticipant(participantId)}
                onKeyDown={(event) => {
                  const requestedIndex =
                    event.key === "ArrowRight"
                      ? (index + 1) % participantCount
                      : event.key === "ArrowLeft"
                        ? (index - 1 + participantCount) % participantCount
                        : event.key === "Home"
                          ? 0
                          : event.key === "End"
                            ? participantCount - 1
                            : null
                  if (requestedIndex === null) return
                  event.preventDefault()
                  setSelectedParticipant(participants[requestedIndex])
                  const tabs =
                    event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>(
                      '[role="tab"]'
                    )
                  tabs?.[requestedIndex]?.focus()
                }}
                role="tab"
                tabIndex={selected ? 0 : -1}
                type="button"
              >
                <span>{`Seat ${index + 1}`}</span>
                <small>{participantId}</small>
              </button>
            )
          })}
        </div>
      ) : null}

      <div
        aria-label={
          participantCount === 1 ? "Seat 1 authority frame" : undefined
        }
        aria-labelledby={
          participantCount > 1
            ? `${tabGroupId}-${selectedParticipant}`
            : undefined
        }
        className="lab-generic-stage"
        id={`${tabGroupId}-panel`}
        role="tabpanel"
      >
        {frameUnavailable ? (
          <div className="lab-generic-frame-unavailable" role="status">
            <Video aria-hidden="true" />
            <div>
              <b>Participant frame unavailable</b>
              <p>
                {run.authorityAvailable === false
                  ? "This process no longer owns the live authority capability. Saved evidence remains below."
                  : "The authority has not published a safe PNG for this participant yet."}
              </p>
            </div>
          </div>
        ) : (
          <img
            alt={`Seat ${selectedIndex + 1} safe authority frame`}
            onError={() => setFailedFrameSrc(frameSrc)}
            src={frameSrc}
          />
        )}
      </div>

      <dl className="lab-generic-evidence" aria-label="Run evidence summary">
        <div>
          <dt>Authority</dt>
          <dd>{summary?.authority?.state ?? lifecycle}</dd>
        </div>
        <div>
          <dt>Mode</dt>
          <dd>{summary?.mode ?? run.mode ?? "not published"}</dd>
        </div>
        <div>
          <dt>Progress</dt>
          <dd>
            {summary?.authority?.authorityTick !== null &&
            summary?.authority?.authorityTick !== undefined
              ? `tick ${summary.authority.authorityTick} · decision ${summary.authority.decisionSequence}`
              : "Waiting for authority receipt"}
          </dd>
        </div>
        <div>
          <dt>Evidence</dt>
          <dd>
            {summary?.terminalAvailable
              ? `${summary.eventCount} sealed events`
              : summary?.authority?.replayState
                ? `Replay ${summary.authority.replayState}`
                : "Live projection"}
          </dd>
        </div>
        <div>
          <dt>Contract</dt>
          <dd>
            {run.contractSha256
              ? shortContractHash(run.contractSha256)
              : "not published"}
          </dd>
        </div>
      </dl>

      <div className="lab-live-footer">
        <span>Run {run.id}</span>
        <span>
          {summary?.gameVersion ?? run.gameVersion ?? "Version not published"}
        </span>
        <span>{participantCount} isolated participant view(s)</span>
      </div>
      <GameCataloguePicker games={games} onSelectGame={onSelectGame} />
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
      <div
        className="lab-spectator-controls"
        aria-label="Live spectator director"
      >
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
            {paused ? (
              <Play aria-hidden="true" />
            ) : (
              <Pause aria-hidden="true" />
            )}
            {paused ? "Resume" : "Pause"}
          </button>
        </div>
        <div
          className="lab-spectator-control-group"
          aria-label="Playback speed"
        >
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
      <circle
        className="lab-map-racer"
        fill={safeRacerColor(racer.color)}
        r="0.3"
      />
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
        cell[0] === currentPath[index]?.[0] &&
        cell[1] === currentPath[index]?.[1]
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

type GameCategoryGroup = {
  category: LabGameCategory
  games: LabGame[]
}

function groupedGames(games: LabGame[]): GameCategoryGroup[] {
  const serverCategories = new Map(
    games.map((game) => [game.primaryCategory.id, game.primaryCategory])
  )
  return LAB_GAME_CATEGORIES.map((fallback) => {
    const category = serverCategories.get(fallback.id) ?? fallback
    return {
      category,
      games: games
        .filter((game) => game.primaryCategory.id === fallback.id)
        .sort((first, second) => first.title.localeCompare(second.title)),
    }
  })
}

function participantLabel(game: LabGame): string {
  const { maximum, minimum } = game.participants
  if (minimum === maximum) {
    return `${minimum} ${minimum === 1 ? "agent" : "agents"}`
  }
  return `${minimum}–${maximum} agents`
}

function supportedCapabilityLabels(
  capabilities: LabGameCapabilities
): string[] {
  return [
    capabilities.liveLaunch ? "Live" : null,
    capabilities.demo ? "Demo" : null,
    capabilities.replay ? "Replay" : null,
    capabilities.spectator ? "Spectator" : null,
    capabilities.benchmark ? "Benchmark" : null,
    capabilities.checkpoint ? "Checkpoint" : null,
  ].filter((label): label is string => label !== null)
}

function gameOptionLabel(game: LabGame): string {
  const capabilityLabels = supportedCapabilityLabels(game.capabilities)
  return [
    game.title,
    readinessLabel(game.readiness),
    participantLabel(game),
    capabilityLabels.length ? capabilityLabels.join(" + ") : "Guide only",
  ].join(" · ")
}

function GameCataloguePicker({
  games,
  onSelectGame,
  selectedGameId = null,
  variant = "rail",
}: {
  games: RemoteState<LabGame[]>
  onSelectGame: (gameId: string) => void
  selectedGameId?: string | null
  variant?: "rail" | "compact"
}) {
  const pickerId = useId()
  if (games.kind !== "ready") {
    return (
      <p className="lab-rail-state">
        Game catalogue {games.kind === "loading" ? "loading" : "is offline"}.
      </p>
    )
  }
  const descriptionId = `${pickerId}-description`
  const groups = groupedGames(games.data)
  const selectedGame =
    games.data.find((game) => game.id === selectedGameId) ?? null
  const selectedCapabilities = selectedGame
    ? supportedCapabilityLabels(selectedGame.capabilities)
    : []

  return (
    <section
      className={`lab-game-picker is-${variant}`}
      aria-label="Browse games"
    >
      <div className="lab-game-picker-control">
        <label htmlFor={pickerId}>
          <span>Game catalogue</span>
          <strong aria-hidden="true">
            {selectedGame
              ? selectedGame.primaryCategory.label
              : "Choose an environment"}
          </strong>
        </label>
        <div className="lab-game-picker-select">
          <select
            aria-describedby={descriptionId}
            id={pickerId}
            onChange={(event) => {
              const gameId = event.currentTarget.value
              if (gameId) onSelectGame(gameId)
            }}
            value={selectedGame?.id ?? ""}
          >
            <option disabled value="">
              Choose a game
            </option>
            {groups.map(({ category, games: categoryGames }) => (
              <optgroup
                key={category.id}
                label={`${category.label} · ${categoryGames.length} ${
                  categoryGames.length === 1 ? "game" : "games"
                }`}
              >
                {categoryGames.length ? (
                  categoryGames.map((game) => (
                    <option key={game.id} value={game.id}>
                      {gameOptionLabel(game)}
                    </option>
                  ))
                ) : (
                  <option disabled value={`empty:${category.id}`}>
                    No admitted games yet
                  </option>
                )}
              </optgroup>
            ))}
          </select>
          <span aria-hidden="true">⌄</span>
        </div>
        <p id={descriptionId}>
          {selectedGame ? (
            <>
              <b>{participantLabel(selectedGame)}</b>
              <span>{selectedGame.interactionKind.replaceAll("_", " ")}</span>
              <span>{readinessLabel(selectedGame.readiness)}</span>
              <span>
                {selectedCapabilities.length
                  ? selectedCapabilities.join(" · ")
                  : "Guide only"}
              </span>
            </>
          ) : (
            "Games are grouped by authority shape, then labelled with readiness, participant count, and supported evidence features."
          )}
        </p>
      </div>
      {variant === "rail" ? (
        <ul
          className="lab-game-picker-groups"
          aria-label="Game catalogue categories"
        >
          {groups.map(({ category, games: categoryGames }) => (
            <li
              className={categoryGames.length ? "" : "is-empty"}
              key={category.id}
            >
              <span>{String(category.order).padStart(2, "0")}</span>
              <div>
                <b>{category.label}</b>
                <small>
                  {categoryGames.length
                    ? `${categoryGames.length} ${
                        categoryGames.length === 1 ? "game" : "games"
                      }`
                    : "Coming soon"}
                </small>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}

export function GameGuide({
  game,
  games,
  onSelectGame,
  sandbox,
  showSwitcher = true,
}: {
  game: LabGame | null
  games: RemoteState<LabGame[]>
  onSelectGame: (gameId: string) => void
  sandbox?: RemoteState<LabSandboxManifest>
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
          <GameCataloguePicker
            games={games}
            onSelectGame={onSelectGame}
            selectedGameId={game.id}
            variant="compact"
          />
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
      {game.id === "operator-action-course" && sandbox ? (
        <SandboxManifestPanel manifest={sandbox} />
      ) : null}
    </section>
  )
}

function SandboxManifestPanel({
  manifest,
}: {
  manifest: RemoteState<LabSandboxManifest>
}) {
  if (manifest.kind === "loading") {
    return (
      <section className="lab-sandbox-panel" aria-labelledby="sandbox-title">
        <header>
          <span>Godot-owned composition</span>
          <h2 id="sandbox-title">Loading Sandbox Primitives</h2>
        </header>
      </section>
    )
  }
  if (manifest.kind === "offline") {
    return (
      <section className="lab-sandbox-panel" aria-labelledby="sandbox-title">
        <header>
          <span>Godot-owned composition</span>
          <h2 id="sandbox-title">Sandbox manifest unavailable</h2>
          <p>
            The course remains documented, but primitive composition metadata
            failed closed.
          </p>
        </header>
      </section>
    )
  }
  const executableRecipe = manifest.data.recipes.find(
    (recipe) => recipe.executable
  )
  const titleById = new Map(
    manifest.data.primitives.map((primitive) => [primitive.id, primitive.title])
  )
  return (
    <section className="lab-sandbox-panel" aria-labelledby="sandbox-title">
      <header>
        <div>
          <span>Godot-owned composition</span>
          <h2 id="sandbox-title">Sandbox Primitives</h2>
          <p>
            Ten reusable authority capabilities, shown in deterministic
            dependency order. The Lab describes the recipe; Godot remains the
            only gameplay authority.
          </p>
        </div>
        <strong>{manifest.data.primitives.length} primitives</strong>
      </header>
      <ol className="lab-sandbox-primitives">
        {manifest.data.primitives.map((primitive, index) => (
          <li key={primitive.id}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <div>
              <h3>{primitive.title}</h3>
              <p>{primitive.summary}</p>
              <small>
                {primitive.dependencies.length
                  ? `Depends on ${primitive.dependencies
                      .map(
                        (dependency) => titleById.get(dependency) ?? dependency
                      )
                      .join(" · ")}`
                  : "Foundation primitive"}
              </small>
            </div>
          </li>
        ))}
      </ol>
      {executableRecipe ? (
        <article
          className="lab-sandbox-recipe"
          aria-labelledby="sandbox-recipe-title"
        >
          <div>
            <span>Single executable recipe</span>
            <h3 id="sandbox-recipe-title">{executableRecipe.title}</h3>
            <p>{executableRecipe.summary}</p>
          </div>
          <dl>
            <div>
              <dt>Authority task</dt>
              <dd>{executableRecipe.taskId}</dd>
            </div>
            <div>
              <dt>Protocol</dt>
              <dd>{executableRecipe.protocolVersion}</dd>
            </div>
            <div>
              <dt>Composition</dt>
              <dd>{executableRecipe.compositionOrder.length} ordered steps</dd>
            </div>
          </dl>
        </article>
      ) : null}
    </section>
  )
}

export function PublicGamePage({
  benchmark,
  game,
  replays,
}: {
  benchmark: RemoteState<LabBenchmark>
  game: RemoteState<LabGame>
  replays: RemoteState<LabPublicReplay[]>
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
  return (
    <div className="lab-public-page">
      <GameGuide
        game={game.data}
        games={{ kind: "ready", data: [game.data] }}
        onSelectGame={() => undefined}
        showSwitcher={false}
      />
      <PublishedReplayShelf game={game.data} replays={replays} />
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

function PublishedReplayShelf({
  game,
  replays,
}: {
  game: LabGame
  replays: RemoteState<LabPublicReplay[]>
}) {
  const requestedSlug =
    typeof window === "undefined"
      ? null
      : new URLSearchParams(window.location.search).get("replay")
  const [selectedSlug, setSelectedSlug] = useState<string | null>(requestedSlug)
  if (replays.kind === "loading") {
    return <LoadingPanel title="Loading published replays" />
  }
  if (replays.kind === "offline") {
    return (
      <EmptyPanel
        title="Published replays are unavailable"
        description="The safe public replay index could not be verified. No replay evidence is shown."
      />
    )
  }
  if (!replays.data.length) {
    return (
      <EmptyPanel
        title="No replay has been published"
        description="Operators must explicitly verify and publish a safe cartridge before it appears on this unlisted page."
      />
    )
  }
  if (
    requestedSlug &&
    !replays.data.some((replay) => replay.publicationSlug === requestedSlug)
  ) {
    return (
      <EmptyPanel
        title="Published replay not found"
        description="This unlisted replay link is invalid or has been unpublished. No different replay was substituted."
      />
    )
  }
  const selected =
    replays.data.find((replay) => replay.publicationSlug === selectedSlug) ??
    replays.data[0]

  function selectReplay(publicationSlug: string) {
    setSelectedSlug(publicationSlug)
    if (typeof window === "undefined") return
    const url = new URL(window.location.href)
    url.searchParams.set("replay", publicationSlug)
    window.history.replaceState(
      window.history.state,
      "",
      `${url.pathname}${url.search}${url.hash}`
    )
  }

  return (
    <section
      className="lab-public-replay"
      aria-labelledby="published-replay-title"
    >
      <header>
        <div>
          <span>Published replay</span>
          <h2 id="published-replay-title">{game.title} evidence</h2>
          <p>
            Explicitly published authority evidence only. Credentials, private
            observations, model memory, prompts, and raw responses are excluded.
          </p>
        </div>
        <span>{replays.data.length} safe replay(s)</span>
      </header>
      <div
        className="lab-public-replay-picker"
        role="list"
        aria-label="Published safe replays"
      >
        {replays.data.map((replay, index) => (
          <div key={replay.publicationSlug} role="listitem">
            <button
              aria-pressed={selected.publicationSlug === replay.publicationSlug}
              onClick={() => selectReplay(replay.publicationSlug)}
              type="button"
            >
              <b>Replay {index + 1}</b>
              <small>
                {replay.lifecycle} ·{" "}
                {new Date(replay.publishedAt).toLocaleDateString()}
              </small>
            </button>
          </div>
        ))}
      </div>
      {selected.frame ? (
        <LiveSpectatorDirector
          fallbackFrame={selected.frame}
          feed={null}
          feedState="ready"
        />
      ) : (
        <dl
          className="lab-generic-evidence"
          aria-label="Published replay evidence"
        >
          <GuideDefinition label="Game version" text={selected.gameVersion} />
          <GuideDefinition label="Lifecycle" text={selected.lifecycle} />
          <GuideDefinition
            label="Entrants"
            text={
              selected.summary
                ? selected.summary.entrants
                    .map((entrant) => entrant.displayName)
                    .join(", ")
                : "Safe roster unavailable"
            }
          />
          <GuideDefinition
            label="Evidence"
            text={`${selected.eventCount} events · sequence ${selected.sequence}`}
          />
        </dl>
      )}
      <small className="lab-public-replay-receipt">
        Publication {selected.publicationSlug.slice(0, 12)}… ·{" "}
        {selected.eventCount} sealed events
      </small>
    </section>
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
  cancelEnabled,
  cloneEnabled,
  evidenceEnabled,
  onCancel,
  onClone,
  onEvidenceAction,
  onOpen,
  runs,
}: {
  cancelEnabled: boolean
  cloneEnabled: boolean
  evidenceEnabled: boolean
  onCancel: (run: LabRun) => Promise<void>
  onClone: (run: LabRun) => Promise<void>
  onEvidenceAction: (
    run: LabRun,
    action: LabRunEvidenceAction
  ) => Promise<LabRunEvidenceResult>
  onOpen: (run: LabRun) => void
  runs: RemoteState<LabRun[]>
}) {
  const [cloningRunId, setCloningRunId] = useState<string | null>(null)
  const [cancellingRunId, setCancellingRunId] = useState<string | null>(null)
  const [cloneNotice, setCloneNotice] = useState("")
  const [evidenceBusy, setEvidenceBusy] = useState<string | null>(null)
  const [evidenceNotice, setEvidenceNotice] =
    useState<LabRunEvidenceResult | null>(null)

  async function clone(run: LabRun) {
    if (!cloneEnabled || !isCloneLaunchSupported(run) || cloningRunId) return
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

  async function actOnEvidence(run: LabRun, action: LabRunEvidenceAction) {
    if (!evidenceEnabled || evidenceBusy) return
    setEvidenceBusy(`${run.id}:${action}`)
    setEvidenceNotice(null)
    try {
      setEvidenceNotice(await onEvidenceAction(run, action))
    } catch {
      setEvidenceNotice({
        run: null,
        notice:
          "The evidence operation was rejected. The existing cartridge was left unchanged.",
        publicPath: null,
      })
    } finally {
      setEvidenceBusy(null)
    }
  }

  async function cancel(run: LabRun) {
    if (!cancelEnabled || cancellingRunId) return
    setCancellingRunId(run.id)
    setEvidenceNotice(null)
    try {
      await onCancel(run)
      setEvidenceNotice({
        run: null,
        notice: "Run cancelled by the authority.",
        publicPath: null,
      })
    } catch {
      setEvidenceNotice({
        run: null,
        notice:
          "Cancellation was rejected. The Lab did not assume that the authority stopped.",
        publicPath: null,
      })
    } finally {
      setCancellingRunId(null)
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
          <span role="columnheader">Run</span>
          <span role="columnheader">Game</span>
          <span role="columnheader">State</span>
          <span role="columnheader">Lineage</span>
          <span role="columnheader">Artifacts</span>
          <span role="columnheader" aria-label="Run actions" />
        </div>
        {runs.data.map((run) => (
          <div className="lab-run-row" key={run.id} role="row">
            <span role="cell">
              <b>{run.id}</b>
              <small>
                {run.createdAt
                  ? new Date(run.createdAt).toLocaleString()
                  : "Creation time not published"}
              </small>
            </span>
            <span role="cell">
              <b>{run.gameId}</b>
              <small>{run.gameVersion ?? "Version not published"}</small>
            </span>
            <span className="lab-run-state" role="cell">
              {isInterruptedRun(run)
                ? "interrupted"
                : run.lifecycle.replaceAll("_", " ")}
              {run.lifecycle === "draft" ? (
                <small>Frozen configuration</small>
              ) : isInterruptedRun(run) ? (
                <small>Authority unavailable · no resume</small>
              ) : null}
            </span>
            <span className="lab-run-lineage" role="cell">
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
            <span className="lab-artifact-pairs" role="cell">
              <small>
                {run.replayAvailable ? "Replay available" : "Replay pending"}
              </small>
              <small>
                {run.videoAvailable ? "Video available" : "Video pending"}
              </small>
            </span>
            <span className="lab-run-actions" role="cell">
              {run.lifecycle === "draft" ? (
                isCloneLaunchSupported(run) ? (
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
                      isCloneLaunchSupported(run)
                        ? `Clone frozen configuration for ${run.id}`
                        : `Clone unavailable for ${run.id}: unsupported launch contract`
                    }
                    className="lab-run-clone"
                    disabled={
                      !cloneEnabled ||
                      !isCloneLaunchSupported(run) ||
                      cloningRunId !== null
                    }
                    onClick={() => void clone(run)}
                    title={
                      isCloneLaunchSupported(run)
                        ? undefined
                        : "Only credential-free Demo and OpenAI Lab runs can be cloned."
                    }
                    type="button"
                  >
                    {!isCloneLaunchSupported(run)
                      ? "Unsupported"
                      : cloningRunId === run.id
                        ? "Cloning…"
                        : "Clone"}
                  </button>
                  {["queued", "running", "checkpointed"].includes(
                    run.lifecycle
                  ) && !isInterruptedRun(run) ? (
                    <button
                      aria-label={`Cancel active run ${run.id}`}
                      className="lab-run-cancel"
                      disabled={!cancelEnabled || cancellingRunId !== null}
                      onClick={() => void cancel(run)}
                      type="button"
                    >
                      {cancellingRunId === run.id ? "Cancelling…" : "Cancel"}
                    </button>
                  ) : null}
                  {run.lifecycle === "completed" ? (
                    <button
                      aria-label={`Seal completed cartridge for ${run.id}`}
                      className="lab-run-evidence"
                      disabled={
                        !evidenceEnabled ||
                        !run.replayAvailable ||
                        evidenceBusy !== null
                      }
                      onClick={() => void actOnEvidence(run, "seal")}
                      type="button"
                    >
                      {evidenceBusy === `${run.id}:seal` ? "Sealing…" : "Seal"}
                    </button>
                  ) : null}
                  {run.lifecycle === "sealed" ? (
                    <button
                      aria-label={`Verify durable evidence for ${run.id}`}
                      className="lab-run-evidence"
                      disabled={!evidenceEnabled || evidenceBusy !== null}
                      onClick={() => void actOnEvidence(run, "verify")}
                      type="button"
                    >
                      {evidenceBusy === `${run.id}:verify`
                        ? "Verifying…"
                        : "Verify"}
                    </button>
                  ) : null}
                  {run.lifecycle === "verified" ? (
                    <button
                      aria-label={`Publish safe replay for ${run.id}`}
                      className="lab-run-evidence"
                      disabled={!evidenceEnabled || evidenceBusy !== null}
                      onClick={() => void actOnEvidence(run, "publish")}
                      type="button"
                    >
                      {evidenceBusy === `${run.id}:publish`
                        ? "Publishing…"
                        : "Publish"}
                    </button>
                  ) : null}
                  {run.lifecycle === "verified" &&
                  run.gameId === "labyrinth-run" &&
                  run.mode === "sealed_benchmark" ? (
                    <button
                      aria-label={`Submit verified benchmark evidence for ${run.id}`}
                      className="lab-run-evidence"
                      disabled={!evidenceEnabled || evidenceBusy !== null}
                      onClick={() => void actOnEvidence(run, "benchmark")}
                      type="button"
                    >
                      {evidenceBusy === `${run.id}:benchmark`
                        ? "Submitting…"
                        : "Leaderboard"}
                    </button>
                  ) : null}
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
      <p className="lab-run-evidence-notice" aria-live="polite">
        {evidenceNotice ? (
          evidenceNotice.publicPath ? (
            <>
              {evidenceNotice.notice}{" "}
              <a href={evidenceNotice.publicPath}>Open unlisted replay</a>
            </>
          ) : (
            evidenceNotice.notice
          )
        ) : (
          "Completed runs can be sealed, verified, and explicitly published without exposing private model material."
        )}
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

function isCloneLaunchSupported(run: LabRun): boolean {
  return (
    (isOpenAiLabRunMode(run.mode) && run.provider === "openai") ||
    (run.mode === "demo" && run.provider === null)
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
        <span role="columnheader">Model</span>
        <span role="columnheader">Completion</span>
        <span role="columnheader">Calls</span>
        <span role="columnheader">Path efficiency</span>
        <span role="columnheader">Cost</span>
        <span role="columnheader">Evidence</span>
      </div>
      {rows.map((row) => (
        <div className="lab-leaderboard-row" key={row.model} role="row">
          <b role="cell">{row.model}</b>
          <span role="cell">{row.completion ?? "—"}</span>
          <span role="cell">{row.calls ?? "—"}</span>
          <span role="cell">{row.pathEfficiency ?? "—"}</span>
          <span role="cell">{row.cost ?? "—"}</span>
          <span role="cell">{row.evidence ?? "—"}</span>
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
  game: LabGame | null
  onConnect: () => Promise<void>
  onLaunchDraft: (draft: LabRun, apiKey: string) => Promise<string | null>
  onLaunchGeneric: (input: LabGenericLaunchInput) => Promise<string | null>
  onLaunchLabyrinth: (input: LabLaunchInput) => Promise<string | null>
  onRequestMagicLink: (email: string) => Promise<void>
  session: RemoteState<LabSession | null>
}

const DEFAULT_COMPOSER_MODELS = [
  "gpt-5.6-sol",
  "gpt-5.6-terra",
  "gpt-5.6-luna",
] as const
const MODEL_IDENTIFIER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/
const GENERIC_SEAT_LABELS = {
  1: ["Agent"],
  2: ["Alpha", "Bravo"],
  3: ["Alpha", "Bravo", "Charlie"],
} as const

function availableLaunchModes(game: LabGame | null): Array<"demo" | "live"> {
  if (!game) return []
  if (game.id === "labyrinth-run") {
    return game.capabilities.liveLaunch ? ["live"] : []
  }
  return [
    game.capabilities.demo ? "demo" : null,
    game.capabilities.liveLaunch ? "live" : null,
  ].filter((mode): mode is "demo" | "live" => mode !== null)
}

function fixedParticipantCount(game: LabGame | null): 1 | 2 | 3 | null {
  if (
    !game ||
    game.participants.minimum !== game.participants.maximum ||
    ![1, 2, 3].includes(game.participants.maximum)
  ) {
    return null
  }
  return game.participants.maximum as 1 | 2 | 3
}

export function RunComposer({
  authMode,
  draft,
  game,
  onConnect,
  onLaunchDraft,
  onLaunchGeneric,
  onLaunchLabyrinth,
  onRequestMagicLink,
  session,
}: ComposerProps) {
  const [apiKey, setApiKey] = useState("")
  const [models, setModels] = useState<string[]>(() => [
    ...DEFAULT_COMPOSER_MODELS,
  ])
  const launchModes = availableLaunchModes(game)
  const [launchMode, setLaunchMode] = useState<"demo" | "live" | null>(() =>
    game?.id === "labyrinth-run"
      ? "live"
      : launchModes.includes("demo")
        ? "demo"
        : (launchModes[0] ?? null)
  )
  const [seed, setSeed] = useState("7")
  const [visionRangeCells, setVisionRangeCells] =
    useState<LabLaunchInput["visionRangeCells"]>("4")
  const [useSkill, setUseSkill] = useState(false)
  const [labyrinthIntent, setLabyrinthIntent] =
    useState<LabLaunchInput["mode"]>("exploratory")
  const [contractConfirmed, setContractConfirmed] = useState(false)
  const [state, setState] = useState<
    "idle" | "submitting" | "accepted" | "failed"
  >("idle")
  const [connecting, setConnecting] = useState(false)
  const [email, setEmail] = useState("")
  const [requestingLink, setRequestingLink] = useState(false)
  const [notice, setNotice] = useState("")
  const participantCount = fixedParticipantCount(game)
  const launchingFrozenClone = draft !== null
  const draftDemo = draft?.mode === "demo"
  const admittedLaunchMode =
    launchMode && launchModes.includes(launchMode)
      ? launchMode
      : (launchModes[0] ?? null)
  const effectiveMode = launchingFrozenClone
    ? draftDemo
      ? "demo"
      : "live"
    : admittedLaunchMode
  const requiresApiKey = effectiveMode === "live"
  const launchAvailable =
    launchingFrozenClone ||
    Boolean(
      game &&
      participantCount &&
      effectiveMode &&
      launchModes.includes(effectiveMode)
    )
  const isLabyrinth = !launchingFrozenClone && game?.id === "labyrinth-run"
  const parsedSeed = Number(seed)
  const seedIsValid =
    seed.trim() !== "" &&
    Number.isInteger(parsedSeed) &&
    parsedSeed >= 0 &&
    parsedSeed <= 2_147_483_647
  const seatLabels = isLabyrinth
    ? (["Sol", "Terra", "Luna"] as const)
    : participantCount
      ? GENERIC_SEAT_LABELS[participantCount]
      : []
  const modelRosterIsValid =
    launchingFrozenClone ||
    effectiveMode !== "live" ||
    (seatLabels.length > 0 &&
      models
        .slice(0, seatLabels.length)
        .every((model) => MODEL_IDENTIFIER_PATTERN.test(model)))

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (
      !launchAvailable ||
      (requiresApiKey && !apiKey) ||
      !modelRosterIsValid ||
      (!launchingFrozenClone && !isLabyrinth && !seedIsValid) ||
      state === "submitting" ||
      session.kind !== "ready" ||
      !session.data
    )
      return
    setState("submitting")
    setNotice("")
    try {
      let runId: string | null
      if (draft) {
        runId = await onLaunchDraft(draft, apiKey)
      } else if (isLabyrinth) {
        runId = await onLaunchLabyrinth({
          apiKey,
          provider: "openai",
          models: { sol: models[0], terra: models[1], luna: models[2] },
          visionRangeCells,
          skillMode: useSkill ? "maze-navigation-v1" : "none",
          mode: labyrinthIntent,
        })
      } else if (game && participantCount && effectiveMode) {
        runId = await onLaunchGeneric({
          gameId: game.id,
          mode: effectiveMode,
          seed: parsedSeed,
          apiKey,
          models: models.slice(0, participantCount),
        })
      } else {
        throw new Error("Game launch is unavailable")
      }
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

  return (
    <aside className="lab-composer" aria-labelledby="composer-title">
      <div className="lab-composer-title">
        <div>
          <span>{launchingFrozenClone ? "Frozen clone" : "Game"}</span>
          <h2 id="composer-title">
            {launchingFrozenClone
              ? `Clone · ${game?.title ?? draft.gameId}`
              : (game?.title ?? "Choose a game")}
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
          <section
            className="lab-frozen-draft"
            aria-labelledby="frozen-draft-title"
          >
            <div>
              <FileClock aria-hidden="true" />
              <span id="frozen-draft-title">Unlaunched frozen draft</span>
            </div>
            <p>
              Run {draft.id} keeps its roster, seed, budgets, and authority
              binding exactly as cloned. Launching starts a fresh authority
              episode; it never resumes the parent.{" "}
              {draftDemo
                ? "This Demo clone is credential-free."
                : "This OpenAI clone requires a fresh session key."}
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
            <legend>
              {effectiveMode === "demo"
                ? "Authority Demo roster"
                : "Model roster"}
            </legend>
            {seatLabels.map((label, index) => (
              <ModelField
                color={["blue", "coral", "teal"][index]}
                disabled={effectiveMode === "demo"}
                invalid={
                  effectiveMode === "live" &&
                  !MODEL_IDENTIFIER_PATTERN.test(models[index] ?? "")
                }
                key={label}
                label={label}
                value={
                  effectiveMode === "demo"
                    ? "Authority Demo policy"
                    : models[index]
                }
                onChange={(value) =>
                  setModels((current) =>
                    current.map((model, modelIndex) =>
                      modelIndex === index ? value : model
                    )
                  )
                }
                required={effectiveMode === "live"}
              />
            ))}
            {!seatLabels.length ? (
              <p className="lab-composer-unavailable">
                This passport does not publish a fixed one-, two-, or
                three-participant launch roster.
              </p>
            ) : null}
          </fieldset>
        )}
        {!draft && launchModes.length ? (
          <label className="lab-field" htmlFor="lab-run-mode">
            <span>Run mode</span>
            <select
              id="lab-run-mode"
              onChange={(event) => {
                const mode = event.target.value as "demo" | "live"
                setLaunchMode(mode)
                setContractConfirmed(false)
                if (mode === "demo") setApiKey("")
              }}
              value={effectiveMode ?? ""}
            >
              {launchModes.map((mode) => (
                <option key={mode} value={mode}>
                  {mode === "demo"
                    ? "Demo · credential-free"
                    : "Live · OpenAI session key"}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {requiresApiKey && launchAvailable ? (
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
        ) : null}
        {!draft && game && game.id !== "labyrinth-run" && launchAvailable ? (
          <label className="lab-field" htmlFor="lab-run-seed">
            <span>Authority seed</span>
            <input
              id="lab-run-seed"
              inputMode="numeric"
              max="2147483647"
              min="0"
              onChange={(event) => setSeed(event.target.value)}
              required
              type="number"
              value={seed}
            />
          </label>
        ) : null}
        {isLabyrinth ? (
          <>
            <label className="lab-field" htmlFor="labyrinth-run-intent">
              <span>Experiment intent</span>
              <select
                id="labyrinth-run-intent"
                onChange={(event) => {
                  const intent = event.target.value as LabLaunchInput["mode"]
                  setLabyrinthIntent(intent)
                  setContractConfirmed(false)
                  if (intent === "sealed_benchmark") {
                    setVisionRangeCells("4")
                    setUseSkill(false)
                  }
                }}
                value={labyrinthIntent}
              >
                <option value="exploratory">Exploratory · configurable</option>
                <option value="sealed_benchmark">
                  Benchmark candidate · frozen recipe
                </option>
              </select>
            </label>
            <label className="lab-field" htmlFor="lab-vision-depth">
              <span>Vision depth</span>
              <select
                disabled={labyrinthIntent === "sealed_benchmark"}
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
                disabled={labyrinthIntent === "sealed_benchmark"}
                onChange={(event) => setUseSkill(event.target.checked)}
                type="checkbox"
              />
            </label>
            {labyrinthIntent === "sealed_benchmark" ? (
              <small className="lab-composer-recipe-note">
                Recipe labyrinth-interactive-v1 locks vision to 4 cells and
                disables the optional skill. Completion still must be sealed and
                verified before leaderboard admission.
              </small>
            ) : null}
          </>
        ) : null}
        <div className="lab-contract-preview">
          <span>{draft ? "Frozen clone contract" : "Experiment contract"}</span>
          <p>
            {draft
              ? draftDemo
                ? "This launch submits an empty envelope. The frozen Demo contract supplies every model, seed, budget, and authority binding."
                : "This launch submits only a new session key. The saved clone contract supplies its exact game configuration; no controls can change it here."
              : !launchAvailable
                ? "No admitted live or Demo runtime is published for this game. Its guide and replay evidence remain available."
                : isLabyrinth
                  ? `${labyrinthIntent === "sealed_benchmark" ? "Frozen benchmark recipe" : "Exploratory contract"}: up to 192 model calls per racer (576 total) and 768 authority ticks per racer on the current map. Tokens, elapsed time, and price remain provider-account dependent.`
                  : effectiveMode === "demo"
                    ? `Credential-free deterministic Demo with ${participantCount} authority-selected ${participantCount === 1 ? "policy" : "policies"} and fixed seed ${seed}.`
                    : `${participantCount} OpenAI ${participantCount === 1 ? "seat" : "seats"} with fixed seed ${seed}; the session key is never persisted by the composer.`}
          </p>
        </div>
        {launchAvailable ? (
          <label className="lab-contract-confirm">
            <input
              checked={contractConfirmed}
              onChange={(event) => setContractConfirmed(event.target.checked)}
              type="checkbox"
            />
            <span>
              {draft
                ? draft.gameId === "labyrinth-run"
                  ? "I reviewed this frozen-clone launch. It starts a fresh race and does not resume the parent."
                  : "I reviewed this frozen-clone launch. It starts a fresh run and does not resume the parent."
                : effectiveMode === "demo"
                  ? "I reviewed this credential-free Demo contract."
                  : isLabyrinth && labyrinthIntent === "sealed_benchmark"
                    ? "I reviewed this sealed benchmark candidate contract."
                    : "I reviewed this exploratory-run envelope."}
            </span>
          </label>
        ) : null}
        <button
          className="lab-launch-button"
          disabled={
            !launchAvailable ||
            (requiresApiKey && !apiKey) ||
            !modelRosterIsValid ||
            (!launchingFrozenClone && !isLabyrinth && !seedIsValid) ||
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
              : launchAvailable
                ? `Launch ${effectiveMode === "demo" ? "Demo" : "experiment"}`
                : "Launch unavailable"}
        </button>
        <p className={`lab-composer-notice is-${state}`} aria-live="polite">
          {notice ||
            (draft
              ? draftDemo
                ? "The cloned Demo relaunches from its frozen contract without credentials."
                : "A new API key is required for this fresh frozen-clone launch."
              : !launchAvailable
                ? "Guide and replay only. No launch authority is admitted."
                : effectiveMode === "demo"
                  ? "Demo mode uses locked authority policies and does not make provider calls."
                  : isLabyrinth && labyrinthIntent === "sealed_benchmark"
                    ? "Benchmark candidate mode. Completion alone does not publish or rank the run."
                    : "Exploratory mode. Existing safety budgets remain enforced by the authority.")}
        </p>
      </form>
    </aside>
  )
}

function ModelField({
  color,
  disabled = false,
  invalid = false,
  label,
  onChange,
  required = false,
  value,
}: {
  color: string
  disabled?: boolean
  invalid?: boolean
  label: string
  value: string
  onChange: (value: string) => void
  required?: boolean
}) {
  return (
    <label className="lab-model-field">
      <span>
        <i className={`is-${color}`} />
        {label}
      </span>
      <input
        aria-label={`${label} model`}
        aria-invalid={invalid || undefined}
        disabled={disabled}
        maxLength={128}
        onChange={(event) => onChange(event.target.value)}
        pattern="[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
        required={required}
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
