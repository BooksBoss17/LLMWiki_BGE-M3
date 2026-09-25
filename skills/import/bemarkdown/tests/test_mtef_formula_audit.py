from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from apply_formula_reviews import apply_audit_reviews
from batch_convert_all import reviewed_wmf_replacements
from formula_candidate_utils import resolve_formula_consensus
from mtef_formula_audit import (
    AuditPaths,
    AuditStore,
    _olefile_module,
    _process_alive,
    _run_jruby_batch,
    _transpect_library_args,
    acquire_task_lock,
    export_reports,
    extract_equation_native,
    parse_pending_mtef,
    parser_runtime_fingerprint,
    render_pending_previews,
    release_task_lock,
    scan_docx,
)
from mtef_mathml import map_mtef_xml


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def mtef_xml(version: int, body: str) -> str:
    return f"""<?xml version="1.0"?><root><mtef><mtef_version>{version}</mtef_version>{body}<end/></mtef></root>"""


def audit_paths(root: Path) -> AuditPaths:
    return AuditPaths(
        root=root,
        db=root / "audit.sqlite3",
        ole=root / "ole",
        wmf=root / "wmf",
        render=root / "render",
        batches=root / "batches",
        reports=root / "reports",
    )


def write_formula_docx(
    path: Path,
    prog_ids: list[str],
    ole_payload: bytes,
    preview_payloads: list[bytes],
    *,
    independent_wmf: bytes | None = None,
) -> None:
    if len(prog_ids) != len(preview_payloads):
        raise ValueError("every synthetic OLE requires one preview")
    objects: list[str] = []
    relationships: list[str] = []
    for index, (prog_id, _preview) in enumerate(zip(prog_ids, preview_payloads), 1):
        objects.append(
            f'<w:r><w:object><v:shape><v:imagedata r:id="rIdPreview{index}"/></v:shape>'
            f'<o:OLEObject ProgID="{prog_id}" r:id="rIdOle{index}"/></w:object></w:r>'
        )
        relationships.extend(
            [
                f'<Relationship Id="rIdOle{index}" Type="oleObject" Target="embeddings/oleObject{index}.bin"/>',
                f'<Relationship Id="rIdPreview{index}" Type="image" Target="media/preview{index}.wmf"/>',
            ]
        )
    if independent_wmf is not None:
        relationships.append('<Relationship Id="rIdIndependent" Type="image" Target="media/independent.wmf"/>')
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">'
        f'<w:body><w:p>{"".join(objects)}</w:p></w:body></w:document>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{"".join(relationships)}</Relationships>'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", rels)
        for index, preview in enumerate(preview_payloads, 1):
            archive.writestr(f"word/embeddings/oleObject{index}.bin", ole_payload)
            archive.writestr(f"word/media/preview{index}.wmf", preview)
        if independent_wmf is not None:
            archive.writestr("word/media/independent.wmf", independent_wmf)


