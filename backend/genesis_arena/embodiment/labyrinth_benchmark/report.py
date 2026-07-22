# ruff: noqa: E501
"""Accessible deterministic SVG/PNG and standalone HTML Labyrinth benchmark report."""

from __future__ import annotations

import hashlib
import html
import io
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont

from .artifacts import BenchmarkArtifactStore, atomic_write, atomic_write_json, load_canonical_json
from .spec import DEFAULT_MODELS, DIFFICULTIES, VISION_DEPTHS

WIDTH = 1280
HEIGHT = 900
_MODEL_LOOKUP = {item.model_id: item for item in DEFAULT_MODELS}
_DASHES = {"solid": "", "dashed": "10 6", "dotted": "3 6"}


class BenchmarkReportError(RuntimeError):
    """A benchmark report cannot be rendered from the supplied analysis."""


@dataclass(frozen=True)
class FigureDefinition:
    slug: str
    title: str
    alt_text: str
    metric: str
    unit: str
    fixed_basis_points: bool = False


_BASELINE_FIGURES = (
    FigureDefinition(
        "calls-by-vision",
        "Budget-charged calls by vision depth (lower is better)",
        "Small multiples compare mean budget-charged calls with 95 percent confidence bands "
        "for Sol, Terra, and Luna across five vision depths and four maze difficulties.",
        "charged_calls",
        "calls",
    ),
    FigureDefinition(
        "completion-by-vision",
        "Completion probability by vision depth",
        "Small multiples compare completion percentage with 95 percent confidence bands for "
        "the three models across vision depths and maze difficulties.",
        "completion_basis_points",
        "completion (%)",
        True,
    ),
    FigureDefinition(
        "path-efficiency-by-vision",
        "Successful-run path efficiency by vision depth",
        "Small multiples compare path efficiency among completed episodes. Missing values are "
        "not converted to zeros.",
        "path_efficiency_basis_points",
        "path efficiency (%)",
        True,
    ),
    FigureDefinition(
        "input-tokens-by-vision",
        "Measured input tokens by vision depth (lower is better)",
        "Small multiples compare measured input tokens with 95 percent confidence bands; "
        "episodes lacking token telemetry are shown as missing.",
        "input_tokens",
        "input tokens",
    ),
    FigureDefinition(
        "output-tokens-by-vision",
        "Measured output tokens by vision depth (lower is better)",
        "Small multiples compare measured output tokens with 95 percent confidence bands; "
        "episodes lacking token telemetry are shown as missing.",
        "output_tokens",
        "output tokens",
    ),
    FigureDefinition(
        "tokens-by-vision",
        "Measured tokens by vision depth (lower is better)",
        "Small multiples compare total measured tokens with 95 percent confidence bands; "
        "episodes lacking token telemetry are excluded and counted as missing.",
        "total_tokens",
        "tokens",
    ),
    FigureDefinition(
        "latency-by-vision",
        "Provider latency by vision depth (lower is better)",
        "Small multiples compare summed per-episode provider latency with 95 percent confidence "
        "bands across models, depths, and difficulties.",
        "latency_ms",
        "milliseconds",
    ),
    FigureDefinition(
        "corridor-control-by-vision",
        "Corridor-command share by vision depth",
        "Small multiples compare the percentage of decisions that explicitly authorized "
        "corridor traversal for each model.",
        "corridor_command_basis_points",
        "corridor commands (%)",
        True,
    ),
    FigureDefinition(
        "memory-peak-by-vision",
        "Peak backend navigation-memory bytes",
        "Small multiples compare peak bounded navigation-memory usage for each model and "
        "vision depth.",
        "peak_memory_bytes",
        "bytes",
    ),
    FigureDefinition(
        "memory-evictions-by-vision",
        "Navigation-memory compaction events by vision depth",
        "Small multiples compare bounded-memory compaction events. At most one compaction event "
        "is counted per model decision.",
        "memory_evictions",
        "evictions",
    ),
    FigureDefinition(
        "recovery-rate-by-vision",
        "Recovery after invalid or failed decisions",
        "Small multiples compare successful next-decision recovery percentages. Cells with no "
        "recovery opportunity are reported as missing rather than perfect recovery.",
        "recovery_basis_points",
        "recovery (%)",
        True,
    ),
)


def baseline_figure_rows(
    analysis: Mapping[str, Any], metric: str
) -> tuple[Mapping[str, Any], ...]:
    groups = analysis.get("groups")
    if not isinstance(groups, list):
        raise BenchmarkReportError("analysis groups are unavailable")
    rows = tuple(
        row
        for row in groups
        if isinstance(row, Mapping)
        and row.get("phase") == "baseline"
        and row.get("skill_mode") == "none"
        and row.get("metric") == metric
    )
    if not rows:
        raise BenchmarkReportError(f"analysis metric is unavailable: {metric}")
    return rows


def _scale_value(value: int, *, basis_points: bool) -> float:
    return value / 100 if basis_points else float(value)


def _format_value(value: float, *, basis_points: bool) -> str:
    if basis_points:
        return f"{value:.1f}%"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:.1f}" if value != round(value) else str(round(value))


def _to_y(value: float, *, top: float, plot_h: float, y_min: float, y_max: float) -> float:
    return top + plot_h - (value - y_min) / (y_max - y_min) * plot_h


def _line_layout(rows: Sequence[Mapping[str, Any]], *, basis_points: bool) -> tuple[float, float]:
    values = [
        _scale_value(int(row[key]), basis_points=basis_points)
        for row in rows
        for key in ("lower", "upper", "point")
        if isinstance(row.get(key), int) and not isinstance(row.get(key), bool)
    ]
    if not values:
        return (0.0, 100.0) if basis_points else (0.0, 1.0)
    minimum = min(0.0, min(values))
    maximum = 100.0 if basis_points else max(values)
    if basis_points and min(values) < 0:
        minimum = min(values)
        maximum = max(values)
    if maximum <= minimum:
        maximum = minimum + 1.0
    padding = (maximum - minimum) * 0.08
    return minimum - (padding if minimum < 0 else 0), maximum + padding


