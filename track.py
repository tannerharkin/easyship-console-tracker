#!/usr/bin/env python3
"""
Easyship / trackmyshipment.co tracking script.

Usage:
  python track.py <TRACKING_NUMBER> [--format=pretty|json|xml|kv]

Formats:
  pretty  (default) Rich-formatted terminal output
  json    Raw JSON from the API
  xml     XML document
  kv      KEY=VALUE pairs suitable for scripting / eval
"""

import sys
import re
import random
import string
import base64
import json
import xml.etree.ElementTree as ET
from xml.dom import minidom
import argparse

import requests

SITEKEY  = "6Ld5iaoaAAAAAHu4CNSoKnF97sO3H_w23R5Dzt6K" # reCAPTCHA site key
DOMAIN   = "https://www.trackmyshipment.co:443"
API_BASE = "https://api.easyship.com/api"

console = None  # lazily initialised Rich Console (only for --format=pretty)


def _init_rich():
    """Import Rich and set up the console. Called only when pretty output is needed."""
    global console
    try:
        from rich.console import Console
        console = Console(highlight=False)
    except ImportError:
        print("Error: the 'rich' package is required for pretty output.", file=sys.stderr)
        print("Install it with:  pip install rich", file=sys.stderr)
        sys.exit(1)

#
#  reCAPTCHA
#

def get_recaptcha_token() -> str:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    })

    # 1. Fetch reCAPTCHA JS to extract the current release version
    r = session.get(
        f"https://www.recaptcha.net/recaptcha/api.js?render={SITEKEY}&hl=en",
        timeout=15,
    )
    r.raise_for_status()
    version = re.search(r"releases/([^/]+)/", r.text).group(1)

    # 2. Hit the anchor endpoint to get a short-lived challenge token
    co = base64.urlsafe_b64encode(DOMAIN.encode()).decode().rstrip("=")
    cb = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    anchor_url = (
        f"https://www.recaptcha.net/recaptcha/api2/anchor"
        f"?ar=1&k={SITEKEY}&co={co}&hl=en&v={version}&size=invisible&cb={cb}"
    )
    r2 = session.get(anchor_url, timeout=15)
    r2.raise_for_status()

    m = re.search(r'id="recaptcha-token" value="([^"]+)"', r2.text)
    if not m:
        raise RuntimeError("Could not extract anchor token from reCAPTCHA response")
    anchor_token = m.group(1)

    # 3. Exchange the challenge token for the final response token we need
    reload_url = f"https://www.recaptcha.net/recaptcha/api2/reload?k={SITEKEY}"
    r3 = session.post(
        reload_url,
        data=f"v={version}&reason=q&c={anchor_token}&k={SITEKEY}&co={co}&hl=en&size=invisible",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.recaptcha.net",
            "Referer": anchor_url,
        },
        timeout=15,
    )
    r3.raise_for_status()

    m2 = re.search(r'"rresp","([^"]+)"', r3.text)
    if not m2:
        raise RuntimeError("Could not extract final reCAPTCHA token from reload response")
    return m2.group(1)

#
#  API Request
#

def fetch_tracking(tracking_number: str) -> dict:
    if console is not None:
        with console.status("[bold cyan]Fetching reCAPTCHA token…[/]", spinner="dots"):
            token = get_recaptcha_token()
        with console.status(f"[bold cyan]Querying API for [yellow]{tracking_number}[/]…[/]", spinner="dots"):
            r = requests.get(
                f"{API_BASE}/v1/track/{tracking_number}",
                headers={"recaptcha-token": token},
                timeout=15,
            )
    else:
        token = get_recaptcha_token()
        r = requests.get(
            f"{API_BASE}/v1/track/{tracking_number}",
            headers={"recaptcha-token": token},
            timeout=15,
        )

    if not r.ok:
        print(f"API error {r.status_code}: {r.text}", file=sys.stderr)
        sys.exit(1)

    return r.json()


#
#  Formatters
#

def _flatten(d: dict, tracking_number: str) -> dict:
    """Return a flat dict of the fields we care about."""
    status      = d.get("last_status_message") or {}
    dates       = d.get("track_dates") or {}
    dest_country = (d.get("destination_country") or {}).get("name", "")
    return {
        "tracking_number":         tracking_number,
        "courier_tracking_number": d.get("tracking_number", ""),
        "courier":                 d.get("courier_name", ""),
        "courier_service":         d.get("courier_service", ""),
        "shipper":                 d.get("company_name", ""),
        "origin":                  d.get("origin_country", ""),
        "destination_city":        d.get("destination_city", ""),
        "destination_country":     dest_country,
        "status":                  status.get("name", ""),
        "status_detail":           status.get("subtitle", ""),
        "expected_delivery":       d.get("expected_delivery_date", ""),
        "min_delivery_days":       d.get("min_delivery_time", ""),
        "max_delivery_days":       d.get("max_delivery_time", ""),
        "dispatched":              dates.get("dispatched", ""),
        "out_for_delivery":        dates.get("out_for_delivery", ""),
        "delivered":               dates.get("delivered", ""),
    }


