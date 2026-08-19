import os
import subprocess
import sys

root = r"d:\dev\L4DBoss"
hits = []
for dirpath, dirnames, filenames in os.walk(root):
    if "python.exe" in filenames:
        exe = os.path.join(dirpath, "python.exe")
        try:
            out = subprocess.run(
                [exe, "-c", "import PySide6; print(PySide6.__version__)"],
                capture_output=True, text=True, timeout=20,
            )
            if out.returncode == 0:
                hits.append((exe, out.stdout.strip()))
        except Exception:
            pass
print("HITS:", len(hits))
for exe, ver in hits:
    print(exe, ver)
