from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
ROOT = SKILL.parents[2]
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

from calibrate_image import calibrate, invert_matrix, orientation_matrix  # noqa: E402
from diagram_common import load_json, sha256_file  # noqa: E402
from diagram_pipeline import approved_reviewer, normalize_request, prepare, render_task, resolve_output_dir, resume, verify_task  # noqa: E402
from render_diagram import render as render_low_level  # noqa: E402
from render_plot import render_plot, safe_sympy_expression  # noqa: E402
from scene_compiler import compile_scene  # noqa: E402


class PhysicsDiagramPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        workspace_tmp = ROOT / "tmp"
        workspace_tmp.mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix="diagram-test-", dir=workspace_tmp))

    def tearDown(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)

    def write_json(self, path: Path, payload: dict) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def make_source(self, name: str = "中文原图.png") -> Path:
        from PIL import Image, ImageDraw

        path = self.directory / name
        image = Image.new("RGB", (640, 400), "white")
        draw = ImageDraw.Draw(image)
        draw.line((80, 300, 560, 300), fill="black", width=5)
        draw.rectangle((260, 210, 380, 300), outline="black", width=5)
        draw.ellipse((300, 245, 320, 265), fill="black")
        image.save(path)
        return path

    def test_orientation_matrices_round_trip(self) -> None:
        for orientation in range(1, 9):
            matrix = orientation_matrix(orientation, 640, 400)
            inverse = invert_matrix(matrix)
            for point in ([0.0, 0.0, 1.0], [123.5, 210.25, 1.0], [639.0, 399.0, 1.0]):
                transformed = [sum(matrix[row][col] * point[col] for col in range(3)) for row in range(3)]
                restored = [sum(inverse[row][col] * transformed[col] for col in range(3)) for row in range(3)]
                self.assertLessEqual(abs(restored[0] - point[0]), 0.5)
                self.assertLessEqual(abs(restored[1] - point[1]), 0.5)

    def test_calibration_chinese_path_and_source_immutability(self) -> None:
        source = self.make_source()
        before = sha256_file(source)
        result = calibrate(source, self.directory / "calibration")
        self.assertTrue(result["ok"])
        self.assertEqual(before, sha256_file(source))
        proposal = load_json(Path(result["proposal"]))
        self.assertEqual(proposal["sanitized"]["size"], [640, 400])
        self.assertTrue(Path(proposal["preview"]["path"]).is_file())
        self.assertTrue(proposal["geometry_proposals"]["candidate_anchors"])
        self.assertEqual(proposal["semantic_assignment"], "unassigned")

    def test_calibration_applies_exif_orientation_and_records_transform(self) -> None:
        from PIL import Image

        source = self.directory / "旋转照片.jpg"
        image = Image.new("RGB", (80, 40), "white")
        exif = image.getexif()
        exif[274] = 6
        image.save(source, exif=exif)
        result = calibrate(source, self.directory / "orientation")
        proposal = load_json(Path(result["proposal"]))
        self.assertEqual(proposal["source"]["exif_orientation"], 6)
        self.assertEqual(proposal["source"]["original_size"], [80, 40])
        self.assertEqual(proposal["sanitized"]["size"], [40, 80])
        self.assertEqual(proposal["coordinate_system"]["source_to_sanitized"], orientation_matrix(6, 80, 40))

    def test_scene_graph_compiles_mechanics_and_annotation_anchors(self) -> None:
        spec = {
            "schema_version": 2,
            "revision": 0,
            "renderer": "scene",
            "coordinate_space": "normalized_scene",
            "canvas": {"width": 900, "height": 600, "background": "#ffffff"},
            "required_labels": ["重力", "支持力"],
            "objects": [{"id": "block", "type": "block", "size": [0.16, 0.12], "on_surface": {"surface": "ground", "fraction": 0.5}, "label": "m"}],
            "surfaces": [{"id": "ground", "type": "ground", "origin": [0.15, 0.68], "length": 0.7}],
            "connectors": [{"id": "spring", "type": "spring", "from": [0.12, 0.62], "to": "block.left"}],
            "vectors": [
                {"id": "gravity", "kind": "force", "attach_to": "block.center", "direction": [0, 1], "length": 0.16, "label": "重力 mg"},
                {"id": "normal", "kind": "force", "attach_to": "block.center", "direction": [0, -1], "length": 0.16, "label": "支持力 N"},
            ],
            "trajectories": [{"id": "path", "type": "parabola", "start": [0.2, 0.45], "end": [0.8, 0.45], "height": 0.18}],
            "annotations": [{"type": "projection", "start": [0.5, 0.2], "end": [0.5, 0.62]}],
        }
        compiled = compile_scene(spec)
        self.assertTrue(compiled["ok"])
        self.assertGreaterEqual(compiled["summary"]["primitive_count"], 8)
        low_path = self.write_json(self.directory / "compiled.json", compiled["low_level_spec"])
        rendered = render_low_level(compiled["low_level_spec"], low_path, self.directory / "scene")
        self.assertTrue(rendered["ok"], rendered)
        self.assertGreaterEqual(rendered["validation"]["arrow_count"], 2)

        annotation = {
            "schema_version": 2,
            "renderer": "scene",
            "coordinate_space": "source_pixels",
            "canvas": {"width": 640, "height": 400, "background": "#ffffff"},
            "objects": [],
            "vectors": [{"id": "v", "kind": "velocity", "start": {"anchor": "object.center"}, "direction": [1, -1], "length": 100, "label": "速度 v"}],
        }
        compiled_annotation = compile_scene(annotation, anchors={"object.center": [320, 260]}, background_image=str(self.make_source("annotation.png")))
        self.assertEqual(compiled_annotation["low_level_spec"]["kind"], "annotation")

    def test_plot_renderer_and_expression_injection_gate(self) -> None:
        spec = {
            "schema_version": 3,
            "renderer": "plot",
            "coordinate_space": "plot_data",
            "purpose": "solution",
            "style_profile": "solution-color",
            "canvas": {"width": 800, "height": 500, "dpi": 100},
            "plot": {
                "x_label": "t/s",
                "y_label": "v/(m·s⁻¹)",
                "x_limits": [0, 5],
                "y_limits": [-1, 12],
                "grid": True,
                "series": [
                    {"type": "line", "x": [0, 1, 2], "y": [0, 4, 8], "label": "实验数据", "marker": "o"},
                    {"type": "function", "variable": "t", "expression": "2*t + 1", "domain": [0, 5], "samples": 101, "label": "拟合"},
                ],
            },
        }
        report = render_plot(spec, self.directory / "plot")
        self.assertTrue(report["ok"])
        self.assertEqual(report["canvas"]["width"], 800)
        for suffix in ("png", "svg", "pdf"):
            self.assertTrue(Path(report["artifacts"][suffix]["path"]).is_file())
        legacy_spec = {key: value for key, value in spec.items() if key not in {"purpose", "style_profile"}}
        legacy_spec["schema_version"] = 2
        self.assertTrue(render_plot(legacy_spec, self.directory / "plot-legacy")["ok"])
        with self.assertRaises(ValueError):
            safe_sympy_expression("__import__('os').system('whoami')", "x")

    def test_failure_gates_reject_unknown_geometry_missing_anchor_and_path_escape(self) -> None:
        bad_object = {
            "schema_version": 2,
            "renderer": "scene",
            "coordinate_space": "normalized_scene",
            "canvas": {"width": 800, "height": 500},
            "objects": [{"id": "bad", "type": "circuit", "position": [0.5, 0.5], "size": [0.1, 0.1]}],
        }
        with self.assertRaises(ValueError):
            compile_scene(bad_object)
        missing_anchor = {
            "schema_version": 2,
            "renderer": "scene",
            "coordinate_space": "source_pixels",
            "canvas": {"width": 640, "height": 400},
            "vectors": [{"id": "v", "kind": "velocity", "start": {"anchor": "missing"}, "direction": [1, 0], "length": 80}],
        }
        with self.assertRaises(ValueError):
            compile_scene(missing_anchor, anchors={})
        with self.assertRaises(ValueError):
            resolve_output_dir({"output_dir": str(self.directory.parent.parent)}, self.directory / "safe-output")
        with self.assertRaises(RuntimeError):
            approved_reviewer({"status": "approved", "reviewer_type": "weak", "uncertainties": []}, "review")

    def test_legacy_request_remains_readable_but_is_marked_draft_only(self) -> None:
        normalized = normalize_request(
            {
                "schema_version": 1,
                "mode": "original",
                "renderer": "scene",
                "question_text": "legacy task",
            }
        )
        self.assertEqual(normalized["schema_version"], 1)
        self.assertTrue(normalized["items"][0]["legacy_contract"])
        self.assertEqual(normalized["items"][0]["purpose"], "legacy")

    def approve_semantic_template(self, task_dir: Path) -> Path:
        review = load_json(task_dir / "semantic_review.template.json")
        review["status"] = "approved"
        review["reviewer_type"] = "strong"
        review["uncertainties"] = []
        for item in review["items"]:
            item["status"] = "approved"
            item["uncertainties"] = []
            for assertion_item in item.get("semantic_assertions", []):
                assertion_item["status"] = "approved"
        return self.write_json(task_dir / "semantic_review.json", review)

    def test_annotation_pipeline_end_to_end_and_idempotent_publish(self) -> None:
        source = self.make_source("批注原图.png")
        request = self.write_json(
            self.directory / "annotation_request.json",
            {
                "schema_version": 2,
                "mode": "annotate",
                "renderer": "scene",
                "model_profile": "strong",
                "purpose": "annotation",
                "style_profile": "solution-color",
                "diagram_intent": {"facts": []},
                "source_image": str(source),
                "source_sha256": sha256_file(source),
                "output_dir": "annotation-output",
            },
        )
        task_root = self.directory / "tasks"
        output_root = self.directory / "output"
        prepared = prepare(request, "annotate-demo", task_root_override=task_root, output_root_override=output_root, test_mode=True)
        task_dir = Path(prepared["task_dir"])
        proposal = load_json(task_dir / "items" / "main" / "prepare" / "coordinate_map.json")
        self.write_json(
            task_dir / "items" / "main" / "calibration_review.json",
            {
                "schema_version": 1,
                "status": "approved",
                "reviewer_type": "strong",
                "proposal_sha256": proposal["proposal_sha256"],
                "source_sha256": sha256_file(source),
                "anchors": [{"id": "object.center", "pixel": [320, 255]}],
                "uncertainties": [],
            },
        )
        spec_path = self.write_json(
            self.directory / "annotation_spec.json",
            {
                "schema_version": 3,
                "renderer": "scene",
                "coordinate_space": "source_pixels",
                "purpose": "annotation",
                "style_profile": "solution-color",
                "canvas": {"width": 640, "height": 400, "background": "#ffffff"},
                "vectors": [
                    {"id": "gravity", "kind": "force", "semantic_role": "analysis", "start": {"anchor": "object.center"}, "direction": [0, 1], "length": 100, "label": "重力 mg"},
                    {"id": "normal", "kind": "force", "semantic_role": "analysis", "start": {"anchor": "object.center"}, "direction": [0, -1], "length": 100, "label": "支持力 N"},
                ],
            },
        )
        render_task("annotate-demo", spec_path, task_root_override=task_root)
        review = self.approve_semantic_template(task_dir)
        state = load_json(task_dir / "state.json")
        geometry_path = Path(state["items"]["main"]["geometry_report"])
        original_geometry = geometry_path.read_bytes()
        tampered_geometry = load_json(geometry_path)
        tampered_geometry["ok"] = False
        self.write_json(geometry_path, tampered_geometry)
        with self.assertRaisesRegex(RuntimeError, "geometry report changed"):
            verify_task("annotate-demo", review, task_root_override=task_root, output_root_override=output_root)
        geometry_path.write_bytes(original_geometry)
        first = verify_task("annotate-demo", review, task_root_override=task_root, output_root_override=output_root)
        self.assertTrue(Path(first["published"]).is_dir())
        second = verify_task("annotate-demo", review, task_root_override=task_root, output_root_override=output_root)
        self.assertTrue(second["idempotent"])
        self.assertEqual(sha256_file(source), load_json(task_dir / "state.json")["items"]["main"]["source_sha256"])

    def test_original_pipeline_uses_two_reviewed_references(self) -> None:
        reference_a = self.make_source("reference-a.png")
        reference_b = self.make_source("reference-b.png")
        from PIL import Image, ImageDraw

        with Image.open(reference_b) as opened:
            changed = opened.convert("RGB")
        ImageDraw.Draw(changed).ellipse((20, 20, 35, 35), fill="red")
        changed.save(reference_b)
        hash_a = sha256_file(reference_a)
        hash_b = sha256_file(reference_b)
        reference_result = self.write_json(
            self.directory / "reference_result.json",
            {
                "ok": True,
                "index": str(ROOT / "BGE-M3" / "runtime" / "index" / "visual-demo"),
                "index_manifest_sha256": "a" * 64,
                "index_artifacts_verified": True,
                "dimension": 1024,
                "source_complete": False,
                "invalid_candidate_count": 1,
                "results": [
                    {"rank": 1, "score": 0.8, "relative_path": reference_a.relative_to(ROOT).as_posix(), "sha256": hash_a, "width": 640, "height": 400},
                    {"rank": 2, "score": 0.7, "relative_path": reference_b.relative_to(ROOT).as_posix(), "sha256": hash_b, "width": 640, "height": 400},
                ],
            },
        )
        request = self.write_json(
            self.directory / "original_request.json",
            {
                "schema_version": 2,
                "mode": "original",
                "renderer": "scene",
                "model_profile": "strong",
                "purpose": "solution",
                "style_profile": "solution-color",
                "diagram_intent": {"facts": []},
                "question_text": "粗糙水平面上的物块受到水平拉力，画出受力示意图。",
                "query_text": "高中物理水平面物块受力图，重力、支持力、拉力、摩擦力",
                "reference_results_path": str(reference_result),
                "output_dir": "original-output",
            },
        )
        task_root = self.directory / "tasks"
        output_root = self.directory / "output"
        prepared = prepare(request, "original-demo", task_root_override=task_root, output_root_override=output_root, test_mode=True)
        task_dir = Path(prepared["task_dir"])
        report = load_json(task_dir / "items" / "main" / "reference_report.json")
        self.assertIsNotNone(report["coverage_warning"])
        incomplete_review_path = task_dir / "items" / "main" / "reference_review.json"
        self.write_json(
            incomplete_review_path,
            {
                "schema_version": 2,
                "status": "approved",
                "reviewer_type": "strong",
                "report_sha256": report["report_sha256"],
                "reliable_candidate_count": 2,
                "accepted_sha256": [hash_a, hash_b],
                "rejected": [],
                "uncertainties": [],
            },
        )
        placeholder_spec = self.write_json(
            self.directory / "placeholder_spec.json",
            {
                "schema_version": 3,
                "renderer": "scene",
                "coordinate_space": "scene_units",
                "purpose": "solution",
                "style_profile": "solution-color",
                "canvas": {"width": 600, "height": 400},
                "scene": {"width": 60, "height": 40},
                "objects": [{"id": "ball", "type": "sphere", "position": [30, 20], "size": [5, 5]}],
            },
        )
        with self.assertRaisesRegex(RuntimeError, "extracted_rules"):
            render_task("original-demo", placeholder_spec, task_root_override=task_root)
        self.write_json(
            incomplete_review_path,
            {
                "schema_version": 1,
                "status": "approved",
                "reviewer_type": "strong",
                "report_sha256": report["report_sha256"],
                "reliable_candidate_count": 2,
                "accepted_sha256": [hash_a, hash_b],
                "rejected": [],
                "extracted_rules": {
                    "topology": ["物块与水平面接触"],
                    "symbols": ["力使用带标签箭头"],
                    "layout": ["物块居中、受力箭头从质心出发"],
                },
                "uncertainties": [],
            },
        )
        spec_path = self.write_json(
            self.directory / "original_spec.json",
            {
                "schema_version": 3,
                "renderer": "scene",
                "coordinate_space": "scene_units",
                "purpose": "solution",
                "style_profile": "solution-color",
                "canvas": {"width": 900, "height": 600, "background": "#ffffff"},
                "scene": {"width": 100, "height": 65, "padding_px": 24},
                "objects": [{"id": "block", "type": "block", "size": [16, 12], "on_surface": {"surface": "ground", "fraction": 0.5}}],
                "surfaces": [{"id": "ground", "type": "ground", "origin": [15, 45], "length": 70}],
                "vectors": [
                    {"id": "gravity", "kind": "force", "semantic_role": "analysis", "attach_to": "block.center", "direction": [0, 1], "length": 14, "label": "mg"},
                    {"id": "normal", "kind": "force", "semantic_role": "analysis", "attach_to": "block.center", "direction": [0, -1], "length": 14, "label": "N"},
                    {"id": "pull", "kind": "force", "semantic_role": "analysis", "attach_to": "block.center", "direction": [1, 0], "length": 14, "label": "F"},
                    {"id": "friction", "kind": "force", "semantic_role": "analysis", "attach_to": "block.center", "direction": [-1, 0], "length": 10, "label": "f"},
                ],
            },
        )
        original_reference_bytes = reference_a.read_bytes()
        reference_a.write_bytes(original_reference_bytes + b"changed")
        with self.assertRaises(RuntimeError):
            render_task("original-demo", spec_path, task_root_override=task_root)
        reference_a.write_bytes(original_reference_bytes)
        render_task("original-demo", spec_path, task_root_override=task_root)
        review = self.approve_semantic_template(task_dir)
        verified = verify_task("original-demo", review, task_root_override=task_root, output_root_override=output_root)
        self.assertTrue(Path(verified["published"]).is_dir())

    def test_v3_incline_fixture_preserves_angle_contact_and_occlusion_across_aspect_ratios(self) -> None:
        context = {
            "purpose": "question",
            "style_profile": "exam-monochrome",
            "diagram_intent": {"facts": [{"id": "angle", "quote": "斜面倾角为30°"}]},
        }
        measured_angles: list[float] = []
        for canvas in ([900, 600], [1200, 500]):
            spec = {
                "schema_version": 3,
                "renderer": "scene",
                "coordinate_space": "scene_units",
                "purpose": "question",
                "style_profile": "exam-monochrome",
                "canvas": {"width": canvas[0], "height": canvas[1], "background": "#ffffff"},
                "scene": {"width": 100, "height": 70, "padding_px": 24},
                "surfaces": [{"id": "incline", "type": "incline", "origin": [12, 56], "length": 72, "angle_degrees": -30}],
                "objects": [
                    {"id": "support", "type": "fixed_support", "position": "incline.start", "size": [5, 14], "angle_degrees": -30},
                    {"id": "block", "type": "block", "size": [14, 9], "on_surface": {"surface": "incline", "fraction": 0.58}, "label": "A"},
                ],
                "dimensions": [
                    {
                        "id": "incline-angle",
                        "type": "angular",
                        "semantic_role": "given",
                        "fact_id": "angle",
                        "vertex": "incline.start",
                        "ray_a": [32, 56],
                        "ray_b": "incline.end",
                        "value_degrees": 30,
                        "label": "30°",
                        "radius": 6,
                        "draw_rays": ["a"],
                    }
                ],
                "relations": [{"id": "block-parallel", "type": "parallel", "a": "block.direction", "b": "incline.direction"}],
            }
            compiled = compile_scene(spec, request_context=context)
            self.assertTrue(compiled["geometry_report"]["ok"])
            self.assertFalse(compiled["geometry_report"]["failed_assertions"])
            incline = next(item for item in compiled["low_level_spec"]["primitives"] if item.get("semantic_id") == "incline")
            start, end = incline["points"][0], incline["points"][-1]
            measured_angles.append(abs(math.degrees(math.atan2(end[1] - start[1], end[0] - start[0]))))
            block = next(item for item in compiled["low_level_spec"]["primitives"] if item.get("semantic_id") == "block")
            self.assertEqual(block["type"], "polygon")
            self.assertEqual(block["fill"], "#ffffff")
            self.assertLess(incline["layer"], block["layer"])
            self.assertTrue(any(item.get("semantic_id") == "incline-angle.ray-a" for item in compiled["low_level_spec"]["primitives"]))
            rendered = render_low_level(
                compiled["low_level_spec"],
                self.write_json(self.directory / f"incline-{canvas[0]}.json", compiled["low_level_spec"]),
                self.directory / f"incline-{canvas[0]}",
            )
            self.assertTrue(rendered["ok"], rendered)
        for measured in measured_angles:
            self.assertLessEqual(abs(measured - 30), 0.5)
        self.assertLessEqual(abs(measured_angles[0] - measured_angles[1]), 1e-9)

    def test_v3_projectile_fixture_has_horizontal_tangent_dimensions_and_unambiguous_impact(self) -> None:
        context = {
            "purpose": "question",
            "style_profile": "exam-monochrome",
            "diagram_intent": {
                "facts": [
                    {"id": "v0", "quote": "小球以初速度v0水平抛出"},
                    {"id": "height", "quote": "抛出点距木板高度为h"},
                    {"id": "range", "quote": "水平距离为x"},
                ]
            },
        }
        spec = {
            "schema_version": 3,
            "renderer": "scene",
            "coordinate_space": "scene_units",
            "purpose": "question",
            "style_profile": "exam-monochrome",
            "canvas": {"width": 1000, "height": 650, "background": "#ffffff"},
            "scene": {"width": 100, "height": 65, "padding_px": 24},
            "objects": [
                {"id": "ball", "type": "sphere", "position": [20, 18], "size": [4, 4]},
                {"id": "impact", "type": "impact_marker", "position": [82, 52], "size": [2.5, 2.5]},
            ],
            "vectors": [{"id": "v0", "kind": "velocity", "semantic_role": "given", "fact_id": "v0", "start": "ball.center", "direction": [1, 0], "length": 14, "label": "v₀"}],
            "trajectories": [{"id": "path", "type": "projectile", "start": "ball.center", "end": "impact.center", "initial_direction": [1, 0]}],
            "dimensions": [
                {"id": "h", "type": "linear", "semantic_role": "given", "fact_id": "height", "from": [20, 18], "to": [20, 52], "offset": [-8, 0], "extension_gaps": [3, 0], "label": "h"},
                {"id": "x", "type": "linear", "semantic_role": "given", "fact_id": "range", "from": [20, 52], "to": "impact.center", "offset": [0, 7], "extension_gaps": [0, 2.5], "label": "x"},
            ],
            "relations": [
                {"id": "launch-common-point", "type": "connected", "a": "path.start", "b": "ball.center"},
                {"id": "impact-common-point", "type": "connected", "a": "path.end", "b": "impact.center"},
                {"id": "launch-tangent", "type": "tangent", "a": "path.start_direction", "b": "v0.direction"},
            ],
        }
        compiled = compile_scene(spec, request_context=context)
        self.assertTrue(compiled["geometry_report"]["ok"])
        path = next(item for item in compiled["low_level_spec"]["primitives"] if item.get("semantic_id") == "path")
        self.assertNotIn("dash", path)
        self.assertGreater(path["points"][1][1], path["points"][0][1])
        self.assertTrue(all(path["points"][index + 1][1] >= path["points"][index][1] for index in range(len(path["points"]) - 1)))
        self.assertEqual(sum(item["type"] == "double_arrow" for item in compiled["low_level_spec"]["primitives"]), 2)
        rendered = render_low_level(compiled["low_level_spec"], self.write_json(self.directory / "projectile.json", compiled["low_level_spec"]), self.directory / "projectile")
        self.assertTrue(rendered["ok"], rendered)
        svg_text = Path(rendered["svg"]).read_text(encoding="utf-8")
        self.assertIn('baseline-shift="sub"', svg_text)
        self.assertIn(">0</tspan>", svg_text)

    def test_v3_ring_fixture_connects_spring_marks_q_and_keeps_rod_consistent(self) -> None:
        context = {
            "purpose": "question",
            "style_profile": "exam-monochrome",
            "diagram_intent": {"facts": [{"id": "pq", "quote": "Q点比P点低0.24 m"}]},
        }
        spec = {
            "schema_version": 3,
            "renderer": "scene",
            "coordinate_space": "scene_units",
            "purpose": "question",
            "style_profile": "exam-monochrome",
            "canvas": {"width": 900, "height": 650, "background": "#ffffff"},
            "scene": {"width": 90, "height": 65, "padding_px": 24},
            "surfaces": [{"id": "rod", "type": "rod", "origin": [58, 8], "length": 50, "angle_degrees": 90, "stroke_width": 4}],
            "objects": [
                {"id": "support", "type": "fixed_support", "position": [15, 32], "size": [5, 15], "label": "O"},
                {"id": "ring", "type": "ring", "position": [58, 32], "size": [8, 8], "label": "P"},
            ],
            "connectors": [{"id": "spring", "type": "spring", "from": "support.connection", "to": "ring.left", "amplitude": 1.4}],
            "landmarks": [
                {"id": "rod-at-p", "type": "point", "position": [58, 32], "marker": "none"},
                {"id": "q", "type": "point", "position": [58, 52], "marker": "dot", "label": "Q", "label_offset": [-5, -5]},
            ],
            "dimensions": [{"id": "pq", "type": "linear", "semantic_role": "given", "fact_id": "pq", "from": "ring.center", "to": "q.center", "offset": [10, 0], "extension_gaps": [5, 2], "label": "0.24 m"}],
            "relations": [
                {"id": "spring-attached", "type": "connected", "a": "spring.to", "b": "ring.left"},
                {"id": "rod-through-ring", "type": "passes_through", "a": "ring.center", "b": "rod-at-p.center"},
            ],
        }
        compiled = compile_scene(spec, request_context=context)
        self.assertTrue(compiled["geometry_report"]["ok"])
        ring = next(item for item in compiled["low_level_spec"]["primitives"] if item.get("semantic_id") == "ring")
        spring = next(item for item in compiled["low_level_spec"]["primitives"] if item.get("semantic_id") == "spring")
        self.assertLessEqual(math.dist(spring["points"][-1], [ring["cx"] - ring["r"], ring["cy"]]), 0.5)
        rod_primitives = [item for item in compiled["low_level_spec"]["primitives"] if item.get("semantic_id") == "rod"]
        self.assertEqual(len(rod_primitives), 1)
        self.assertEqual(rod_primitives[0]["stroke_width"], 4)
        self.assertTrue(any(item.get("text") == "Q" for item in compiled["low_level_spec"]["primitives"]))
        self.assertTrue(any(item["type"] == "double_arrow" and item.get("semantic_id") == "pq" for item in compiled["low_level_spec"]["primitives"]))
        rendered = render_low_level(compiled["low_level_spec"], self.write_json(self.directory / "ring.json", compiled["low_level_spec"]), self.directory / "ring")
        self.assertTrue(rendered["ok"], rendered)

    def test_question_purpose_rejects_analysis_and_requires_given_fact_binding(self) -> None:
        base = {
            "schema_version": 3,
            "renderer": "scene",
            "coordinate_space": "scene_units",
            "purpose": "question",
            "style_profile": "exam-monochrome",
            "canvas": {"width": 800, "height": 500},
            "scene": {"width": 80, "height": 50, "padding_px": 20},
            "objects": [{"id": "ball", "type": "sphere", "position": [30, 25], "size": [5, 5]}],
        }
        context = {"purpose": "question", "style_profile": "exam-monochrome", "diagram_intent": {"facts": [{"id": "given-v", "quote": "速度v水平向右"}]}}
        analysis = {**base, "vectors": [{"id": "mg", "kind": "force", "semantic_role": "analysis", "start": "ball.center", "direction": [0, 1], "length": 10, "label": "mg"}]}
        with self.assertRaisesRegex(ValueError, "forbids analysis"):
            compile_scene(analysis, request_context=context)
        missing_binding = {**base, "vectors": [{"id": "v", "kind": "velocity", "semantic_role": "given", "start": "ball.center", "direction": [1, 0], "length": 10, "label": "v"}]}
        with self.assertRaisesRegex(ValueError, "fact_id"):
            compile_scene(missing_binding, request_context=context)
        allowed = {**base, "vectors": [{"id": "v", "kind": "velocity", "semantic_role": "given", "fact_id": "given-v", "start": "ball.center", "direction": [1, 0], "length": 10, "label": "v"}]}
        compiled = compile_scene(allowed, request_context=context)
        vector = next(item for item in compiled["low_level_spec"]["primitives"] if item.get("semantic_id") == "v")
        self.assertEqual(vector["color"], "#111111")

    def test_v3_unknown_relation_and_text_collision_fail_closed(self) -> None:
        context = {"purpose": "solution", "style_profile": "solution-color", "diagram_intent": {"facts": []}}
        base = {
            "schema_version": 3,
            "renderer": "scene",
            "coordinate_space": "scene_units",
            "purpose": "solution",
            "style_profile": "solution-color",
            "canvas": {"width": 800, "height": 500},
            "scene": {"width": 80, "height": 50, "padding_px": 20},
            "surfaces": [{"id": "ground", "type": "ground", "origin": [10, 30], "length": 60}],
        }
        with self.assertRaisesRegex(ValueError, "unsupported relation"):
            compile_scene({**base, "relations": [{"id": "bad", "type": "near", "a": "ground.start", "b": "ground.end"}]}, request_context=context)
        width_conflict = {
            **base,
            "surfaces": [{"id": "rod", "type": "rod", "origin": [40, 10], "length": 30, "angle_degrees": 90, "stroke_width": 6}],
            "annotations": [{"id": "overlay", "type": "line", "semantic_role": "analysis", "start": "rod.start", "end": "rod.end", "stroke_width": 2}],
        }
        with self.assertRaisesRegex(ValueError, "lines.consistent-overlap-width"):
            compile_scene(width_conflict, request_context=context)
        collision = {**base, "annotations": [{"id": "label", "type": "text", "position": [35, 28], "text": "压在线上的文字"}]}
        compiled = compile_scene(collision, request_context=context)
        report = render_low_level(compiled["low_level_spec"], self.write_json(self.directory / "collision.json", compiled["low_level_spec"]), self.directory / "collision")
        self.assertFalse(report["ok"])
        self.assertTrue(any("text collision" in issue for issue in report["validation"]["issues"]))

    def test_physical_regression_fixture_files_compile_and_render(self) -> None:
        fixture_root = SKILL / "tests" / "fixtures"
        for fixture_path in sorted(fixture_root.glob("*.json")):
            fixture = load_json(fixture_path)
            compiled = compile_scene(fixture["spec"], request_context=fixture["request_context"])
            self.assertTrue(compiled["geometry_report"]["ok"], fixture_path.name)
            rendered = render_low_level(
                compiled["low_level_spec"],
                self.write_json(self.directory / f"{fixture_path.stem}.json", compiled["low_level_spec"]),
                self.directory / fixture_path.stem,
            )
            self.assertTrue(rendered["ok"], {"fixture": fixture_path.name, "report": rendered})

    def test_resume_blocks_when_annotation_source_changes(self) -> None:
        source = self.make_source("mutable.png")
        request = self.write_json(
            self.directory / "resume_request.json",
            {
                "schema_version": 1,
                "mode": "annotate",
                "renderer": "scene",
                "source_image": str(source),
                "source_sha256": sha256_file(source),
            },
        )
        task_root = self.directory / "tasks"
        prepare(request, "resume-demo", task_root_override=task_root, output_root_override=self.directory / "output", test_mode=True)
        source.write_bytes(source.read_bytes() + b"changed")
        result = resume("resume-demo", task_root_override=task_root)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
