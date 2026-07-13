#!/usr/bin/env python3
"""
xui_dedupe_shows.py - Remove duplicate TV shows (series) from an XUI.one /
Xtream Codes panel and write a text report of every show that had duplicates.

How it works
------------
The script connects directly to the panel's MySQL/MariaDB database, groups
series by their (normalized) title, and for every group with more than one
entry it keeps the "best" copy and deletes the rest, including their episode
rows and the underlying episode streams.

By default the "best" copy is the one with the most episodes (ties broken by
the lowest/oldest series id).

SAFE BY DEFAULT: without --apply the script only performs a DRY RUN. It scans,
prints what it *would* delete, and still writes the report file - nothing is
modified. Re-run with --apply to actually delete.

A text report (duplicate_shows_<timestamp>.txt by default) is always written.
It contains a plain list of all show titles that had duplicates, followed by
a detailed breakdown of what was kept and what was deleted.

Supported panels
----------------
* XUI.one        (tables: series / episodes,        database: xui)
* Xtream Codes 2 (tables: streams_series / streams_episodes,
                  database: xtream_iptvpro)
The table names are auto-detected at runtime.

Requirements
------------
    pip install pymysql        (or mysql-connector-python)

Usage examples
--------------
    # Dry run (recommended first) - auto-read DB credentials from the panel
    python3 xui_dedupe_shows.py --auto-config

    # Dry run with explicit credentials
    python3 xui_dedupe_shows.py --password 'dbpass'

    # Actually delete the duplicates
    python3 xui_dedupe_shows.py --auto-config --apply

    # Only treat shows as duplicates within the same category
    python3 xui_dedupe_shows.py --auto-config --per-category

    # Looser matching: ignore punctuation and a trailing "(2008)" year tag
    python3 xui_dedupe_shows.py --auto-config --loose --strip-year

The database password can also be supplied via the XUI_DB_PASSWORD
environment variable instead of --password.

ALWAYS take a database backup before running with --apply, e.g.:
    mysqldump -h 127.0.0.1 -P 7999 -u user_iptvpro -p xui > xui_backup.sql
"""

import argparse
import base64
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

# ---------------------------------------------------------------------------
# MySQL driver: prefer PyMySQL, fall back to mysql-connector-python
# ---------------------------------------------------------------------------
_DRIVER = None
try:
    import pymysql as _mysql  # type: ignore

    _DRIVER = "pymysql"
except ImportError:
    try:
        import mysql.connector as _mysql  # type: ignore

        _DRIVER = "mysql.connector"
    except ImportError:
        pass

# XOR key used by Xtream Codes / XUI.one to encode their config file.
_XC_CONFIG_KEY = "5709650b0d7806074842c6de575025b1"
_DEFAULT_CONFIG_PATHS = (
    "/home/xui/config",
    "/home/xtreamcodes/iptv_xtream_codes/config",
)

CHUNK = 1000  # max ids per IN (...) clause


