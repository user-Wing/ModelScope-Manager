# Third-party components

This portable distribution includes:

- Python 3.12 — Python Software Foundation License
- PySide6 Essentials / Qt for Python — LGPLv3/GPLv3/commercial licensing options
- pywebview 6.1 — BSD-3-Clause; uses the system Microsoft Edge WebView2 Runtime on Windows
- pythonnet 3.1.0 / clr-loader 0.3.1 — MIT
- cffi 2.1.1 — MIT; pycparser 3.0 — BSD-3-Clause
- bottle 0.13.4 — MIT; proxy_tools 0.1.0 — BSD; typing_extensions 4.16.0 — PSF-2.0
- PySide6-Fluent-Widgets 1.11.3 — GPLv3 for non-commercial use or a separately purchased commercial license; source: https://github.com/zhiyiYo/PyQt-Fluent-Widgets/tree/PySide6
- PySideSix-Frameless-Window 0.8.2 — LGPLv3; source: https://github.com/zhiyiYo/PyQt-Frameless-Window/tree/PySide6
- darkdetect 0.8.0 — BSD-3-Clause
- pywin32 312 — Python Software Foundation License
- ModelScope Hub SDK — Apache License 2.0
- aria2-next 2.5.6 — GNU General Public License v2 or later; source: https://github.com/AnInsomniacy/aria2-next
- 7-Zip Zstandard 26.02 / Zstd 1.5.7 — LGPL-2.1-or-later and BSD-3-Clause components; license retained in `embedded-tools/7zip-zstd/License.txt`; source: https://github.com/mcmilk/7-Zip-zstd
- PotPlayer is not distributed with ModelScope Manager. It is downloaded only when the user explicitly chooses installation, from the user-configured ModelScope dataset archive.
- FFmpeg is an optional on-demand plugin and is not bundled in the base ModelScope Manager package. The plugin archive is hosted at `ARXChem/Software-List/ffmpeg/FFmpeg.7z`; the current build identifies itself as FFmpeg `git-2026-09-01-c27482a18d` and reports `--enable-gpl --enable-version3 --enable-nonfree`. FFmpeg and every enabled third-party codec/library remain subject to their own licenses; redistribution of this optional build must be reviewed separately from ModelScope Manager.
- AWJimage (AGPL-3.0) was consulted for its documented AVIF defaults and benchmark profile; its C++ implementation and binaries are not included.
- Requests, urllib3, tqdm, filelock, certifi, idna, charset-normalizer, colorama and their bundled license metadata

Package license files and metadata are retained inside `runtime/Lib/site-packages`.
