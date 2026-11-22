#!/usr/bin/env python3
"""
Fortnite Hit Tracker  (v3 - built-in replay reader, expanded stats)
====================================================
Reads Fortnite replay files (.replay) from your Demos folder and logs your
per-match stats - most importantly your ACCURACY (hit percentage) - to a CSV
file, then prints a report: overall average, last 10 matches, and trend.

Fully independent of Fortnite: it never touches the running game, it only
reads replay files Fortnite already saved. Safe by design.

v2 note: the reader is built in (no external library). It finds the match
stats event inside the replay, decrypts it with the key stored in the
replay's own header (pure-Python AES), and reads the same numbers the
end-of-match screen shows.

Usage:
    python HitTracker.py            Scan for new replays, log them, show report
    python HitTracker.py --watch    Keep running; log each match as it finishes
    python HitTracker.py --report   Just show the report from the existing log
    python HitTracker.py --dir PATH Use a different replay folder

Data lives next to this script:
    hit-log.csv       one row per match (the permanent record)
    processed.json    which replay files have already been logged
    report.txt        the latest report, as text
"""

import argparse
import csv
import json
import os
import re
import struct
import sys
import time
from datetime import datetime

if getattr(sys, "frozen", False):
    # Running as a PyInstaller .exe: keep the data files next to the .exe
    # itself, not in the temp folder the .exe unpacks into at runtime.
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_CSV = os.path.join(APP_DIR, "hit-log.csv")
STATE_JSON = os.path.join(APP_DIR, "processed.json")
REPORT_TXT = os.path.join(APP_DIR, "report.txt")
PARSER_VERSION = 3

CSV_FIELDS = [
    "date", "mode", "accuracy_pct", "eliminations", "assists",
    "damage_to_players", "placement", "total_players",
    "match_minutes", "replay_file",
    "weapon_damage", "other_damage", "damage_taken", "revives",
]

# ---------------------------------------------------------------------------
# Pure-Python AES (decrypt only, ECB) - just enough to read replay events.
# Tables are generated from the GF(2^8) math, not copied blobs.
# ---------------------------------------------------------------------------

def _gf_mul(a, b):
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _gf_pow(a, n):
    r = 1
    while n:
        if n & 1:
            r = _gf_mul(r, a)
        a = _gf_mul(a, a)
        n >>= 1
    return r


def _rotl8(x, n):
    return ((x << n) | (x >> (8 - n))) & 0xFF


def _build_sboxes():
    sbox = [0] * 256
    for x in range(256):
        inv = 0 if x == 0 else _gf_pow(x, 254)
        sbox[x] = (inv ^ _rotl8(inv, 1) ^ _rotl8(inv, 2) ^ _rotl8(inv, 3)
                   ^ _rotl8(inv, 4) ^ 0x63)
    inv_sbox = [0] * 256
    for i, v in enumerate(sbox):
        inv_sbox[v] = i
    return sbox, inv_sbox


_SBOX, _INV_SBOX = _build_sboxes()
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36,
         0x6C, 0xD8, 0xAB, 0x4D]