def _svg_marker(x: float, y: float, model_id: str) -> str:
    model = _MODEL_LOOKUP[model_id]
    if model.marker == "square":
        return f'<rect x="{x - 4:.1f}" y="{y - 4:.1f}" width="8" height="8" fill="{model.color}"/>'
    if model.marker == "triangle":
        return (
            f'<polygon points="{x:.1f},{y - 5:.1f} {x - 5:.1f},{y + 4:.1f} '
            f'{x + 5:.1f},{y + 4:.1f}" fill="{model.color}"/>'
        )
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{model.color}"/>'


def _baseline_line_svg(definition: FigureDefinition, rows: Sequence[Mapping[str, Any]]) -> str:
    y_min, y_max = _line_layout(rows, basis_points=definition.fixed_basis_points)
    title_id = f"{definition.slug}-title"
    desc_id = f"{definition.slug}-desc"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'role="img" aria-labelledby="{title_id} {desc_id}">',
        f'<title id="{title_id}">{html.escape(definition.title)}</title>',
        f'<desc id="{desc_id}">{html.escape(definition.alt_text)}</desc>',
        '<rect width="1280" height="900" fill="#ffffff"/>',
        f'<text x="52" y="44" font-family="DejaVu Sans, sans-serif" font-size="26" '
        f'font-weight="700" fill="#17212b">{html.escape(definition.title)}</text>',
        f'<text x="52" y="72" font-family="DejaVu Sans, sans-serif" font-size="14" '
        f'fill="#52606d">Mean with deterministic 95% hierarchical bootstrap interval · '
        f'{html.escape(definition.unit)}</text>',
    ]
    panel_w, panel_h = 570, 330
    origins = ((70, 115), (680, 115), (70, 505), (680, 505))
    x_labels = ("1", "2", "4", "8", "∞")
    for difficulty, (origin_x, origin_y) in zip(DIFFICULTIES, origins):
        panel_rows = [row for row in rows if row["difficulty"] == difficulty]
        if not panel_rows:
            raise BenchmarkReportError("baseline figure difficulty is incomplete")
        left, top = origin_x + 58, origin_y + 42
        plot_w, plot_h = panel_w - 126, panel_h - 92
        parts.append(
            f'<text x="{origin_x}" y="{origin_y + 20}" font-family="DejaVu Sans, sans-serif" '
            f'font-size="18" font-weight="700" fill="#17212b">'
            f'{html.escape(difficulty.replace("_", " ").title())}</text>'
        )
        sample_sizes = sorted({int(row["sample_size"]) for row in panel_rows})
        censored = sum(int(row["censored_count"]) for row in panel_rows)
        missing = sum(int(row["missing_count"]) for row in panel_rows)
        parts.append(
            f'<text x="{origin_x + panel_w - 2}" y="{origin_y + 20}" text-anchor="end" '
            f'font-family="DejaVu Sans, sans-serif" font-size="12" fill="#697785">'
            f'n/model={"/".join(map(str, sample_sizes))}; censored={censored}; missing={missing}</text>'
        )
        for grid_index in range(5):
            value = y_min + (y_max - y_min) * grid_index / 4
            y = top + plot_h - plot_h * grid_index / 4
            parts.extend(
                (
                    f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" '
                    'stroke="#dde3e8" stroke-width="1"/>',
                    f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" '
                    f'font-family="DejaVu Sans, sans-serif" font-size="11" fill="#52606d">'
                    f'{html.escape(_format_value(value, basis_points=definition.fixed_basis_points))}</text>',
                )
            )
        x_points = [left + plot_w * index / 4 for index in range(5)]
        for x, label in zip(x_points, x_labels):
            parts.append(
                f'<text x="{x:.1f}" y="{top + plot_h + 22}" text-anchor="middle" '
                f'font-family="DejaVu Sans, sans-serif" font-size="12" fill="#52606d">{label}</text>'
            )
        for model_index, model in enumerate(DEFAULT_MODELS):
            model_rows = {
                row["vision_depth"]: row
                for row in panel_rows
                if row["model_id"] == model.model_id
            }
            if set(model_rows) != set(VISION_DEPTHS):
                raise BenchmarkReportError("baseline figure model-depth cells are incomplete")
            segments: list[list[tuple[float, float, float, float]]] = []
            segment: list[tuple[float, float, float, float]] = []
            for x, vision in zip(x_points, VISION_DEPTHS):
                row = model_rows[vision]
                if row.get("point") is None:
                    if segment:
                        segments.append(segment)
                        segment = []
                    parts.append(
                        f'<text x="{x:.1f}" y="{top + plot_h - 8:.1f}" text-anchor="middle" '
                        'font-family="DejaVu Sans, sans-serif" font-size="12" fill="#89949e">NA</text>'
                    )
                    continue
                point = _scale_value(int(row["point"]), basis_points=definition.fixed_basis_points)
                lower = _scale_value(int(row["lower"]), basis_points=definition.fixed_basis_points)
                upper = _scale_value(int(row["upper"]), basis_points=definition.fixed_basis_points)
                segment.append(
                    (
                        x,
                        _to_y(point, top=top, plot_h=plot_h, y_min=y_min, y_max=y_max),
                        _to_y(lower, top=top, plot_h=plot_h, y_min=y_min, y_max=y_max),
                        _to_y(upper, top=top, plot_h=plot_h, y_min=y_min, y_max=y_max),
                    )
                )
            if segment:
                segments.append(segment)
            dash = _DASHES[model.line_style]
            dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
            plotted_points = []
            for current in segments:
                polygon = " ".join(
                    f"{x:.1f},{y:.1f}"
                    for x, _point, lower, upper in current
                    for y in (upper,)
                ) + " " + " ".join(
                    f"{x:.1f},{lower:.1f}"
                    for x, _point, lower, _upper in reversed(current)
                )
                points = [(x, point) for x, point, _lower, _upper in current]
                parts.append(
                    f'<polygon points="{polygon}" fill="{model.color}" fill-opacity="0.11"/>'
                )
                parts.append(
                    f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in points)}" '
                    f'fill="none" stroke="{model.color}" stroke-width="2.5"{dash_attr}/>'
                )
                parts.extend(_svg_marker(x, y, model.model_id) for x, y in points)
                plotted_points.extend(points)
            if not plotted_points:
                continue
            end_x, end_y = plotted_points[-1]
            label_y = end_y + (-15 + model_index * 15)
            parts.append(
                f'<text x="{end_x + 8:.1f}" y="{label_y:.1f}" font-family="DejaVu Sans, sans-serif" '
                f'font-size="12" font-weight="700" fill="{model.color}">{model.display_name}</text>'
            )
        parts.append(
            f'<text x="{left + plot_w / 2:.1f}" y="{top + plot_h + 43}" text-anchor="middle" '
            'font-family="DejaVu Sans, sans-serif" font-size="12" fill="#52606d">Vision depth (cells)</text>'
        )
    parts.append(
        '<text x="52" y="882" font-family="DejaVu Sans, sans-serif" font-size="12" '
        'fill="#52606d">Bands resample map seeds first, then repetitions. Failed episodes remain '
        'budget-censored for calls and completion.</text></svg>'
    )
    return "".join(parts)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default()


