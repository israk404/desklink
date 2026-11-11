"""Build the portable Windows package (DeskLink-Windows.zip) on any OS — no Windows machine needed.

The result is a folder with DeskLink.exe (a tiny launcher), the app, and its own Python runtime,
so nothing needs to be installed on the PC:

    DeskLink/
      DeskLink.exe      starts runtime\\pythonw.exe app\\DeskLink.pyw
      app/                DeskLink source
      runtime/            Python 3.12 for Windows + PySide6, cryptography, psutil, segno, winrt (text reader)
      README.txt

Usage:
    pip install ziglang          # C compiler used for the launcher
    python windows/build_portable.py [--out windows/dist] [--cache ~/.cache/desklink-build]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PBS_TAG = "20260718"
PBS_PYTHON = "3.12.13"
PBS_FILE = f"cpython-{PBS_PYTHON}%2B{PBS_TAG}-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
PBS_URLS = [
    f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS_TAG}/{PBS_FILE}",
    f"https://releases.astral.sh/github/python-build-standalone/releases/download/{PBS_TAG}/{PBS_FILE}",
]

# Only what DeskLink uses from Qt; everything else (QML, Quick, Designer, tools, translations…) is dropped.
QT_KEEP_FILES = {
    "__init__.py", "_config.py", "_git_pyside_version.py", "py.typed",
    "QtCore.pyd", "QtGui.pyd", "QtWidgets.pyd", "QtNetwork.pyd",
    "Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Network.dll", "Qt6Svg.dll", "pyside6.abi3.dll",
    "concrt140.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_codecvt_ids.dll",
    "vcruntime140.dll", "vcruntime140_1.dll",
}
QT_KEEP_DIRS = {"plugins", "support"}
QT_KEEP_PLUGINS = {"platforms", "styles", "imageformats", "iconengines", "tls", "networkinformation", "generic"}
QT_DROP_PLUGIN_FILES = {"imageformats/qpdf.dll", "tls/qopensslbackend.dll"}
RUNTIME_DROP = ["tcl", "include", "libs", "Scripts", "Lib/tkinter", "Lib/idlelib", "Lib/turtledemo", "Lib/test",
                "Lib/ensurepip", "Lib/lib2to3", "Lib/turtle.py", "DLLs/_tkinter.pyd", "DLLs/tcl86t.dll",
                "DLLs/tk86t.dll"]

README = """DeskLink {version} for Windows
=================================

1. Keep this whole folder together (for example in Documents\\DeskLink).
2. Double-click DeskLink.exe.
3. When Windows Firewall asks, tick "Private networks" and click Allow
   (it may show as "pythonw.exe" — that's DeskLink's built-in runtime).
4. Install DeskLink.apk on your phone, then click "Pair phone" here.

Tip: drag a photo onto DeskLink.exe to open it straight in Photo Fix.

If SmartScreen says "Windows protected your PC": More info -> Run anyway.

Guide: https://github.com/israk404/desklink#readme
"""


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def download(urls: list[str], dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    for url in urls:
        try:
            log(f"downloading {url}")
            with urllib.request.urlopen(url, timeout=300) as r, open(dest.with_suffix(".part"), "wb") as f:
                shutil.copyfileobj(r, f)
            dest.with_suffix(".part").replace(dest)
            return dest
        except Exception as exc:  # try the next mirror
            log(f"  failed: {exc}")
    sys.exit("could not download the Windows Python runtime")


def safe_extract_tar(archive: Path, target: Path) -> None:
    with tarfile.open(archive) as tar:
        for m in tar.getmembers():
            if m.name.startswith("/") or ".." in Path(m.name).parts:
                sys.exit(f"unsafe path in archive: {m.name}")
        tar.extractall(target, filter="data")


def safe_extract_zip(archive: Path, target: Path) -> None:
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            if name.startswith("/") or ".." in Path(name).parts:
                sys.exit(f"unsafe path in wheel: {name}")
        z.extractall(target)


def prune_qt(qt: Path) -> None:
    for item in qt.iterdir():
        if item.is_dir():
            if item.name not in QT_KEEP_DIRS:
                shutil.rmtree(item)
        elif item.name not in QT_KEEP_FILES:
            item.unlink()
    plugins = qt / "plugins"
    for item in plugins.iterdir():
        if item.name not in QT_KEEP_PLUGINS:
            shutil.rmtree(item)
    for rel in QT_DROP_PLUGIN_FILES:
        (plugins / rel).unlink(missing_ok=True)


def build_launcher(out_exe: Path) -> None:
    src = HERE / "launcher"
    cmd = [sys.executable, "-m", "ziglang", "build-exe", "launcher.c", "launcher.rc",
           "-target", "x86_64-windows-gnu", "-lc", "-O", "ReleaseSmall", "--subsystem", "windows",
           f"-femit-bin={out_exe}"]
    log("compiling DeskLink.exe launcher")
    subprocess.run(cmd, cwd=src, check=True)
    for junk in out_exe.parent.glob("DeskLink.pdb"):
        junk.unlink()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "dist"))
    ap.add_argument("--cache", default=str(Path.home() / ".cache" / "desklink-build"))
    args = ap.parse_args()
    out, cache = Path(args.out), Path(args.cache)
    sys.path.insert(0, str(HERE))
    from desklink import __version__ as version

    stage = out / "stage"
    root = stage / "DeskLink"
    shutil.rmtree(stage, ignore_errors=True)
    root.mkdir(parents=True)

    runtime_archive = download(PBS_URLS, cache / PBS_FILE.replace("%2B", "+"))
    log("unpacking Python runtime")
    safe_extract_tar(runtime_archive, stage)
    (stage / "python").rename(root / "runtime")
    for rel in RUNTIME_DROP:
        p = root / "runtime" / rel
        shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)

    wheels = cache / "wheels"
    log("downloading Windows wheels")
    subprocess.run([sys.executable, "-m", "pip", "download", "-q", "--only-binary=:all:", "--platform", "win_amd64",
                    "--python-version", "3.12", "--implementation", "cp", "-d", str(wheels),
                    "-r", str(HERE / "requirements.txt"), "-r", str(HERE / "requirements-windows.txt")], check=True)
    site = root / "runtime" / "Lib" / "site-packages"
    for whl in sorted(wheels.glob("*.whl")):
        if whl.name.startswith("pip-"):
            continue
        safe_extract_zip(whl, site)
    for p in list(site.glob("pip")) + list(site.glob("pip-*.dist-info")):
        shutil.rmtree(p)
    prune_qt(site / "PySide6")
    for pyi in site.rglob("*.pyi"):
        pyi.unlink()

    log("copying the app")
    app = root / "app"
    shutil.copytree(HERE / "desklink", app / "desklink", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(HERE / "DeskLink.pyw", app / "DeskLink.pyw")
    (root / "README.txt").write_text(README.format(version=version).replace("\n", "\r\n"), "utf-8")
    build_launcher(root / "DeskLink.exe")

    for cache_dir in root.rglob("__pycache__"):
        shutil.rmtree(cache_dir)
    zip_path = out / "DeskLink-Windows.zip"
    log(f"zipping {zip_path}")
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(root.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(stage).as_posix())
    shutil.copy2(root / "DeskLink.exe", out / "DeskLink.exe")
    shutil.rmtree(stage)
    log(f"done: {zip_path} ({zip_path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
