"""Add PartForge to the Start Menu and Desktop (no console window).

    python install.py              install shortcuts
    python install.py --uninstall  remove them
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "PartForge"


def folders(dest=None):
    if dest:
        return [Path(dest)]
    ps = "[Environment]::GetFolderPath('Desktop'); [Environment]::GetFolderPath('Programs')"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.split()
    return [Path(p) for p in out if p]


def install(dest=None):
    if sys.version_info < (3, 10):
        sys.exit("PartForge needs Python 3.10 or newer.")
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        sys.exit(f"pythonw.exe not found next to {sys.executable}")
    for folder in folders(dest):
        lnk = folder / f"{NAME}.lnk"
        ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:PF_LNK);"
              "$s.TargetPath=$env:PF_TARGET;$s.Arguments=$env:PF_ARGS;$s.WorkingDirectory=$env:PF_DIR;"
              "$s.IconLocation=$env:PF_ICON;$s.Description='Prompt-engineer parts with a local AI and FreeCAD';$s.Save()")
        env = {**os.environ, "PF_LNK": str(lnk), "PF_TARGET": str(pythonw), "PF_ARGS": f'"{HERE / "PartForge.pyw"}"',
               "PF_DIR": str(HERE), "PF_ICON": str(HERE / "icon.ico")}
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, check=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print("created", lnk)


def uninstall(dest=None):
    for folder in folders(dest):
        lnk = folder / f"{NAME}.lnk"
        if lnk.exists():
            lnk.unlink()
            print("removed", lnk)


if __name__ == "__main__":
    dest = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--dest=")), None)   # for testing
    (uninstall if "--uninstall" in sys.argv else install)(dest)