def _baseline_line_png(
    definition: FigureDefinition, rows: Sequence[Mapping[str, Any]]
) -> bytes:
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image, "RGBA")
    draw.text((52, 28), definition.title, fill="#17212b", font=_font(26, bold=True))
    draw.text(
        (52, 64),
        f"Mean with 95% hierarchical bootstrap interval · {definition.unit}",
        fill="#52606d",
        font=_font(14),
    )
    y_min, y_max = _line_layout(rows, basis_points=definition.fixed_basis_points)
    panel_w, panel_h = 570, 330
    origins = ((70, 115), (680, 115), (70, 505), (680, 505))
    for difficulty, (origin_x, origin_y) in zip(DIFFICULTIES, origins):
        panel_rows = [row for row in rows if row["difficulty"] == difficulty]
        left, top = origin_x + 58, origin_y + 42
        plot_w, plot_h = panel_w - 126, panel_h - 92
        draw.text(
            (origin_x, origin_y),
            difficulty.replace("_", " ").title(),
            fill="#17212b",
            font=_font(18, bold=True),
        )
        for grid_index in range(5):
            value = y_min + (y_max - y_min) * grid_index / 4
            y = top + plot_h - plot_h * grid_index / 4
            draw.line((left, y, left + plot_w, y), fill="#dde3e8", width=1)
            draw.text(
                (left - 54, y - 7),
                _format_value(value, basis_points=definition.fixed_basis_points),
                fill="#52606d",
                font=_font(11),
            )
        x_points = [left + plot_w * index / 4 for index in range(5)]
        for x, label in zip(x_points, ("1", "2", "4", "8", "∞")):
            draw.text((x - 4, top + plot_h + 7), label, fill="#52606d", font=_font(12))
        for model_index, model in enumerate(DEFAULT_MODELS):
            model_rows = {
                row["vision_depth"]: row
                for row in panel_rows
                if row["model_id"] == model.model_id
            }
            segments: list[list[tuple[float, float, float, float]]] = []
            segment: list[tuple[float, float, float, float]] = []
            for x, vision in zip(x_points, VISION_DEPTHS):
                row = model_rows[vision]
                if row.get("point") is None:
                    if segment:
                        segments.append(segment)
                        segment = []
                    draw.text(
                        (x - 8, top + plot_h - 18),
                        "NA",
                        fill="#89949e",
                        font=_font(11),
                    )
                    continue
                segment.append(
                    (
                        x,
                        _to_y(
                            _scale_value(
                                int(row["point"]), basis_points=definition.fixed_basis_points
                            ), top=top, plot_h=plot_h, y_min=y_min, y_max=y_max
                        ),
                        _to_y(
                            _scale_value(
                                int(row["lower"]), basis_points=definition.fixed_basis_points
                            ), top=top, plot_h=plot_h, y_min=y_min, y_max=y_max
                        ),
                        _to_y(
                            _scale_value(
                                int(row["upper"]), basis_points=definition.fixed_basis_points
                            ), top=top, plot_h=plot_h, y_min=y_min, y_max=y_max
                        ),
                    )
                )
            if segment:
                segments.append(segment)
            color = model.color
            plotted_points = []
            for current in segments:
                points = [(x, point) for x, point, _lower, _upper in current]
                lower = [(x, lower) for x, _point, lower, _upper in current]
                upper = [(x, upper) for x, _point, _lower, upper in current]
                draw.polygon([*upper, *reversed(lower)], fill=color + "20")
                if len(points) > 1:
                    draw.line(points, fill=color, width=3, joint="curve")
                for x, y in points:
                    if model.marker == "square":
                        draw.rectangle((x - 4, y - 4, x + 4, y + 4), fill=color)
                    elif model.marker == "triangle":
                        draw.polygon(
                            ((x, y - 5), (x - 5, y + 4), (x + 5, y + 4)), fill=color
                        )
                    else:
                        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=color)
                plotted_points.extend(points)
            if not plotted_points:
                continue
            end_x, end_y = plotted_points[-1]
            draw.text(
                (end_x + 7, end_y - 22 + model_index * 15),
                model.display_name,
                fill=color,
                font=_font(12, bold=True),
            )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=9)
    return buffer.getvalue()