def output_pretty(d: dict, tracking_number: str) -> None:
    from rich.table import Table
    from rich.panel import Panel
    from rich import box

    status      = d.get("last_status_message") or {}
    dates       = d.get("track_dates") or {}
    checkpoints = d.get("checkpoints") or []
    dest_city    = d.get("destination_city", "")
    dest_country = (d.get("destination_country") or {}).get("name", "")
    destination  = ", ".join(filter(None, [dest_city, dest_country]))
    expected     = d.get("expected_delivery_date", "N/A") or "N/A"
    min_days     = d.get("min_delivery_time")
    max_days     = d.get("max_delivery_time")
    if min_days and max_days:
        expected += f"  [dim]({min_days}-{max_days} days estimated)[/]"

    # Status colour
    colour_map = {"blue": "cyan", "green": "green", "red": "red", "orange": "yellow"}
    status_colour = colour_map.get(status.get("status_color", ""), "white")

    # Summary panel
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold dim", justify="right")
    summary.add_column()
    summary.add_row("Easyship ID",      f"[bold]{tracking_number}[/]")
    summary.add_row("Courier Tracking", d.get("tracking_number", "N/A"))
    summary.add_row("Courier",          f"{d.get('courier_name','N/A')}  [dim]/[/]  {d.get('courier_service','')}")
    summary.add_row("Shipper",          d.get("company_name", "N/A") or "N/A")
    summary.add_row("Route",            f"{d.get('origin_country','N/A')}  [dim]->[/]  {destination}")
    summary.add_row("Status",           f"[{status_colour} bold]{status.get('name','N/A')}[/]  [dim]{status.get('subtitle','')}[/]")
    summary.add_row("Expected Delivery", expected)

    console.print()
    console.print(Panel(summary, title="[bold white]Shipment Summary[/]", border_style="bright_blue", expand=False))

    # Key dates panel
    date_rows = [
        ("Dispatched",       dates.get("dispatched")),
        ("Out for Delivery", dates.get("out_for_delivery")),
        ("Delivered",        dates.get("delivered")),
    ]
    date_rows = [(lbl, val) for lbl, val in date_rows if val]
    if date_rows:
        dt = Table("Event", "Timestamp", box=box.SIMPLE_HEAVY, show_header=True,
                   header_style="bold bright_blue")
        for lbl, val in date_rows:
            dt.add_row(lbl, val[:19].replace("T", "  "))
        console.print(Panel(dt, title="[bold white]Key Dates[/]", border_style="bright_blue", expand=False))

    # Tracking history
    if checkpoints:
        ht = Table("Timestamp", "Event", "Location", box=box.SIMPLE_HEAVY,
                   show_header=True, header_style="bold bright_blue",
                   show_edge=False)
        for cp in checkpoints:
            ts  = (cp.get("checkpoint_time") or cp.get("created_at") or "")[:19].replace("T", "  ")
            msg = cp.get("message") or cp.get("status") or ""
            loc = cp.get("location") or cp.get("city") or ""
            ht.add_row(f"[dim]{ts}[/]", msg, f"[dim]{loc}[/]")
        console.print(Panel(ht, title="[bold white]Tracking History[/]", border_style="bright_blue", expand=False))

    console.print()


def output_json(d: dict, tracking_number: str) -> None:
    print(json.dumps(d, indent=2))


def output_xml(d: dict, tracking_number: str) -> None:
    flat = _flatten(d, tracking_number)
    root = ET.Element("shipment")

    summary = ET.SubElement(root, "summary")
    for k, v in flat.items():
        el = ET.SubElement(summary, k)
        el.text = str(v) if v is not None else ""

    checkpoints_el = ET.SubElement(root, "checkpoints")
    for cp in (d.get("checkpoints") or []):
        cp_el = ET.SubElement(checkpoints_el, "checkpoint")
        for field in ("checkpoint_time", "created_at", "message", "status", "location", "city"):
            if cp.get(field):
                el = ET.SubElement(cp_el, field)
                el.text = str(cp[field])

    pretty = minidom.parseString(ET.tostring(root, encoding="unicode")).toprettyxml(indent="  ")
    print(pretty)


def output_kv(d: dict, tracking_number: str) -> None:
    flat = _flatten(d, tracking_number)
    for k, v in flat.items():
        # Quote irregular values
        val = str(v) if v is not None else ""
        if any(c in val for c in (' ', '\t', '"', "'")):
            val = f'"{val}"'
        print(f"{k.upper()}={val}")

    checkpoints = d.get("checkpoints") or []
    print(f"CHECKPOINT_COUNT={len(checkpoints)}")
    for i, cp in enumerate(checkpoints):
        ts  = (cp.get("checkpoint_time") or cp.get("created_at") or "")[:19]
        msg = cp.get("message") or cp.get("status") or ""
        loc = cp.get("location") or cp.get("city") or ""
        print(f'CHECKPOINT_{i}_TIME="{ts}"')
        print(f'CHECKPOINT_{i}_MSG="{msg}"')
        print(f'CHECKPOINT_{i}_LOC="{loc}"')


#
# Program entry
#

FORMATTERS = {
    "pretty": output_pretty,
    "json":   output_json,
    "xml":    output_xml,
    "kv":     output_kv,
}


def main():
    parser = argparse.ArgumentParser(
        description="Track an Easyship shipment.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Formats: pretty (default), json, xml, kv",
    )
    parser.add_argument("tracking_number", help="Easyship tracking number, e.g. ESgb123456789")
    parser.add_argument(
        "--format", "-f",
        choices=FORMATTERS.keys(),
        default="pretty",
        metavar="FORMAT",
        help="Output format: pretty | json | xml | kv  (default: pretty)",
    )
    args = parser.parse_args()

    # Only load Rich when pretty output is requested
    if args.format == "pretty":
        _init_rich()

    data = fetch_tracking(args.tracking_number.strip())
    FORMATTERS[args.format](data, args.tracking_number.strip())


if __name__ == "__main__":
    main()
