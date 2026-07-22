"""Command-line entry point for the deterministic Labyrinth capability benchmark."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
from pathlib import Path
from typing import Mapping, Sequence

from .analysis import analyze_store, flatten_results, select_best_vision_depth
from .artifacts import BenchmarkArtifactStore, export_curated_report
from .report import generate_report
from .runner import (
    OpenAILabyrinthBenchmarkExecutor,
    acknowledge_terminal_stop,
    build_pilot_projection,
    run_full_season,
    run_schedule,
    validate_pilot_gate,
)
from .spec import (
    build_benchmark_spec,
    build_schedule,
    generate_map_suite,
)

DEFAULT_OUTPUT_ROOT = Path("runs/labyrinth-benchmarks")


def generate_season(output_root: Path, season_id: str) -> BenchmarkArtifactStore:
    pilot_maps = generate_map_suite("pilot")
    main_maps = generate_map_suite("main")
    spec = build_benchmark_spec(season_id, pilot_maps, main_maps)
    pilot = build_schedule(spec, pilot_maps, "pilot")
    baseline = build_schedule(spec, main_maps, "baseline")
    store = BenchmarkArtifactStore(output_root, season_id)
    store.initialize(spec, pilot_maps, main_maps, (pilot, baseline))
    return store


def _print_projection(value: Mapping[str, object]) -> None:
    projected_input = value["projected_remaining_input_tokens"]
    projected_output = value["projected_remaining_output_tokens"]
    projected_cost = value["projected_remaining_cost_microusd"]
    print("Pilot projection persisted before baseline continuation:")
    print(f"  remaining races: {value['remaining_races']}")
    print(f"  projected calls: {value['projected_remaining_calls']}")
    print(
        "  projected tokens: "
        f"{projected_input if projected_input is not None else 'unavailable'} input + "
        f"{projected_output if projected_output is not None else 'unavailable'} output"
    )
    print(f"  projected elapsed ms: {value['projected_remaining_elapsed_ms']}")
    print(
        "  pilot-scaled projected cost (micro-USD): "
        f"{projected_cost if projected_cost is not None else 'unavailable'}"
    )
    if not value["token_telemetry_complete"]:
        print(
            "  token/cost projection unavailable: "
            f"{value['token_telemetry_missing_episodes']} pilot episodes lack complete telemetry"
        )
    print(f"  pricing source: {value['pricing_source']} (as of {value['pricing_as_of']})")


def _progress(position: int, total: int, race_id: str) -> None:
    print(f"[{position}/{total}] {race_id}", flush=True)


async def _live_executor(store: BenchmarkArtifactStore) -> OpenAILabyrinthBenchmarkExecutor:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required in the local process environment")
    return OpenAILabyrinthBenchmarkExecutor(api_key, store.load_specification())


async def _pilot(store: BenchmarkArtifactStore) -> None:
    executor = await _live_executor(store)
    try:
        await run_schedule(
            store,
            store.load_schedule("pilot"),
            store.load_map_manifest("pilot"),
            executor,
            progress=_progress,
        )
        validate_pilot_gate(store)
        _print_projection(build_pilot_projection(store))
    finally:
        await executor.aclose()


async def _run(store: BenchmarkArtifactStore, phase: str) -> None:
    executor = await _live_executor(store)
    try:
        if phase == "all":
            await run_full_season(
                store,
                executor,
                progress=_progress,
                projection_callback=_print_projection,
            )
            analysis = analyze_store(store)
            manifest = generate_report(store)
            print(f"Selected skill depth: {analysis['selected_vision_depth']}")
            print(f"Report: {store.report_path}")
            print(f"Report digest: {manifest['html_sha256']}")
            return
        if phase == "baseline":
            validate_pilot_gate(store)
            _print_projection(build_pilot_projection(store))
            await run_schedule(
                store,
                store.load_schedule("baseline"),
                store.load_map_manifest("main"),
                executor,
                progress=_progress,
            )
            return
        if phase == "skill":
            rows = flatten_results(store.iter_results("baseline"))
            selected = select_best_vision_depth(rows)
            main_maps = store.load_map_manifest("main")
            skill = build_schedule(
                store.load_specification(), main_maps, "skill", selected_vision_depth=selected
            )
            store.save_schedule(skill)
            await run_schedule(store, skill, main_maps, executor, progress=_progress)
            return
        raise RuntimeError("benchmark phase is invalid")
    finally:
        await executor.aclose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="genesis-labyrinth-benchmark",
        description="Generate, resume, analyze, and report the Labyrinth capability benchmark.",
    )
    parser.add_argument(
        "--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT, help="Season parent directory"
    )
    parser.add_argument("--season-id", required=True, help="Frozen safe season identifier")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate", help="Generate frozen maps, spec, and pilot/baseline schedule")
    commands.add_parser("pilot", help="Run or resume the 120-race live pilot")
    run = commands.add_parser("run", help="Run or resume an official phase")
    run.add_argument("--phase", choices=("all", "baseline", "skill"), default="all")
    commands.add_parser("analyze", help="Analyze a complete 120/600/120 season")
    commands.add_parser("report", help="Render HTML, SVG, and PNG from complete analysis")
    commands.add_parser(
        "acknowledge-stop",
        help="Explicitly reopen a stopped season after credentials or infrastructure recover",
    )
    export = commands.add_parser(
        "export", help="Export only commit-safe frozen inputs, aggregates, and report artifacts"
    )
    export.add_argument("--destination", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "generate":
        store = generate_season(arguments.output_root, arguments.season_id)
        print(f"Season generated: {store.root}")
        print("Races: 120 pilot + 600 baseline; skill schedule follows objective depth selection")
        return 0
    store = BenchmarkArtifactStore(arguments.output_root, arguments.season_id)
    if arguments.command == "pilot":
        asyncio.run(_pilot(store))
    elif arguments.command == "run":
        asyncio.run(_run(store, arguments.phase))
    elif arguments.command == "analyze":
        analysis = analyze_store(store)
        print(f"Analysis: {store.analysis_path}")
        print(f"Selected skill depth: {analysis['selected_vision_depth']}")
    elif arguments.command == "report":
        manifest = generate_report(store)
        print(f"Report: {store.report_path}")
        print(f"Report digest: {manifest['html_sha256']}")
    elif arguments.command == "acknowledge-stop":
        acknowledge_terminal_stop(store)
        print("Stopped season acknowledged; the next run resumes from durable results.")
    elif arguments.command == "export":
        manifest = export_curated_report(store, arguments.destination)
        print(f"Curated export: {arguments.destination}")
        print(f"Curated export digest: {manifest['export_sha256']}")
    else:  # pragma: no cover - argparse owns the command domain.
        raise RuntimeError("benchmark command is invalid")
    rendered_report = arguments.command == "report" or (
        arguments.command == "run" and arguments.phase == "all"
    )
    if importlib.util.find_spec("matplotlib") is None and rendered_report:
        print(
            "Optional high-fidelity plotting stack is not installed; the deterministic Pillow/SVG "
            "renderer was used. Install with `pip install -e '.[benchmark]'` if desired."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "generate_season", "main"]
