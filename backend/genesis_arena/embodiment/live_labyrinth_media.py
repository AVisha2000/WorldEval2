"""Public-only Movie Maker export for a completed live Labyrinth Run.

The live maze authority deliberately retains controller output and scratchpads only in process
memory.  This module accepts the already verified public spatial replay, projects it into the
older broadcast scene's narrow replay shape, and produces one public MP4.  It never accepts a
provider response, credential, prompt, or protected decision object.

``LiveLabyrinthBroadcastRenderer`` is synchronous by design.  The async lifecycle can run it
through :func:`asyncio.to_thread` after the authority has sealed its replay, without holding an
event-loop thread while Godot and FFmpeg run.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .labyrinth_run import HEADINGS, PROTOCOL_VERSION
from .live_labyrinth import LIVE_TASK_ID, normalize_vision_range, verify_live_replay
from .maze_maps import MazeMapError, MazeMapSpec
from .protocol import canonical_json_bytes, strict_json_loads

_BROADCAST_TASK_ID = "trio-maze-race-v0"
_BROADCAST_SCHEMA = "worldarena/live-labyrinth-broadcast-projection/1"
_MOVIE_SCRIPT = "res://scripts/embodiment/trio_games/trio_maze_movie_maker_cli.gd"
_WIDTH = 1920
_HEIGHT = 1080
_FPS = 30
_INTRO_FRAMES = 5 * _FPS
_OUTRO_FRAMES = 7 * _FPS
_FRAMES_PER_TICK = 3
_MAXIMUM_RENDER_TICKS = 100_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class LiveLabyrinthMediaError(RuntimeError):
    """A public maze replay or its broadcast export is invalid."""


@dataclass(frozen=True)
class LiveLabyrinthVideo:
    """Small, browser-safe descriptor for a rendered public broadcast."""

    episode_id: str
    video_path: Path
    sha256: str
    size_bytes: int
    duration_milliseconds: int
    width: int = _WIDTH
    height: int = _HEIGHT
    fps: int = _FPS

    def public_dict(self) -> dict[str, object]:
        return {
            "available": True,
            "duration_seconds": self.duration_milliseconds / 1000,
            "fps": self.fps,
            "height": self.height,
            "mime_type": "video/mp4",
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "width": self.width,
        }


def project_live_labyrinth_broadcast_replay(replay: Mapping[str, Any]) -> dict[str, Any]:
    """Project a verified v1 spatial replay into the legacy maze broadcast input shape.

    The Godot scene only needs public positions, headings, entrant labels, and short public event
    labels.  In particular, it does not receive the model's request, raw output, scratchpad, or
    any field derived from them.  Calling the live verifier first also makes this a fail-closed
    public boundary.
    """

    try:
        verify_live_replay(replay)
    except Exception as error:
        raise LiveLabyrinthMediaError("live maze replay failed public verification") from error

    racers = replay["racers"]
    if not isinstance(racers, list):  # Defensive after verification, keeps this boundary local.
        raise LiveLabyrinthMediaError("live maze racers are unavailable")
    events = replay.get("events")
    result = replay.get("result")
    if not isinstance(events, list) or not isinstance(result, Mapping):
        raise LiveLabyrinthMediaError("live maze public timeline is unavailable")
    vision = replay.get("vision")
    if not isinstance(vision, Mapping):
        raise LiveLabyrinthMediaError("live maze public vision configuration is unavailable")
    vision_range_cells = vision.get("range_cells")
    maximum_ticks = replay.get("maximum_ticks")
    if (
        isinstance(maximum_ticks, bool)
        or not isinstance(maximum_ticks, int)
        or not 1 <= maximum_ticks <= _MAXIMUM_RENDER_TICKS
    ):
        raise LiveLabyrinthMediaError("live maze maximum ticks is invalid")
    public_map = _project_public_map(replay.get("map"))
    rows = tuple(public_map["rows"])

    events_by_participant: dict[str, list[Mapping[str, Any]]] = {}
    for event in events:
        if not isinstance(event, Mapping):
            raise LiveLabyrinthMediaError("live maze public event is invalid")
        participant_id = event.get("participant_id")
        if not isinstance(participant_id, str):
            raise LiveLabyrinthMediaError("live maze public event participant is invalid")
        events_by_participant.setdefault(participant_id, []).append(event)

    projected_racers = []
    for racer in sorted(racers, key=lambda value: str(value["participant_id"])):
        if not isinstance(racer, Mapping):
            raise LiveLabyrinthMediaError("live maze racer is invalid")
        participant_id = racer["participant_id"]
        if not isinstance(participant_id, str):
            raise LiveLabyrinthMediaError("live maze racer participant is invalid")
        keyframes = _project_keyframes(
            racer,
            events_by_participant.get(participant_id, ()),
            rows=rows,
            maximum_ticks=maximum_ticks,
            vision_range_cells=vision_range_cells,
        )
        projected_racers.append(
            {
                # The scene uses exactly this small public subset.
                "participant_id": participant_id,
                "entrant_id": str(racer["entrant_id"]),
                "display_name": str(racer["display_name"]),
                "model": str(racer["model"]),
                "color": str(racer["color"]),
                "finish_tick": racer["finish_tick"],
                "distance_cells": int(racer["distance_cells"]),
                "shortest_path_cells": int(racer["shortest_path_cells"]),
                "path_efficiency_basis_points": int(racer["path_efficiency_basis_points"]),
                "keyframes": keyframes,
            }
        )

    completion_tick = result.get("completion_tick")
    if isinstance(completion_tick, bool) or not isinstance(completion_tick, int):
        raise LiveLabyrinthMediaError("live maze completion tick is invalid")
    reason = result.get("reason")
    if not isinstance(reason, str):
        raise LiveLabyrinthMediaError("live maze result reason is invalid")
    projected_events = [_project_event(event) for event in events]
    body: dict[str, Any] = {
        "schema_version": _BROADCAST_SCHEMA,
        # The existing Godot scene checks its original replay identity.  This is a presentation
        # adapter only; the source v1 replay remains the authority and is never rewritten.
        "task_id": _BROADCAST_TASK_ID,
        "protocol_version": PROTOCOL_VERSION,
        "episode_id": replay["episode_id"],
        "maximum_ticks": maximum_ticks,
        "map": public_map,
        "vision": dict(vision),
        "racers": projected_racers,
        "events": projected_events,
        "result": {
            "completion_tick": completion_tick,
            "finish_order": list(result.get("finish_order", ())),
            "reason": reason,
            "winner_id": result.get("winner_id"),
        },
        "source": {
            "final_state_sha256": replay["final_state_sha256"],
            "task_id": LIVE_TASK_ID,
        },
    }
    body["projection_sha256"] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    return body


def _project_keyframes(
    racer: Mapping[str, Any],
    events: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
    *,
    rows: tuple[str, ...],
    maximum_ticks: int,
    vision_range_cells: object,
) -> list[dict[str, object]]:
    path = racer.get("path")
    if not isinstance(path, list) or not path:
        raise LiveLabyrinthMediaError("live maze path is unavailable")
    cells: list[tuple[int, int]] = []
    for cell in path:
        if (
            not isinstance(cell, list)
            or len(cell) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in cell)
        ):
            raise LiveLabyrinthMediaError("live maze path cell is invalid")
        cells.append((cell[0], cell[1]))
    ordered: dict[int, list[Mapping[str, Any]]] = {}
    for event in events:
        tick = event.get("tick")
        if isinstance(tick, bool) or not isinstance(tick, int) or not 0 < tick <= maximum_ticks:
            raise LiveLabyrinthMediaError("live maze event tick is invalid")
        ordered.setdefault(tick, []).append(event)

    current = cells[0]
    heading = 0
    cursor = 1
    frames: list[dict[str, object]] = [
        {
            "tick": 0,
            "cell": list(current),
            "heading": heading,
            "state": "thinking",
            "task": "exploring",
            "visible_cells": [
                list(cell) for cell in _visible_cells(rows, current, vision_range_cells)
            ],
        }
    ]
    for tick in sorted(ordered):
        group = ordered[tick]
        move_count = sum(1 for event in group if event.get("kind") == "move")
        if move_count > 1 or (move_count == 1 and cursor >= len(cells)):
            raise LiveLabyrinthMediaError("live maze event/path track differs")
        state = "thinking"
        task = "waiting"
        if move_count:
            target = cells[cursor]
            delta = (target[0] - current[0], target[1] - current[1])
            try:
                heading = HEADINGS.index(delta)
            except ValueError as error:
                raise LiveLabyrinthMediaError("live maze move heading is invalid") from error
            current = target
            cursor += 1
            state = "walk"
            task = "exploring"
        kinds = {str(event.get("kind")) for event in group}
        if "finish" in kinds:
            state = "celebrate"
            task = "finished"
        elif "invalid" in kinds:
            task = "plan_rejected"
        elif "provider_failure" in kinds:
            task = "provider_wait"
        elif move_count == 0 and "accepted" in kinds:
            task = "waiting"
        frames.append(
            {
                "tick": tick,
                "cell": list(current),
                "heading": heading,
                "state": state,
                "task": task,
                "visible_cells": [
                    list(cell) for cell in _visible_cells(rows, current, vision_range_cells)
                ],
            }
        )
    if cursor != len(cells):
        raise LiveLabyrinthMediaError("live maze public move track is incomplete")
    return frames


def _project_public_map(value: object) -> dict[str, Any]:
    """Return the verifier-approved, JSON-safe public map without adding private state."""

    if not isinstance(value, Mapping):
        raise LiveLabyrinthMediaError("live maze public map is unavailable")
    try:
        return MazeMapSpec.from_dict(value).as_dict()
    except MazeMapError:
        # Live replays produced before map-spec/1 carried the fixed showcase geometry plus flat
        # topology metrics.  Their authority verifier remains the compatibility boundary while
        # archived episodes drain; all newly generated maps take the strict branch above.
        if "schema_version" in value or "start" in value or "exit" in value:
            raise LiveLabyrinthMediaError("live maze public map is invalid") from None
    try:
        public_map = strict_json_loads(canonical_json_bytes(value))
    except (TypeError, ValueError) as error:
        raise LiveLabyrinthMediaError("live maze public map is invalid") from error
    if not isinstance(public_map, dict):
        raise LiveLabyrinthMediaError("live maze public map is invalid")
    rows = public_map.get("rows")
    if (
        not isinstance(rows, list)
        or not rows
        or any(not isinstance(row, str) or not row for row in rows)
        or len({len(row) for row in rows}) != 1
    ):
        raise LiveLabyrinthMediaError("live maze public map rows are invalid")
    return public_map


def _visible_cells(
    rows: tuple[str, ...],
    position: tuple[int, int],
    vision_range_cells: object,
) -> tuple[tuple[int, int], ...]:
    """Project straight-line sightlines against the replay's injected public geometry."""

    try:
        vision_range = normalize_vision_range(vision_range_cells)
    except ValueError as error:
        raise LiveLabyrinthMediaError("live maze public vision range is invalid") from error

    width = len(rows[0])

    def walkable(cell: tuple[int, int]) -> bool:
        x, y = cell
        return 0 <= y < len(rows) and 0 <= x < width and rows[y][x] != "#"

    if not walkable(position):
        raise LiveLabyrinthMediaError("live maze public path is outside the map")
    limit = max(width, len(rows)) if vision_range == "infinite" else vision_range
    visible = {position}
    for dx, dy in HEADINGS:
        for distance in range(1, limit + 1):
            candidate = (position[0] + dx * distance, position[1] + dy * distance)
            if not walkable(candidate):
                break
            visible.add(candidate)
    return tuple(sorted(visible))