def parse_args():
    p = argparse.ArgumentParser(
        description="Delete duplicate TV shows from an XUI.one / Xtream Codes "
        "panel and write a text report of the shows that had duplicates.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--host", default="127.0.0.1", help="MySQL host")
    p.add_argument("--port", type=int, default=7999, help="MySQL port")
    p.add_argument("--user", default="user_iptvpro", help="MySQL user")
    p.add_argument(
        "--password",
        default=os.environ.get("XUI_DB_PASSWORD", ""),
        help="MySQL password (or set XUI_DB_PASSWORD env var)",
    )
    p.add_argument(
        "--database",
        default=None,
        help="Database name (default: auto-try 'xui' then 'xtream_iptvpro')",
    )
    p.add_argument(
        "--auto-config",
        nargs="?",
        const="auto",
        default=None,
        metavar="PATH",
        help="Read DB credentials from the panel's encoded config file "
        "(default paths: %s)" % ", ".join(_DEFAULT_CONFIG_PATHS),
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete duplicates. Without this flag the script only "
        "does a dry run (report is still written).",
    )
    p.add_argument(
        "--per-category",
        action="store_true",
        help="Only consider shows duplicates when they are in the same category",
    )
    p.add_argument(
        "--keep",
        choices=["most-episodes", "lowest-id", "highest-id"],
        default="most-episodes",
        help="Which copy of a duplicated show to keep",
    )
    p.add_argument(
        "--loose",
        action="store_true",
        help="Loose title matching: compare letters/digits only "
        "(ignores punctuation, spacing and case)",
    )
    p.add_argument(
        "--strip-year",
        action="store_true",
        help="Ignore a trailing year tag like '(2008)' or '[2008]' when comparing titles",
    )
    p.add_argument(
        "--report",
        default=None,
        metavar="FILE",
        help="Report file path (default: duplicate_shows_<timestamp>.txt)",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Credentials / connection
# ---------------------------------------------------------------------------
def decode_panel_config(path):
    """Decode the base64+XOR encoded XC/XUI config file into a dict."""
    with open(path, "rb") as fh:
        raw = base64.b64decode(fh.read())
    decoded = "".join(
        chr(byte ^ ord(_XC_CONFIG_KEY[i % len(_XC_CONFIG_KEY)]))
        for i, byte in enumerate(raw)
    )
    return json.loads(decoded)


def load_auto_config(arg_value):
    paths = _DEFAULT_CONFIG_PATHS if arg_value == "auto" else (arg_value,)
    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            cfg = decode_panel_config(path)
        except Exception as exc:  # noqa: BLE001 - report and try next path
            print(f"[!] Could not decode {path}: {exc}")
            continue
        print(f"[*] Loaded DB credentials from {path}")
        return {
            "host": cfg.get("host", "127.0.0.1"),
            "port": int(cfg.get("db_port", 7999)),
            "user": cfg.get("db_user", "user_iptvpro"),
            "password": cfg.get("db_pass", ""),
            "database": cfg.get("db_name"),
        }
    print("[!] No usable panel config file found; falling back to CLI options.")
    return {}


def connect(host, port, user, password, database):
    kwargs = dict(
        host=host, port=port, user=user, password=password, database=database
    )
    if _DRIVER == "pymysql":
        kwargs["charset"] = "utf8mb4"
        kwargs["autocommit"] = False
    conn = _mysql.connect(**kwargs)
    if _DRIVER == "mysql.connector":
        conn.autocommit = False
    return conn


def connect_with_fallback(args, auto):
    host = auto.get("host", args.host)
    port = auto.get("port", args.port)
    user = auto.get("user", args.user)
    password = auto.get("password") or args.password
    if not password:
        sys.exit(
            "[!] No database password. Use --password, XUI_DB_PASSWORD, "
            "or --auto-config."
        )
    databases = (
        [args.database]
        if args.database
        else [auto.get("database")] if auto.get("database") else ["xui", "xtream_iptvpro"]
    )
    last_err = None
    for db in databases:
        try:
            conn = connect(host, port, user, password, db)
            print(f"[*] Connected to {user}@{host}:{port}/{db}")
            return conn, db
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    sys.exit(f"[!] Could not connect to the database: {last_err}")


# ---------------------------------------------------------------------------
# Schema detection
# ---------------------------------------------------------------------------
def detect_tables(cur, database):
    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
        (database,),
    )
    tables = {row[0].lower() for row in cur.fetchall()}
    if "series" in tables:
        series_tbl = "series"
    elif "streams_series" in tables:
        series_tbl = "streams_series"
    else:
        sys.exit("[!] Could not find a series table (series / streams_series).")
    if "episodes" in tables:
        episodes_tbl = "episodes"
    elif "streams_episodes" in tables:
        episodes_tbl = "streams_episodes"
    else:
        sys.exit("[!] Could not find an episodes table (episodes / streams_episodes).")
    if "streams" not in tables:
        sys.exit("[!] Could not find the streams table.")
    print(f"[*] Using tables: {series_tbl}, {episodes_tbl}, streams")
    return series_tbl, episodes_tbl


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------
_YEAR_RE = re.compile(r"[\(\[]\s*(19|20)\d{2}\s*[\)\]]\s*$")
_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")
_WS_RE = re.compile(r"\s+")


def normalize_title(title, loose=False, strip_year=False):
    t = (title or "").strip()
    if strip_year:
        t = _YEAR_RE.sub("", t).strip()
    t = t.casefold()
    if loose:
        t = _NON_ALNUM_RE.sub("", t)
    else:
        t = _WS_RE.sub(" ", t)
    return t


def fetch_series(cur, series_tbl, episodes_tbl):
    cur.execute(
        f"SELECT s.id, s.title, s.category_id, COUNT(e.id) "
        f"FROM {series_tbl} s "
        f"LEFT JOIN {episodes_tbl} e ON e.series_id = s.id "
        f"GROUP BY s.id, s.title, s.category_id"
    )
    return [
        {"id": r[0], "title": r[1] or "", "category": r[2], "episodes": r[3]}
        for r in cur.fetchall()
    ]


def group_duplicates(series, args):
    groups = defaultdict(list)
    for s in series:
        key = normalize_title(s["title"], loose=args.loose, strip_year=args.strip_year)
        if not key:
            continue  # never group/delete series with empty titles
        if args.per_category:
            key = (key, str(s["category"]))
        groups[key].append(s)
    return {k: v for k, v in groups.items() if len(v) > 1}


