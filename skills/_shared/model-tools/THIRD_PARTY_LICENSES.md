# Portable Runtime Third-Party Notices

The portable runtime is assembled only from the component allowlist in
`registry.yaml`. The package-level `PORTABLE_RUNTIME_MANIFEST.json` records the
exact files, SHA-256 hashes, component licenses, upstream URLs, and byte totals.

| Component | Version | License | Upstream |
|---|---:|---|---|
| CPython | 3.11.15 | PSF-2.0 | https://www.python.org/ |
| Node.js | 24.16.0 | MIT | https://nodejs.org/ |
| FFmpeg / ffprobe full build | 8.1.1 | GPL-3.0-or-later | https://ffmpeg.org/ |
| LibreOffice | 26.2.4.2 | MPL-2.0 OR LGPL-3.0-or-later | https://www.libreoffice.org/ |
| uv | 0.11.28 | Apache-2.0 OR MIT | https://github.com/astral-sh/uv |
| Eclipse Temurin JRE | 21.0.11+10 | GPL-2.0-only WITH Classpath-exception-2.0 | https://adoptium.net/temurin/ |
| JRuby Complete | 9.3.8.0 | EPL-2.0 OR GPL-2.0-only OR LGPL-2.1-only | https://www.jruby.org/ |
| Apache Batik | 1.19 | Apache-2.0 | https://xmlgraphics.apache.org/batik/ |
| transpect mathtype parser subset | 0.0.7.5 | MIT AND BSD-2-Clause AND MIT AND MIT | https://github.com/transpect/mathtype |
| olefile | 0.47 | BSD-2-Clause | https://pypi.org/project/olefile/0.47/ |
| bilibili-mcp-js | 0.1.3 | MIT | https://github.com/34892002/bilibili-mcp-js |
| OfficeCLI | 1.0.135 | Apache-2.0 | https://github.com/iOfficeAI/OfficeCLI |
| PaddleOCR/PaddleX models | registry revisions | Apache-2.0 | https://huggingface.co/PaddlePaddle |
| TexTeller | 1.0.2 model/runtime | Apache-2.0 | https://github.com/OleehyO/TexTeller |
| faster-whisper models | large-v3 / medium | MIT | https://huggingface.co/Systran |
| Pillow (Role D) | 11.3.0 | MIT-CMU | https://python-pillow.org/ |
| PyMuPDF (Role D) | 1.28.0 | AGPL-3.0-only OR commercial | https://pymupdf.readthedocs.io/ |
| PyYAML (Role D) | 6.0.3 | MIT | https://pyyaml.org/ |

The packaged FFmpeg build is GPL-enabled. Recipients must retain its included
license files and the corresponding-source offer supplied by its upstream build
publisher. Runtime synchronization must fail if the registry marks a component
as blocked for redistribution or omits its license.

The MathType runtime contains only the license-clear Ruby parser core and its
declared dependencies. It deliberately excludes transpect's top-level Java,
XProc, XSLT, tests, and build glue. Temurin and JRuby notices must remain with
their packaged directories.

The bundled PyMuPDF copy is distributed under its AGPL option unless the
recipient supplies a separate Artifex commercial license. Recipients must keep
the AGPL notice and corresponding-source offer with the portable package.
