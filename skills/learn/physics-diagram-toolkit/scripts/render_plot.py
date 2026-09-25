#!/usr/bin/env python
"""Render deterministic high-school physics plots from a safe JSON specification."""
from __future__ import annotations

import argparse
import ast
import json
import math
import os
from pathlib import Path
from typing import Any

from diagram_common import atomic_write_json, finite_number, load_json, sha256_file


SERIES_TYPES = {"line", "scatter", "function", "piecewise"}
ALLOWED_FUNCTIONS = {"sin", "cos", "tan", "exp", "sqrt", "abs"}
ALLOWED_BINARY = {ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow}
ALLOWED_UNARY = {ast.UAdd, ast.USub}


def finite_array(values: Any, field: str) -> list[float]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{field} must be a non-empty array")
    return [finite_number(value, f"{field}[{index}]") for index, value in enumerate(values)]


def safe_sympy_expression(expression: str, variable: str):
    import sympy as sp

    if variable not in {"x", "t"}:
        raise ValueError("function variable must be x or t")
    if len(expression) > 240 or "__" in expression:
        raise ValueError("expression is too long or unsafe")
    tree = ast.parse(expression, mode="eval")
    symbol = sp.Symbol(variable, real=True)
    functions = {
        "sin": sp.sin,
        "cos": sp.cos,
        "tan": sp.tan,
        "exp": sp.exp,
        "sqrt": sp.sqrt,
        "abs": sp.Abs,
    }

    def convert(node: ast.AST):
        if isinstance(node, ast.Expression):
            return convert(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return sp.Float(node.value) if isinstance(node.value, float) else sp.Integer(node.value)
        if isinstance(node, ast.Name):
            if node.id == variable:
                return symbol
            if node.id == "pi":
                return sp.pi
            if node.id == "E":
                return sp.E
            raise ValueError(f"unsupported name in expression: {node.id}")
        if isinstance(node, ast.BinOp) and type(node.op) in ALLOWED_BINARY:
            left, right = convert(node.left), convert(node.right)
            return {
                ast.Add: left + right,
                ast.Sub: left - right,
                ast.Mult: left * right,
                ast.Div: left / right,
                ast.Pow: left**right,
            }[type(node.op)]
        if isinstance(node, ast.UnaryOp) and type(node.op) in ALLOWED_UNARY:
            value = convert(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id not in ALLOWED_FUNCTIONS or len(node.args) != 1 or node.keywords:
                raise ValueError(f"unsupported function call: {node.func.id}")
            return functions[node.func.id](convert(node.args[0]))
        raise ValueError(f"unsupported expression syntax: {type(node).__name__}")

    result = convert(tree)
    if result.free_symbols - {symbol}:
        raise ValueError("expression contains unsupported symbols")
    return result, symbol


def sample_expression(expression: str, variable: str, domain: Any, samples: int) -> tuple[list[float], list[float]]:
    import numpy as np
    import sympy as sp

    if not isinstance(domain, list) or len(domain) != 2:
        raise ValueError("domain must be [start, end]")
    start = finite_number(domain[0], "domain[0]")
    end = finite_number(domain[1], "domain[1]")
    if not start < end:
        raise ValueError("domain start must be less than end")
    if not 2 <= samples <= 5000:
        raise ValueError("samples must be between 2 and 5000")
    parsed, symbol = safe_sympy_expression(expression, variable)
    function = sp.lambdify(symbol, parsed, modules=["numpy"])
    x = np.linspace(start, end, samples, dtype=np.float64)
    with np.errstate(all="ignore"):
        y = np.asarray(function(x), dtype=np.float64)
    if y.ndim == 0:
        y = np.full_like(x, float(y))
    if y.shape != x.shape or not np.all(np.isfinite(y)):
        raise ValueError("expression produced non-finite or incorrectly shaped values")
    return x.tolist(), y.tolist()


def local_font_path() -> Path:
    runtime = Path(os.environ.get("LLMWIKI_MODEL_RUNTIME_ROOT", ""))
    candidates = [
        runtime / "fonts" / "noto-sans-cjk-sc" / "2.004" / "NotoSansCJKsc-Regular.otf",
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("no local Chinese-capable font found")


def render_plot(spec: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    if spec.get("schema_version") not in {2, 3} or spec.get("renderer") != "plot":
        raise ValueError("plot spec must use schema_version=2|3 and renderer=plot")
    if spec.get("coordinate_space") != "plot_data":
        raise ValueError("plot coordinate_space must be plot_data")
    canvas = spec.get("canvas")
    if not isinstance(canvas, dict):
        raise ValueError("canvas must be an object")
    width = int(finite_number(canvas.get("width", 1000), "canvas.width"))
    height = int(finite_number(canvas.get("height", 700), "canvas.height"))
    dpi = int(finite_number(canvas.get("dpi", 150), "canvas.dpi"))
    if not (400 <= width <= 4000 and 300 <= height <= 4000 and 72 <= dpi <= 600):
        raise ValueError("invalid canvas width, height, or dpi")
    plot = spec.get("plot")
    if not isinstance(plot, dict):
        raise ValueError("plot must be an object")
    series = plot.get("series")
    if not isinstance(series, list) or not series:
        raise ValueError("plot.series must be a non-empty array")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    font_path = local_font_path()
    font_manager.fontManager.addfont(str(font_path))
    font_name = font_manager.FontProperties(fname=str(font_path)).get_name()
    matplotlib.rcParams.update(
        {
            "font.family": [font_name, "DejaVu Sans"],
            "axes.unicode_minus": False,
            "axes.linewidth": 1.4,
            "svg.hashsalt": "llmwiki-physics-diagram-v1",
            "path.simplify": False,
            "figure.facecolor": str(canvas.get("background", "#ffffff")),
            "savefig.facecolor": str(canvas.get("background", "#ffffff")),
        }
    )
    fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)
    fig.subplots_adjust(left=0.14, right=0.96, bottom=0.15, top=0.94)
    palette = ["#1668c1", "#d12b2b", "#17823b", "#7b3fb3", "#a45b13"]
    reports: list[dict[str, Any]] = []
    for index, item in enumerate(series):
        if not isinstance(item, dict):
            raise ValueError(f"plot.series[{index}] must be an object")
        kind = str(item.get("type", ""))
        if kind not in SERIES_TYPES:
            raise ValueError(f"unsupported series type: {kind}")
        color = str(item.get("color", palette[index % len(palette)]))
        label = str(item.get("label", ""))
        linewidth = finite_number(item.get("line_width", 2.4), "series.line_width")
        if kind in {"line", "scatter"}:
            x = finite_array(item.get("x"), f"series[{index}].x")
            y = finite_array(item.get("y"), f"series[{index}].y")
            if len(x) != len(y):
                raise ValueError(f"series[{index}] x and y lengths differ")
            if kind == "line":
                ax.plot(x, y, color=color, linewidth=linewidth, linestyle=str(item.get("line_style", "-")), marker=item.get("marker"), label=label or None)
            else:
                ax.scatter(x, y, color=color, s=finite_number(item.get("marker_size", 35), "marker_size"), marker=str(item.get("marker", "o")), label=label or None)
            reports.append({"index": index, "type": kind, "point_count": len(x)})
        elif kind == "function":
            samples = int(finite_number(item.get("samples", 401), "series.samples"))
            x, y = sample_expression(str(item.get("expression", "")), str(item.get("variable", "x")), item.get("domain"), samples)
            ax.plot(x, y, color=color, linewidth=linewidth, linestyle=str(item.get("line_style", "-")), label=label or None)
            reports.append({"index": index, "type": kind, "point_count": len(x), "expression": str(item.get("expression"))})
        else:
            segments = item.get("segments")
            if not isinstance(segments, list) or not segments:
                raise ValueError("piecewise series requires segments")
            total = 0
            for segment_index, segment in enumerate(segments):
                if not isinstance(segment, dict):
                    raise ValueError("piecewise segment must be an object")
                samples = int(finite_number(segment.get("samples", 201), "segment.samples"))
                x, y = sample_expression(str(segment.get("expression", "")), str(item.get("variable", "x")), segment.get("domain"), samples)
                ax.plot(x, y, color=color, linewidth=linewidth, linestyle=str(item.get("line_style", "-")), label=label if segment_index == 0 and label else None)
                total += len(x)
            reports.append({"index": index, "type": kind, "point_count": total, "segment_count": len(segments)})

    ax.set_xlabel(str(plot.get("x_label", "x")), fontsize=14)
    ax.set_ylabel(str(plot.get("y_label", "y")), fontsize=14, rotation=90, labelpad=14)
    if plot.get("title"):
        ax.set_title(str(plot["title"]), fontsize=15)
    if plot.get("x_limits") is not None:
        limits = finite_array(plot["x_limits"], "plot.x_limits")
        if len(limits) != 2 or not limits[0] < limits[1]:
            raise ValueError("plot.x_limits must be increasing [min, max]")
        ax.set_xlim(limits)
    if plot.get("y_limits") is not None:
        limits = finite_array(plot["y_limits"], "plot.y_limits")
        if len(limits) != 2 or not limits[0] < limits[1]:
            raise ValueError("plot.y_limits must be increasing [min, max]")
        ax.set_ylim(limits)
    if bool(plot.get("origin_axes", True)):
        xmin, xmax = ax.get_xlim()
        ymin, ymax = ax.get_ylim()
        if ymin <= 0 <= ymax:
            ax.axhline(0, color="#111111", linewidth=1.2, zorder=0)
        if xmin <= 0 <= xmax:
            ax.axvline(0, color="#111111", linewidth=1.2, zorder=0)
    if bool(plot.get("grid", False)):
        ax.grid(True, color="#d9d9d9", linewidth=0.8, linestyle="--")
    if any(str(item.get("label", "")) for item in series):
        ax.legend(frameon=False, fontsize=11)
    ax.tick_params(direction="out", length=5, width=1.1, labelsize=11)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {suffix: out_dir / f"diagram.{suffix}" for suffix in ("png", "svg", "pdf")}
    fig.savefig(paths["png"], dpi=dpi, metadata={"Software": "LLMWiki physics-diagram-toolkit"})
    fig.savefig(paths["svg"], format="svg", metadata={"Date": None, "Creator": "LLMWiki physics-diagram-toolkit"})
    fig.savefig(paths["pdf"], format="pdf", metadata={"CreationDate": None, "ModDate": None, "Creator": "LLMWiki physics-diagram-toolkit"})
    plt.close(fig)
    from PIL import Image

    with Image.open(paths["png"]) as image:
        actual_size = list(image.size)
    if actual_size != [width, height]:
        raise RuntimeError(f"plot PNG size mismatch: expected {[width, height]}, got {actual_size}")
    artifacts = {
        suffix: {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}
        for suffix, path in paths.items()
    }
    report = {
        "ok": True,
        "renderer": "plot",
        "canvas": {"width": width, "height": height, "dpi": dpi},
        "font": str(font_path),
        "series": reports,
        "artifacts": artifacts,
    }
    report_path = out_dir / "render_report.json"
    atomic_write_json(report_path, report)
    report["report"] = str(report_path)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = render_plot(load_json(Path(args.spec).resolve()), Path(args.out_dir).resolve())
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result["report"])
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