def _key_expansion(key):
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        temp = list(w[i - 1])
        if i % nk == 0:
            temp = [_SBOX[temp[1]] ^ _RCON[i // nk - 1],
                    _SBOX[temp[2]], _SBOX[temp[3]], _SBOX[temp[0]]]
        elif nk > 6 and i % nk == 4:
            temp = [_SBOX[c] for c in temp]
        w.append([w[i - nk][j] ^ temp[j] for j in range(4)])
    return w, nr


def _add_round_key(state, w, rnd):
    for c in range(4):
        for r in range(4):
            state[r][c] ^= w[4 * rnd + c][r]


def _inv_shift_rows(state):
    for r in range(1, 4):
        row = [state[r][c] for c in range(4)]
        for c in range(4):
            state[r][c] = row[(c - r) % 4]


def _inv_sub_bytes(state):
    for r in range(4):
        for c in range(4):
            state[r][c] = _INV_SBOX[state[r][c]]


def _inv_mix_columns(state):
    for c in range(4):
        a = [state[r][c] for r in range(4)]
        state[0][c] = (_gf_mul(a[0], 14) ^ _gf_mul(a[1], 11)
                       ^ _gf_mul(a[2], 13) ^ _gf_mul(a[3], 9))
        state[1][c] = (_gf_mul(a[0], 9) ^ _gf_mul(a[1], 14)
                       ^ _gf_mul(a[2], 11) ^ _gf_mul(a[3], 13))
        state[2][c] = (_gf_mul(a[0], 13) ^ _gf_mul(a[1], 9)
                       ^ _gf_mul(a[2], 14) ^ _gf_mul(a[3], 11))
        state[3][c] = (_gf_mul(a[0], 11) ^ _gf_mul(a[1], 13)
                       ^ _gf_mul(a[2], 9) ^ _gf_mul(a[3], 14))


def aes_ecb_decrypt(key, data):
    """Decrypt AES-ECB (128/192/256-bit key). Data length must be /16."""
    w, nr = _key_expansion(key)
    out = bytearray()
    for off in range(0, len(data), 16):
        block = data[off:off + 16]
        state = [[block[4 * c + r] for c in range(4)] for r in range(4)]
        _add_round_key(state, w, nr)
        for rnd in range(nr - 1, 0, -1):
            _inv_shift_rows(state)
            _inv_sub_bytes(state)
            _add_round_key(state, w, rnd)
            _inv_mix_columns(state)
        _inv_shift_rows(state)
        _inv_sub_bytes(state)
        _add_round_key(state, w, 0)
        for c in range(4):
            for r in range(4):
                out.append(state[r][c])
    return bytes(out)


# ---------------------------------------------------------------------------
# Replay reading
# ---------------------------------------------------------------------------

def _candidate_keys(data):
    """Encryption keys are stored length-prefixed in the replay header.
    Yield plausible keys from the first chunk of the file."""
    head = data[:8192]
    seen = set()
    for off in range(0, len(head) - 4):
        n = struct.unpack_from("<I", head, off)[0]
        if n in (16, 24, 32):
            key = head[off + 4:off + 4 + n]
            if len(key) == n and key not in seen and any(key):
                seen.add(key)
                yield key


def _find_event_buffer(data, name):
    """Locate an event by its metadata string; return its raw (encrypted)
    buffer using the [start][end][size] fields that follow the string."""
    needle = name.encode() + b"\x00"
    start = 0
    while True:
        idx = data.find(needle, start)
        if idx == -1:
            return None
        after = idx + len(needle)
        if after + 12 <= len(data):
            _t0, _t1, size = struct.unpack_from("<III", data, after)
            if 0 < size <= 8192 and size % 16 == 0 and after + 12 + size <= len(data):
                return data[after + 12:after + 12 + size]
        start = idx + 1


def _plausible_stats(pt):
    if len(pt) < 52:
        return False
    acc = struct.unpack_from("<f", pt, 4)[0]
    vals = struct.unpack_from("<10I", pt, 8)
    return (0.0 <= acc <= 100.0
            and all(v <= 2_000_000 for v in vals[:9])
            and vals[1] <= 1000)  # eliminations


def parse_replay(path):
    """Return (row dict, None) on success or (None, error string)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        return None, f"could not open file: {exc}"
    if len(data) < 100 or struct.unpack_from("<I", data, 0)[0] != 0x1CA2E27F:
        return None, "not a Fortnite replay file"

    # The replay header stores the recording length in milliseconds
    # (offset 32 in the v7 layout). Sanity-checked to a plausible match
    # length; anything outside that range is treated as unknown.
    match_minutes = ""
    raw_ms = struct.unpack_from("<I", data, 32)[0]
    if 60_000 <= raw_ms <= 2_700_000:
        match_minutes = round(raw_ms / 60000.0, 1)

    stats_buf = _find_event_buffer(data, "AthenaMatchStats")
    if stats_buf is None:
        return None, "no match-stats event found (menu/creative replay?)"
    team_buf = _find_event_buffer(data, "AthenaMatchTeamStats")

    stats_pt = None
    used_key = None
    for key in _candidate_keys(data):
        try:
            pt = aes_ecb_decrypt(key, stats_buf)
        except Exception:
            continue
        if _plausible_stats(pt):
            stats_pt, used_key = pt, key
            break
    if stats_pt is None:
        return None, "could not decrypt the match stats"

    acc = struct.unpack_from("<f", stats_pt, 4)[0]
    if acc <= 1.0:
        acc *= 100.0
    (assists, elims, weapon_dmg, other_dmg, revives, dmg_taken,
     _dmg_struct, _mats_g, _mats_u, _travelled) = struct.unpack_from(
        "<10I", stats_pt, 8)

    placement, total_players = "", ""
    if team_buf is not None:
        try:
            tpt = aes_ecb_decrypt(used_key, team_buf)
            placement, total_players = struct.unpack_from("<II", tpt, 4)
            if not (0 < placement <= total_players <= 100000):
                placement, total_players = "", ""
        except Exception:
            placement, total_players = "", ""

    m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})[-_](\d{2})\.(\d{2})\.(\d{2})",
                  os.path.basename(path))
    if m:
        y, mo, d, h, mi, s = (int(x) for x in m.groups())
        date_str = f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}"
    else:
        date_str = datetime.fromtimestamp(
            os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")

    row = {
        "date": date_str,
        "mode": "Battle Royale",
        "accuracy_pct": round(acc, 1),
        "eliminations": elims,
        "assists": assists,
        "damage_to_players": weapon_dmg + other_dmg,
        "placement": placement,
        "total_players": total_players,
        "match_minutes": match_minutes,
        "replay_file": os.path.basename(path),
        "weapon_damage": weapon_dmg,
        "other_damage": other_dmg,
        "damage_taken": dmg_taken,
        "revives": revives,
    }
    return row, None


# ---------------------------------------------------------------------------
# Log + state
# ---------------------------------------------------------------------------


def load_state():
    if os.path.exists(STATE_JSON):
        try:
            with open(STATE_JSON, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            return {}
        # New reader version: give previously-failed files another chance.
        if state.get("_parser_version") != PARSER_VERSION:
            state = {k: v for k, v in state.items()
                     if not (isinstance(v, dict) and v.get("status") == "failed")}
            state["_parser_version"] = PARSER_VERSION
        return state
    return {"_parser_version": PARSER_VERSION}


def save_state(state):
    state["_parser_version"] = PARSER_VERSION
    with open(STATE_JSON, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)


def _clean_row(r):
    return {k: (r.get(k) if r.get(k) is not None else "")
            for k in CSV_FIELDS}


def rewrite_log(rows):
    with open(LOG_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(_clean_row(r))


def ensure_log_schema():
    """If hit-log.csv predates the current columns, rewrite it with the
    new header, keeping every existing row (new fields left blank)."""
    if not os.path.exists(LOG_CSV):
        return
    with open(LOG_CSV, newline="", encoding="utf-8") as f:
        first = f.readline().strip()
    if first == ",".join(CSV_FIELDS):
        return
    rewrite_log(read_log())


_BACKFILL_DONE = False


def backfill_rows(demos_dir):
    """Fill the v3 stat columns for matches logged before v3, using the
    replay files still on disk. A fill only happens when the re-parsed
    replay provably matches the logged row (same date, accuracy, elims).
    Runs once per program start so Watch mode doesn't repeat the work."""
    global _BACKFILL_DONE
    if _BACKFILL_DONE:
        return 0
    _BACKFILL_DONE = True
    rows = read_log()
    if not rows:
        return 0
    changed = 0
    for r in rows:
        if (str(r.get("weapon_damage") or "").strip() != ""
                and str(r.get("match_minutes") or "").strip() != ""):
            continue
        fname = r.get("replay_file") or ""
        if not fname:
            continue
        path = os.path.join(demos_dir, fname)
        if not os.path.exists(path):
            continue
        parsed, _err = parse_replay(path)
        if not parsed:
            continue
        try:
            if parsed["date"] != r.get("date"):
                continue
            if abs(float(parsed["accuracy_pct"])
                   - float(r["accuracy_pct"])) > 0.05:
                continue
            if int(parsed["eliminations"]) != int(r["eliminations"]):
                continue
        except (TypeError, ValueError):
            continue
        filled = False
        for k in ("weapon_damage", "other_damage", "damage_taken",
                  "revives", "match_minutes"):
            pv = parsed.get(k)
            if str(r.get(k) or "").strip() == "" \
                    and pv is not None and str(pv).strip() != "":
                r[k] = pv
                filled = True
        if filled:
            changed += 1
    if changed:
        rewrite_log(rows)
    return changed


def append_rows(rows):
    ensure_log_schema()
    exists = os.path.exists(LOG_CSV)
    with open(LOG_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not exists:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def read_log():
    if not os.path.exists(LOG_CSV):
        return []
    with open(LOG_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def default_demos_dir():
    override = os.environ.get("FORTNITE_DEMOS")
    if override:
        return override
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser(
        "~/AppData/Local"
    )
    return os.path.join(local, "FortniteGame", "Saved", "Demos")


def scan(demos_dir):
    """Parse any new replay files. Returns (new_rows, failures)."""
    state = load_state()
    new_rows, failures = [], []
    if not os.path.isdir(demos_dir):
        return new_rows, failures

    files = sorted(
        (os.path.join(demos_dir, n) for n in os.listdir(demos_dir)
         if n.lower().endswith(".replay")),
        key=os.path.getmtime,
    )
    now = time.time()
    for path in files:
        name = os.path.basename(path)
        try:
            size = os.path.getsize(path)
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        prev = state.get(name)
        if isinstance(prev, dict) and prev.get("size") == size:
            continue  # already handled at this size
        if now - mtime < 90:
            continue  # probably still being written (match in progress)
        row, err = parse_replay(path)
        if row:
            new_rows.append(row)
            state[name] = {"size": size, "status": "ok"}
        else:
            failures.append((name, err))
            state[name] = {"size": size, "status": "failed"}
    if new_rows:
        append_rows(new_rows)
    save_state(state)
    return new_rows, failures


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _num(r, key):
    try:
        return float(r.get(key))
    except (TypeError, ValueError):
        return None


def fmt_row(r):
    acc = float(r["accuracy_pct"])
    place = ""
    if r.get("placement") not in (None, ""):
        place = f"  #{r['placement']}"
        if r.get("total_players"):
            place += f"/{r['total_players']}"
    ast = _num(r, "assists")
    ast_s = f"  ast {int(ast):>2}" if ast is not None else ""
    taken = _num(r, "damage_taken")
    taken_s = f"  taken {int(taken):>5}" if taken is not None else ""
    mins = _num(r, "match_minutes")
    mins_s = f"  {mins:.0f} min" if mins is not None else ""
    return (
        f"{r['date']}  {r['mode'][:26]:<26}  acc {acc:>5.1f}%  "
        f"elims {r['eliminations']:>2}{ast_s}  "
        f"dmg {r['damage_to_players']:>5}{taken_s}{place}{mins_s}"
    )


def build_report(rows):
    lines = []
    lines.append("=" * 68)
    lines.append("  FORTNITE HIT TRACKER - your accuracy history")
    lines.append("=" * 68)
    if not rows:
        lines.append("  No matches logged yet.")
        lines.append("  Play a match with Fortnite replays turned ON, then run")
        lines.append("  this again - your accuracy will appear here.")
        return "\n".join(lines)

    accs = [float(r["accuracy_pct"]) for r in rows]
    elims = [int(r["eliminations"]) for r in rows]
    lines.append(f"  Matches logged : {len(rows)}")
    lines.append(f"  Average accuracy: {sum(accs) / len(accs):.1f}%")
    lines.append(f"  Best accuracy   : {max(accs):.1f}%")
    lines.append(f"  Avg eliminations: {sum(elims) / len(elims):.1f} per match")

    assists = [v for v in (_num(r, "assists") for r in rows)
               if v is not None]
    if assists:
        lines.append(
            f"  Avg assists     : {sum(assists) / len(assists):.1f} per match")

    places = [int(v) for v in (_num(r, "placement") for r in rows)
              if v is not None]
    if places:
        wins = sum(1 for p in places if p == 1)
        top10 = sum(1 for p in places if p <= 10)
        lines.append(
            f"  Wins            : {wins} "
            f"({100.0 * wins / len(places):.1f}% of matches)")
        lines.append(
            f"  Top 10 finishes : {top10} "
            f"({100.0 * top10 / len(places):.1f}%)")
        lines.append(
            f"  Avg placement   : #{sum(places) / len(places):.1f}")

    dmgs = [int(r["damage_to_players"]) for r in rows]
    total_dmg = sum(dmgs)
    total_elims = sum(elims)
    lines.append(f"  Avg damage      : {total_dmg / len(dmgs):.0f} per match")
    if total_elims:
        lines.append(
            f"  Damage per elim : {total_dmg / total_elims:.0f}")
    best_dmg_row = max(rows, key=lambda r: int(r["damage_to_players"]))
    lines.append(
        f"  Best damage game: {int(best_dmg_row['damage_to_players'])} "
        f"({best_dmg_row['date']})")
    most_elims_row = max(rows, key=lambda r: int(r["eliminations"]))
    lines.append(
        f"  Most elims game : {int(most_elims_row['eliminations'])} "
        f"({most_elims_row['date']})")

    v3rows = [r for r in rows if _num(r, "damage_taken") is not None]
    if v3rows:
        taken = [_num(r, "damage_taken") for r in v3rows]
        lines.append(
            f"  Avg damage taken: {sum(taken) / len(taken):.0f} per match "
            f"(from {len(v3rows)} match"
            f"{'es' if len(v3rows) != 1 else ''} with full stats)")
        wd = [_num(r, "weapon_damage") for r in v3rows]
        od = [_num(r, "other_damage") for r in v3rows]
        if all(v is not None for v in wd + od):
            lines.append(
                f"  Damage split    : weapon {sum(wd) / len(wd):.0f} / "
                f"other {sum(od) / len(od):.0f} per match")
        rev = [_num(r, "revives") for r in v3rows]
        if all(v is not None for v in rev):
            lines.append(f"  Revives (total) : {int(sum(rev))}")

    timed = [r for r in rows if _num(r, "match_minutes") is not None]
    if timed:
        mins = [_num(r, "match_minutes") for r in timed]
        total_min = sum(mins)
        lines.append(
            f"  Avg match length: {total_min / len(mins):.1f} min "
            f"({len(timed)} match{'es' if len(timed) != 1 else ''} timed)")
        lines.append(
            f"  Total time played: {int(total_min // 60)}h "
            f"{int(total_min % 60):02d}m")
        longest = max(timed, key=lambda r: _num(r, "match_minutes"))
        lines.append(
            f"  Longest match   : {_num(longest, 'match_minutes'):.1f} min "
            f"({longest['date']})")

    if len(rows) >= 20:
        prev10 = accs[-20:-10]
        last10 = accs[-10:]
        delta = sum(last10) / 10 - sum(prev10) / 10
        arrow = "UP" if delta > 0.5 else ("DOWN" if delta < -0.5 else "FLAT")
        lines.append(
            f"  Trend           : last 10 matches {arrow} "
            f"({sum(last10) / 10:.1f}% vs {sum(prev10) / 10:.1f}% before)"
        )

    days = {}
    for r in rows:
        days.setdefault(str(r["date"])[:10], []).append(r)
    if len(days) > 1:
        lines.append("")
        lines.append("  By day (most recent 7 days played):")
        for day in sorted(days)[-7:]:
            drows = days[day]
            daccs = [float(r["accuracy_pct"]) for r in drows]
            delims = sum(int(r["eliminations"]) for r in drows)
            dwins = sum(1 for r in drows
                        if _num(r, "placement") == 1)
            lines.append(
                f"   {day}: {len(drows)} matches, "
                f"acc {sum(daccs) / len(daccs):.1f}%, "
                f"elims {delims}, wins {dwins}")

    lines.append("")
    lines.append("  Last 10 matches:")
    for r in rows[-10:]:
        lines.append("   " + fmt_row(r))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description="Fortnite Hit Tracker")
    ap.add_argument("--watch", action="store_true",
                    help="keep running and log matches as they finish")
    ap.add_argument("--report", action="store_true",
                    help="show the report without scanning")
    ap.add_argument("--dir", default=None, help="replay folder override")
    args = ap.parse_args()

    demos_dir = args.dir or default_demos_dir()

    if not args.report:
        print(f"Replay folder: {demos_dir}")
        if not os.path.isdir(demos_dir):
            print()
            print("That folder doesn't exist yet. It gets created once")
            print("Fortnite saves a replay. Check in Fortnite:")
            print("  Settings > Game > Replays > Record Replays = ON")
            print("Then play a match and run this again.")
            return

        def do_scan():
            filled = backfill_rows(demos_dir)
            if filled:
                print(f"  (filled in extra stats for {filled} earlier "
                      f"match(es) from their replay files)")
            new_rows, failures = scan(demos_dir)
            for r in new_rows:
                print("  New match logged: " + fmt_row(r))
            if not new_rows:
                print("  No new completed matches found.")
            if failures:
                print(f"  ({len(failures)} replay file(s) couldn't be read.")
                print("   First one: " + failures[0][0] + ")")
            return new_rows

        if args.watch:
            print("Watching for finished matches... (close window to stop)")
            while True:
                do_scan()
                time.sleep(60)
        else:
            do_scan()

    report = build_report(read_log())
    print()
    print(report)
    with open(REPORT_TXT, "w", encoding="utf-8") as f:
        f.write(report + "\n")


if __name__ == "__main__":
    main()
    try:
        input("\nPress Enter to close...")
    except EOFError:
        pass