def _paired_skill_svg(analysis: Mapping[str, Any]) -> tuple[str, str]:
    rows = [
        row
        for row in analysis.get("paired_skill_effects", [])
        if isinstance(row, Mapping) and row.get("metric") == "charged_calls_delta"
    ]
    if len(rows) != 12:
        raise BenchmarkReportError("paired skill call effects are incomplete")
    definition = FigureDefinition(
        "skill-call-effect",
        "Maze skill effect on budget-charged calls",
        "Lines show paired skill-on minus skill-off mean call differences with 95 percent "
        "confidence intervals. Negative values favor the skill.",
        "charged_calls_delta",
        "paired call difference",
    )
    values = [int(row[key]) for row in rows for key in ("lower", "upper", "point")]
    y_min, y_max = min(0, min(values)), max(0, max(values))
    padding = max(1.0, (y_max - y_min) * 0.12)
    y_min, y_max = y_min - padding, y_max + padding
    left, top, plot_w, plot_h = 110, 130, 980, 620
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
        'aria-labelledby="skill-title skill-desc">',
        f'<title id="skill-title">{definition.title}</title><desc id="skill-desc">'
        f'{definition.alt_text}</desc><rect width="1280" height="900" fill="#fff"/>',
        f'<text x="52" y="50" font-family="DejaVu Sans, sans-serif" font-size="28" '
        f'font-weight="700" fill="#17212b">{definition.title}</text>',
        '<text x="52" y="80" font-family="DejaVu Sans, sans-serif" font-size="14" '
        'fill="#52606d">Paired skill-on minus matching skill-off episode · negative favors skill</text>',
    ]
    def skill_y(value: float) -> float:
        return _to_y(value, top=top, plot_h=plot_h, y_min=y_min, y_max=y_max)

    zero_y = skill_y(0)
    parts.append(
        f'<line x1="{left}" y1="{zero_y:.1f}" x2="{left + plot_w}" y2="{zero_y:.1f}" '
        'stroke="#17212b" stroke-width="1.5"/>'
    )
    x_points = [left + plot_w * index / 3 for index in range(4)]
    for x, difficulty in zip(x_points, DIFFICULTIES):
        parts.append(
            f'<text x="{x:.1f}" y="{top + plot_h + 30}" text-anchor="middle" '
            f'font-family="DejaVu Sans, sans-serif" font-size="14" fill="#52606d">'
            f'{html.escape(difficulty.replace("_", " ").title())}</text>'
        )
    for model_index, model in enumerate(DEFAULT_MODELS):
        model_rows = {row["difficulty"]: row for row in rows if row["model_id"] == model.model_id}
        points = [(x, skill_y(int(model_rows[difficulty]["point"]))) for x, difficulty in zip(x_points, DIFFICULTIES)]
        parts.append(
            f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in points)}" '
            f'fill="none" stroke="{model.color}" stroke-width="3"/>'
        )
        for (x, y), difficulty in zip(points, DIFFICULTIES):
            row = model_rows[difficulty]
            parts.append(
                f'<line x1="{x:.1f}" y1="{skill_y(int(row["lower"])):.1f}" x2="{x:.1f}" '
                f'y2="{skill_y(int(row["upper"])):.1f}" stroke="{model.color}" stroke-width="2"/>'
            )
            parts.append(_svg_marker(x, y, model.model_id))
        end_x, end_y = points[-1]
        parts.append(
            f'<text x="{end_x + 12:.1f}" y="{end_y - 12 + model_index * 14:.1f}" '
            f'font-family="DejaVu Sans, sans-serif" font-size="13" font-weight="700" '
            f'fill="{model.color}">{model.display_name}</text>'
        )
    parts.append(
        '<text x="52" y="870" font-family="DejaVu Sans, sans-serif" font-size="12" '
        'fill="#52606d">Each point pairs the exact map, repetition, model, and participant seat. '
        'Intervals resample maps then repetitions.</text></svg>'
    )
    return definition.slug, "".join(parts)


def _heatmap_svg(analysis: Mapping[str, Any]) -> tuple[str, str, str]:
    rows = baseline_figure_rows(analysis, "completion_basis_points")
    title = "Baseline completion heatmap by model and condition"
    alt = (
        "A model-by-condition heatmap reports baseline completion percentages for each maze "
        "difficulty and vision depth; every cell includes its numeric value."
    )
    cell_w, cell_h = 48, 92
    left, top = 250, 180
    columns = [(difficulty, vision) for difficulty in DIFFICULTIES for vision in VISION_DEPTHS]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
        'aria-labelledby="heat-title heat-desc">',
        f'<title id="heat-title">{title}</title><desc id="heat-desc">{alt}</desc>',
        '<rect width="1280" height="900" fill="#fff"/>',
        f'<text x="52" y="52" font-family="DejaVu Sans, sans-serif" font-size="28" '
        f'font-weight="700" fill="#17212b">{title}</text>',
        '<text x="52" y="84" font-family="DejaVu Sans, sans-serif" font-size="14" '
        'fill="#52606d">Completion percentage · values printed in every cell</text>',
    ]
    lookup = {
        (row["model_id"], row["difficulty"], row["vision_depth"]): int(row["point"]) / 100
        for row in rows
    }
    for column_index, (difficulty, vision) in enumerate(columns):
        x = left + column_index * cell_w
        if column_index % 5 == 0:
            parts.append(
                f'<text x="{x + cell_w * 2.5:.1f}" y="{top - 55}" text-anchor="middle" '
                f'font-family="DejaVu Sans, sans-serif" font-size="14" font-weight="700" '
                f'fill="#17212b">{html.escape(difficulty.replace("_", " ").title())}</text>'
            )
        vision_label = "∞" if vision == "infinite" else str(vision)
        parts.append(
            f'<text x="{x + cell_w / 2:.1f}" y="{top - 22}" text-anchor="middle" '
            f'font-family="DejaVu Sans, sans-serif" font-size="11" fill="#52606d">{vision_label}</text>'
        )
    for row_index, model in enumerate(DEFAULT_MODELS):
        y = top + row_index * cell_h
        parts.append(
            f'<text x="{left - 18}" y="{y + cell_h / 2 + 5:.1f}" text-anchor="end" '
            f'font-family="DejaVu Sans, sans-serif" font-size="16" font-weight="700" '
            f'fill="{model.color}">{model.display_name}</text>'
        )
        for column_index, (difficulty, vision) in enumerate(columns):
            value = lookup[(model.model_id, difficulty, vision)]
            intensity = max(0, min(255, round(245 - value * 1.55)))
            fill = f"rgb({intensity},{min(250, intensity + 18)},{255})"
            text_fill = "#ffffff" if value >= 72 else "#17212b"
            x = left + column_index * cell_w
            parts.extend(
                (
                    f'<rect x="{x}" y="{y}" width="{cell_w - 2}" height="{cell_h - 2}" '
                    f'fill="{fill}" stroke="#ffffff"/>',
                    f'<text x="{x + cell_w / 2:.1f}" y="{y + cell_h / 2 + 5:.1f}" '
                    f'text-anchor="middle" font-family="DejaVu Sans, sans-serif" font-size="11" '
                    f'font-weight="700" fill="{text_fill}">{value:.0f}%</text>',
                )
            )
    parts.append(
        '<text x="52" y="870" font-family="DejaVu Sans, sans-serif" font-size="12" '
        'fill="#52606d">Columns repeat vision depths 1, 2, 4, 8, and infinite within each '
        'difficulty. Cell values are hierarchical-bootstrap point estimates.</text></svg>'
    )
    return "completion-heatmap", "".join(parts), alt


