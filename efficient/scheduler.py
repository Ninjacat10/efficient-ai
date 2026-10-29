"""Windows Task Scheduler + autostart integration.

The nightly task wakes the PC from sleep, only runs on AC power, and runs
`EfficientAI.exe nightly` (or `pythonw -m efficient nightly` from source).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import winreg
from pathlib import Path

TASK_NAME = "EfficientAI Nightly"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
FROZEN = getattr(sys, "frozen", False)  # running as the installed EfficientAI.exe
PROJECT_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent.parent
ICON = (Path(getattr(sys, "_MEIPASS", PROJECT_DIR)) / "assets" / "efficient.ico")


def _pythonw() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def launcher(command: str) -> tuple[str, str]:
    """(program, arguments) that run an Efficient AI command with no console window."""
    if FROZEN:
        return sys.executable, command
    return _pythonw(), f"-m efficient {command}"


TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Efficient AI: nightly knowledge build and morning suggestions.</Description></RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T{time}:00</StartBoundary>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <WakeToRun>true</WakeToRun>
    <DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python}</Command>
      <Arguments>{args}</Arguments>
      <WorkingDirectory>{cwd}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""


def install_nightly(time: str = "03:00") -> None:
    exe, args = launcher("nightly")
    xml = TASK_XML.format(time=time, python=exe, args=args, cwd=PROJECT_DIR)
    path = Path(tempfile.gettempdir()) / "efficient_task.xml"
    path.write_text(xml, encoding="utf-16")
    subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(path), "/F"], check=True)
    path.unlink(missing_ok=True)
    # Wake timers are often disabled on the active power plan; enable them for AC only.
    subprocess.run(["powercfg", "/SETACVALUEINDEX", "SCHEME_CURRENT", "SUB_SLEEP", "RTCWAKE", "1"], check=False)
    subprocess.run(["powercfg", "/SETACTIVE", "SCHEME_CURRENT"], check=False)


def uninstall_nightly() -> None:
    subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=False)


def create_shortcuts() -> list[str]:
    """Start Menu + Desktop shortcuts named "Efficient AI" that launch the tray app with no console window."""
    import os

    import win32com.client

    shell = win32com.client.Dispatch("WScript.Shell")
    made = []
    for folder in (Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs", Path(shell.SpecialFolders("Desktop"))):
        lnk = shell.CreateShortcut(str(folder / "Efficient AI.lnk"))
        lnk.TargetPath, lnk.Arguments = launcher("run")
        lnk.WorkingDirectory = str(PROJECT_DIR)
        lnk.Description = "Efficient AI"
        lnk.IconLocation = sys.executable if FROZEN else str(ICON)
        lnk.Save()
        made.append(str(folder / "Efficient AI.lnk"))
    return made


def set_autostart(on: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            exe, args = launcher("run")
            winreg.SetValueEx(k, "EfficientAI", 0, winreg.REG_SZ, f'"{exe}" {args}')
        else:
            try:
                winreg.DeleteValue(k, "EfficientAI")
            except FileNotFoundError:
                pass