def pick_keeper(entries, keep_mode):
    if keep_mode == "most-episodes":
        return max(entries, key=lambda s: (s["episodes"], -s["id"]))
    if keep_mode == "lowest-id":
        return min(entries, key=lambda s: s["id"])
    return max(entries, key=lambda s: s["id"])


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------
def chunked(seq, size=CHUNK):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def delete_series(cur, series_tbl, episodes_tbl, series_ids):
    """Delete series rows plus their episodes and episode streams.

    Returns (episodes_deleted, streams_deleted).
    """
    stream_ids = []
    for chunk in chunked(series_ids):
        marks = ",".join(["%s"] * len(chunk))
        cur.execute(
            f"SELECT stream_id FROM {episodes_tbl} WHERE series_id IN ({marks})",
            chunk,
        )
        stream_ids.extend(r[0] for r in cur.fetchall() if r[0] is not None)

    streams_deleted = 0
    for chunk in chunked(stream_ids):
        marks = ",".join(["%s"] * len(chunk))
        cur.execute(f"DELETE FROM streams WHERE id IN ({marks})", chunk)
        streams_deleted += cur.rowcount

    episodes_deleted = 0
    for chunk in chunked(series_ids):
        marks = ",".join(["%s"] * len(chunk))
        cur.execute(f"DELETE FROM {episodes_tbl} WHERE series_id IN ({marks})", chunk)
        episodes_deleted += cur.rowcount
        cur.execute(f"DELETE FROM {series_tbl} WHERE id IN ({marks})", chunk)

    return episodes_deleted, streams_deleted


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def write_report(path, dup_groups, plan, applied, totals):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    lines.append("XUI duplicate TV show report")
    lines.append(f"Generated : {now}")
    lines.append(f"Mode      : {'APPLIED (duplicates deleted)' if applied else 'DRY RUN (nothing deleted)'}")
    lines.append(f"Shows with duplicates : {len(dup_groups)}")
    lines.append(f"Duplicate series {'deleted' if applied else 'to delete'} : {totals['series']}")
    lines.append(f"Episodes {'deleted' if applied else 'to delete'}         : {totals['episodes']}")
    lines.append("")
    lines.append("=" * 70)
    lines.append("SHOWS THAT HAD DUPLICATES")
    lines.append("=" * 70)
    for title in sorted(plan, key=lambda x: x["display_title"].casefold()):
        lines.append(title["display_title"])
    lines.append("")
    lines.append("=" * 70)
    lines.append("DETAILS")
    lines.append("=" * 70)
    for item in sorted(plan, key=lambda x: x["display_title"].casefold()):
        lines.append("")
        lines.append(f"== {item['display_title']} ==")
        k = item["keeper"]
        lines.append(
            f"  KEPT     id={k['id']:<8} episodes={k['episodes']:<5} "
            f"category={k['category']}  title={k['title']!r}"
        )
        for d in item["deleted"]:
            lines.append(
                f"  {'DELETED' if applied else 'DELETE '}  id={d['id']:<8} "
                f"episodes={d['episodes']:<5} category={d['category']}  "
                f"title={d['title']!r}"
            )
    lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    args = parse_args()
    if _DRIVER is None:
        sys.exit(
            "[!] No MySQL driver found. Install one with:\n"
            "    pip install pymysql\n"
            "or\n"
            "    pip install mysql-connector-python"
        )

    auto = load_auto_config(args.auto_config) if args.auto_config else {}
    conn, database = connect_with_fallback(args, auto)
    cur = conn.cursor()

    series_tbl, episodes_tbl = detect_tables(cur, database)
    series = fetch_series(cur, series_tbl, episodes_tbl)
    print(f"[*] Found {len(series)} series in the panel")

    dup_groups = group_duplicates(series, args)
    if not dup_groups:
        print("[*] No duplicate shows found. Nothing to do.")
        report_path = args.report or "duplicate_shows_%s.txt" % datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )
        write_report(report_path, {}, [], args.apply, {"series": 0, "episodes": 0})
        print(f"[*] Report written to {report_path}")
        cur.close()
        conn.close()
        return

    # Build the deletion plan
    plan = []
    all_delete_ids = []
    total_episodes_to_delete = 0
    for entries in dup_groups.values():
        keeper = pick_keeper(entries, args.keep)
        deleted = sorted(
            (e for e in entries if e["id"] != keeper["id"]), key=lambda s: s["id"]
        )
        plan.append(
            {
                "display_title": keeper["title"].strip() or f"(untitled id={keeper['id']})",
                "keeper": keeper,
                "deleted": deleted,
            }
        )
        all_delete_ids.extend(e["id"] for e in deleted)
        total_episodes_to_delete += sum(e["episodes"] for e in deleted)

    print(
        f"[*] {len(dup_groups)} shows have duplicates -> "
        f"{len(all_delete_ids)} duplicate series "
        f"({total_episodes_to_delete} episodes) marked for deletion"
    )

    episodes_deleted = total_episodes_to_delete
    if args.apply:
        print("[*] Deleting duplicates...")
        try:
            episodes_deleted, streams_deleted = delete_series(
                cur, series_tbl, episodes_tbl, all_delete_ids
            )
            conn.commit()
        except Exception:
            conn.rollback()
            print("[!] Error during deletion - all changes rolled back.")
            raise
        print(
            f"[*] Deleted {len(all_delete_ids)} series, "
            f"{episodes_deleted} episode entries, {streams_deleted} streams."
        )
    else:
        print("[*] DRY RUN - nothing was deleted. Re-run with --apply to delete.")

    report_path = args.report or "duplicate_shows_%s.txt" % datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )
    write_report(
        report_path,
        dup_groups,
        plan,
        args.apply,
        {"series": len(all_delete_ids), "episodes": episodes_deleted},
    )
    print(f"[*] Report written to {report_path}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