def _project_event(event: Mapping[str, Any]) -> dict[str, object]:
    participant_id = event.get("participant_id")
    kind = event.get("kind")
    tick = event.get("tick")
    if (
        not isinstance(participant_id, str)
        or not isinstance(kind, str)
        or isinstance(tick, bool)
        or not isinstance(tick, int)
    ):
        raise LiveLabyrinthMediaError("live maze event is invalid")
    labels = {
        "accepted": "Waiting at passage",
        "finish": "Exit found!",
        "invalid": "Plan rejected",
        "move": "Advancing through maze",
        "provider_failure": "Provider unavailable",
    }
    return {
        "tick": tick,
        "participant_id": participant_id,
        "kind": kind,
        "label": labels.get(kind, "Exploring"),
    }


def render_live_labyrinth_broadcast_mp4(
    *,
    replay: Mapping[str, Any],
    output_path: Path,
    godot_executable: Path,
    godot_project_path: Path,
    ffmpeg_executable: Path,
) -> LiveLabyrinthVideo:
    """Synchronously render a verified public maze replay to a fast-start 1080p MP4."""

    projected = project_live_labyrinth_broadcast_replay(replay)
    output = Path(output_path).resolve()
    godot = Path(godot_executable).resolve()
    project = Path(godot_project_path).resolve()
    ffmpeg = Path(ffmpeg_executable).resolve()
    if output.suffix.lower() != ".mp4":
        raise LiveLabyrinthMediaError("live maze broadcast output must be an MP4")
    if not project.is_dir() or not (project / "project.godot").is_file():
        raise LiveLabyrinthMediaError("Godot project is unavailable")
    for executable, label in (
        (godot, "pinned Godot executable"),
        (ffmpeg, "local FFmpeg executable"),
    ):
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise LiveLabyrinthMediaError(f"{label} is unavailable")
    if "remotion" in str(ffmpeg).casefold():
        raise LiveLabyrinthMediaError("live maze broadcast encoder is invalid")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="worldarena-live-maze-media-") as temporary:
        staging = Path(temporary)
        replay_path = staging / "public-broadcast.replay.json"
        movie_path = staging / "maze.avi"
        replay_path.write_bytes(canonical_json_bytes(projected))
        _run(
            (
                str(godot),
                "--no-header",
                "--audio-driver",
                "Dummy",
                "--path",
                str(project),
                "--rendering-method",
                "gl_compatibility",
                "--resolution",
                f"{_WIDTH}x{_HEIGHT}",
                "--fixed-fps",
                str(_FPS),
                "--disable-vsync",
                "--write-movie",
                str(movie_path),
                "--script",
                _MOVIE_SCRIPT,
                "--",
                f"--labyrinth-replay={replay_path}",
            ),
            cwd=project,
            timeout_s=900,
            failure="Godot live maze Movie Maker render failed",
        )
        if not movie_path.is_file() or movie_path.stat().st_size < 1024:
            raise LiveLabyrinthMediaError("Godot live maze Movie Maker produced no video")
        _run(
            (
                str(ffmpeg),
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(movie_path),
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-shortest",
                "-map_metadata",
                "-1",
                "-vf",
                "scale=1920:1080:force_original_aspect_ratio=decrease:"
                "in_range=full:out_range=tv,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p",
                "-r",
                str(_FPS),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-color_range",
                "tv",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(output),
            ),
            cwd=project,
            timeout_s=600,
            failure="FFmpeg live maze broadcast encoding failed",
        )
    duration_ms = _verify_video(output, ffmpeg)
    expected_duration_ms = _expected_duration_milliseconds(int(projected["maximum_ticks"]))
    if abs(duration_ms - expected_duration_ms) > 100:
        output.unlink(missing_ok=True)
        raise LiveLabyrinthMediaError("live maze broadcast duration differs")
    payload = output.read_bytes()
    return LiveLabyrinthVideo(
        episode_id=str(replay["episode_id"]),
        video_path=output,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        duration_milliseconds=duration_ms,
    )