def _paired_skill_png(analysis: Mapping[str, Any]) -> bytes:
    rows = [
        row
        for row in analysis.get("paired_skill_effects", [])
        if isinstance(row, Mapping) and row.get("metric") == "charged_calls_delta"
    ]
    values = [int(row[key]) for row in rows for key in ("lower", "upper", "point")]
    y_min, y_max = min(0, min(values)), max(0, max(values))
    padding = max(1.0, (y_max - y_min) * 0.12)
    y_min, y_max = y_min - padding, y_max + padding
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    draw.text(
        (52, 40),
        "Maze skill effect on budget-charged calls",
        fill="#17212b",
        font=_font(28, bold=True),
    )
    draw.text(
        (52, 78),
        "Paired skill-on minus skill-off · negative favors skill",
        fill="#52606d",
        font=_font(14),
    )
    left, top, plot_w, plot_h = 110, 130, 980, 620
    def skill_y(value: float) -> float:
        return _to_y(value, top=top, plot_h=plot_h, y_min=y_min, y_max=y_max)

    for grid_index in range(5):
        value = y_min + (y_max - y_min) * grid_index / 4
        y = skill_y(value)
        draw.line((left, y, left + plot_w, y), fill="#dde3e8", width=1)
        draw.text((left - 58, y - 7), f"{value:.1f}", fill="#52606d", font=_font(11))
    draw.line((left, skill_y(0), left + plot_w, skill_y(0)), fill="#17212b", width=2)
    x_points = [left + plot_w * index / 3 for index in range(4)]
    for x, difficulty in zip(x_points, DIFFICULTIES):
        draw.text(
            (x - 42, top + plot_h + 18),
            difficulty.replace("_", " ").title(),
            fill="#52606d",
            font=_font(12),
        )
    for model_index, model in enumerate(DEFAULT_MODELS):
        model_rows = {row["difficulty"]: row for row in rows if row["model_id"] == model.model_id}
        points = [
            (x, skill_y(int(model_rows[difficulty]["point"])))
            for x, difficulty in zip(x_points, DIFFICULTIES)
        ]
        draw.line(points, fill=model.color, width=3)
        for (x, y), difficulty in zip(points, DIFFICULTIES):
            row = model_rows[difficulty]
            draw.line(
                (x, skill_y(int(row["lower"])), x, skill_y(int(row["upper"]))),
                fill=model.color,
                width=2,
            )
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=model.color)
        end_x, end_y = points[-1]
        draw.text(
            (end_x + 10, end_y - 18 + model_index * 15),
            model.display_name,
            fill=model.color,
            font=_font(12, bold=True),
        )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=9)
    return buffer.getvalue()


def _paired_skill_png_matplotlib(analysis: Mapping[str, Any]) -> bytes:
    """Use the optional benchmark extra for a publication-oriented raster export."""

    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    rows = [
        row
        for row in analysis.get("paired_skill_effects", [])
        if isinstance(row, Mapping) and row.get("metric") == "charged_calls_delta"
    ]
    figure, axis = plt.subplots(figsize=(12.8, 9), dpi=100)
    x_values = list(range(len(DIFFICULTIES)))
    mpl_markers = {"circle": "o", "square": "s", "triangle": "^"}
    mpl_styles = {"solid": "-", "dashed": "--", "dotted": ":"}
    for model_index, model in enumerate(DEFAULT_MODELS):
        model_rows = {row["difficulty"]: row for row in rows if row["model_id"] == model.model_id}
        points = [int(model_rows[difficulty]["point"]) for difficulty in DIFFICULTIES]
        lower = [int(model_rows[difficulty]["lower"]) for difficulty in DIFFICULTIES]
        upper = [int(model_rows[difficulty]["upper"]) for difficulty in DIFFICULTIES]
        errors = ([point - low for point, low in zip(points, lower)], [high - point for point, high in zip(points, upper)])
        axis.errorbar(
            x_values,
            points,
            yerr=errors,
            color=model.color,
            marker=mpl_markers[model.marker],
            linestyle=mpl_styles[model.line_style],
            linewidth=2.2,
            capsize=4,
        )
        axis.annotate(
            model.display_name,
            (x_values[-1], points[-1]),
            xytext=(10, -10 + model_index * 13),
            textcoords="offset points",
            color=model.color,
            fontweight="bold",
        )
    axis.axhline(0, color="#17212b", linewidth=1)
    axis.set_xticks(x_values, [value.replace("_", " ").title() for value in DIFFICULTIES])
    axis.set_ylabel("Skill-on minus skill-off budget-charged calls")
    axis.set_title("Maze skill effect on budget-charged calls", loc="left", fontweight="bold")
    axis.grid(axis="y", color="#dde3e8")
    for side in ("top", "right"):
        axis.spines[side].set_visible(False)
    figure.text(
        0.1,
        0.02,
        "Paired by map, repetition, model, and participant seat. Negative values favor skill.",
        fontsize=9,
        color="#52606d",
    )
    buffer = io.BytesIO()
    figure.savefig(
        buffer,
        format="png",
        dpi=100,
        facecolor="white",
        metadata={"Software": "WorldArena deterministic benchmark reporter"},
    )
    plt.close(figure)
    return buffer.getvalue()


