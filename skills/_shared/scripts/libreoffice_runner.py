"""Safe, full-frame LibreOffice vector rendering shared by import and curation.

The public seam is deliberately small: choose the command-line launcher and
render one vector asset.  Windows launcher quirks, isolated profiles, timeout
cleanup, hashing, native-bounds parsing, and geometry diagnostics stay here.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import io
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Sequence


VECTOR_SUFFIXES = {".wmf", ".emf"}
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_WMF_DPI = 1200
DEFAULT_MIN_LONG_EDGE = 300
DEFAULT_MAX_EDGE = 8000
DEFAULT_MAX_PIXELS = 32_000_000


class LibreOfficeRenderError(RuntimeError):
    """Raised when a source-preserving vector render cannot be produced."""


def portable_soffice() -> Path | None:
    model_scripts = Path(__file__).resolve().parents[1] / "model-tools" / "scripts"
    if str(model_scripts) not in sys.path:
        sys.path.insert(0, str(model_scripts))
    try:
        from model_runtime import resolve_component

        path = Path(resolve_component("libreoffice-26.2.4.2").get("resolved_path") or "")
        return path if path.is_file() else None
    except Exception:
        return None


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_soffice(
    explicit: str | Path | None = None,
    *,
    platform_name: str | None = None,
    program_dir: str | Path | None = None,
) -> Path:
    """Return a usable LibreOffice launcher, preferring ``soffice.com`` on Windows."""
    explicit_value = explicit or os.environ.get("LIBREOFFICE_SOFFICE")
    if explicit_value:
        selected = Path(explicit_value).expanduser()
        if not selected.is_file():
            raise FileNotFoundError(f"LibreOffice launcher not found: {selected}")
        return selected.resolve()

    current_platform = platform_name or sys.platform
    candidates: list[Path] = []
    if current_platform.startswith("win"):
        if program_dir is not None:
            directories = [Path(program_dir)]
        else:
            portable = portable_soffice()
            if portable is not None:
                candidates.append(portable)
            directories = [
                Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "LibreOffice" / "program",
                Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "LibreOffice" / "program",
            ]
        for directory in directories:
            candidates.extend((directory / "soffice.com", directory / "soffice.exe"))
        path_com = shutil.which("soffice.com")
        path_default = shutil.which("soffice")
        candidates.extend(Path(value) for value in (path_com, path_default) if value)
    else:
        candidates.extend(Path(value) for value in (shutil.which("soffice"), shutil.which("libreoffice")) if value)

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("LibreOffice command-line launcher was not found")


def select_powershell() -> str:
    """Prefer modern PowerShell for deterministic native WMF startup."""
    for executable in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
        resolved = shutil.which(executable)
        if resolved:
            return resolved
    raise FileNotFoundError("PowerShell was not found for native WMF rendering")


def vector_source_bounds(path: Path) -> dict[str, Any] | None:
    """Read WMF/EMF logical bounds without changing the source."""
    data = path.read_bytes()[:88]
    if path.suffix.lower() == ".wmf" and len(data) >= 22:
        key = struct.unpack_from("<I", data, 0)[0]
        if key == 0x9AC6CDD7:
            _, _, left, top, right, bottom, units_per_inch, _, _ = struct.unpack_from(
                "<IHhhhhHIH", data, 0
            )
            width, height = abs(right - left), abs(bottom - top)
            return {
                "format": "placeable_wmf",
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
                "width": width,
                "height": height,
                "units_per_inch": units_per_inch,
                "aspect": round(width / height, 8) if height else None,
            }
    if path.suffix.lower() == ".emf" and len(data) >= 40:
        record_type, record_size = struct.unpack_from("<II", data, 0)
        if record_type == 1 and record_size >= 88:
            left, top, right, bottom = struct.unpack_from("<iiii", data, 8)
            width, height = abs(right - left), abs(bottom - top)
            return {
                "format": "emf",
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
                "width": width,
                "height": height,
                "aspect": round(width / height, 8) if height else None,
            }
    return None


def png_dimensions(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise LibreOfficeRenderError(f"LibreOffice did not produce a valid PNG: {path}")
    return struct.unpack(">II", header[16:24])


def _content_geometry(path: Path, width: int, height: int) -> tuple[list[int] | None, float | None, bool | None]:
    try:
        from PIL import Image, ImageChops  # type: ignore

        with Image.open(path).convert("RGBA") as image:
            background = Image.new("RGBA", image.size, "white")
            background.alpha_composite(image)
            rgb = background.convert("RGB")
            difference = ImageChops.difference(rgb, Image.new("RGB", image.size, "white"))
            bbox = difference.getbbox()
            if bbox is None:
                return None, 0.0, True
            content_width = max(0, bbox[2] - bbox[0])
            content_height = max(0, bbox[3] - bbox[1])
            ratio = (content_width * content_height) / max(1, width * height)
            return [int(value) for value in bbox], round(ratio, 8), False
    except Exception:
        return None, None, None


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.kill()
    except OSError:
        pass


def _run_conversion(command: Sequence[str], source: Path, out_dir: Path, profile: Path, timeout: int) -> tuple[int, str, str]:
    profile_uri = profile.resolve().as_uri()
    args = [
        *[str(value) for value in command],
        "--headless",
        "--invisible",
        "--norestore",
        f"-env:UserInstallation={profile_uri}",
        "--convert-to",
        "png",
        "--outdir",
        str(out_dir),
        str(source),
    ]
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(process)
        stdout, stderr = process.communicate()
        raise LibreOfficeRenderError(
            f"LibreOffice vector render timed out after {timeout}s: {source}"
        ) from exc
    return int(process.returncode or 0), stdout, stderr


def _fit_native_render_size(
    bounds: dict[str, Any],
    *,
    dpi: int = DEFAULT_WMF_DPI,
    min_long_edge: int = DEFAULT_MIN_LONG_EDGE,
    max_edge: int = DEFAULT_MAX_EDGE,
    max_pixels: int = DEFAULT_MAX_PIXELS,
) -> tuple[int, int]:
    """Choose the one-pass vector render size while preserving source aspect."""
    width_units = int(bounds.get("width") or 0)
    height_units = int(bounds.get("height") or 0)
    units_per_inch = int(bounds.get("units_per_inch") or 0)
    if width_units < 1 or height_units < 1 or units_per_inch < 1:
        raise LibreOfficeRenderError("placeable WMF has invalid native bounds")

    width = max(1, round(width_units * dpi / units_per_inch))
    height = max(1, round(height_units * dpi / units_per_inch))
    long_edge = max(width, height)
    if long_edge < min_long_edge:
        scale = min_long_edge / long_edge
        width = max(1, round(width * scale))
        height = max(1, round(height * scale))

    scale = min(1.0, max_edge / max(width, height))
    pixels = width * height
    if pixels > max_pixels:
        scale = min(scale, (max_pixels / pixels) ** 0.5)
    if scale < 1.0:
        width = max(1, round(width * scale))
        height = max(1, round(height * scale))
    return width, height


def _run_native_wmf_conversion(
    source: Path,
    destination: Path,
    bounds: dict[str, Any],
    timeout: int,
) -> tuple[list[str], str, str]:
    """Render a placeable WMF through Windows GDI+ at its native bounds."""
    script = Path(__file__).with_name("render_wmf_native.ps1")
    if not script.is_file():
        raise LibreOfficeRenderError(f"native WMF adapter is missing: {script}")
    try:
        powershell = select_powershell()
    except FileNotFoundError as exc:
        raise LibreOfficeRenderError(str(exc)) from exc
    width, height = _fit_native_render_size(bounds)
    command = [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Source",
        str(source),
        "-Output",
        str(destination),
        "-Width",
        str(width),
        "-Height",
        str(height),
    ]
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(process)
        process.communicate()
        raise LibreOfficeRenderError(
            f"native WMF render timed out after {timeout}s: {source}"
        ) from exc
    if process.returncode != 0 or not destination.is_file():
        destination.unlink(missing_ok=True)
        raise LibreOfficeRenderError(
            "native WMF render failed: "
            f"returncode={process.returncode} stderr={stderr.strip()}"
        )
    return command, stdout, stderr


def _wmf_has_mathtype_comments(source: Path) -> bool:
    """Detect MathType MFCOMMENT records without asking GDI to play them."""
    payload = source.read_bytes()
    if len(payload) < 40 or struct.unpack_from("<I", payload, 0)[0] != 0x9AC6CDD7:
        return False
    offset = 40  # 22-byte placeable header + 18-byte METAHEADER.
    while offset + 6 <= len(payload):
        size_words, function = struct.unpack_from("<IH", payload, offset)
        size_bytes = int(size_words) * 2
        if size_words < 3 or offset + size_bytes > len(payload):
            raise LibreOfficeRenderError(f"invalid WMF record table: {source}")
        if function == 0:
            return False
        if function == 0x0626 and size_bytes >= 10:
            escape_function, byte_count = struct.unpack_from("<HH", payload, offset + 6)
            data = payload[offset + 10 : offset + 10 + min(int(byte_count), size_bytes - 10)]
            if escape_function == 15 and (b"MathType" in data or b"AppsMFCC" in data or b"MathTypeUU" in data):
                return True
        offset += size_bytes
    raise LibreOfficeRenderError(f"WMF record table has no META_EOF: {source}")


def _filter_wmf_non_drawing_comments(payload: bytes) -> tuple[bytes, int]:
    """Return a placeable WMF with MFCOMMENT records removed and header sizes repaired."""
    if len(payload) < 46 or struct.unpack_from("<I", payload, 0)[0] != 0x9AC6CDD7:
        raise LibreOfficeRenderError("input is not a complete placeable WMF")
    output = bytearray(payload[:40])
    offset = 40
    max_record_words = 3
    skipped = 0
    saw_eof = False
    while offset + 6 <= len(payload):
        size_words, function = struct.unpack_from("<IH", payload, offset)
        size_bytes = int(size_words) * 2
        if size_words < 3 or offset + size_bytes > len(payload):
            raise LibreOfficeRenderError("WMF record table is truncated or invalid")
        is_comment = (
            function == 0x0626
            and size_words >= 5
            and struct.unpack_from("<H", payload, offset + 6)[0] == 15
        )
        if is_comment:
            skipped += 1
        else:
            output.extend(payload[offset:offset + size_bytes])
            max_record_words = max(max_record_words, int(size_words))
        offset += size_bytes
        if function == 0:
            saw_eof = True
            break
    if not saw_eof:
        raise LibreOfficeRenderError("WMF record table has no META_EOF")
    struct.pack_into("<I", output, 28, (len(output) - 22) // 2)
    struct.pack_into("<I", output, 34, max_record_words)
    return bytes(output), skipped


def _pillow_wmf_worker(task_path: Path) -> int:
    from PIL import Image  # type: ignore

    jobs = json.loads(task_path.read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    ok = True
    for job in jobs:
        source = Path(job["source"])
        output = Path(job["output"])
        try:
            filtered, skipped = _filter_wmf_non_drawing_comments(source.read_bytes())
            source_width = max(1, int(job["source_width"]))
            source_height = max(1, int(job["source_height"]))
            units_per_inch = max(72, int(job["units_per_inch"]))
            scale = min(int(job["width"]) / source_width, int(job["height"]) / source_height)
            dpi = max(72, round(units_per_inch * scale))
            output.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(io.BytesIO(filtered)) as image:
                image.load(dpi=dpi)
                image.convert("RGB").save(output, format="PNG")
            results.append({"source": str(source), "output": str(output), "dpi": dpi, "skipped_comments": skipped})
        except Exception as exc:
            output.unlink(missing_ok=True)
            results.append({"source": str(source), "output": str(output), "error": repr(exc)})
            ok = False
    print(json.dumps(results, ensure_ascii=False))
    return 0 if ok else 1


def _run_pillow_wmf_task(
    jobs: Sequence[dict[str, Any]],
    timeout: int,
) -> tuple[list[str], str, str, bool]:
    with tempfile.TemporaryDirectory(prefix="wmf-pillow-batch-") as temp_dir:
        task_path = Path(temp_dir) / "task.json"
        task_payload = json.dumps(list(jobs), ensure_ascii=False)
        task_path.write_text(task_payload, encoding="utf-8")
        command = [sys.executable, str(Path(__file__).resolve()), "--pillow-wmf-task", str(task_path)]
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=creationflags,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            stdout, stderr = process.communicate()
            stderr = (stderr + f"\nPillow WMF batch timed out after {timeout}s").strip()
        audited = command[:-1] + [f"sha256:{hashlib.sha256(task_payload.encode('utf-8')).hexdigest()}"]
        return audited, stdout, stderr, process.returncode == 0


def _run_pillow_wmf_batches(
    jobs: Sequence[dict[str, Any]],
    timeout: int,
) -> dict[str, tuple[list[str], str, str] | Exception]:
    """Render filtered WMFs in an isolated Pillow/GDI worker with timeout bisection."""
    results: dict[str, tuple[list[str], str, str] | Exception] = {}

    def run(chunk: list[dict[str, Any]]) -> None:
        if not chunk:
            return
        attempt_timeout = max(5, min(timeout, 5 + len(chunk) // 8))
        try:
            command, stdout, stderr, success = _run_pillow_wmf_task(chunk, attempt_timeout)
        except Exception as exc:
            command, stdout, stderr, success = [], "", repr(exc), False
        missing = [job["output"] for job in chunk if not Path(job["output"]).is_file()]
        if success and not missing:
            for job in chunk:
                results[job["source"]] = (command, stdout, stderr)
            return
        for job in chunk:
            Path(job["output"]).unlink(missing_ok=True)
        if len(chunk) == 1:
            results[chunk[0]["source"]] = LibreOfficeRenderError(
                f"Pillow WMF render failed: missing={missing[:1]} stderr={stderr[-2000:]}"
            )
            return
        midpoint = len(chunk) // 2
        run(chunk[:midpoint])
        run(chunk[midpoint:])

    run(list(jobs))
    return results


def _run_batik_wmf_batch(
    jobs: Sequence[tuple[Path, Path, dict[str, Any]]],
    timeout: int,
) -> tuple[list[str], str, str]:
    """Render WMF jobs through bundled Batik in one JVM."""
    if not jobs:
        raise ValueError("Batik batch requires at least one job")
    model_runtime = Path(__file__).resolve().parents[1] / "model-tools" / "scripts" / "model_runtime.py"
    java_arguments = [
        "org.llmwiki.bemarkdown.BatikBridge",
        "batch-wmf2png",
    ]
    for source, destination, bounds in jobs:
        width, height = _fit_native_render_size(bounds)
        java_arguments.extend([str(source), str(destination), str(width), str(height)])
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with tempfile.TemporaryDirectory(prefix="batik-java-args-") as temp_dir:
        argfile = Path(temp_dir) / "batch.args"
        payload = "\n".join(_java_argfile_quote(value) for value in java_arguments) + "\n"
        argfile.write_text(payload, encoding="utf-8", newline="\n")
        argfile_sha256 = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        command = [
            sys.executable,
            str(model_runtime),
            "exec",
            "--id",
            "apache-batik-1.19",
            "--",
            "--java-argfile",
            str(argfile),
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=creationflags,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _kill_process_tree(process)
            process.communicate()
            raise LibreOfficeRenderError(f"Batik WMF batch timed out after {timeout}s") from exc
    missing = [str(destination) for _, destination, _ in jobs if not destination.is_file()]
    if process.returncode != 0 or missing:
        for _, destination, _ in jobs:
            destination.unlink(missing_ok=True)
        raise LibreOfficeRenderError(
            "Batik WMF render failed: "
            f"returncode={process.returncode} missing={missing[:3]} stderr={stderr[-2000:]}"
        )
    audited_command = command[:-1] + [f"sha256:{argfile_sha256}"]
    return audited_command, stdout, stderr


def _crop_libreoffice_svg_to_source_bounds(
    source_svg: Path,
    cropped_svg: Path,
    bounds: dict[str, Any],
) -> dict[str, Any]:
    """Remove an A4 wrapper only when LibreOffice exposes a strict source-shaped BoundingBox."""
    try:
        tree = ET.parse(source_svg)
    except (ET.ParseError, OSError) as exc:
        raise LibreOfficeRenderError(f"invalid LibreOffice SVG: {source_svg}: {exc}") from exc
    root = tree.getroot()
    rectangles: list[tuple[float, float, float, float]] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "rect":
            continue
        if "BoundingBox" not in str(element.attrib.get("class") or "").split():
            continue
        try:
            x = float(element.attrib["x"])
            y = float(element.attrib["y"])
            width = float(element.attrib["width"])
            height = float(element.attrib["height"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LibreOfficeRenderError(f"LibreOffice SVG has a malformed BoundingBox: {source_svg}") from exc
        if width <= 0 or height <= 0:
            raise LibreOfficeRenderError(f"LibreOffice SVG has an empty BoundingBox: {source_svg}")
        rectangles.append((x, y, x + width, y + height))
    if not rectangles:
        raise LibreOfficeRenderError(f"LibreOffice SVG has no semantic BoundingBox: {source_svg}")
    left = min(value[0] for value in rectangles)
    top = min(value[1] for value in rectangles)
    right = max(value[2] for value in rectangles)
    bottom = max(value[3] for value in rectangles)
    width = right - left
    height = bottom - top
    source_aspect = float(bounds.get("aspect") or 0.0)
    bbox_aspect = width / height
    if source_aspect <= 0:
        raise LibreOfficeRenderError(f"source bounds have no usable aspect ratio: {source_svg}")
    aspect_delta = abs(bbox_aspect - source_aspect) / source_aspect
    if aspect_delta > 0.01:
        raise LibreOfficeRenderError(
            f"LibreOffice SVG BoundingBox/source aspect mismatch: {bbox_aspect:.8f} vs {source_aspect:.8f}"
        )
    page_view_box = str(root.attrib.get("viewBox") or "").split()
    if len(page_view_box) != 4:
        raise LibreOfficeRenderError(f"LibreOffice SVG has no four-value page viewBox: {source_svg}")
    try:
        page_left, page_top, page_width, page_height = [float(value) for value in page_view_box]
    except ValueError as exc:
        raise LibreOfficeRenderError(f"LibreOffice SVG page viewBox is invalid: {source_svg}") from exc
    padding_ratio = 0.03
    padded_left = left - width * padding_ratio
    padded_top = top - height * padding_ratio
    padded_right = right + width * padding_ratio
    padded_bottom = bottom + height * padding_ratio
    if (
        padded_left < page_left
        or padded_top < page_top
        or padded_right > page_left + page_width
        or padded_bottom > page_top + page_height
    ):
        raise LibreOfficeRenderError(f"LibreOffice SVG BoundingBox padding exceeds the page frame: {source_svg}")
    padded_width = padded_right - padded_left
    padded_height = padded_bottom - padded_top
    target_width, target_height = _fit_native_render_size(bounds)
    root.set("viewBox", f"{padded_left:g} {padded_top:g} {padded_width:g} {padded_height:g}")
    root.set("width", f"{target_width}px")
    root.set("height", f"{target_height}px")
    root.set("preserveAspectRatio", "xMidYMid meet")
    cropped_svg.parent.mkdir(parents=True, exist_ok=True)
    tree.write(cropped_svg, encoding="utf-8", xml_declaration=True)
    return {
        "bounding_box": [left, top, right, bottom],
        "bounding_box_aspect": round(bbox_aspect, 8),
        "source_aspect_delta": round(aspect_delta, 8),
        "padding_ratio": padding_ratio,
        "cropped_view_box": [padded_left, padded_top, padded_right, padded_bottom],
        "target_size": [target_width, target_height],
    }


def _run_libreoffice_svg_bbox_batch(
    jobs: Sequence[tuple[Path, Path, dict[str, Any]]],
    timeout: int,
) -> tuple[list[str], str, str, dict[str, dict[str, Any]]]:
    """Convert WMF to SVG, verify LO's semantic bounds, then batch-rasterize with bundled Batik."""
    if not jobs:
        raise ValueError("LibreOffice SVG batch requires at least one job")
    soffice = select_soffice()
    model_runtime = Path(__file__).resolve().parents[1] / "model-tools" / "scripts" / "model_runtime.py"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with tempfile.TemporaryDirectory(prefix="lo-wmf-svg-batch-") as temp_dir:
        temp = Path(temp_dir)
        profile = temp / "profile"
        converted = temp / "converted"
        cropped = temp / "cropped"
        rasterized = temp / "png"
        for directory in (profile, converted, cropped, rasterized):
            directory.mkdir(parents=True, exist_ok=True)
        lo_command = [
            str(soffice),
            "--headless",
            "--invisible",
            "--norestore",
            f"-env:UserInstallation={profile.resolve().as_uri()}",
            "--convert-to",
            "svg",
            "--outdir",
            str(converted),
            *[str(source) for source, _, _ in jobs],
        ]
        lo_process = subprocess.Popen(
            lo_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=creationflags,
        )
        try:
            lo_stdout, lo_stderr = lo_process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _kill_process_tree(lo_process)
            lo_process.communicate()
            raise LibreOfficeRenderError(f"LibreOffice WMF-to-SVG batch timed out after {timeout}s") from exc
        if lo_process.returncode != 0:
            raise LibreOfficeRenderError(
                f"LibreOffice WMF-to-SVG batch failed: returncode={lo_process.returncode} stderr={lo_stderr[-2000:]}"
            )

        metadata: dict[str, dict[str, Any]] = {}
        cropped_paths: list[Path] = []
        for source, _, bounds in jobs:
            source_svg = converted / f"{source.stem}.svg"
            cropped_svg = cropped / f"{source.stem}.svg"
            if not source_svg.is_file():
                raise LibreOfficeRenderError(f"LibreOffice SVG output is missing: {source_svg}")
            metadata[str(source)] = _crop_libreoffice_svg_to_source_bounds(source_svg, cropped_svg, bounds)
            cropped_paths.append(cropped_svg)

        java_arguments = [
            "org.apache.batik.apps.rasterizer.Main",
            "-d",
            str(rasterized),
            *[str(path) for path in cropped_paths],
        ]
        argfile = temp / "svg-batch.args"
        payload = "\n".join(_java_argfile_quote(value) for value in java_arguments) + "\n"
        argfile.write_text(payload, encoding="utf-8", newline="\n")
        java_command = [
            sys.executable,
            str(model_runtime),
            "exec",
            "--id",
            "apache-batik-1.19",
            "--",
            "--java-argfile",
            str(argfile),
        ]
        java_process = subprocess.Popen(
            java_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=creationflags,
        )
        try:
            java_stdout, java_stderr = java_process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _kill_process_tree(java_process)
            java_process.communicate()
            raise LibreOfficeRenderError(f"Batik SVG batch timed out after {timeout}s") from exc
        missing: list[str] = []
        for source, destination, _ in jobs:
            rendered = rasterized / f"{source.stem}.png"
            if not rendered.is_file():
                missing.append(str(rendered))
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(rendered, destination)
        if java_process.returncode != 0 or missing:
            for _, destination, _ in jobs:
                destination.unlink(missing_ok=True)
            raise LibreOfficeRenderError(
                f"Batik SVG batch failed: returncode={java_process.returncode} missing={missing[:3]} stderr={java_stderr[-2000:]}"
            )
        audit_payload = json.dumps(
            {
                "sources": [str(source) for source, _, _ in jobs],
                "lo": lo_command[:7],
                "batik_args_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        audited_command = ["libreoffice-svg+bbox+batik", f"sha256:{hashlib.sha256(audit_payload).hexdigest()}"]
        return audited_command, lo_stdout + java_stdout, lo_stderr + java_stderr, metadata


def _java_argfile_quote(value: str) -> str:
    """Encode one Java launcher argfile token without path interpretation."""
    if "\r" in value or "\n" in value or "\x00" in value:
        raise ValueError("Java argfile values cannot contain CR, LF, or NUL")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _finalize_render_result(
    source_path: Path,
    destination: Path,
    source_hash_before: str,
    bounds: dict[str, Any] | None,
    selected_command: list[str],
    renderer_adapter: str,
    stdout: str,
    stderr: str,
    fallback_reason: str | None,
) -> dict[str, Any]:
    source_hash_after = sha256_path(source_path)
    if source_hash_after != source_hash_before:
        destination.unlink(missing_ok=True)
        raise LibreOfficeRenderError(f"source vector changed during rendering: {source_path}")

    width, height = png_dimensions(destination)
    render_aspect = width / height if height else None
    source_aspect = bounds.get("aspect") if bounds else None
    aspect_delta = None
    if source_aspect and render_aspect:
        aspect_delta = round(abs(render_aspect - float(source_aspect)) / float(source_aspect), 8)
    content_bbox, content_ratio, blank = _content_geometry(destination, width, height)
    issues: list[str] = []
    if blank is True:
        issues.append("blank_render")
    if width < 16 or height < 16:
        issues.append("render_too_small")
    if aspect_delta is not None and aspect_delta > 0.03:
        issues.append("source_render_aspect_mismatch")
    if content_ratio is not None and 0 < content_ratio < 0.002:
        issues.append("content_occupancy_too_low")
    return {
        "source_path": str(source_path),
        "source_sha256": source_hash_before,
        "source_bounds": bounds,
        "output_path": str(destination),
        "visual_sha256": sha256_path(destination),
        "render_geometry": {
            "width": width,
            "height": height,
            "pixels": width * height,
            "aspect": round(render_aspect, 8) if render_aspect else None,
            "aspect_delta": aspect_delta,
            "content_bbox": content_bbox,
            "content_ratio": content_ratio,
        },
        "geometry_issues": issues,
        "cropped": False,
        "resized": False,
        "full_resolution": True,
        "renderer_adapter": renderer_adapter,
        "renderer_command": selected_command,
        "renderer_fallback_reason": fallback_reason,
        "stdout": stdout,
        "stderr": stderr,
    }


def render_vector_full_frame(
    source: str | Path,
    output_dir: str | Path,
    *,
    output_name: str | None = None,
    soffice: str | Path | None = None,
    command: Sequence[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Render one WMF/EMF without crop or raster resize and return hash-bound metadata."""
    source_path = Path(source).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if source_path.suffix.lower() not in VECTOR_SUFFIXES:
        raise ValueError(f"unsupported vector format: {source_path.suffix}")
    if timeout < 1:
        raise ValueError("timeout must be positive")

    source_hash_before = sha256_path(source_path)
    destination_dir = Path(output_dir).resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / (output_name or f"{source_path.stem}.png")
    bounds = vector_source_bounds(source_path)
    renderer_adapter = "libreoffice_cli"
    fallback_reason: str | None = None
    staged = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp.png")
    selected_command: list[str]
    stdout = ""
    stderr = ""

    use_native_wmf = (
        command is None
        and os.name == "nt"
        and source_path.suffix.lower() == ".wmf"
        and bounds is not None
        and bounds.get("format") == "placeable_wmf"
    )
    if use_native_wmf:
        route_batik = _wmf_has_mathtype_comments(source_path)
        try:
            if route_batik:
                selected_command, stdout, stderr = _run_batik_wmf_batch(
                    [(source_path, staged, bounds)], timeout
                )
                renderer_adapter = "apache_batik"
                fallback_reason = "MathType MFCOMMENT records routed away from native GDI"
            else:
                selected_command, stdout, stderr = _run_native_wmf_conversion(
                    source_path, staged, bounds, timeout
                )
                renderer_adapter = "windows_gdiplus"
        except LibreOfficeRenderError as native_exc:
            staged.unlink(missing_ok=True)
            try:
                selected_command, stdout, stderr = _run_batik_wmf_batch(
                    [(source_path, staged, bounds)], timeout
                )
                renderer_adapter = "apache_batik"
                fallback_reason = str(native_exc)
            except LibreOfficeRenderError as batik_exc:
                fallback_reason = f"native={native_exc}; batik={batik_exc}"
                staged.unlink(missing_ok=True)
                use_native_wmf = False

    if not use_native_wmf:
        selected_command = list(command) if command is not None else [str(select_soffice(soffice))]
        with tempfile.TemporaryDirectory(prefix="lo-vector-render-") as conversion_directory, tempfile.TemporaryDirectory(
            prefix="lo-vector-profile-"
        ) as profile_directory:
            conversion_root = Path(conversion_directory)
            returncode, stdout, stderr = _run_conversion(
                selected_command,
                source_path,
                conversion_root,
                Path(profile_directory),
                timeout,
            )
            candidates = sorted(conversion_root.glob("*.png"))
            if returncode != 0 or not candidates:
                raise LibreOfficeRenderError(
                    "LibreOffice full-frame vector render failed: "
                    f"returncode={returncode} stderr={stderr.strip()}"
                )
            shutil.copy2(candidates[0], staged)
    os.replace(staged, destination)

    return _finalize_render_result(
        source_path,
        destination,
        source_hash_before,
        bounds,
        selected_command,
        renderer_adapter,
        stdout,
        stderr,
        fallback_reason,
    )


def _run_native_wmf_task(
    jobs: Sequence[dict[str, Any]],
    timeout: int,
) -> tuple[list[str], str, str, bool]:
    """Run one native WMF task file and always clean up its process tree."""
    script = Path(__file__).with_name("render_wmf_native.ps1")
    try:
        powershell = select_powershell()
    except FileNotFoundError as exc:
        raise LibreOfficeRenderError(str(exc)) from exc
    if not script.is_file():
        raise LibreOfficeRenderError("native WMF batch adapter is unavailable")
    with tempfile.TemporaryDirectory(prefix="wmf-native-batch-") as temp_dir:
        task_path = Path(temp_dir) / "task.json"
        task_path.write_text(json.dumps(list(jobs), ensure_ascii=False), encoding="utf-8")
        command = [
            powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script), "-Task", str(task_path),
        ]
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            stdout, stderr = process.communicate()
            stderr = (stderr + f"\nnative WMF batch timed out after {timeout}s").strip()
        return command, stdout, stderr, process.returncode == 0


def _run_native_wmf_batches(
    jobs: Sequence[dict[str, Any]],
    timeout: int,
) -> dict[str, tuple[list[str], str, str] | Exception]:
    """Render native jobs with timeout bisection so one WMF cannot poison a batch."""
    results: dict[str, tuple[list[str], str, str] | Exception] = {}

    def run(chunk: list[dict[str, Any]]) -> None:
        if not chunk:
            return
        # PowerShell + System.Drawing initialization can take about 20 seconds
        # on a cold Windows process.  Keep batches small enough for checkpoint
        # granularity, but give valid WMFs enough time before bisection.
        attempt_timeout = max(30, min(timeout, 60 + 20 * len(chunk)))
        try:
            command, stdout, stderr, success = _run_native_wmf_task(chunk, attempt_timeout)
        except Exception as exc:
            command, stdout, stderr, success = [], "", repr(exc), False
        missing = [job["output"] for job in chunk if not Path(job["output"]).is_file()]
        if success and not missing:
            for job in chunk:
                results[job["source"]] = (command, stdout, stderr)
            return
        for job in chunk:
            Path(job["output"]).unlink(missing_ok=True)
        if len(chunk) == 1:
            results[chunk[0]["source"]] = LibreOfficeRenderError(
                "native WMF batch render failed: "
                f"missing={missing[:1]} stderr={stderr[-2000:]}"
            )
            return
        midpoint = len(chunk) // 2
        run(chunk[:midpoint])
        run(chunk[midpoint:])

    # Classic GDI serializes enough global state that high process fan-out
    # starves every renderer.  Two workers gave the best stable throughput on
    # the real MathType corpus while preserving isolation and bisection.
    worker_count = min(2, max(1, (os.cpu_count() or 2) // 2), len(jobs))
    chunk_size = max(1, (len(jobs) + worker_count - 1) // worker_count)
    initial_chunks = [list(jobs[index:index + chunk_size]) for index in range(0, len(jobs), chunk_size)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(run, chunk) for chunk in initial_chunks]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    return results


def render_vectors_full_frame(
    sources: Sequence[str | Path],
    output_dir: str | Path,
    *,
    timeout: int = 1200,
    reuse_existing: bool = False,
) -> dict[str, dict[str, Any] | Exception]:
    """Batch-render placeable WMFs through isolated native GDI, then strict fallbacks."""
    source_paths = [Path(value).resolve() for value in sources]
    destination_dir = Path(output_dir).resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict[str, Any] | Exception] = {}
    pillow_jobs: list[dict[str, Any]] = []
    pillow_meta: list[tuple[Path, Path, Path, str, dict[str, Any]]] = []
    native_jobs: list[dict[str, Any]] = []
    native_meta: list[tuple[Path, Path, Path, str, dict[str, Any]]] = []
    batik_meta: list[tuple[Path, Path, Path, str, dict[str, Any]]] = []
    libreoffice_meta: list[tuple[Path, Path, Path, str, dict[str, Any]]] = []
    batik_blocked: dict[str, dict[str, Any]] = {}
    for source in source_paths:
        if not source.is_file():
            results[str(source)] = FileNotFoundError(source)
            continue
        bounds = vector_source_bounds(source)
        if os.name == "nt" and source.suffix.lower() == ".wmf" and bounds and bounds.get("format") == "placeable_wmf":
            width, height = _fit_native_render_size(bounds)
            destination = destination_dir / f"{source.stem}.png"
            if (
                reuse_existing
                and destination.is_file()
                and destination.stat().st_mtime_ns >= source.stat().st_mtime_ns
            ):
                try:
                    cached_result = _finalize_render_result(
                        source,
                        destination,
                        sha256_path(source),
                        bounds,
                        [],
                        "validated_wmf_render_cache",
                        "",
                        "",
                        None,
                    )
                    if not cached_result["geometry_issues"]:
                        results[str(source)] = cached_result
                        continue
                    destination.unlink(missing_ok=True)
                except Exception:
                    destination.unlink(missing_ok=True)
            staged = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp.png")
            meta = (source, destination, staged, sha256_path(source), bounds)
            pillow_jobs.append({
                "source": str(source),
                "output": str(staged),
                "width": width,
                "height": height,
                "source_width": int(bounds["width"]),
                "source_height": int(bounds["height"]),
                "units_per_inch": int(bounds.get("units_per_inch") or 1440),
            })
            pillow_meta.append(meta)
        else:
            try:
                results[str(source)] = render_vector_full_frame(source, destination_dir, timeout=timeout)
            except Exception as exc:
                results[str(source)] = exc
    if pillow_jobs:
        pillow_results = _run_pillow_wmf_batches(pillow_jobs, timeout)
        for source, destination, staged, source_hash, bounds in pillow_meta:
            try:
                native = pillow_results.get(str(source))
                if isinstance(native, Exception) or native is None or not staged.is_file():
                    raise native if isinstance(native, Exception) else LibreOfficeRenderError("Pillow WMF result is missing")
                command, stdout, stderr = native
                os.replace(staged, destination)
                pillow_result = _finalize_render_result(
                    source,
                    destination,
                    source_hash,
                    bounds,
                    command,
                    "windows_pillow_gdi_filtered",
                    stdout[-2000:],
                    stderr[-2000:],
                    None,
                )
                results[str(source)] = pillow_result
                if pillow_result["geometry_issues"]:
                    batik_staged = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.batik.tmp.png")
                    batik_meta.append((source, destination, batik_staged, source_hash, bounds))
            except Exception as exc:
                staged.unlink(missing_ok=True)
                results[str(source)] = exc
                batik_staged = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.batik.tmp.png")
                batik_meta.append((source, destination, batik_staged, source_hash, bounds))

    if batik_meta:
        jobs = [(source, staged, bounds) for source, _, staged, _, bounds in batik_meta]
        try:
            batik_command, _, batik_stderr = _run_batik_wmf_batch(jobs, timeout)
            for source, destination, staged, source_hash, bounds in batik_meta:
                os.replace(staged, destination)
                batik_result = _finalize_render_result(
                    source,
                    destination,
                    source_hash,
                    bounds,
                    batik_command,
                    "apache_batik_batch",
                    "",
                    batik_stderr[-2000:],
                    "Pillow native WMF render failed or failed geometry checks",
                )
                results[str(source)] = batik_result
                if batik_result["geometry_issues"]:
                    lo_staged = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.lo-svg.tmp.png")
                    libreoffice_meta.append((source, destination, lo_staged, source_hash, bounds))
                    batik_blocked[str(source)] = batik_result
        except Exception as exc:
            for source, _, staged, _, _ in batik_meta:
                staged.unlink(missing_ok=True)
                previous = results.get(str(source))
                if isinstance(previous, dict):
                    previous["renderer_fallback_reason"] = (
                        str(previous.get("renderer_fallback_reason") or "")
                        + f"; Batik WMF retry failed: {exc}"
                    ).lstrip("; ")
                else:
                    results[str(source)] = exc

    if libreoffice_meta:
        lo_jobs = [(source, staged, bounds) for source, _, staged, _, bounds in libreoffice_meta]
        try:
            lo_command, lo_stdout, lo_stderr, lo_audits = _run_libreoffice_svg_bbox_batch(lo_jobs, timeout)
            for source, destination, staged, source_hash, bounds in libreoffice_meta:
                os.replace(staged, destination)
                lo_result = _finalize_render_result(
                    source,
                    destination,
                    source_hash,
                    bounds,
                    lo_command,
                    "libreoffice_svg_bbox_batik",
                    lo_stdout[-2000:],
                    lo_stderr[-2000:],
                    "Batik WMF geometry invalid; LibreOffice semantic BoundingBox matched source bounds",
                )
                lo_result["svg_bbox_audit"] = lo_audits.get(str(source), {})
                lo_result["a4_wrapper_removed"] = True
                content_bbox = lo_result["render_geometry"].get("content_bbox")
                width = int(lo_result["render_geometry"]["width"])
                height = int(lo_result["render_geometry"]["height"])
                if content_bbox and (
                    content_bbox[0] <= 0
                    or content_bbox[1] <= 0
                    or content_bbox[2] >= width
                    or content_bbox[3] >= height
                ):
                    lo_result["geometry_issues"].append("content_touches_render_edge")
                results[str(source)] = lo_result
                if lo_result["geometry_issues"]:
                    width, height = _fit_native_render_size(bounds)
                    native_staged = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.native.tmp.png")
                    native_jobs.append({"source": str(source), "output": str(native_staged), "width": width, "height": height})
                    native_meta.append((source, destination, native_staged, source_hash, bounds))
                    batik_blocked[str(source)] = lo_result
                else:
                    batik_blocked.pop(str(source), None)
        except Exception as exc:
            for source, destination, staged, source_hash, bounds in libreoffice_meta:
                staged.unlink(missing_ok=True)
                fallback = batik_blocked.get(str(source))
                if fallback is not None:
                    fallback["renderer_fallback_reason"] = (
                        str(fallback.get("renderer_fallback_reason") or "")
                        + f"; LibreOffice SVG BoundingBox retry failed: {exc}"
                    )
                width, height = _fit_native_render_size(bounds)
                native_staged = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.native.tmp.png")
                native_jobs.append({"source": str(source), "output": str(native_staged), "width": width, "height": height})
                native_meta.append((source, destination, native_staged, source_hash, bounds))

    if not native_jobs:
        return results

    native_results = _run_native_wmf_batches(native_jobs, timeout)
    for source, destination, staged, source_hash, bounds in native_meta:
        try:
            native = native_results.get(str(source))
            if isinstance(native, Exception) or native is None or not staged.is_file():
                raise native if isinstance(native, Exception) else LibreOfficeRenderError("native WMF result is missing")
            command, stdout, stderr = native
            os.replace(staged, destination)
            results[str(source)] = _finalize_render_result(
                source,
                destination,
                source_hash,
                bounds,
                command,
                "windows_gdiplus_batch",
                "",
                stderr[-2000:],
                None,
            )
        except Exception as exc:
            staged.unlink(missing_ok=True)
            if str(source) in batik_blocked:
                fallback = batik_blocked[str(source)]
                fallback["renderer_fallback_reason"] = (
                    str(fallback.get("renderer_fallback_reason") or "")
                    + f"; native geometry retry failed: {exc}"
                )
                results[str(source)] = fallback
            else:
                results[str(source)] = exc
    return results


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--pillow-wmf-task":
        raise SystemExit(_pillow_wmf_worker(Path(sys.argv[2])))
    raise SystemExit("usage: libreoffice_runner.py --pillow-wmf-task TASK.json")
