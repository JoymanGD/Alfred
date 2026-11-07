import json
import subprocess


def list_running_windows() -> list[dict[str, str | int]]:
    """Return the current visible windows without an application name dictionary."""
    script = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "Get-Process | Where-Object { $_.MainWindowTitle } | "
        "Select-Object Id,ProcessName,MainWindowTitle,Path | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(rows, dict):
        rows = [rows]
    windows = []
    for row in rows:
        try:
            pid = int(row["Id"])
            name = str(row["ProcessName"] or "").strip()
            title = str(row["MainWindowTitle"] or "").strip()
        except (KeyError, TypeError, ValueError):
            continue
        if pid > 0 and name and title:
            windows.append({"pid": pid, "name": name, "title": title, "path": str(row.get("Path") or "")})
    return windows