def _heatmap_png(analysis: Mapping[str, Any]) -> bytes:
    rows = baseline_figure_rows(analysis, "completion_basis_points")
    lookup = {
        (row["model_id"], row["difficulty"], row["vision_depth"]): int(row["point"]) / 100
        for row in rows
    }
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    draw.text(
        (52, 42),
        "Baseline completion heatmap by model and condition",
        fill="#17212b",
        font=_font(28, bold=True),
    )
    draw.text(
        (52, 80),
        "Completion percentage · values printed in every cell",
        fill="#52606d",
        font=_font(14),
    )
    cell_w, cell_h = 48, 92
    left, top = 250, 180
    columns = [(difficulty, vision) for difficulty in DIFFICULTIES for vision in VISION_DEPTHS]
    for column_index, (difficulty, vision) in enumerate(columns):
        x = left + column_index * cell_w
        if column_index % 5 == 0:
            draw.text(
                (x + 55, top - 66),
                difficulty.replace("_", " ").title(),
                fill="#17212b",
                font=_font(13, bold=True),
            )
        draw.text(
            (x + 17, top - 28),
            "∞" if vision == "infinite" else str(vision),
            fill="#52606d",
            font=_font(11),
        )
    for row_index, model in enumerate(DEFAULT_MODELS):
        y = top + row_index * cell_h
        draw.text(
            (left - 75, y + 34),
            model.display_name,
            fill=model.color,
            font=_font(16, bold=True),
        )
        for column_index, (difficulty, vision) in enumerate(columns):
            value = lookup[(model.model_id, difficulty, vision)]
            intensity = max(0, min(255, round(245 - value * 1.55)))
            fill = (intensity, min(250, intensity + 18), 255)
            text_fill = "#ffffff" if value >= 72 else "#17212b"
            x = left + column_index * cell_w
            draw.rectangle((x, y, x + cell_w - 2, y + cell_h - 2), fill=fill)
            draw.text(
                (x + 8, y + 35),
                f"{value:.0f}%",
                fill=text_fill,
                font=_font(11, bold=True),
            )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=9)
    return buffer.getvalue()


def generate_report(store: BenchmarkArtifactStore) -> Mapping[str, object]:
    analysis = load_canonical_json(store.analysis_path)
    figures_dir = store.root / "report" / "figures"
    figures = []
    inline_svgs = []
    for definition in _BASELINE_FIGURES:
        rows = baseline_figure_rows(analysis, definition.metric)
        svg = _baseline_line_svg(definition, rows)
        png = _baseline_line_png(definition, rows)
        svg_path = figures_dir / f"{definition.slug}.svg"
        png_path = figures_dir / f"{definition.slug}.png"
        atomic_write(svg_path, svg.encode("utf-8"))
        atomic_write(png_path, png)
        figures.append(
            {
                "slug": definition.slug,
                "title": definition.title,
                "alt_text": definition.alt_text,
                "svg_path": f"figures/{definition.slug}.svg",
                "svg_sha256": hashlib.sha256(svg.encode("utf-8")).hexdigest(),
                "png_path": f"figures/{definition.slug}.png",
                "png_sha256": hashlib.sha256(png).hexdigest(),
            }
        )
        inline_svgs.append((definition, svg))
    skill_slug, skill_svg = _paired_skill_svg(analysis)
    skill_alt = (
        "Paired lines show skill-on minus skill-off budget-charged call differences by model "
        "and difficulty, with negative values favoring the skill."
    )
    try:
        skill_png = _paired_skill_png_matplotlib(analysis)
        skill_png_renderer = "matplotlib"
    except ImportError:
        skill_png = _paired_skill_png(analysis)
        skill_png_renderer = "pillow"
    atomic_write(figures_dir / f"{skill_slug}.svg", skill_svg.encode("utf-8"))
    atomic_write(figures_dir / f"{skill_slug}.png", skill_png)
    figures.append(
        {
            "slug": skill_slug,
            "title": "Maze skill effect on budget-charged calls",
            "alt_text": skill_alt,
            "svg_path": f"figures/{skill_slug}.svg",
            "svg_sha256": hashlib.sha256(skill_svg.encode()).hexdigest(),
            "png_path": f"figures/{skill_slug}.png",
            "png_sha256": hashlib.sha256(skill_png).hexdigest(),
            "png_renderer": skill_png_renderer,
        }
    )
    inline_svgs.append(
        (
            FigureDefinition(skill_slug, "Maze skill effect on budget-charged calls", skill_alt, "", ""),
            skill_svg,
        )
    )
    heat_slug, heat_svg, heat_alt = _heatmap_svg(analysis)
    heat_png = _heatmap_png(analysis)
    atomic_write(figures_dir / f"{heat_slug}.svg", heat_svg.encode("utf-8"))
    atomic_write(figures_dir / f"{heat_slug}.png", heat_png)
    figures.append(
        {
            "slug": heat_slug,
            "title": "Baseline completion heatmap",
            "alt_text": heat_alt,
            "svg_path": f"figures/{heat_slug}.svg",
            "svg_sha256": hashlib.sha256(heat_svg.encode()).hexdigest(),
            "png_path": f"figures/{heat_slug}.png",
            "png_sha256": hashlib.sha256(heat_png).hexdigest(),
        }
    )
    inline_svgs.append(
        (FigureDefinition(heat_slug, "Baseline completion heatmap", heat_alt, "", ""), heat_svg)
    )
    report_html = _report_html(analysis, inline_svgs)
    atomic_write(store.report_path, report_html.encode("utf-8"))
    manifest = {
        "schema_version": "worldarena/labyrinth-benchmark-report/1",
        "season_id": store.season_id,
        "analysis_sha256": analysis["analysis_sha256"],
        "html_path": "index.html",
        "html_sha256": hashlib.sha256(report_html.encode("utf-8")).hexdigest(),
        "aggregate_csv_path": "../analysis/aggregate.csv",
        "aggregate_csv_sha256": hashlib.sha256(store.aggregate_csv_path.read_bytes()).hexdigest(),
        "paired_csv_path": "../analysis/paired-skill.csv",
        "paired_csv_sha256": hashlib.sha256(store.paired_csv_path.read_bytes()).hexdigest(),
        "figures": figures,
    }
    atomic_write_json(store.root / "report" / "manifest.json", manifest)
    store.audit_public_tree()
    return manifest


