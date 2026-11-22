
# Fortnite Hit Tracker

Reads your Fortnite replay files and logs your per-match stats — most
importantly your **accuracy (hit percentage)** — to a permanent CSV log,
then shows you a report: overall average, last 10 matches, trend, win
rate, damage splits, and more.

Every match you play saves a replay file on your PC (if replays are on).
Those files contain the same end-of-match stats the game shows you. This
tool reads them with a built-in replay reader (pure Python, no external
libraries) and keeps a log that survives even after Fortnite deletes old
replays.

## Download & use (no Python needed)

1. Go to the **Releases** page of this repo and download
   `FortniteHitTracker.exe`.
2. Put it in any folder you like (its data files are created next to it).
3. In Fortnite: **Settings → Game → Replays → Record Replays = ON**
   (usually already on by default on PC).
4. Play matches as normal, then double-click the exe any time to scan
   for new matches and see your report.

Optional ways to run it (from a command prompt):

```
FortniteHitTracker.exe            Scan for new replays, log them, show report
FortniteHitTracker.exe --watch    Keep running; log each match as it finishes
FortniteHitTracker.exe --report   Just show the report from the existing log
FortniteHitTracker.exe --dir PATH Use a different replay folder

Data files, created next to the exe:

- `hit-log.csv` — one row per match (your permanent record)
- `processed.json` — which replay files have already been logged
- `report.txt` — the latest report, as text

## Privacy & safety

- Fully independent of Fortnite: nothing runs inside the game, nothing
  injects, nothing connects to Epic's servers or anywhere else. It has
  **no network code at all** — it only reads replay files already on
  your disk.
- It only reads a replay *after* a match has finished writing it, so it
  cannot cause lag.

## Windows SmartScreen note

The exe is unsigned (code-signing certificates cost money), so Windows
may warn *"Windows protected your PC"* the first time you run it. Click
**More info → Run anyway**. PCs with Smart App Control switched on may
block unsigned apps entirely; building from source (below) is the
alternative there.

## Build from source

Requires Python 3 on Windows. Either double-click **`Build-Exe.bat`**
(it installs PyInstaller and builds), or run:

```
pip install pyinstaller
pyinstaller --onefile --name FortniteHitTracker HitTracker.py
```

The exe lands in the `dist` folder. You can also run it without
building: `python HitTracker.py` — it uses only the Python standard
library.

## Honest limits

- If Epic changes the replay format in a future season, some replay
  files may show as "couldn't be read" until the reader catches up.
  Your existing log is never affected.
- Accuracy comes straight from the game's own match stats — the same
  number as the end-of-match screen.
- Windows only (it reads the default `%LOCALAPPDATA%` replay folder;
  `--dir` can point it anywhere).

Unofficial fan-made tool. Not affiliated with or endorsed by Epic Games, Inc. Fortnite is a trademark of Epic Games, Inc.
