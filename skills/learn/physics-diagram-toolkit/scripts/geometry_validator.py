#!/usr/bin/env python
"""Deterministic geometry assertions for physics diagram scene graphs."""
from __future__ import annotations

import math
from typing import Any


Point = tuple[float, float]


def distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def normalized(vector: Point, field: str) -> Point:
    length = math.hypot(vector[0], vector[1])
    if length < 1e-9:
        raise ValueError(f"{field} cannot be a zero vector")
    return vector[0] / length, vector[1] / length


def angle_degrees(a: Point, b: Point) -> float:
    ua = normalized(a, "relation direction a")
    ub = normalized(b, "relation direction b")
    dot = max(-1.0, min(1.0, ua[0] * ub[0] + ua[1] * ub[1]))
    return math.degrees(math.acos(dot))


def assertion(
    assertion_id: str,
    category: str,
    ok: bool,
    *,
    measured: Any = None,
    tolerance: Any = None,
    detail: str,
) -> dict[str, Any]:
    return {
        "id": assertion_id,
        "category": category,
        "ok": bool(ok),
        "measured": measured,
        "tolerance": tolerance,
        "detail": detail,
    }


def validate_relations(
    relations: Any,
    points: dict[str, Point],
    directions: dict[str, Point],
    *,
    point_tolerance_px: float = 0.5,
    angle_tolerance_degrees: float = 0.5,
) -> list[dict[str, Any]]:
    if relations is None:
        return []
    if not isinstance(relations, list):
        raise ValueError("relations must be an array")
    supported = {
        "coincident",
        "connected",
        "contact",
        "fixed_to",
        "tangent",
        "parallel",
        "perpendicular",
        "passes_through",
    }
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, relation in enumerate(relations):
        if not isinstance(relation, dict):
            raise ValueError(f"relations[{index}] must be an object")
        relation_id = str(relation.get("id", f"relation-{index}"))
        if not relation_id or relation_id in seen:
            raise ValueError(f"duplicate relation id: {relation_id}")
        seen.add(relation_id)
        kind = str(relation.get("type", ""))
        if kind not in supported:
            raise ValueError(f"unsupported relation type: {kind}")
        a_ref, b_ref = str(relation.get("a", "")), str(relation.get("b", ""))
        if kind in {"coincident", "connected", "contact", "fixed_to", "passes_through"}:
            if a_ref not in points or b_ref not in points:
                raise ValueError(f"relation {relation_id} references an unknown point")
            residual = distance(points[a_ref], points[b_ref])
            tolerance = float(relation.get("tolerance_px", point_tolerance_px))
            results.append(
                assertion(
                    relation_id,
                    kind,
                    residual <= tolerance,
                    measured={"residual_px": residual},
                    tolerance={"residual_px": tolerance},
                    detail=f"{a_ref} must coincide with {b_ref}",
                )
            )
            continue
        if a_ref not in directions or b_ref not in directions:
            raise ValueError(f"relation {relation_id} references an unknown direction")
        measured = angle_degrees(directions[a_ref], directions[b_ref])
        tolerance = float(relation.get("tolerance_degrees", angle_tolerance_degrees))
        if kind in {"parallel", "tangent"}:
            residual = min(measured, abs(180.0 - measured))
            target = 0.0
        else:
            residual = abs(90.0 - measured)
            target = 90.0
        results.append(
            assertion(
                relation_id,
                kind,
                residual <= tolerance,
                measured={"angle_degrees": measured, "residual_degrees": residual},
                tolerance={"angle_degrees": tolerance, "target_degrees": target},
                detail=f"{a_ref} must be {kind} to {b_ref}",
            )
        )
    return results


def validate_projectile(
    trajectory_id: str,
    points: list[Point],
    initial_direction: Point,
    *,
    slope_tolerance: float = 1e-6,
) -> list[dict[str, Any]]:
    if len(points) < 3:
        raise ValueError("projectile trajectory requires at least three samples")
    direction = normalized(initial_direction, "projectile.initial_direction")
    start_dx = points[1][0] - points[0][0]
    start_dy = points[1][1] - points[0][1]
    initial_slope = start_dy / start_dx if abs(start_dx) > 1e-12 else math.inf
    horizontal = abs(direction[1]) <= slope_tolerance and direction[0] > 0
    non_decreasing_y = all(points[index + 1][1] + 1e-9 >= points[index][1] for index in range(len(points) - 1))
    increasing_x = all(points[index + 1][0] > points[index][0] for index in range(len(points) - 1))
    second_differences = [
        points[index + 1][1] - 2 * points[index][1] + points[index - 1][1]
        for index in range(1, len(points) - 1)
    ]
    curvature_continuous = bool(second_differences) and max(second_differences) - min(second_differences) <= 1e-6
    return [
        assertion(
            f"{trajectory_id}.horizontal-launch",
            "projectile",
            horizontal,
            measured={"initial_direction": [direction[0], direction[1]]},
            tolerance={"vertical_component": slope_tolerance},
            detail="horizontal projectile initial direction must be rightward and horizontal",
        ),
        assertion(
            f"{trajectory_id}.initial-tangent",
            "projectile",
            horizontal and abs(initial_slope) <= 0.05,
            measured={"analytic_initial_slope": 0.0, "first_segment_slope": initial_slope},
            tolerance={"analytic_absolute_slope": slope_tolerance, "sampled_absolute_slope": 0.05},
            detail="the analytic curve leaves horizontally and the first sampled segment approximates that tangent",
        ),
        assertion(
            f"{trajectory_id}.monotonic-motion",
            "projectile",
            non_decreasing_y and increasing_x,
            measured={"x_increasing": increasing_x, "y_downward_monotonic": non_decreasing_y},
            detail="projectile samples must move continuously rightward and downward",
        ),
        assertion(
            f"{trajectory_id}.continuous-curvature",
            "projectile",
            curvature_continuous,
            measured={"second_difference_spread": max(second_differences) - min(second_differences)},
            tolerance={"spread": 1e-6},
            detail="projectile curvature must not contain a piecewise kink",
        ),
    ]


def finalize_report(
    *,
    schema_version: int,
    coordinate_space: str,
    assertions: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failed = [item["id"] for item in assertions if not item.get("ok")]
    return {
        "schema_version": 1,
        "spec_schema_version": schema_version,
        "coordinate_space": coordinate_space,
        "ok": not failed,
        "assertion_count": len(assertions),
        "failed_assertions": failed,
        "assertions": assertions,
        "metadata": metadata or {},
    }