class MtefFormulaAuditTests(unittest.TestCase):
    def test_process_liveness_and_stale_task_lock_are_windows_safe(self) -> None:
        self.assertTrue(_process_alive(os.getpid()))
        nonexistent_pid = 2**30 - 1
        self.assertFalse(_process_alive(nonexistent_pid))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / ".audit.lock"
            lock.write_text(
                json.dumps({"pid": nonexistent_pid, "token": "stale", "started_at": "2000-01-01T00:00:00Z"}),
                encoding="utf-8",
            )

            acquired, token = acquire_task_lock(root)

            self.assertEqual(acquired, lock)
            self.assertNotEqual(token, "stale")
            release_task_lock(acquired, token)
            self.assertFalse(lock.exists())

    @unittest.skipUnless(os.name == "nt", "Windows PID reuse contract")
    def test_legacy_task_lock_rejects_pid_reuse_but_preserves_live_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / ".audit.lock"
            lock.write_text(
                json.dumps({"pid": os.getpid(), "token": "legacy", "started_at": "2000-01-01T00:00:00Z"}),
                encoding="utf-8",
            )

            acquired, token = acquire_task_lock(root)
            current = json.loads(acquired.read_text(encoding="utf-8"))
            self.assertEqual(current["pid"], os.getpid())
            self.assertTrue(current["process_identity"].startswith("windows-filetime:"))
            with self.assertRaisesRegex(RuntimeError, "already running"):
                acquire_task_lock(root)
            release_task_lock(acquired, token)

    def test_extracts_v3_and_v5_equation_native_streams(self) -> None:
        versions = []
        for name in ("mtef_v3_frac.bin", "mtef_v5_equation.bin"):
            native, payload, version = extract_equation_native((FIXTURES / name).read_bytes())
            self.assertEqual(int.from_bytes(native[:4], "little"), 28)
            self.assertEqual(payload[0], version)
            versions.append(version)
        self.assertEqual(versions, [3, 5])

    def test_rejects_missing_or_wrong_equation_native_header(self) -> None:
        with self.assertRaisesRegex(ValueError, "damaged OLE"):
            extract_equation_native(b"not-an-ole")

        payload = (FIXTURES / "mtef_v5_equation.bin").read_bytes()
        stream_name = "Equation Native".encode("utf-16le")
        renamed_stream = "Equation Xative".encode("utf-16le")
        self.assertEqual(payload.count(stream_name), 1)
        with self.assertRaisesRegex(ValueError, "no Equation Native stream"):
            extract_equation_native(payload.replace(stream_name, renamed_stream, 1))

        with tempfile.TemporaryDirectory() as directory:
            damaged = Path(directory) / "wrong-header.bin"
            shutil.copy2(FIXTURES / "mtef_v5_equation.bin", damaged)
            olefile = _olefile_module()
            container = olefile.OleFileIO(str(damaged), write_mode=True)
            native = bytearray(container.openstream("Equation Native").read())
            native[:4] = (27).to_bytes(4, "little")
            container.write_stream("Equation Native", bytes(native))
            container.close()
            with self.assertRaisesRegex(ValueError, "unexpected Equation Native header size: 27"):
                extract_equation_native(damaged.read_bytes())

    def test_scans_dsmt4_ksee3_equation3_shared_mtef_and_independent_wmf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = audit_paths(root)
            source = root / "legacy-equations.docx"
            write_formula_docx(
                source,
                ["Equation.DSMT4", "Equation.KSEE3", "Equation.3"],
                (FIXTURES / "mtef_v5_equation.bin").read_bytes(),
                [b"preview-dsmt4", b"preview-ksee3", b"preview-equation3"],
                independent_wmf=b"independent-wmf",
            )
            store = AuditStore(paths.db)

            result = scan_docx(source, store, paths)
            cached = scan_docx(source, store, paths)
            rows = list(
                store.connection.execute(
                    "SELECT source_kind,prog_id,paragraph_index,mtef_sha256,wmf_sha256 FROM formula_occurrences ORDER BY object_index"
                )
            )

            self.assertEqual(result["occurrences"], 4)
            self.assertEqual(cached["status"], "cached")
            equation_rows = [row for row in rows if row["source_kind"] == "equation_ole"]
            independent_rows = [row for row in rows if row["source_kind"] == "independent_wmf"]
            self.assertEqual({row["prog_id"] for row in equation_rows}, {"Equation.DSMT4", "Equation.KSEE3", "Equation.3"})
            self.assertEqual({row["paragraph_index"] for row in equation_rows}, {1})
            self.assertEqual(len({row["mtef_sha256"] for row in equation_rows}), 1)
            self.assertEqual(len({row["wmf_sha256"] for row in equation_rows}), 3)
            self.assertEqual(len(independent_rows), 1)
            self.assertIsNone(independent_rows[0]["mtef_sha256"])
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM mtef_results").fetchone()[0], 1)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM previews").fetchone()[0], 4)
            store.close()

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows portable MTEF/WMF smoke")
    def test_unicode_space_path_uses_bundled_parser_and_native_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "中文 空格"
            paths = audit_paths(root / "审计 状态")
            source = root / "公式 文档.docx"
            preview = base64.b64decode(
                (FIXTURES / "mathtype_formula.wmf.b64").read_text(encoding="ascii").strip()
            )
            write_formula_docx(
                source,
                ["Equation.DSMT4"],
                (FIXTURES / "mtef_v5_equation.bin").read_bytes(),
                [preview],
            )
            modern_powershell = shutil.which("pwsh.exe") or shutil.which("pwsh")
            self.assertIsNotNone(modern_powershell)
            isolated_path = str(Path(str(modern_powershell)).parent)
            store = AuditStore(paths.db)
            store.set_meta("runtime_fingerprint", "runtime-portable")
            store.connection.commit()
            with patch.dict(
                os.environ,
                {
                    "PATH": isolated_path,
                    "JAVA_HOME": "",
                    "JRUBY_HOME": "",
                    "LIBREOFFICE_SOFFICE": "",
                },
            ):
                scanned = scan_docx(source, store, paths)
                parsed = parse_pending_mtef(
                    store,
                    paths,
                    "runtime-portable",
                    parser_runtime_fingerprint(),
                    timeout=60,
                )
                rendered = render_pending_previews(store, paths, timeout=30)

            self.assertEqual(scanned["occurrences"], 1)
            self.assertEqual(parsed.get("structure_candidate"), 1)
            self.assertEqual(rendered.get("rendered"), 1)
            formula = store.connection.execute("SELECT * FROM mtef_results").fetchone()
            evidence = store.connection.execute("SELECT * FROM previews").fetchone()
            review = paths.root / "portable-review.jsonl"
            review.write_text(
                json.dumps(
                    {
                        "review_key": formula["mtef_sha256"],
                        "status": "resolved",
                        "mtef_sha256": formula["mtef_sha256"],
                        "runtime_fingerprint": "runtime-portable",
                        "previews": [
                            {
                                "wmf_sha256": evidence["wmf_sha256"],
                                "png_sha256": evidence["png_sha256"],
                            }
                        ],
                        "final_kind": "latex",
                        "final_latex": formula["structure_latex"],
                        "visual_evidence": "Hash-bound portable-path integration fixture.",
                        "confidence": 1.0,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            store.close()

            applied = apply_audit_reviews(paths.db, review, require_all=True)
            reopened = AuditStore(paths.db)
            completed = export_reports(reopened, paths, "runtime-portable")
            reopened.close()

            self.assertTrue(applied["ok"])
            self.assertTrue(completed["ok"])
            self.assertTrue((paths.root / "completion.json").is_file())

    def test_jruby_blocks_corrupt_trailing_mtef_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            damaged = root / "trailing-record.bin"
            shutil.copy2(FIXTURES / "mtef_v5_equation.bin", damaged)
            olefile = _olefile_module()
            container = olefile.OleFileIO(str(damaged), write_mode=True)
            native = bytearray(container.openstream("Equation Native").read())
            native[-1] = 0x7F
            container.write_stream("Equation Native", bytes(native))
            container.close()
            digest = hashlib.sha256(bytes(native[28:])).hexdigest()
            task = root / "task.jsonl"
            task.write_text(
                json.dumps({"id": digest, "mtef_sha256": digest, "ole_path": str(damaged)}) + "\n",
                encoding="utf-8",
            )
            output = root / "result.jsonl"

            process = _run_jruby_batch(task, output, 60)
            record = json.loads(output.read_text(encoding="utf-8").strip())

            self.assertEqual(process.returncode, 0)
            self.assertFalse(record["ok"])
            self.assertRegex(record["error"], "selection|trailing|consume|record")

    def test_bundled_jruby_completely_consumes_v3_and_v5(self) -> None:
        # Resolve the libraries first so a missing portable runtime is a hard
        # test failure, not a silent skip.
        self.assertEqual(len(_transpect_library_args()), 8)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tasks = []
            for name in ("mtef_v3_frac.bin", "mtef_v5_equation.bin"):
                source = FIXTURES / name
                _, payload, _ = extract_equation_native(source.read_bytes())
                digest = hashlib.sha256(payload).hexdigest()
                tasks.append({"id": digest, "mtef_sha256": digest, "ole_path": str(source)})
            task = root / "task.jsonl"
            task.write_text("".join(json.dumps(row) + "\n" for row in tasks), encoding="utf-8")
            output = root / "result.jsonl"
            process = _run_jruby_batch(task, output, 60)
            self.assertEqual(process.returncode, 0, process.stderr)
            records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["version"] for row in records], [3, 5])
            self.assertTrue(all(row["consumed_bytes"] == row["total_bytes"] for row in records))
            self.assertTrue(all(row["segment_count"] == 1 for row in records))

    def test_maps_supported_structure_and_blocks_unknown_record(self) -> None:
        fraction = mtef_xml(
            5,
            """
            <slot><options>0</options><tmpl><selector>tmFRACT</selector>
              <slot><char><mt_code_value>0x0031</mt_code_value></char><end/></slot>
              <slot><char><mt_code_value>0x0032</mt_code_value></char><end/></slot>
            <end/></tmpl><end/></slot>
            """,
        )
        mapped = map_mtef_xml(fraction)
        self.assertEqual(mapped.status, "structure_candidate")
        self.assertEqual(mapped.latex, r"\frac{1}{2}")
        self.assertIn("<mfrac", mapped.mathml or "")

        blocked = map_mtef_xml(mtef_xml(5, "<unknown_record/>"))
        self.assertEqual(blocked.status, "blocked")
        self.assertIn("unknown top-level MTEF record", blocked.unsupported[0])

    def test_maps_declared_non_marking_mathtype_spacing(self) -> None:
        value = map_mtef_xml(
            mtef_xml(
                5,
                "<slot><char><mt_code_value>0x0041</mt_code_value></char>"
                "<char><mt_code_value>0xEF04</mt_code_value></char>"
                "<char><mt_code_value>0x0042</mt_code_value></char><end/></slot>",
            )
        )
        self.assertEqual(value.status, "structure_candidate")
        self.assertEqual(value.latex, "AB")

    def test_maps_declared_unicode_glyph_and_blocks_private_font_glyph(self) -> None:
        greek = map_mtef_xml(
            mtef_xml(5, "<slot><char><mt_code_value>0x03B1</mt_code_value></char><end/></slot>")
        )
        private = map_mtef_xml(
            mtef_xml(5, "<slot><char><mt_code_value>0xE98F</mt_code_value></char><end/></slot>")
        )
        self.assertEqual(greek.status, "structure_candidate")
        self.assertEqual(greek.latex, r"\alpha")
        self.assertEqual(private.status, "blocked")
        self.assertIn("unmapped private-use MathType glyph: 0xE98F", private.unsupported)

    def test_unknown_template_is_blocked_instead_of_flattened(self) -> None:
        value = map_mtef_xml(
            mtef_xml(
                5,
                "<slot><tmpl><selector>tmUNKNOWN</selector>"
                "<slot><char><mt_code_value>0x0078</mt_code_value></char><end/></slot>"
                "<end/></tmpl><end/></slot>",
            )
        )
        self.assertEqual(value.status, "blocked")
        self.assertTrue(any("template" in issue.lower() and "tmUNKNOWN" in issue for issue in value.unsupported))

    def test_script_slots_follow_mtef_subobject_order(self) -> None:
        value = map_mtef_xml(
            mtef_xml(
                5,
                "<slot><char><mt_code_value>0x0073</mt_code_value></char>"
                "<tmpl><selector>tmSUP</selector><sub/>"
                "<slot><end/></slot>"
                "<slot><char><mt_code_value>0x0032</mt_code_value></char><end/></slot>"
                "<end/></tmpl><end/></slot>",
            )
        )
        self.assertEqual(value.status, "structure_candidate")
        self.assertEqual(value.latex, "{s}^{2}")

    def test_nested_script_only_slot_keeps_explicit_empty_base(self) -> None:
        value = map_mtef_xml(
            mtef_xml(
                5,
                "<slot><char><mt_code_value>0x0035</mt_code_value></char>"
                "<tmpl><selector>tmSUP</selector><sub/><slot><end/></slot><slot>"
                "<tmpl><selector>tmSUP</selector><sub/><slot><end/></slot><slot>"
                "<char><mt_code_value>0x00B0</mt_code_value></char><end/></slot><end/></tmpl>"
                "<end/></slot><end/></tmpl><end/></slot>",
            )
        )
        self.assertEqual(value.status, "structure_candidate")
        self.assertEqual(value.latex, r"{5}^{{}^{^{\circ}}}")
        self.assertIn("nuclear_or_multi_script", value.risk_flags)

    def test_maps_declared_interval_variation_without_guessing_fences(self) -> None:
        value = map_mtef_xml(
            mtef_xml(
                5,
                "<slot><tmpl><selector>tmINTERVAL</selector><variation>tvINTV_LBRP</variation>"
                "<slot><char><mt_code_value>0x0078</mt_code_value></char><end/></slot>"
                "<end/></tmpl><end/></slot>",
            )
        )
        self.assertEqual(value.status, "structure_candidate")
        self.assertEqual(value.latex, r"\left[x\right)")

    def test_three_source_consensus_requires_all_engines(self) -> None:
        three = [
            {"engine": "mtef", "text": "x+1", "confidence": 1.0},
            {"engine": "pp_formulanet", "text": "x+1", "confidence": 0.99},
            {"engine": "texteller", "text": "x+1", "confidence": 0.99},
        ]
        result = resolve_formula_consensus(
            three,
            required_engines=("mtef", "pp_formulanet", "texteller"),
        )
        self.assertEqual(result["status"], "machine_final")
        result = resolve_formula_consensus(
            three[1:],
            required_engines=("mtef", "pp_formulanet", "texteller"),
        )
        self.assertEqual(result["status"], "needs_vlm")

    def test_audit_review_is_bound_to_mtef_preview_and_runtime_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "audit.sqlite3"
            store = AuditStore(database)
            mtef_sha = "a" * 64
            wmf_sha = "b" * 64
            png = root / "evidence.png"
            png.write_bytes(b"png-evidence")
            png_sha = hashlib.sha256(png.read_bytes()).hexdigest()
            store.set_meta("runtime_fingerprint", "runtime-v1")
            store.connection.execute(
                "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)",
                ("doc", "source.docx", "c" * 64, 1, 1, "scanned", "[]", "now"),
            )
            store.connection.execute(
                """
                INSERT INTO formula_occurrences(
                    document_key,object_index,source_kind,mtef_sha256,wmf_sha256,mtef_version,status
                ) VALUES(?,?,?,?,?,?,?)
                """,
                ("doc", 1, "equation_ole", mtef_sha, wmf_sha, 5, "needs_vlm"),
            )
            store.connection.execute(
                """
                INSERT INTO mtef_results(
                    mtef_sha256,representative_ole_path,mtef_version,parser_fingerprint,runtime_fingerprint,
                    contract_version,parser_status,final_status,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (mtef_sha, "source.bin", 5, "parser", "runtime-v1", "contract", "parsed", "needs_vlm", "now"),
            )
            store.connection.execute(
                """
                INSERT INTO previews(
                    wmf_sha256,source_path,render_status,png_path,png_sha256,final_status,updated_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (wmf_sha, "source.wmf", "rendered", str(png), png_sha, "needs_vlm", "now"),
            )
            store.connection.commit()
            store.close()
            reviews = root / "reviews.jsonl"
            record = {
                "review_key": mtef_sha,
                "status": "resolved",
                "mtef_sha256": mtef_sha,
                "runtime_fingerprint": "runtime-v1",
                "previews": [{"wmf_sha256": wmf_sha, "png_sha256": "0" * 64}],
                "final_latex": "x+1",
                "visual_evidence": "Full-resolution preview shows x plus one.",
                "confidence": 1.0,
            }
            reviews.write_text(json.dumps(record) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "preview hash set mismatch"):
                apply_audit_reviews(database, reviews)
            record["previews"][0]["png_sha256"] = png_sha
            reviews.write_text(json.dumps(record) + "\n", encoding="utf-8")
            result = apply_audit_reviews(database, reviews, require_all=True)
            self.assertTrue(result["ok"])
            connection = __import__("sqlite3").connect(database)
            self.assertEqual(
                connection.execute("SELECT final_status FROM mtef_results WHERE mtef_sha256=?", (mtef_sha,)).fetchone()[0],
                "reviewed_final",
            )
            connection.close()

    def test_hash_bound_blank_mtef_review_resolves_to_empty_without_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "audit.sqlite3"
            store = AuditStore(database)
            mtef_sha = "d" * 64
            wmf_payload = b"blank-wmf-source"
            wmf_sha = hashlib.sha256(wmf_payload).hexdigest()
            png = root / "blank.png"
            png.write_bytes(b"blank-png-evidence")
            png_sha = hashlib.sha256(png.read_bytes()).hexdigest()
            store.set_meta("runtime_fingerprint", "runtime-empty")
            store.connection.execute(
                "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)",
                ("doc", "source.docx", "c" * 64, 1, 1, "scanned", "[]", "now"),
            )
            store.connection.execute(
                """
                INSERT INTO formula_occurrences(
                    document_key,object_index,source_kind,mtef_sha256,wmf_sha256,mtef_version,status
                ) VALUES(?,?,?,?,?,?,?)
                """,
                ("doc", 1, "equation_ole", mtef_sha, wmf_sha, 5, "blocked"),
            )
            store.connection.execute(
                """
                INSERT INTO mtef_results(
                    mtef_sha256,representative_ole_path,mtef_version,parser_fingerprint,runtime_fingerprint,
                    contract_version,parser_status,final_status,issue_json,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    mtef_sha, "source.bin", 5, "parser", "runtime-empty", "contract", "parsed", "blocked",
                    json.dumps(["MTEF mapping produced empty LaTeX"]), "now",
                ),
            )
            store.connection.execute(
                """
                INSERT INTO previews(
                    wmf_sha256,source_path,render_status,png_path,png_sha256,render_json,final_status,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    wmf_sha, "source.wmf", "blocked", str(png), png_sha,
                    json.dumps({"geometry_issues": ["blank_render"]}), "needs_vlm", "now",
                ),
            )
            store.connection.commit()
            store.close()
            reviews = root / "reviews.jsonl"
            reviews.write_text(
                json.dumps(
                    {
                        "review_key": mtef_sha,
                        "status": "resolved",
                        "mtef_sha256": mtef_sha,
                        "runtime_fingerprint": "runtime-empty",
                        "previews": [{"wmf_sha256": wmf_sha, "png_sha256": png_sha}],
                        "final_kind": "empty",
                        "visual_evidence": "Full-resolution preview is blank and MTEF has no visible records.",
                        "confidence": 1.0,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = apply_audit_reviews(database, reviews)

            self.assertTrue(result["ok"])
            connection = __import__("sqlite3").connect(database)
            self.assertEqual(
                connection.execute(
                    "SELECT final_kind,final_latex,final_status FROM mtef_results WHERE mtef_sha256=?", (mtef_sha,)
                ).fetchone(),
                ("empty", None, "reviewed_final"),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT render_status,final_kind,final_status FROM previews WHERE wmf_sha256=?", (wmf_sha,)
                ).fetchone(),
                ("reviewed_empty", "empty", "reviewed_final"),
            )
            connection.close()
            media = root / "media"
            media.mkdir()
            (media / "blank.wmf").write_bytes(wmf_payload)
            replacements = reviewed_wmf_replacements(database, media, ["media/blank.wmf"])
            self.assertEqual(replacements["media/blank.wmf"]["kind"], "empty")

    def test_runtime_fingerprint_remap_invalidates_linked_empty_preview_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = AuditPaths(
                root=root,
                db=root / "audit.sqlite3",
                ole=root / "ole",
                wmf=root / "wmf",
                render=root / "render",
                batches=root / "batches",
                reports=root / "reports",
            )
            store = AuditStore(paths.db)
            mtef_sha = "e" * 64
            wmf_sha = "f" * 64
            png = root / "blank.png"
            png.write_bytes(b"blank-png-evidence")
            png_sha = hashlib.sha256(png.read_bytes()).hexdigest()
            render_json = json.dumps({"geometry_issues": ["blank_render"]})
            store.connection.execute(
                "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)",
                ("doc", "source.docx", "c" * 64, 1, 1, "scanned", "[]", "now"),
            )
            store.connection.execute(
                """
                INSERT INTO formula_occurrences(
                    document_key,object_index,source_kind,mtef_sha256,wmf_sha256,mtef_version,status
                ) VALUES(?,?,?,?,?,?,?)
                """,
                ("doc", 1, "equation_ole", mtef_sha, wmf_sha, 5, "blocked"),
            )
            store.connection.execute(
                """
                INSERT INTO mtef_results(
                    mtef_sha256,representative_ole_path,mtef_version,parser_fingerprint,runtime_fingerprint,
                    contract_version,parser_status,parser_xml,final_kind,final_status,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    mtef_sha,
                    "source.bin",
                    5,
                    "parser-current",
                    "runtime-old",
                    "contract-old",
                    "parsed",
                    mtef_xml(5, "<slot><end/></slot>"),
                    "empty",
                    "reviewed_final",
                    "now",
                ),
            )
            store.connection.execute(
                """
                INSERT INTO previews(
                    wmf_sha256,source_path,render_status,png_path,png_sha256,render_json,
                    final_kind,final_value,final_status,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    wmf_sha,
                    "source.wmf",
                    "reviewed_empty",
                    str(png),
                    png_sha,
                    render_json,
                    "empty",
                    "",
                    "reviewed_final",
                    "now",
                ),
            )
            store.connection.commit()

            result = parse_pending_mtef(
                store,
                paths,
                "runtime-current",
                "parser-current",
            )

            mtef_state = tuple(
                store.connection.execute(
                    "SELECT final_kind,final_status,runtime_fingerprint FROM mtef_results WHERE mtef_sha256=?",
                    (mtef_sha,),
                ).fetchone()
            )
            preview_state = tuple(
                store.connection.execute(
                    """
                    SELECT render_status,final_kind,final_value,final_status,png_path,png_sha256,render_json
                    FROM previews WHERE wmf_sha256=?
                    """,
                    (wmf_sha,),
                ).fetchone()
            )
            store.close()
            self.assertEqual(result["invalidated_empty_previews"], 1)
            self.assertEqual(
                mtef_state,
                (None, "blocked", "runtime-current"),
            )
            self.assertEqual(
                preview_state,
                ("blocked", None, None, "needs_vlm", str(png), png_sha, render_json),
            )

    def test_independent_wmf_requires_hash_bound_review_classification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = audit_paths(root)
            store = AuditStore(paths.db)
            wmf_sha = "a" * 64
            png = root / "independent.png"
            png.write_bytes(b"independent-preview")
            png_sha = hashlib.sha256(png.read_bytes()).hexdigest()
            store.set_meta("runtime_fingerprint", "runtime-independent")
            store.connection.execute(
                "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)",
                ("doc", "source.docx", "c" * 64, 1, 1, "scanned", "[]", "now"),
            )
            store.connection.execute(
                """
                INSERT INTO formula_occurrences(
                    document_key,object_index,source_kind,wmf_sha256,status
                ) VALUES(?,?,?,?,?)
                """,
                ("doc", 1, "independent_wmf", wmf_sha, "needs_vlm"),
            )
            store.connection.execute(
                """
                INSERT INTO previews(
                    wmf_sha256,source_path,render_status,png_path,png_sha256,final_status,updated_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (wmf_sha, "source.wmf", "rendered", str(png), png_sha, "needs_vlm", "now"),
            )
            store.connection.commit()
            store.close()
            reviews = root / "reviews.jsonl"
            review = {
                "review_key": f"wmf:{wmf_sha}",
                "status": "resolved",
                "wmf_sha256": wmf_sha,
                "png_sha256": "0" * 64,
                "runtime_fingerprint": "runtime-independent",
                "final_kind": "image",
                "final_value": "media/independent.wmf",
                "visual_evidence": "Full-resolution preview is a non-formula diagram.",
                "confidence": 1.0,
            }
            reviews.write_text(json.dumps(review) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "PNG evidence hash mismatch"):
                apply_audit_reviews(paths.db, reviews)
            review["png_sha256"] = png_sha
            reviews.write_text(json.dumps(review) + "\n", encoding="utf-8")

            result = apply_audit_reviews(paths.db, reviews, require_all=True)

            self.assertTrue(result["ok"])
            connection = __import__("sqlite3").connect(paths.db)
            self.assertEqual(
                connection.execute(
                    "SELECT final_kind,final_value,final_status FROM previews WHERE wmf_sha256=?",
                    (wmf_sha,),
                ).fetchone(),
                ("image", "media/independent.wmf", "reviewed_final"),
            )
            connection.close()

    def test_completion_is_atomic_and_removed_when_a_formula_regresses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = audit_paths(root)
            store = AuditStore(paths.db)
            mtef_sha = "b" * 64
            wmf_sha = "c" * 64
            fingerprint = "runtime-complete"
            store.set_meta("runtime_fingerprint", fingerprint)
            store.connection.execute(
                "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)",
                ("doc", "source.docx", "d" * 64, 1, 1, "scanned", "[]", "now"),
            )
            store.connection.execute(
                """
                INSERT INTO formula_occurrences(
                    document_key,object_index,source_kind,mtef_sha256,wmf_sha256,mtef_version,status
                ) VALUES(?,?,?,?,?,?,?)
                """,
                ("doc", 1, "equation_ole", mtef_sha, wmf_sha, 5, "structure_pending"),
            )
            store.connection.execute(
                """
                INSERT INTO mtef_results(
                    mtef_sha256,representative_ole_path,mtef_version,runtime_fingerprint,
                    contract_version,parser_status,structure_latex,final_latex,final_kind,final_status,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    mtef_sha, "source.bin", 5, fingerprint, "contract", "parsed",
                    "x", "x", "latex", "reviewed_final", "now",
                ),
            )
            store.connection.execute(
                """
                INSERT INTO previews(wmf_sha256,source_path,render_status,final_status,updated_at)
                VALUES(?,?,?,?,?)
                """,
                (wmf_sha, "source.wmf", "rendered", "needs_vlm", "now"),
            )
            store.connection.commit()

            complete = export_reports(store, paths, fingerprint)
            completion_payload = json.loads((paths.root / "completion.json").read_text(encoding="utf-8"))
            self.assertTrue(complete["ok"])
            self.assertEqual(completion_payload["status"], "completion_ready")
            store.connection.execute(
                "UPDATE mtef_results SET final_latex=NULL,final_kind=NULL,final_status='needs_vlm' WHERE mtef_sha256=?",
                (mtef_sha,),
            )
            store.connection.commit()

            incomplete = export_reports(store, paths, fingerprint)

            self.assertFalse(incomplete["ok"])
            self.assertFalse((paths.root / "completion.json").exists())
            store.close()


if __name__ == "__main__":
    unittest.main()
