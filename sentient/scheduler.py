"""Windows Task Scheduler + autostart integration.

The nightly task wakes the PC from sleep, only runs on AC power, and runs
`pythonw -m sentient nightly` (knowledge build + proactive cards).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import winreg
from pathlib import Path

TASK_NAME = "SentientWin Nightly"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
PROJECT_DIR = Path(__file__).resolve().parent.parent


def _pythonw() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Sentient for Windows: nightly knowledge build and morning suggestions.</Description></RegistrationInfo>
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
      <Arguments>-m sentient nightly</Arguments>
      <WorkingDirectory>{cwd}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""


def install_nightly(time: str = "03:00") -> None:
    xml = TASK_XML.format(time=time, python=_pythonw(), cwd=PROJECT_DIR)
    path = Path(tempfile.gettempdir()) / "sentient_task.xml"
    path.write_text(xml, encoding="utf-16")
    subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(path), "/F"], check=True)
    path.unlink(missing_ok=True)
    # Wake timers are often disabled on the active power plan; enable them for AC only.
    subprocess.run(["powercfg", "/SETACVALUEINDEX", "SCHEME_CURRENT", "SUB_SLEEP", "RTCWAKE", "1"], check=False)
    subprocess.run(["powercfg", "/SETACTIVE", "SCHEME_CURRENT"], check=False)


def uninstall_nightly() -> None:
    subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=False)


def set_autostart(on: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "SentientWin", 0, winreg.REG_SZ, f'"{_pythonw()}" -m sentient run')
        else:
            try:
                winreg.DeleteValue(k, "SentientWin")
            except FileNotFoundError:
                pass
