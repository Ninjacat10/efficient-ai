# PyInstaller build for EfficientAI.exe - run packaging\build.ps1 rather than calling this directly.
# One-folder build: starts fast and trips antivirus far less often than a single self-extracting .exe.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent

datas = [(str(ROOT / "assets"), "assets")]
binaries = []
hiddenimports = collect_submodules("efficient") + [
    "pystray._win32", "win32timezone", "pythoncom", "pywintypes", "win32com.client", "keyboard._winkeyboard",
]
for pkg in ("uiautomation", "comtypes", "winocr", "mss"):  # ship their DLLs and data files too
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["matplotlib", "numpy.tests", "tkinter.test", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EfficientAI",
    icon=str(ROOT / "assets" / "efficient.ico"),
    version=str(ROOT / "packaging" / "version.txt"),
    console=False,  # no black console window
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="EfficientAI", upx=False)