def _expected_duration_milliseconds(maximum_ticks: int) -> int:
    frames = _INTRO_FRAMES + maximum_ticks * _FRAMES_PER_TICK + _OUTRO_FRAMES
    return frames * 1000 // _FPS


class LiveLabyrinthBroadcastRenderer:
    """Synchronous callback suitable for a service's post-completion archive task."""

    def __init__(
        self,
        *,
        output_root: Path,
        godot_executable: Path,
        godot_project_path: Path,
        ffmpeg_executable: Path,
    ) -> None:
        self._output_root = Path(output_root).resolve()
        self._godot = Path(godot_executable).resolve()
        self._project = Path(godot_project_path).resolve()
        self._ffmpeg = Path(ffmpeg_executable).resolve()

    def __call__(self, replay: Mapping[str, Any]) -> LiveLabyrinthVideo:
        return self.render(replay)

    def render(self, replay: Mapping[str, Any]) -> LiveLabyrinthVideo:
        episode_id = replay.get("episode_id") if isinstance(replay, Mapping) else None
        if not isinstance(episode_id, str) or not re.fullmatch(
            r"ep_[A-Za-z0-9_-]{1,160}", episode_id
        ):
            raise LiveLabyrinthMediaError("live maze episode id is invalid")
        return render_live_labyrinth_broadcast_mp4(
            replay=replay,
            output_path=self._output_root / episode_id / "labyrinth-run-broadcast.mp4",
            godot_executable=self._godot,
            godot_project_path=self._project,
            ffmpeg_executable=self._ffmpeg,
        )