def _report_html(
    analysis: Mapping[str, Any], figures: Sequence[tuple[FigureDefinition, str]]
) -> str:
    counts = analysis["sample_counts"]
    totals = analysis.get("phase_totals", {})
    total_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(phase).title())}</td>"
        f"<td>{row['race_results']}</td><td>{row['api_calls']}</td>"
        f"<td>{row['input_tokens']}</td><td>{row['output_tokens']}</td>"
        f"<td>{row['total_tokens']}</td><td>{row['provider_latency_ms']}</td>"
        f"<td>{row['elapsed_wall_time_ms']}</td>"
        f"<td>{row['token_telemetry_missing_episodes']}</td>"
        f"<td>{row['latency_telemetry_missing_episodes']}</td></tr>"
        for phase, row in totals.items()
        if isinstance(row, Mapping)
    )
    comparisons = analysis.get("model_skill_comparisons", [])
    paired_effects = analysis.get("paired_skill_effects", [])
    paired_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row['difficulty']).replace('_', ' ').title())}</td>"
        f"<td>{html.escape(str(row['model_id']).title())}</td>"
        f"<td>{html.escape(str(row['metric']).replace('_', ' '))}</td>"
        f"<td>{row['point'] if row['point'] is not None else 'NA'}</td>"
        f"<td>{row['lower'] if row['lower'] is not None else 'NA'}"
        f"{' to ' if row['lower'] is not None else ''}"
        f"{row['upper'] if row['upper'] is not None else ''}</td>"
        f"<td>{row['sample_size']}</td><td>{row.get('missing_pair_count', 0)}</td>"
        "</tr>"
        for row in paired_effects
    )
    failures = analysis.get("provider_failures", {})
    failure_rows = "".join(
        f"<tr><td>{html.escape(str(kind))}</td><td>{count}</td></tr>"
        for kind, count in failures.items()
    ) or '<tr><td colspan="2">No provider failures recorded.</td></tr>'
    voids = analysis.get("infrastructure_voids_by_phase", {})
    comparison_rows = "".join(
        "<tr>"
        f"<td>Skilled Luna vs unskilled {html.escape(str(row['baseline_model_id']).title())}</td>"
        f"<td>{'Yes' if row['matches_or_exceeds'] else 'No'}</td>"
        f"<td>{row['skilled_completion_count']} / {row['sample_size_each']}</td>"
        f"<td>{row['baseline_completion_count']} / {row['sample_size_each']}</td>"
        f"<td>{'complete' if row['token_telemetry_complete'] else 'incomplete'}</td>"
        "</tr>"
        for row in comparisons
    )
    figure_html = "".join(
        f'<figure aria-label="{html.escape(definition.alt_text)}">{svg}'
        f'<figcaption><strong>{html.escape(definition.title)}</strong> '
        f'<span>{html.escape(definition.alt_text)}</span> '
        f'<a href="figures/{definition.slug}.svg">SVG</a> · '
        f'<a href="figures/{definition.slug}.png">PNG</a></figcaption></figure>'
        for definition, svg in figures
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Labyrinth Capability Benchmark</title><style>
:root{{--ink:#17212b;--muted:#52606d;--line:#dce2e8;--paper:#fff;--wash:#f5f7f9}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--wash);color:var(--ink);font:16px/1.55 system-ui,sans-serif}}
main{{max-width:1320px;margin:auto;padding:32px}} header,section,figure{{background:var(--paper);border:1px solid var(--line);border-radius:12px}}
header,section{{padding:28px;margin-bottom:22px}} h1{{font-size:clamp(2rem,5vw,3.4rem);line-height:1.05;margin:.15em 0}} h2{{margin-top:0}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}} .kpi{{padding:16px;background:var(--wash);border-radius:8px}}
.kpi b{{display:block;font-size:1.65rem}} figure{{margin:0 0 24px;padding:16px;overflow:hidden}} figure svg{{display:block;width:100%;height:auto}}
figcaption{{display:block;padding:10px 8px 2px;color:var(--muted)}} figcaption strong{{display:block;color:var(--ink)}}
table{{border-collapse:collapse;width:100%}} th,td{{text-align:left;border-bottom:1px solid var(--line);padding:10px}} a{{color:#075e75}}
.caveat{{border-left:4px solid #c78b00;padding-left:14px}} code{{overflow-wrap:anywhere}}
@media(max-width:760px){{main{{padding:12px}} header,section{{padding:18px}} .kpis{{grid-template-columns:1fr 1fr}} figure{{padding:4px}}}}
</style></head><body><main>
<header><p>WorldArena · deterministic repeated evaluation</p><h1>Labyrinth Capability Benchmark</h1>
<p>Three model snapshots navigate frozen, hidden-at-inference maze seeds across vision depths, difficulty tiers, and a paired generic maze-skill ablation.</p></header>
<section aria-labelledby="summary"><h2 id="summary">Season summary</h2><div class="kpis">
<div class="kpi"><b>{counts['race_results']}</b>race results</div><div class="kpi"><b>{counts['model_episodes']}</b>model episodes</div>
<div class="kpi"><b>{counts['completed_model_episodes']}</b>completed</div><div class="kpi"><b>{counts['censored_model_episodes']}</b>budget-censored</div>
</div><p>Objectively selected skill-study vision depth: <strong>{html.escape(str(analysis['selected_vision_depth']))}</strong>.</p></section>
<section aria-labelledby="samples"><h2 id="samples">Pilot and main evaluation samples</h2><table><thead><tr><th>Stage</th><th>Maps</th><th>Races</th><th>Model episodes</th><th>Purpose</th></tr></thead><tbody>
<tr><td>Pilot</td><td>12 separate pilot maps</td><td>{counts['pilot_races']}</td><td>{counts['pilot_model_episodes']}</td><td>Technical and cost/throughput gate; not pooled into main claims</td></tr>
<tr><td>Main baseline</td><td>40 frozen hidden-at-inference maps</td><td>{counts['baseline_races']}</td><td>{counts['baseline_model_episodes']}</td><td>Model × vision × difficulty comparison</td></tr>
<tr><td>Paired skill study</td><td>Same 40 main maps</td><td>{counts['skill_races']}</td><td>{counts['skill_model_episodes']}</td><td>Skill-on/off comparison at selected depth</td></tr>
</tbody></table></section>
<section aria-labelledby="totals"><h2 id="totals">Calls, tokens, and elapsed time</h2>
<table><thead><tr><th>Phase</th><th>Races</th><th>API calls</th><th>Input tokens</th><th>Output tokens</th><th>Total tokens</th><th>Provider latency ms</th><th>Wall time ms</th><th>Missing token episodes</th><th>Missing latency episodes</th></tr></thead><tbody>{total_rows}</tbody></table>
<p>Token and provider-latency totals are measured known values; use the missing-episode columns to assess completeness.</p></section>
<section aria-labelledby="failures"><h2 id="failures">Failures and infrastructure voids</h2>
<table><thead><tr><th>Provider outcome</th><th>Decision count</th></tr></thead><tbody>{failure_rows}</tbody></table>
<p>Voided/requeued races — pilot: {voids.get('pilot', 0)}, baseline: {voids.get('baseline', 0)}, skill: {voids.get('skill', 0)}. Voids are excluded and requeued; timeout, refusal, malformed, and illegal decisions remain model outcomes.</p></section>
<section aria-labelledby="method"><h2 id="method">Method and interpretation</h2>
<p>Each point is a mean. The 95% interval uses 10,000 deterministic hierarchical bootstrap replicates, resampling map seeds first and repetitions within each selected map. Maps—not individual calls—are the primary generalisation unit.</p>
<p class="caveat">Incomplete episodes remain in completion and calls analyses and are charged their full participant budget. Successful-run path efficiency excludes incomplete episodes. Token analyses exclude missing telemetry. Confidence intervals describe uncertainty across these frozen maps and repetitions; they are not guarantees about every possible maze.</p>
<p>Difficulty reflects exploration capacity: walkable cells and edge count increase by tier, while hard and memory-stress maps add loops. Shortest-path length is reported as a measured covariate and is not assumed to increase monotonically after braiding.</p>
<p>Frozen spec digest: <code>{analysis['spec_sha256']}</code><br>
Pilot map manifest: <code>{analysis['frozen_hashes']['pilot_manifest_sha256']}</code><br>
Main map manifest: <code>{analysis['frozen_hashes']['main_manifest_sha256']}</code><br>
Protocol prompt: <code>{analysis['frozen_hashes']['protocol_prompt_sha256']}</code><br>
Maze skill: <code>{analysis['frozen_hashes']['skill_sha256']}</code><br>
Pilot schedule: <code>{analysis['schedule_sha256s'].get('pilot', 'unavailable')}</code><br>
Baseline schedule: <code>{analysis['schedule_sha256s'].get('baseline', 'unavailable')}</code><br>
Skill schedule: <code>{analysis['schedule_sha256s'].get('skill', 'unavailable')}</code><br>
Analysis digest: <code>{analysis['analysis_sha256']}</code></p>
<p><a href="../analysis/aggregate.csv">Download aggregate CSV</a> · <a href="../analysis/paired-skill.csv">Download paired-skill CSV</a></p></section>
{figure_html}
<section aria-labelledby="paired"><h2 id="paired">All paired maze-skill effects</h2><p>Effects are skill-on minus matching skill-off. Negative calls/tokens and positive completion/path efficiency favor the skill. Token pairs require complete telemetry; path pairs require both episodes to finish.</p>
<table><thead><tr><th>Difficulty</th><th>Model</th><th>Metric</th><th>Mean effect</th><th>95% interval</th><th>Pairs</th><th>Missing pairs</th></tr></thead><tbody>{paired_rows}</tbody></table></section>
<section aria-labelledby="comparison"><h2 id="comparison">Can skilled Luna match larger unskilled models?</h2>
<table><thead><tr><th>Comparison</th><th>Matches/exceeds</th><th>Skilled completion</th><th>Baseline completion</th><th>Token evidence</th></tr></thead>
<tbody>{comparison_rows}</tbody></table><p>Comparison uses completion first, then budget-charged calls, then tokens only when both sides have complete telemetry. Participant-ID tie-breaking is never treated as a model win.</p></section>
</main></body></html>"""


__all__ = [
    "BenchmarkReportError",
    "FigureDefinition",
    "baseline_figure_rows",
    "generate_report",
]
