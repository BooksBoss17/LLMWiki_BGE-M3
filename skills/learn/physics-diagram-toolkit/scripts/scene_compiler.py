#!/usr/bin/env python
"""Compile mechanics scene graphs into deterministic primitives and geometry assertions."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from diagram_common import atomic_write_json, finite_number, load_json
from geometry_validator import assertion, distance, finalize_report, validate_projectile, validate_relations


OBJECT_TYPES = {"block", "point_mass", "cart", "sphere", "pulley", "collar", "ring", "fixed_support", "impact_marker"}
SURFACE_TYPES = {"ground", "wall", "line", "incline", "rod"}
CONNECTOR_TYPES = {"rope", "spring"}
VECTOR_KINDS = {"force", "velocity", "acceleration", "displacement", "generic"}
TRAJECTORY_TYPES = {"polyline", "parabola", "projectile", "circular_arc"}
ANNOTATION_TYPES = {"text", "math_label", "line", "projection", "angle", "highlight", "leader", "point_marker"}
DIMENSION_TYPES = {"linear", "angular"}
RELATION_TYPES = {"coincident", "connected", "contact", "tangent", "parallel", "perpendicular", "fixed_to", "passes_through"}
SEMANTIC_ROLES = {"given", "motion", "analysis"}
STYLE_PROFILES = {
    "exam-monochrome": {
        "structure": "#111111",
        "force": "#111111",
        "motion": "#111111",
        "dimension": "#111111",
        "auxiliary": "#555555",
    },
    "solution-color": {
        "structure": "#111111",
        "force": "#c62828",
        "motion": "#1565c0",
        "dimension": "#2e7d32",
        "auxiliary": "#666666",
    },
    "legacy-color": {
        "structure": "#111111",
        "force": "#d12b2b",
        "motion": "#1668c1",
        "dimension": "#7b3fb3",
        "auxiliary": "#666666",
    },
}
LAYER_DEFAULTS = {
    "surface": 10,
    "structure": 20,
    "connector": 30,
    "trajectory": 40,
    "dimension": 50,
    "vector": 60,
    "annotation": 70,
    "label": 80,
}


def number(value: Any, field: str) -> float:
    return finite_number(value, field)


def pair(value: Any, field: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{field} must be [x, y]")
    return number(value[0], f"{field}[0]"), number(value[1], f"{field}[1]")


class SceneCompiler:
    def __init__(
        self,
        spec: dict[str, Any],
        anchors: dict[str, list[float]] | None = None,
        request_context: dict[str, Any] | None = None,
    ):
        self.schema_version = int(spec.get("schema_version", 0))
        if self.schema_version not in {2, 3} or spec.get("renderer") != "scene":
            raise ValueError("scene spec must use schema_version=2|3 and renderer=scene")
        self.spec = spec
        self.request_context = request_context or {}
        self.space = str(spec.get("coordinate_space", ""))
        allowed_spaces = {2: {"normalized_scene", "source_pixels"}, 3: {"scene_units", "source_pixels"}}
        if self.space not in allowed_spaces[self.schema_version]:
            raise ValueError(f"unsupported coordinate_space for scene schema v{self.schema_version}: {self.space}")
        canvas = spec.get("canvas")
        if not isinstance(canvas, dict):
            raise ValueError("canvas must be an object")
        self.width = int(number(canvas.get("width"), "canvas.width"))
        self.height = int(number(canvas.get("height"), "canvas.height"))
        if not (200 <= self.width <= 4000 and 200 <= self.height <= 4000):
            raise ValueError("canvas dimensions must be between 200 and 4000")
        self.background = str(canvas.get("background", "#ffffff"))
        self.anchors = anchors or {}
        self.object_anchors: dict[str, dict[str, tuple[float, float]]] = {}
        self.points: dict[str, tuple[float, float]] = {}
        self.directions: dict[str, tuple[float, float]] = {}
        self.primitives: list[dict[str, Any]] = []
        self.ids: set[str] = set()
        self.assertions: list[dict[str, Any]] = []
        self.required_labels = [str(item) for item in spec.get("required_labels", [])]
        self.labels: list[str] = []
        self.scene_scale = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        if self.space == "scene_units":
            scene = spec.get("scene")
            if not isinstance(scene, dict):
                raise ValueError("schema v3 scene_units requires scene {width,height,padding_px}")
            scene_width = number(scene.get("width"), "scene.width")
            scene_height = number(scene.get("height"), "scene.height")
            padding = number(scene.get("padding_px", 24), "scene.padding_px")
            if scene_width <= 0 or scene_height <= 0 or padding < 0:
                raise ValueError("scene dimensions must be positive and padding non-negative")
            usable_width, usable_height = self.width - 2 * padding, self.height - 2 * padding
            if usable_width <= 0 or usable_height <= 0:
                raise ValueError("scene padding leaves no drawable canvas")
            self.scene_scale = min(usable_width / scene_width, usable_height / scene_height)
            self.offset_x = (self.width - scene_width * self.scene_scale) / 2
            self.offset_y = (self.height - scene_height * self.scene_scale) / 2
        self.purpose = str(self.request_context.get("purpose") or spec.get("purpose") or ("legacy" if self.schema_version == 2 else ""))
        self.style_profile = str(self.request_context.get("style_profile") or spec.get("style_profile") or ("legacy-color" if self.schema_version == 2 else ""))
        if self.schema_version == 3:
            if self.purpose not in {"question", "solution", "annotation"}:
                raise ValueError("schema v3 requires purpose=question|solution|annotation")
            expected_style = "exam-monochrome" if self.purpose == "question" else "solution-color"
            if self.purpose != "annotation" and self.style_profile != expected_style:
                raise ValueError(f"purpose={self.purpose} requires style_profile={expected_style}")
            if self.style_profile not in {"exam-monochrome", "solution-color"}:
                raise ValueError("schema v3 requires a supported style_profile")
        if self.style_profile not in STYLE_PROFILES:
            self.style_profile = "legacy-color"
        facts = self.request_context.get("diagram_intent", {}).get("facts", [])
        self.fact_ids = {str(item.get("id")) for item in facts if isinstance(item, dict) and str(item.get("id", ""))}

    def add_id(self, item: dict[str, Any], field: str) -> str:
        item_id = str(item.get("id", ""))
        if not item_id or item_id in self.ids:
            raise ValueError(f"{field}.id must be non-empty and unique")
        self.ids.add(item_id)
        return item_id

    def unit_x(self, value: float) -> float:
        if self.space == "normalized_scene":
            return value * (self.width - 1)
        if self.space == "scene_units":
            return self.offset_x + value * self.scene_scale
        return value

    def unit_y(self, value: float) -> float:
        if self.space == "normalized_scene":
            return value * (self.height - 1)
        if self.space == "scene_units":
            return self.offset_y + value * self.scene_scale
        return value

    def vector_to_pixels(self, value: Any, field: str) -> tuple[float, float]:
        x, y = pair(value, field)
        if self.space == "normalized_scene":
            return x * (self.width - 1), y * (self.height - 1)
        if self.space == "scene_units":
            return x * self.scene_scale, y * self.scene_scale
        return x, y

    def length(self, value: Any, field: str) -> float:
        raw = number(value, field)
        if self.space == "normalized_scene":
            return raw * min(self.width, self.height)
        if self.space == "scene_units":
            return raw * self.scene_scale
        return raw

    def point(self, value: Any, field: str) -> tuple[float, float]:
        if isinstance(value, str):
            return self.resolve_point_ref(value, field)
        if isinstance(value, dict):
            anchor = str(value.get("anchor", ""))
            if anchor:
                base = self.resolve_point_ref(anchor, field)
                offset = self.vector_to_pixels(value.get("offset", [0, 0]), f"{field}.offset")
                if self.space == "scene_units" and anchor in self.anchors:
                    raise ValueError("calibration anchors require source_pixels coordinate space")
                return base[0] + offset[0], base[1] + offset[1]
        x, y = pair(value, field)
        return self.unit_x(x), self.unit_y(y)

    def resolve_point_ref(self, reference: str, field: str) -> tuple[float, float]:
        if reference in self.points:
            return self.points[reference]
        if reference in self.anchors:
            x, y = pair(self.anchors[reference], f"anchor {reference}")
            return x, y
        if "." not in reference:
            reference += ".center"
        object_id, anchor_name = reference.rsplit(".", 1)
        if object_id in self.object_anchors and anchor_name in self.object_anchors[object_id]:
            return self.object_anchors[object_id][anchor_name]
        raise ValueError(f"{field} references unknown point: {reference}")

    def register_points(self, prefix: str, anchors: dict[str, tuple[float, float]]) -> None:
        for name, value in anchors.items():
            self.points[f"{prefix}.{name}"] = value

    def add_primitive(self, primitive: dict[str, Any], layer_name: str, layer: Any = None) -> None:
        primitive["layer"] = int(number(layer if layer is not None else LAYER_DEFAULTS[layer_name], "layer"))
        self.primitives.append(primitive)

    def palette(self, role: str, explicit: Any = None) -> str:
        if self.style_profile == "exam-monochrome":
            return STYLE_PROFILES[self.style_profile][role]
        return str(explicit or STYLE_PROFILES[self.style_profile][role])

    def semantic_role(self, item: dict[str, Any], field: str) -> tuple[str, str | None]:
        role = str(item.get("semantic_role", ""))
        fact_id = str(item.get("fact_id", "")) or None
        if self.schema_version == 2:
            return role or "analysis", fact_id
        if role not in SEMANTIC_ROLES:
            raise ValueError(f"{field}.semantic_role must be given|motion|analysis")
        if self.purpose == "question":
            if role == "analysis":
                raise ValueError(f"question diagram forbids analysis overlay: {field}")
            if not fact_id or fact_id not in self.fact_ids:
                raise ValueError(f"question diagram {field} must bind a declared diagram_intent fact_id")
        return role, fact_id

    def add_text(
        self,
        x: float,
        y: float,
        text: str,
        *,
        size: int = 22,
        color: str | None = None,
        semantic_id: str | None = None,
        allow_overlap: bool = False,
    ) -> None:
        if not text:
            return
        self.labels.append(text)
        primitive = {
            "type": "text",
            "x": x,
            "y": y,
            "text": text,
            "size": size,
            "color": color or self.palette("structure"),
            "allow_overlap": allow_overlap,
        }
        if semantic_id:
            primitive["semantic_id"] = semantic_id
        self.add_primitive(primitive, "label")

    def surface_points(self, item: dict[str, Any], index: int) -> list[tuple[float, float]]:
        item_type = str(item.get("type", ""))
        if item_type not in SURFACE_TYPES:
            raise ValueError(f"unsupported surface type: {item_type}")
        if "points" in item:
            raw_points = item["points"]
            if not isinstance(raw_points, list) or len(raw_points) < 2:
                raise ValueError("surface points require at least two points")
            return [self.point(value, f"surfaces[{index}].points") for value in raw_points]
        origin = self.point(item.get("origin"), f"surfaces[{index}].origin")
        default_length = 70 if self.space == "scene_units" else (0.7 if self.space == "normalized_scene" else 400)
        length = self.length(item.get("length", default_length), "surface.length")
        default_angle = {"ground": 0.0, "wall": -90.0, "incline": -25.0, "line": 0.0, "rod": -90.0}[item_type]
        angle = math.radians(number(item.get("angle_degrees", default_angle), "surface.angle_degrees"))
        return [origin, (origin[0] + length * math.cos(angle), origin[1] + length * math.sin(angle))]

    def compile_surfaces(self) -> None:
        surfaces = self.spec.get("surfaces", [])
        if not isinstance(surfaces, list):
            raise ValueError("surfaces must be an array")
        for index, item in enumerate(surfaces):
            if not isinstance(item, dict):
                raise ValueError(f"surfaces[{index}] must be an object")
            item_id = self.add_id(item, f"surfaces[{index}]")
            points = self.surface_points(item, index)
            start, end = points[0], points[-1]
            self.register_points(item_id, {"start": start, "end": end})
            self.directions[f"{item_id}.direction"] = (end[0] - start[0], end[1] - start[1])
            self.add_primitive(
                {
                    "type": "polyline",
                    "points": [[x, y] for x, y in points],
                    "color": self.palette("structure", item.get("color")),
                    "stroke_width": int(number(item.get("stroke_width", 4), "stroke_width")),
                    "semantic_id": item_id,
                },
                "surface",
                item.get("layer"),
            )

    def compile_objects(self) -> None:
        objects = self.spec.get("objects", [])
        if not isinstance(objects, list):
            raise ValueError("objects must be an array")
        surfaces = self.spec.get("surfaces", [])
        for index, item in enumerate(objects):
            if not isinstance(item, dict):
                raise ValueError(f"objects[{index}] must be an object")
            item_id = self.add_id(item, f"objects[{index}]")
            item_type = str(item.get("type", ""))
            if item_type not in OBJECT_TYPES:
                raise ValueError(f"unsupported object type: {item_type}")
            default_size = [12, 8] if self.space == "scene_units" else ([0.12, 0.08] if self.space == "normalized_scene" else [90, 60])
            sx, sy = pair(item.get("size", default_size), f"objects[{index}].size")
            if self.space == "normalized_scene":
                object_width, object_height = self.unit_x(sx), self.unit_y(sy)
            elif self.space == "scene_units":
                object_width, object_height = sx * self.scene_scale, sy * self.scene_scale
            else:
                object_width, object_height = sx, sy
            if object_width <= 0 or object_height <= 0:
                raise ValueError(f"objects[{index}].size must be positive")
            relation = item.get("on_surface")
            contact: tuple[float, float] | None = None
            if relation is not None:
                if not isinstance(relation, dict):
                    raise ValueError(f"objects[{index}].on_surface must be an object")
                surface_id = str(relation.get("surface", ""))
                matching = [(i, s) for i, s in enumerate(surfaces) if isinstance(s, dict) and str(s.get("id", "")) == surface_id]
                if len(matching) != 1:
                    raise ValueError(f"objects[{index}] references unknown or duplicate surface: {surface_id}")
                surface_index, surface = matching[0]
                surface_points = self.surface_points(surface, surface_index)
                start, end = surface_points[0], surface_points[-1]
                fraction = number(relation.get("fraction", 0.5), f"objects[{index}].on_surface.fraction")
                if not 0 <= fraction <= 1:
                    raise ValueError("on_surface.fraction must be between 0 and 1")
                dx, dy = end[0] - start[0], end[1] - start[1]
                tangent_length = math.hypot(dx, dy)
                if tangent_length < 1:
                    raise ValueError(f"surface is too short for object {item_id}")
                ux, uy = dx / tangent_length, dy / tangent_length
                outward_x, outward_y = uy, -ux
                clearance = self.length(relation.get("clearance", 0), f"objects[{index}].on_surface.clearance")
                contact = (start[0] + dx * fraction, start[1] + dy * fraction)
                cx = contact[0] + outward_x * (object_height / 2 + clearance)
                cy = contact[1] + outward_y * (object_height / 2 + clearance)
                angle = math.atan2(dy, dx)
            else:
                cx, cy = self.point(item.get("position"), f"objects[{index}].position")
                angle = math.radians(number(item.get("angle_degrees", 0), f"objects[{index}].angle_degrees"))
            stroke = self.palette("structure", item.get("color"))
            fill = str(item.get("fill") or (self.background if self.schema_version == 3 else "")) or None
            stroke_width = int(number(item.get("stroke_width", 4), "stroke_width"))

            def rotated(local_x: float, local_y: float) -> tuple[float, float]:
                return cx + local_x * math.cos(angle) - local_y * math.sin(angle), cy + local_x * math.sin(angle) + local_y * math.cos(angle)

            anchors = {
                "center": (cx, cy),
                "top": rotated(0, -object_height / 2),
                "bottom": rotated(0, object_height / 2),
                "left": rotated(-object_width / 2, 0),
                "right": rotated(object_width / 2, 0),
            }
            radius = min(object_width, object_height) / 2
            if item_type in {"point_mass", "sphere", "pulley", "collar", "ring"}:
                anchors.update({"top": (cx, cy - radius), "bottom": (cx, cy + radius), "left": (cx - radius, cy), "right": (cx + radius, cy)})
            if item_type == "fixed_support":
                anchors["connection"] = (cx, cy)
            self.object_anchors[item_id] = anchors
            self.register_points(item_id, anchors)
            self.directions[f"{item_id}.direction"] = (math.cos(angle), math.sin(angle))
            if item_type == "block":
                corners = [rotated(-object_width / 2, -object_height / 2), rotated(object_width / 2, -object_height / 2), rotated(object_width / 2, object_height / 2), rotated(-object_width / 2, object_height / 2)]
                self.add_primitive(
                    {"type": "polygon", "points": [[*p] for p in corners], "stroke": stroke, "fill": fill or "#ffffff", "stroke_width": stroke_width, "semantic_id": item_id},
                    "structure",
                    item.get("layer"),
                )
            elif item_type in {"point_mass", "sphere", "pulley", "collar", "ring"}:
                self.add_primitive(
                    {"type": "circle", "cx": cx, "cy": cy, "r": radius, "stroke": stroke, "fill": fill, "stroke_width": stroke_width, "semantic_id": item_id},
                    "structure",
                    item.get("layer"),
                )
                if item_type == "point_mass":
                    self.add_primitive({"type": "circle", "cx": cx, "cy": cy, "r": max(3, radius * 0.12), "stroke": stroke, "fill": stroke, "stroke_width": max(2, stroke_width), "semantic_id": item_id}, "structure", item.get("layer"))
            elif item_type == "cart":
                body_height = object_height * 0.65
                body = [rotated(-object_width / 2, -object_height / 2), rotated(object_width / 2, -object_height / 2), rotated(object_width / 2, -object_height / 2 + body_height), rotated(-object_width / 2, -object_height / 2 + body_height)]
                self.add_primitive({"type": "polygon", "points": [[*p] for p in body], "stroke": stroke, "fill": fill or "#ffffff", "stroke_width": stroke_width, "semantic_id": item_id}, "structure", item.get("layer"))
                wheel_radius = max(4, object_height * 0.16)
                for local_x in (-object_width * 0.3, object_width * 0.3):
                    wx, wy = rotated(local_x, object_height / 2 - wheel_radius)
                    self.add_primitive({"type": "circle", "cx": wx, "cy": wy, "r": wheel_radius, "stroke": stroke, "fill": self.background, "stroke_width": stroke_width, "semantic_id": item_id}, "structure", item.get("layer"))
            elif item_type == "fixed_support":
                tangent = (math.cos(angle + math.pi / 2), math.sin(angle + math.pi / 2))
                half = object_height / 2
                start, end = (cx - tangent[0] * half, cy - tangent[1] * half), (cx + tangent[0] * half, cy + tangent[1] * half)
                self.add_primitive({"type": "line", "x1": start[0], "y1": start[1], "x2": end[0], "y2": end[1], "color": stroke, "stroke_width": stroke_width, "semantic_id": item_id}, "structure", item.get("layer"))
                normal = (math.cos(angle), math.sin(angle))
                for fraction in (-0.4, -0.2, 0.0, 0.2, 0.4):
                    bx, by = cx + tangent[0] * half * 2 * fraction, cy + tangent[1] * half * 2 * fraction
                    self.add_primitive({"type": "line", "x1": bx, "y1": by, "x2": bx - normal[0] * object_width * 0.45 + tangent[0] * object_width * 0.2, "y2": by - normal[1] * object_width * 0.45 + tangent[1] * object_width * 0.2, "color": stroke, "stroke_width": max(2, stroke_width - 1), "semantic_id": item_id}, "structure", item.get("layer"))
                self.add_primitive({"type": "circle", "cx": cx, "cy": cy, "r": max(3, stroke_width * 1.15), "stroke": stroke, "fill": stroke, "stroke_width": 2, "semantic_id": f"{item_id}.connection"}, "structure", item.get("layer"))
            elif item_type == "impact_marker":
                arm = radius
                self.add_primitive({"type": "line", "x1": cx - arm, "y1": cy - arm, "x2": cx + arm, "y2": cy + arm, "color": stroke, "stroke_width": stroke_width, "semantic_id": item_id}, "structure", item.get("layer"))
                self.add_primitive({"type": "line", "x1": cx - arm, "y1": cy + arm, "x2": cx + arm, "y2": cy - arm, "color": stroke, "stroke_width": stroke_width, "semantic_id": item_id}, "structure", item.get("layer"))
            if contact is not None:
                residual = distance(anchors["bottom"], contact)
                self.points[f"{item_id}.contact"] = contact
                self.assertions.append(assertion(f"{item_id}.surface-contact", "contact", residual <= 0.5, measured={"residual_px": residual}, tolerance={"residual_px": 0.5}, detail=f"{item_id} bottom must contact its surface without a gap or penetration"))
            label = str(item.get("label", ""))
            if label:
                if item_type in {"collar", "ring", "impact_marker"}:
                    lx, ly = cx + radius + 8, cy - radius - 12
                    self.add_text(lx, ly, label, size=int(number(item.get("label_size", 22), "label_size")), color=stroke, semantic_id=f"{item_id}.label")
                elif item_type == "fixed_support":
                    self.add_text(cx + 8, cy - 34, label, size=int(number(item.get("label_size", 22), "label_size")), color=stroke, semantic_id=f"{item_id}.label")
                else:
                    self.add_text(cx - object_width * 0.2, cy - 12, label, size=int(number(item.get("label_size", 22), "label_size")), color=stroke, semantic_id=f"{item_id}.label", allow_overlap=True)

    def endpoint(self, value: Any, field: str) -> tuple[float, float]:
        return self.point(value, field)

    def spring_points(self, start: tuple[float, float], end: tuple[float, float], turns: int, amplitude: float) -> list[list[float]]:
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length < 2:
            raise ValueError("spring endpoints are too close")
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        points = [[start[0], start[1]]]
        segments = max(4, turns * 2)
        for index in range(1, segments):
            fraction = index / segments
            offset = amplitude * (1 if index % 2 else -1)
            points.append([start[0] + dx * fraction + px * offset, start[1] + dy * fraction + py * offset])
        points.append([end[0], end[1]])
        return points

    def compile_connectors(self) -> None:
        connectors = self.spec.get("connectors", [])
        if not isinstance(connectors, list):
            raise ValueError("connectors must be an array")
        for index, item in enumerate(connectors):
            if not isinstance(item, dict):
                raise ValueError(f"connectors[{index}] must be an object")
            item_id = self.add_id(item, f"connectors[{index}]")
            item_type = str(item.get("type", ""))
            if item_type not in CONNECTOR_TYPES:
                raise ValueError(f"unsupported connector type: {item_type}")
            start = self.endpoint(item.get("from"), f"connectors[{index}].from")
            end = self.endpoint(item.get("to"), f"connectors[{index}].to")
            self.register_points(item_id, {"from": start, "to": end, "start": start, "end": end})
            self.directions[f"{item_id}.direction"] = (end[0] - start[0], end[1] - start[1])
            if item_type == "rope":
                via = item.get("via", [])
                if not isinstance(via, list):
                    raise ValueError("connector.via must be an array")
                points = [[start[0], start[1]], *[[*self.point(value, "connector.via")] for value in via], [end[0], end[1]]]
            else:
                turns = int(number(item.get("turns", 8), "connector.turns"))
                default_amplitude = 1.5 if self.space == "scene_units" else (0.015 if self.space == "normalized_scene" else 10)
                amplitude = self.length(item.get("amplitude", default_amplitude), "connector.amplitude")
                points = self.spring_points(start, end, turns, amplitude)
            self.add_primitive({"type": "polyline", "points": points, "color": self.palette("structure", item.get("color")), "stroke_width": int(number(item.get("stroke_width", 3), "stroke_width")), "semantic_id": item_id}, "connector", item.get("layer"))

    def compile_vectors(self) -> None:
        vectors = self.spec.get("vectors", [])
        if not isinstance(vectors, list):
            raise ValueError("vectors must be an array")
        for index, item in enumerate(vectors):
            if not isinstance(item, dict):
                raise ValueError(f"vectors[{index}] must be an object")
            item_id = self.add_id(item, f"vectors[{index}]")
            kind = str(item.get("kind", "generic"))
            if kind not in VECTOR_KINDS:
                raise ValueError(f"unsupported vector kind: {kind}")
            semantic_role, _fact_id = self.semantic_role(item, f"vectors[{index}]")
            start = self.point(item["start"], f"vectors[{index}].start") if "start" in item else self.endpoint(item.get("attach_to"), f"vectors[{index}].attach_to")
            dx, dy = pair(item.get("direction"), f"vectors[{index}].direction")
            norm = math.hypot(dx, dy)
            if norm < 1e-9:
                raise ValueError(f"vectors[{index}].direction cannot be zero")
            default_length = 14 if self.space == "scene_units" else (0.14 if self.space == "normalized_scene" else 90)
            length = self.length(item.get("length", default_length), f"vectors[{index}].length")
            end = (start[0] + dx / norm * length, start[1] + dy / norm * length)
            self.register_points(item_id, {"start": start, "end": end})
            self.directions[f"{item_id}.direction"] = (end[0] - start[0], end[1] - start[1])
            palette_role = "force" if kind == "force" else "motion"
            label = str(item.get("label", ""))
            if label:
                self.labels.append(label)
            self.add_primitive({"type": "arrow", "x1": start[0], "y1": start[1], "x2": end[0], "y2": end[1], "label": label, "color": self.palette(palette_role, item.get("color")), "stroke_width": int(number(item.get("stroke_width", 4), "stroke_width")), "semantic_id": item_id, "semantic_role": semantic_role}, "vector", item.get("layer"))

    def sampled_arc(self, center: tuple[float, float], radius: float, start: float, end: float, samples: int = 48) -> list[list[float]]:
        return [[center[0] + radius * math.cos(math.radians(start + (end - start) * i / samples)), center[1] + radius * math.sin(math.radians(start + (end - start) * i / samples))] for i in range(samples + 1)]

    def compile_trajectories(self) -> None:
        trajectories = self.spec.get("trajectories", [])
        if not isinstance(trajectories, list):
            raise ValueError("trajectories must be an array")
        for index, item in enumerate(trajectories):
            if not isinstance(item, dict):
                raise ValueError(f"trajectories[{index}] must be an object")
            item_id = self.add_id(item, f"trajectories[{index}]")
            kind = str(item.get("type", ""))
            declared_start_direction: tuple[float, float] | None = None
            if kind not in TRAJECTORY_TYPES:
                raise ValueError(f"unsupported trajectory type: {kind}")
            if kind == "polyline":
                raw_points = item.get("points")
                if not isinstance(raw_points, list) or len(raw_points) < 2:
                    raise ValueError("polyline trajectory requires at least two points")
                points = [[*self.point(value, "trajectory.points")] for value in raw_points]
            elif kind == "parabola":
                if self.schema_version == 3:
                    raise ValueError("schema v3 forbids generic parabola; use projectile")
                start, end = self.point(item.get("start"), "trajectory.start"), self.point(item.get("end"), "trajectory.end")
                height = self.length(item.get("height"), "trajectory.height")
                points = []
                for sample in range(49):
                    fraction = sample / 48
                    x = start[0] + (end[0] - start[0]) * fraction
                    baseline = start[1] + (end[1] - start[1]) * fraction
                    points.append([x, baseline - 4 * height * fraction * (1 - fraction)])
            elif kind == "projectile":
                if self.schema_version != 3:
                    raise ValueError("projectile trajectory requires schema v3")
                start, end = self.point(item.get("start"), "trajectory.start"), self.point(item.get("end"), "trajectory.end")
                initial_direction = pair(item.get("initial_direction", [1, 0]), "trajectory.initial_direction")
                declared_start_direction = initial_direction
                if end[0] <= start[0] or end[1] <= start[1]:
                    raise ValueError("horizontal projectile end must be rightward and below start")
                samples = int(number(item.get("samples", 65), "trajectory.samples"))
                if not 17 <= samples <= 1001:
                    raise ValueError("projectile samples must be 17-1001")
                points = []
                for sample in range(samples):
                    fraction = sample / (samples - 1)
                    points.append([start[0] + (end[0] - start[0]) * fraction, start[1] + (end[1] - start[1]) * fraction * fraction])
                self.assertions.extend(validate_projectile(item_id, [(p[0], p[1]) for p in points], initial_direction))
            else:
                center = self.point(item.get("center"), "trajectory.center")
                radius = self.length(item.get("radius"), "trajectory.radius")
                points = self.sampled_arc(center, radius, number(item.get("start_angle", 0), "start_angle"), number(item.get("end_angle", 360), "end_angle"))
            start, end = (points[0][0], points[0][1]), (points[-1][0], points[-1][1])
            self.register_points(item_id, {"start": start, "end": end})
            self.directions[f"{item_id}.start_direction"] = declared_start_direction or (points[1][0] - points[0][0], points[1][1] - points[0][1])
            primitive = {"type": "polyline", "points": points, "color": self.palette("motion", item.get("color")), "stroke_width": int(number(item.get("stroke_width", 3), "stroke_width")), "semantic_id": item_id}
            if item.get("dash") is not None:
                primitive["dash"] = item["dash"]
            self.add_primitive(primitive, "trajectory", item.get("layer"))

    def compile_landmarks(self) -> None:
        landmarks = self.spec.get("landmarks", [])
        if not isinstance(landmarks, list):
            raise ValueError("landmarks must be an array")
        for index, item in enumerate(landmarks):
            if not isinstance(item, dict):
                raise ValueError(f"landmarks[{index}] must be an object")
            item_id = self.add_id(item, f"landmarks[{index}]")
            center = self.point(item.get("position"), f"landmarks[{index}].position")
            self.register_points(item_id, {"center": center})
            radius = self.length(item.get("radius", 0.7 if self.space == "scene_units" else (0.008 if self.space == "normalized_scene" else 5)), "landmark.radius")
            marker = str(item.get("marker", "dot"))
            stroke = self.palette("structure", item.get("color"))
            if marker == "none":
                pass
            elif marker == "dot":
                self.add_primitive({"type": "circle", "cx": center[0], "cy": center[1], "r": radius, "stroke": stroke, "fill": stroke, "stroke_width": 2, "semantic_id": item_id}, "annotation", item.get("layer"))
            elif marker == "cross":
                for sign in (-1, 1):
                    self.add_primitive({"type": "line", "x1": center[0] - radius, "y1": center[1] + sign * radius, "x2": center[0] + radius, "y2": center[1] - sign * radius, "color": stroke, "stroke_width": 3, "semantic_id": item_id}, "annotation", item.get("layer"))
            else:
                raise ValueError(f"unsupported landmark marker: {marker}")
            label = str(item.get("label", ""))
            if label:
                offset = self.vector_to_pixels(item.get("label_offset", [1.5, -3]), "landmark.label_offset")
                self.add_text(center[0] + offset[0], center[1] + offset[1], label, size=int(number(item.get("label_size", 22), "label_size")), color=stroke, semantic_id=f"{item_id}.label")

    def compile_dimensions(self) -> None:
        dimensions = self.spec.get("dimensions", [])
        if not isinstance(dimensions, list):
            raise ValueError("dimensions must be an array")
        for index, item in enumerate(dimensions):
            if not isinstance(item, dict):
                raise ValueError(f"dimensions[{index}] must be an object")
            item_id = self.add_id(item, f"dimensions[{index}]")
            kind = str(item.get("type", ""))
            if kind not in DIMENSION_TYPES:
                raise ValueError(f"unsupported dimension type: {kind}")
            self.semantic_role(item, f"dimensions[{index}]")
            stroke = self.palette("dimension", item.get("color"))
            if kind == "linear":
                source_start = self.point(item.get("from"), "dimension.from")
                source_end = self.point(item.get("to"), "dimension.to")
                offset = self.vector_to_pixels(item.get("offset", [0, 0]), "dimension.offset")
                start, end = (source_start[0] + offset[0], source_start[1] + offset[1]), (source_end[0] + offset[0], source_end[1] + offset[1])
                if distance(start, end) < 2:
                    raise ValueError("linear dimension endpoints are too close")
                self.register_points(item_id, {"start": start, "end": end, "source_start": source_start, "source_end": source_end})
                self.directions[f"{item_id}.direction"] = (end[0] - start[0], end[1] - start[1])
                if offset != (0.0, 0.0):
                    raw_gaps = item.get("extension_gaps", [0, 0])
                    if not isinstance(raw_gaps, list) or len(raw_gaps) != 2:
                        raise ValueError("linear dimension extension_gaps must be [start, end]")
                    gaps = [self.length(raw_gaps[0], "dimension.extension_gaps[0]"), self.length(raw_gaps[1], "dimension.extension_gaps[1]")]
                    for gap, source, target in ((gaps[0], source_start, start), (gaps[1], source_end, end)):
                        dx, dy = target[0] - source[0], target[1] - source[1]
                        segment_length = math.hypot(dx, dy)
                        if gap >= segment_length:
                            raise ValueError("linear dimension extension gap consumes the extension line")
                        extension_start = (source[0] + dx / segment_length * gap, source[1] + dy / segment_length * gap)
                        self.add_primitive({"type": "line", "x1": extension_start[0], "y1": extension_start[1], "x2": target[0], "y2": target[1], "color": stroke, "stroke_width": 2, "semantic_id": item_id}, "dimension", item.get("layer"))
                self.add_primitive({"type": "double_arrow", "x1": start[0], "y1": start[1], "x2": end[0], "y2": end[1], "color": stroke, "stroke_width": int(number(item.get("stroke_width", 3), "stroke_width")), "semantic_id": item_id}, "dimension", item.get("layer"))
                midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
                label_offset = self.vector_to_pixels(item.get("label_offset", [1, -3]), "dimension.label_offset")
                self.add_text(midpoint[0] + label_offset[0], midpoint[1] + label_offset[1], str(item.get("label", "")), size=int(number(item.get("label_size", 22), "label_size")), color=stroke, semantic_id=f"{item_id}.label")
                self.assertions.append(assertion(f"{item_id}.endpoints", "dimension", True, measured={"length_px": distance(start, end)}, detail="linear dimension has two distinct, bound endpoints"))
            else:
                vertex = self.point(item.get("vertex"), "dimension.vertex")
                ray_a = self.point(item.get("ray_a"), "dimension.ray_a")
                ray_b = self.point(item.get("ray_b"), "dimension.ray_b")
                va, vb = (ray_a[0] - vertex[0], ray_a[1] - vertex[1]), (ray_b[0] - vertex[0], ray_b[1] - vertex[1])
                angle_a, angle_b = math.degrees(math.atan2(va[1], va[0])), math.degrees(math.atan2(vb[1], vb[0]))
                while angle_b < angle_a:
                    angle_b += 360
                if angle_b - angle_a > 180:
                    angle_a, angle_b = angle_b, angle_a + 360
                radius = self.length(item.get("radius", 5 if self.space == "scene_units" else (0.05 if self.space == "normalized_scene" else 30)), "dimension.radius")
                points = self.sampled_arc(vertex, radius, angle_a, angle_b, 32)
                measured = abs(angle_b - angle_a)
                expected = number(item.get("value_degrees", measured), "dimension.value_degrees")
                residual = abs(measured - expected)
                self.register_points(item_id, {"vertex": vertex, "ray_a": ray_a, "ray_b": ray_b})
                self.directions[f"{item_id}.ray_a"] = va
                self.directions[f"{item_id}.ray_b"] = vb
                draw_rays = item.get("draw_rays", [])
                if not isinstance(draw_rays, list) or any(value not in {"a", "b"} for value in draw_rays):
                    raise ValueError("angular dimension draw_rays must contain only a and/or b")
                for ray_name, vector in (("a", va), ("b", vb)):
                    if ray_name not in draw_rays:
                        continue
                    vector_length = math.hypot(vector[0], vector[1])
                    reference_length = min(vector_length, radius * 1.45)
                    ray_end = (
                        vertex[0] + vector[0] / vector_length * reference_length,
                        vertex[1] + vector[1] / vector_length * reference_length,
                    )
                    self.add_primitive({"type": "line", "x1": vertex[0], "y1": vertex[1], "x2": ray_end[0], "y2": ray_end[1], "color": stroke, "stroke_width": 2, "semantic_id": f"{item_id}.ray-{ray_name}"}, "dimension", item.get("layer"))
                self.add_primitive({"type": "polyline", "points": points, "color": stroke, "stroke_width": int(number(item.get("stroke_width", 3), "stroke_width")), "semantic_id": item_id}, "dimension", item.get("layer"))
                middle_angle = math.radians((angle_a + angle_b) / 2)
                label_center = (
                    vertex[0] + radius * 1.55 * math.cos(middle_angle),
                    vertex[1] + radius * 1.55 * math.sin(middle_angle),
                )
                label_size = int(number(item.get("label_size", 21), "label_size"))
                label_text = str(item.get("label", f"{expected:g}°"))
                self.add_text(label_center[0] - label_size * max(1, len(label_text)) * 0.28, label_center[1] - label_size * 0.5, label_text, size=label_size, color=stroke, semantic_id=f"{item_id}.label")
                self.assertions.append(assertion(f"{item_id}.angle", "angle", residual <= 0.5, measured={"degrees": measured, "residual_degrees": residual}, tolerance={"degrees": 0.5}, detail="angular dimension must match the two bound rays"))

    def compile_annotations(self) -> None:
        annotations = self.spec.get("annotations", [])
        if not isinstance(annotations, list):
            raise ValueError("annotations must be an array")
        for index, item in enumerate(annotations):
            if not isinstance(item, dict):
                raise ValueError(f"annotations[{index}] must be an object")
            kind = str(item.get("type", ""))
            if kind not in ANNOTATION_TYPES:
                raise ValueError(f"unsupported annotation type: {kind}")
            item_id = str(item.get("id", f"annotation-{index}"))
            if item.get("id"):
                self.add_id(item, f"annotations[{index}]")
            if self.schema_version == 3 and kind not in {"text", "math_label", "point_marker"}:
                self.semantic_role(item, f"annotations[{index}]")
            color = self.palette("auxiliary", item.get("color"))
            if kind in {"text", "math_label"}:
                if self.schema_version == 3 and item.get("semantic_role"):
                    self.semantic_role(item, f"annotations[{index}]")
                x, y = self.point(item.get("position"), "annotation.position")
                self.add_text(x, y, str(item.get("text", "")), size=int(number(item.get("size", 22), "annotation.size")), color=color, semantic_id=item_id, allow_overlap=bool(item.get("allow_overlap", False)))
            elif kind in {"line", "projection"}:
                start, end = self.point(item.get("start"), "annotation.start"), self.point(item.get("end"), "annotation.end")
                primitive = {"type": "line", "x1": start[0], "y1": start[1], "x2": end[0], "y2": end[1], "color": color, "stroke_width": int(number(item.get("stroke_width", 3), "stroke_width")), "semantic_id": item_id}
                if kind == "projection" or item.get("dash"):
                    primitive["dash"] = item.get("dash", [8, 6])
                self.add_primitive(primitive, "annotation", item.get("layer"))
            elif kind == "leader":
                target, label_point = self.point(item.get("target"), "leader.target"), self.point(item.get("label_position"), "leader.label_position")
                self.add_primitive({"type": "line", "x1": target[0], "y1": target[1], "x2": label_point[0], "y2": label_point[1], "color": color, "stroke_width": int(number(item.get("stroke_width", 2), "stroke_width")), "semantic_id": item_id}, "annotation", item.get("layer"))
                self.add_text(label_point[0] + 5, label_point[1] - 22, str(item.get("text", "")), size=int(number(item.get("size", 21), "annotation.size")), color=color, semantic_id=f"{item_id}.label")
            elif kind == "point_marker":
                center = self.point(item.get("position"), "point_marker.position")
                radius = self.length(item.get("radius", 0.7 if self.space == "scene_units" else (0.008 if self.space == "normalized_scene" else 5)), "point_marker.radius")
                self.add_primitive({"type": "circle", "cx": center[0], "cy": center[1], "r": radius, "stroke": color, "fill": color, "stroke_width": 2, "semantic_id": item_id}, "annotation", item.get("layer"))
            elif kind == "angle":
                if self.schema_version == 3:
                    raise ValueError("schema v3 uses dimensions[type=angular], not free angle annotations")
                center = self.point(item.get("center"), "annotation.center")
                radius = self.length(item.get("radius", 0.05 if self.space == "normalized_scene" else 30), "annotation.radius")
                points = self.sampled_arc(center, radius, number(item.get("start_angle"), "start_angle"), number(item.get("end_angle"), "end_angle"), 24)
                self.add_primitive({"type": "polyline", "points": points, "color": color, "stroke_width": 3, "semantic_id": item_id}, "annotation", item.get("layer"))
                if item.get("label"):
                    middle = points[len(points) // 2]
                    self.add_text(middle[0] + 4, middle[1] - 18, str(item["label"]), size=20, color=color, semantic_id=f"{item_id}.label")
            else:
                center = self.point(item.get("center"), "annotation.center")
                radius = self.length(item.get("radius", 0.04 if self.space == "normalized_scene" else 25), "annotation.radius")
                self.add_primitive({"type": "circle", "cx": center[0], "cy": center[1], "r": radius, "stroke": color, "stroke_width": 5, "semantic_id": item_id}, "annotation", item.get("layer"))

    def line_width_consistency_assertion(self) -> dict[str, Any]:
        segments: list[tuple[str, int, tuple[float, float], tuple[float, float]]] = []
        for primitive in self.primitives:
            primitive_type = primitive.get("type")
            semantic_id = str(primitive.get("semantic_id", ""))
            width = int(primitive.get("stroke_width", 1))
            if primitive_type == "line":
                points = [(float(primitive["x1"]), float(primitive["y1"])), (float(primitive["x2"]), float(primitive["y2"]))]
            elif primitive_type == "polyline":
                points = [(float(point[0]), float(point[1])) for point in primitive.get("points", [])]
            else:
                continue
            segments.extend((semantic_id, width, start, end) for start, end in zip(points, points[1:]))

        def overlap(first: tuple[float, float], second: tuple[float, float], other_first: tuple[float, float], other_second: tuple[float, float]) -> bool:
            dx, dy = second[0] - first[0], second[1] - first[1]
            length = math.hypot(dx, dy)
            if length < 1:
                return False
            cross_a = abs(dx * (other_first[1] - first[1]) - dy * (other_first[0] - first[0])) / length
            cross_b = abs(dx * (other_second[1] - first[1]) - dy * (other_second[0] - first[0])) / length
            if max(cross_a, cross_b) > 0.5:
                return False
            ux, uy = dx / length, dy / length
            first_interval = (0.0, length)
            other_values = sorted(((other_first[0] - first[0]) * ux + (other_first[1] - first[1]) * uy, (other_second[0] - first[0]) * ux + (other_second[1] - first[1]) * uy))
            return min(first_interval[1], other_values[1]) - max(first_interval[0], other_values[0]) > 0.5

        conflicts: list[dict[str, Any]] = []
        for index, (semantic_a, width_a, start_a, end_a) in enumerate(segments):
            for semantic_b, width_b, start_b, end_b in segments[index + 1 :]:
                if semantic_a == semantic_b or width_a == width_b:
                    continue
                if overlap(start_a, end_a, start_b, end_b):
                    conflicts.append({"a": semantic_a, "b": semantic_b, "widths": [width_a, width_b]})
        return assertion("lines.consistent-overlap-width", "line-width", not conflicts, measured={"conflicts": conflicts}, detail="collinear overlapping semantic lines must not create visible width jumps")

    def compile(self, background_image: str | None = None) -> dict[str, Any]:
        self.compile_surfaces()
        self.compile_objects()
        self.compile_connectors()
        self.compile_trajectories()
        self.compile_landmarks()
        self.compile_dimensions()
        self.compile_vectors()
        self.compile_annotations()
        self.assertions.extend(validate_relations(self.spec.get("relations", []), self.points, self.directions))
        if not self.primitives:
            raise ValueError("scene must produce at least one primitive")
        missing = [label for label in self.required_labels if not any(label in actual for actual in self.labels)]
        if missing:
            raise ValueError("missing required labels: " + ", ".join(missing))
        self.assertions.append(assertion("labels.required", "labels", not missing, measured={"labels": self.labels}, detail="all required labels are present"))
        if self.schema_version == 3:
            self.assertions.append(self.line_width_consistency_assertion())
            self.assertions.append(assertion("coordinate.isotropic", "coordinates", self.space != "scene_units" or self.scene_scale > 0, measured={"scale_x": self.scene_scale, "scale_y": self.scene_scale}, tolerance={"difference": 0}, detail="scene x and y use one shared scale"))
            self.assertions.append(assertion("layers.surface-behind-objects", "occlusion", LAYER_DEFAULTS["surface"] < LAYER_DEFAULTS["structure"], measured={"surface": LAYER_DEFAULTS["surface"], "structure": LAYER_DEFAULTS["structure"]}, detail="surfaces render behind filled objects"))
        self.primitives.sort(key=lambda item: int(item.get("layer", 0)))
        geometry_report = finalize_report(schema_version=self.schema_version, coordinate_space=self.space, assertions=self.assertions, metadata={"purpose": self.purpose, "style_profile": self.style_profile, "scene_scale": self.scene_scale, "offset": [self.offset_x, self.offset_y], "supported_relation_types": sorted(RELATION_TYPES)})
        if not geometry_report["ok"]:
            raise ValueError("geometry assertions failed: " + ", ".join(geometry_report["failed_assertions"]))
        low_level = {
            "schema_version": 1,
            "revision": int(self.spec.get("revision", 0)),
            "kind": "annotation" if background_image else ("free_body" if self.spec.get("vectors") else "trajectory"),
            "canvas": {"width": self.width, "height": self.height, "background": self.background},
            "primitives": self.primitives,
            "collision_check": self.schema_version == 3,
        }
        if background_image:
            low_level["background_image"] = background_image
        return {
            "ok": True,
            "low_level_spec": low_level,
            "geometry_report": geometry_report,
            "summary": {
                "object_count": len(self.spec.get("objects", [])),
                "surface_count": len(self.spec.get("surfaces", [])),
                "connector_count": len(self.spec.get("connectors", [])),
                "vector_count": len(self.spec.get("vectors", [])),
                "trajectory_count": len(self.spec.get("trajectories", [])),
                "dimension_count": len(self.spec.get("dimensions", [])),
                "relation_count": len(self.spec.get("relations", [])),
                "primitive_count": len(self.primitives),
            },
        }


def compile_scene(
    spec: dict[str, Any],
    anchors: dict[str, list[float]] | None = None,
    background_image: str | None = None,
    request_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return SceneCompiler(spec, anchors=anchors, request_context=request_context).compile(background_image=background_image)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--anchors")
    parser.add_argument("--background-image")
    parser.add_argument("--request-context")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        spec = load_json(Path(args.spec).resolve())
        anchors = load_json(Path(args.anchors).resolve()) if args.anchors else None
        anchor_map = anchors.get("anchors") if anchors else None
        if isinstance(anchor_map, list):
            anchor_map = {str(item["id"]): item["pixel"] for item in anchor_map}
        request_context = load_json(Path(args.request_context).resolve()) if args.request_context else None
        result = compile_scene(spec, anchors=anchor_map, background_image=args.background_image, request_context=request_context)
        atomic_write_json(Path(args.out).resolve(), result["low_level_spec"])
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else args.out)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