def _run(command: tuple[str, ...], *, cwd: Path, timeout_s: float, failure: str) -> None:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LiveLabyrinthMediaError(failure) from error
    if completed.returncode != 0:
        raise LiveLabyrinthMediaError(failure)


def _verify_video(path: Path, ffmpeg: Path) -> int:
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise LiveLabyrinthMediaError("live maze broadcast video is missing") from error
    if (
        len(payload) < 1024
        or b"ftyp" not in payload[:32]
        or payload.find(b"moov") < 0
        or payload.find(b"mdat") < 0
        or payload.find(b"moov") > payload.find(b"mdat")
    ):
        raise LiveLabyrinthMediaError("live maze broadcast video is not fast-start MP4")
    try:
        completed = subprocess.run(
            (str(ffmpeg), "-hide_banner", "-i", str(path), "-f", "null", "-"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LiveLabyrinthMediaError("live maze broadcast video probe failed") from error
    report = completed.stdout
    match = re.search(r"Duration: (\d{2}):(\d{2}):(\d{2})\.(\d{2})", report)
    if (
        completed.returncode != 0
        or match is None
        or "Video: h264" not in report
        or "yuv420p(" not in report
        or "yuvj420p" in report
        or f"{_WIDTH}x{_HEIGHT}" not in report
        or "30 fps" not in report
        or "Audio: aac" not in report
        or "48000 Hz, stereo" not in report
    ):
        raise LiveLabyrinthMediaError("live maze broadcast video codec differs")
    hours, minutes, seconds, centiseconds = (int(value) for value in match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + centiseconds * 10


__all__ = [
    "LiveLabyrinthBroadcastRenderer",
    "LiveLabyrinthMediaError",
    "LiveLabyrinthVideo",
    "project_live_labyrinth_broadcast_replay",
    "render_live_labyrinth_broadcast_mp4",
]
