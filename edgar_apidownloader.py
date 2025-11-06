import argparse
import json
import time
import re
import subprocess
import shutil
from pathlib import Path
from typing import List, Optional, Tuple
import requests
import difflib
from datetime import date, timedelta

# file: edgar_apidownloader.py


# Optional deps (batch + pdf)
try:
    import pandas as pd  # for --input-xlsx
except Exception:
    pd = None

try:
    import pdfkit  # for wkhtmltopdf route (optional)
except Exception:
    pdfkit = None

SEC_BASE = "https://www.sec.gov"
DATA_BASE = "https://data.sec.gov"
# SEC-compliant UA
UA = UA = "Sukumar Chigurupati sukumarchigurupati11@gmail.com EDGARFetcher/1.0"

# Minimal legacy CIK map (reorgs / name changes)
# Format: ticker -> [(cutoff_date_YYYYMMDD, cik)]
LEGACY_CIK = {
    # Disney: use old CIK for filings before 2019-03-21
    "DIS": [("20190321", "0001001039")],
}

# ----------------------------- Utils -----------------------------


def http_get(
    url: str, *, retries: int = 3, sleep: float = 0.25, headers: Optional[dict] = None
):
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=hdrs, timeout=60)
            if r.status_code == 200:
                time.sleep(sleep)
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(sleep * attempt * 2)
                last = r
                continue
            r.raise_for_status()
        except requests.RequestException as e:
            last = e
            time.sleep(sleep * attempt * 2)
    if isinstance(last, requests.Response):
        raise RuntimeError(f"GET {url} failed, status={last.status_code}")
    raise RuntimeError(f"GET {url} failed: {last}")


def is_blank(val) -> bool:
    try:
        if pd is not None and pd.isna(val):
            return True
    except Exception:
        pass
    if val is None:
        return True
    return isinstance(val, str) and val.strip() == ""


def safe_str(val) -> Optional[str]:
    return None if is_blank(val) else str(val)


def normalize_name(s: str) -> str:
    s = (s or "").casefold()
    s = re.sub(r"[^0-9a-z\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_ticker(x) -> Optional[str]:
    if is_blank(x):
        return None
    t = str(x).strip()
    if t.lower() in {"nan", "none", "null"}:
        return None
    t = t.upper().split()[0]  # "BMY US" -> "BMY"
    t = re.sub(r"[^A-Z0-9\.-]", "", t)
    t = t.replace(".US", "")
    return t or None


# ------------------------- SEC lookups ---------------------------

_company_tickers_cache = None


def _load_company_tickers_json() -> dict:
    global _company_tickers_cache
    if _company_tickers_cache is None:
        _company_tickers_cache = http_get(
            f"{SEC_BASE}/files/company_tickers.json").json()
    return _company_tickers_cache


def get_cik_for_ticker(ticker: str, cache: Optional[dict] = None) -> str:
    data = cache or _load_company_tickers_json()
    t = ticker.upper()
    for v in data.values():
        if v.get("ticker", "").upper() == t:
            return str(v["cik_str"]).zfill(10)
    raise RuntimeError(f"Ticker {ticker!r} not found in SEC mapping")


def get_cik_for_name_primary(name: str, cache: Optional[dict] = None) -> Optional[str]:
    """company_tickers.json: exact → startswith → contains on 'title'."""
    if not name:
        return None
    data = cache or _load_company_tickers_json()
    target = normalize_name(name)
    for v in data.values():
        if normalize_name(v.get("title", "")) == target:
            return str(v["cik_str"]).zfill(10)
    for v in data.values():
        if normalize_name(v.get("title", "")).startswith(target):
            return str(v["cik_str"]).zfill(10)
    for v in data.values():
        if target in normalize_name(v.get("title", "")):
            return str(v["cik_str"]).zfill(10)
    return None


# Broad fallback file (covers legacy/foreign issuers)
_cik_lookup_cache = None


def _load_cik_lookup_data() -> list[tuple[str, str]]:
    global _cik_lookup_cache
    if _cik_lookup_cache is not None:
        return _cik_lookup_cache
    txt = http_get(
        "https://www.sec.gov/Archives/edgar/cik-lookup-data.txt").text
    rows = []
    for line in txt.splitlines():
        parts = line.strip().split("|")
        if len(parts) >= 2:
            nm = parts[0].strip()
            cik = parts[1].strip()
            if nm and cik.isdigit():
                rows.append((normalize_name(nm), cik.zfill(10)))
    _cik_lookup_cache = rows
    return rows


def get_cik_for_name_broad(name: str) -> Optional[str]:

    target = normalize_name(name)
    if not target:
        return None
    rows = _load_cik_lookup_data()
    for nm, cik in rows:
        if nm == target:
            return cik
    for nm, cik in rows:
        if nm.startswith(target) or target in nm:
            return cik
    best = None
    best_ratio = 0.0
    for nm, cik in rows:
        r = difflib.SequenceMatcher(None, target, nm).ratio()
        if r > best_ratio:
            best_ratio, best = r, cik
    return best if best_ratio >= 0.90 else None


def get_cik_for_name(name: str, cache: Optional[dict] = None) -> Optional[str]:
    return get_cik_for_name_primary(name, cache) or get_cik_for_name_broad(name)


def apply_legacy_cik_if_needed(ticker: str, anchor_yyyymmdd: str, cik: str) -> str:
    t = (ticker or "").upper()
    if t in LEGACY_CIK:
        for cutoff, legacy in LEGACY_CIK[t]:
            if anchor_yyyymmdd and anchor_yyyymmdd < cutoff:
                return legacy.zfill(10)
    return cik


def fetch_company_submissions(cik: str) -> dict:
    return http_get(f"{DATA_BASE}/submissions/CIK{cik}.json").json()


# ---------------- historical filings (key fix) -------------------


def _extract_filings_block(block: dict) -> list[dict]:
    """Turn a 'recent' block into [{form, accession, date}]."""
    forms = block.get("form", []) or []
    accessions = block.get("accessionNumber", []) or []
    dates = block.get("filingDate", []) or []
    out = []
    for f, acc, dt in zip(forms, accessions, dates):
        out.append({"form": f, "accession": acc, "date": dt})
    return out


def _overlaps(a_from: str, a_to: str, b_from: str, b_to: str) -> bool:
    return not (a_to < b_from or a_from > b_to)


def gather_filings(
    cik: str, form: str, after: Optional[str], before: Optional[str], include_amends: bool, limit: Optional[int]
) -> list[dict]:
    """
    Collect filings from BOTH 'recent' AND historical files that overlap the date window.
    Returns [{form, accession, date, cik}, ...] sorted by date.
    """
    subs = fetch_company_submissions(cik)
    want = form.upper()
    collected: list[dict] = []

    # 1) recent
    recent_block = subs.get("filings", {}).get("recent", {}) or {}
    for it in _extract_filings_block(recent_block):
        base_form = it["form"].split("/")[0].upper()
        if base_form != want:
            continue
        if not include_amends and it["form"].upper().endswith("/A"):
            continue
        if after and it["date"] < after:
            continue
        if before and it["date"] > before:
            continue
        it["cik"] = subs["cik"]
        collected.append(it)

    # 2) historical
    files = subs.get("filings", {}).get("files", []) or []
    for f in files:
        f_from = f.get("filingFrom")
        f_to = f.get("filingTo")
        name = f.get("name")
        if not (f_from and f_to and name):
            continue
        if (after and before) and not _overlaps(after, before, f_from, f_to):
            continue
        try:
            hist = http_get(f"{DATA_BASE}/submissions/{name}").json()
        except Exception:
            continue
        block = hist.get("filings", {}).get("recent", {}) or {}
        for it in _extract_filings_block(block):
            base_form = it["form"].split("/")[0].upper()
            if base_form != want:
                continue
            if not include_amends and it["form"].upper().endswith("/A"):
                continue
            if after and it["date"] < after:
                continue
            if before and it["date"] > before:
                continue
            it["cik"] = subs["cik"]
            collected.append(it)

    collected.sort(key=lambda x: x["date"])
    if limit:
        collected = collected[:limit]
    return collected


# -------- unified document listing: index.json → txt → html ------


def fetch_listing_names(cik: str, accession: str) -> list[str]:
    """
    Returns a list of document filenames for this accession using:
    1) index.json (preferred)
    2) full-submission.txt (<FILENAME> tags)
    3) accession-index.html (regex href scrape)
    """
    acc_nodash = accession.replace("-", "")
    base = f"{SEC_BASE}/Archives/edgar/data/{int(cik)}/{acc_nodash}"

    # 1) index.json
    try:
        idx = http_get(f"{base}/index.json").json()
        items = idx.get("directory", {}).get("item", []) or []
        names = [it.get("name") for it in items if it.get("name")]
        if names:
            return names
    except Exception:
        pass

    # 2) full-submission.txt
    try:
        txt = http_get(f"{base}/full-submission.txt").text
        names = re.findall(
            r"<FILENAME>\s*([^\r\n<>]+)", txt, flags=re.IGNORECASE)
        names = [n.strip() for n in names if n.strip()]
        if names:
            # de-dup preserve order
            return list(dict.fromkeys(names))
    except Exception:
        pass

    # 3) accession-index.html
    try:
        html = http_get(f"{base}/{accession}-index.html").text
        names = re.findall(
            r'href="([^"]+\.(?:htm|html|txt|xml|xsd|zip|jpg|png|gif|xlsx))"',
            html,
            flags=re.IGNORECASE,
        )
        names = [Path(n).name for n in names]
        if names:
            return list(dict.fromkeys(names))
    except Exception:
        pass

    return []


# ------------------ Exhibit detection (2.1, fallback 10.1) -------

EX_PRIMARY = [
    "ex2-1", "ex2_1", "ex21.", "dex21", "exhibit2.1",
    "ex2d1", "dex2-1", "dex2_1"           # NEW: catch ex2d1 + dex2-1/_1
]
EX_FALLBACK = [
    "ex10.1", "ex10_1", "ex101.", "dex101", "exhibit10.1",
    "ex10d1", "dex10-1", "dex10_1"        # NEW: catch ex10d1 + dex10-1/_1
]


def classify_exhibits_from_names(names: list[str]) -> dict:
    out = {"primary": [], "fallback": []}
    # primary=EX-2.1; fallback=EX-10.1
    for nm in names:
        n = (nm or "").lower()
        if n.endswith((".htm", ".html")):
            if any(k in n for k in EX_PRIMARY):
                out["primary"].append(nm)
            elif any(k in n for k in EX_FALLBACK):
                out["fallback"].append(nm)
    return out


# ------------------------ HTML → PDF ----------------------------


def _find_executable(candidates):
    for p in candidates:
        if Path(p).exists():
            return str(p)
        if shutil.which(str(p)):
            return shutil.which(str(p))
    return None


def path_to_file_url(p: Path) -> str:
    return p.resolve().as_uri()


def convert_html_to_pdf(html_path: Path, pdf_path: Path) -> bool:
    """Try wkhtmltopdf via pdfkit, then Edge/Chrome headless. Log errors per-file."""
    err_log = html_path.with_suffix(".conversion_errors.txt")

    # wkhtmltopdf first
    wkhtml_candidates = [
        r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe",
        r"C:\Program Files (x86)\wkhtmltopdf\bin\wkhtmltopdf.exe",
        "wkhtmltopdf",
    ]
    wkhtml = _find_executable(wkhtml_candidates)
    if pdfkit is not None and wkhtml:
        try:
            cfg = pdfkit.configuration(wkhtmltopdf=wkhtml)
            options = {
                "quiet": "",
                "enable-local-file-access": "",
                "load-error-handling": "ignore",
                "load-media-error-handling": "ignore",
                "page-size": "Letter",
            }
            pdfkit.from_file(str(html_path), str(pdf_path),
                             configuration=cfg, options=options)
            return pdf_path.exists() and pdf_path.stat().st_size > 0
        except Exception as e:
            try:
                err_log.write_text(f"[wkhtmltopdf] {e}\n", encoding="utf-8")
            except Exception:
                pass

    # Edge/Chrome headless
    browser_candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "chrome",
        "google-chrome",
        "chromium",
        "msedge",
        "edge",
    ]
    exe = _find_executable(browser_candidates)
    if exe:
        try:
            file_url = path_to_file_url(html_path)
            cmd = [
                exe,
                "--headless",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--allow-file-access-from-files",
                "--virtual-time-budget=15000",
                f"--print-to-pdf={str(pdf_path)}",
                file_url,
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                err_log.write_text(
                    proc.stderr or "headless browser failed without stderr\n", encoding="utf-8")
            return pdf_path.exists() and pdf_path.stat().st_size > 0
        except Exception as e:
            try:
                err_log.write_text(f"[headless] {e}\n", encoding="utf-8")
            except Exception:
                pass

    try:
        if not err_log.exists():
            err_log.write_text(
                "No PDF engine found. Install wkhtmltopdf or Chrome/Edge.\n", encoding="utf-8")
    except Exception:
        pass

    return False


# -------------------------- Dates -------------------------------

DATE_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\s*$")


def excel_date_to_yyyy_mm_dd(val) -> Optional[str]:
    if val is None or (pd is not None and isinstance(val, float) and pd.isna(val)):
        return None
    if pd is not None and isinstance(val, (pd.Timestamp, )):
        return val.strftime("%Y-%m-%d")
    if isinstance(val, str):
        m = DATE_RE.match(val)
        if m:
            mth, day, yr = map(int, m.groups())
            if yr < 100:
                yr += 2000 if yr < 70 else 1900
            return f"{yr:04d}-{mth:02d}-{day:02d}"
    return None


def window_from_anchor(anchor: str, days: int) -> Tuple[str, str]:

    y, m, d = map(int, anchor.split("-"))
    base = date(y, m, d)
    lo = base - timedelta(days=days)
    hi = base + timedelta(days=days)
    return lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")


# --------------------- Core download logic ----------------------


def find_targets_for_company(
    cik: str, filing: str, after: str, before: str, include_amends: bool, limit: Optional[int]
) -> Tuple[List[Tuple[str, str, str]], bool]:
    """
    Returns (targets, used_primary)
    targets = list of (accession, filename, label) where label is "EX-2.1" or "EX-10.1"
    used_primary = True if at least one EX-2.1 found across all matched filings
    """
    filings = gather_filings(cik, filing, after, before, include_amends, limit)
    primary: List[Tuple[str, str, str]] = []
    fallback: List[Tuple[str, str, str]] = []
    for f in filings:
        names = fetch_listing_names(str(cik), f["accession"])
        if not names:
            continue
        ex = classify_exhibits_from_names(names)
        for nm in ex["primary"]:
            primary.append((f["accession"], nm, "EX-2.1"))
        for nm in ex["fallback"]:
            fallback.append((f["accession"], nm, "EX-10.1"))
    return (primary if primary else fallback, len(primary) > 0)


def download_targets_for_company(
    row_no: int,
    label: str,
    cik: str,
    filing: str,
    after: str,
    before: str,
    save_dir: Path,
    include_amends: bool,
    limit: Optional[int],
    log_missing: List[str],
) -> int:
    targets, used_primary = find_targets_for_company(
        cik, filing, after, before, include_amends, limit)
    if not targets:
        log_missing.append(
            f"Row {row_no} | {label} | CIK={cik} | Window={after}..{before} | no Exhibit 2.1 found"
        )
        return 0

    out_dir = save_dir / label
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = 0

    for accession, name, tag in targets:
        base_href = f"/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"
        url = f"{SEC_BASE}{base_href}/{name}"
        # Prefix the tag into filename so you can tell 2.1 vs 10.1 at a glance
        safe_name = f"{tag}__{accession}__{name}"
        dest_html = out_dir / safe_name
        dest_pdf = out_dir / (Path(safe_name).stem + ".pdf")

        if not dest_html.exists():
            try:
                r = http_get(url)
                dest_html.write_bytes(r.content)
                print(f" ✓ {label}: {safe_name} ({len(r.content)/1024:.1f} KB)")
                saved += 1
            except Exception as e:
                print(f" ⚠️ {label}: failed {safe_name}: {e}")
                continue
        else:
            saved += 1

        if not dest_pdf.exists():
            ok = convert_html_to_pdf(dest_html, dest_pdf)
            if ok:
                print(f" → PDF: {dest_pdf.name}")
            else:
                print(f" ⚠️ PDF conversion failed for {safe_name}")

    if not used_primary:
        # If we only used 10.1 fallbacks, add a helpful note in missing list
        log_missing.append(
            f"Row {row_no} | {label} | CIK={cik} | Window={after}..{before} | no EX-2.1; used EX-10.1 fallback"
        )

    return saved


# --------------------------- Batch ------------------------------


def run_batch_from_excel(args):
    if pd is None:
        raise SystemExit("Please install: pip install pandas openpyxl")

    xlsx = Path(args.input_xlsx)
    df = pd.read_excel(xlsx)

    # Normalize column names
    cols = {c.lower().strip(): c for c in df.columns}

    def col(*names):
        for n in names:
            c = cols.get(n.lower())
            if c:
                return c
        return None

    c_announce = col("Announce Date", "Announcement Date")
    c_acq_tkr = col("Acquirer Ticker", "Acquirer_Ticker")
    c_tar_tkr = col("Target Ticker", "Target_Ticker")
    c_acq_cik = col("Acquirer CIK", "Acquirer_CIK")
    c_tar_cik = col("Target CIK", "Target_CIK")
    c_acq_name = col("Acquirer Name")
    c_tar_name = col("Target Name")

    if not c_announce or not (c_acq_tkr or c_tar_tkr or c_acq_cik or c_tar_cik):
        raise SystemExit(
            "Spreadsheet must contain Announce Date and a ticker/CIK column (Acquirer or Target).")

    save_dir = Path(args.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    missing_lines: List[str] = []
    notfound_lines: List[str] = []
    mapping_cache = _load_company_tickers_json()

    for i, row in df.iterrows():
        row_no = i + 1
        anchor = excel_date_to_yyyy_mm_dd(row.get(c_announce))
        if not anchor:
            missing_lines.append(f"Row {row_no} | invalid Announce Date")
            continue

        after, before = window_from_anchor(anchor, args.window_days)
        anchor_yyyymmdd = anchor.replace("-", "")
        specs = []

        if args.filer in ("acquirer", "both"):
            specs.append(
                (
                    "acquirer",
                    row.get(c_acq_cik),
                    row.get(c_acq_tkr),
                    row.get(c_tar_tkr),
                    row.get(c_acq_name),
                    row.get(c_tar_name),
                )
            )
        if args.filer in ("target", "both"):
            specs.append(
                (
                    "target",
                    row.get(c_tar_cik),
                    row.get(c_tar_tkr),
                    row.get(c_acq_tkr),
                    row.get(c_tar_name),
                    row.get(c_acq_name),
                )
            )

        for (
            role,
            cik_cell,
            primary_tkr_cell,
            alt_tkr_cell,
            primary_name_cell,
            alt_name_cell,
        ) in specs:
            cik_text = safe_str(cik_cell)
            primary_tkr = normalize_ticker(primary_tkr_cell)
            alt_tkr = normalize_ticker(alt_tkr_cell)
            primary_name = safe_str(primary_name_cell)
            alt_name = safe_str(alt_name_cell)

            tried = []
            cik = None
            label = None

            if cik_text and cik_text.isdigit():
                cik = cik_text.zfill(10)
                label = primary_tkr or alt_tkr or (
                    primary_name or alt_name) or cik
            else:
                # tickers: primary then alt
                for tkr in (primary_tkr, alt_tkr):
                    if not tkr:
                        continue
                    try:
                        base_cik = get_cik_for_ticker(tkr, cache=mapping_cache)
                        cik = apply_legacy_cik_if_needed(
                            tkr, anchor_yyyymmdd, base_cik)
                        label = tkr
                        break
                    except Exception as e:
                        tried.append(f"ticker={tkr} ({e})")

                # names: primary then alt
                if cik is None:
                    for nm in (primary_name, alt_name):
                        if not nm:
                            continue
                        name_cik = get_cik_for_name(nm, cache=mapping_cache)
                        if name_cik:
                            cik = name_cik
                            label = label or primary_tkr or alt_tkr or nm
                            break
                        else:
                            tried.append(f"name={nm} (no match)")

            if cik is None:
                notfound_lines.append(
                    f"Row {row_no} [{role}] | NO ID | after={after} before={before} | tried: {', '.join(tried) or 'n/a'}"
                )
                continue

            label = (label or "").strip() or cik
            print(
                f"\n🔸 Row {row_no} [{role}] {(primary_name or label)} — {label} — {after}..{before}")

            saved = download_targets_for_company(
                row_no=row_no,
                label=label,
                cik=cik,
                filing=args.filing,
                after=after,
                before=before,
                save_dir=save_dir,
                include_amends=args.include_amends,
                limit=args.limit,
                log_missing=missing_lines,
            )

    # Write logs
    (save_dir / "missingexhibit2.1.txt").write_text("\n".join(missing_lines) +
                                                    ("\n" if missing_lines else ""), encoding="utf-8")
    (save_dir / "tickersnotfound.txt").write_text("\n".join(notfound_lines) +
                                                  ("\n" if notfound_lines else ""), encoding="utf-8")

    print(
        f"\n📄 Missing Exhibit 2.1 list: {save_dir / 'missingexhibit2.1.txt'}")
    print(f"📄 Tickers/CIKs not found: {save_dir / 'tickersnotfound.txt'}")
    print("\n📘 Batch complete.")


# --------------------------- Single -----------------------------


def run_single(args):
    save_dir = Path(args.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    missing_lines: List[str] = []
    notfound_lines: List[str] = []
    mapping_cache = _load_company_tickers_json()

    if args.cik:
        cik = str(args.cik).zfill(10)
        label = args.ticker.upper() if args.ticker else cik
        print(f"🔹 Using provided CIK: {cik}")
    else:
        anchor_yyyymmdd = (args.after or "").replace("-", "")
        label = None
        cik = None
        tried = []

        if args.ticker:
            try:
                tkr = args.ticker.upper()
                base_cik = get_cik_for_ticker(tkr, cache=mapping_cache)
                cik = apply_legacy_cik_if_needed(
                    tkr, anchor_yyyymmdd, base_cik)
                label = tkr
            except Exception as e:
                tried.append(f"ticker={args.ticker} ({e})")

        if cik is None and args.name:
            name_cik = get_cik_for_name(args.name, cache=mapping_cache)
            if name_cik:
                cik = name_cik
                label = label or args.name
            else:
                tried.append(f"name={args.name} (no match)")

        if cik is None:
            line = f"Single | NO ID | after={args.after} before={args.before} | tried: {', '.join(tried) or 'n/a'}"
            (save_dir / "tickersnotfound.txt").write_text(line + "\n", encoding="utf-8")
            print(
                f"📄 Tickers/CIKs not found: {save_dir / 'tickersnotfound.txt'}")
            return

    saved = download_targets_for_company(
        row_no=0,
        label=label,
        cik=cik,
        filing=args.filing,
        after=args.after,
        before=args.before,
        save_dir=save_dir,
        include_amends=args.include_amends,
        limit=args.limit,
        log_missing=missing_lines,
    )

    if saved == 0:
        (save_dir / "missingexhibit2.1.txt").write_text(
            f"Row 0 | {label} | CIK={cik} | Window={args.after}..{args.before} | no Exhibit 2.1 found\n",
            encoding="utf-8",
        )

    print("\n✅ Done.")


# ---------------------------- CLI --------------------------------


def main():
    ap = argparse.ArgumentParser(
        description=(
            "EDGAR Exhibit fetcher (EX-2.1 primary, EX-10.1 fallback). "
            "Downloads HTML + PDF into <save-dir>/<TICKER>/ . Supports batch Excel or single run."
        )
    )

    # Batch
    ap.add_argument("--input-xlsx", help="Excel file with deals (batch mode)")

    # Single
    ap.add_argument("--ticker", help="Company ticker (e.g., BMY, DIS)")
    ap.add_argument("--name", help="Company legal name (single-mode fallback)")
    ap.add_argument("--cik", help="Optional CIK (10 digits)")

    # Filters
    ap.add_argument("--filing", default="8-K",
                    help="Filing type (default: 8-K)")
    ap.add_argument("--after", help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--before", help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--limit", type=int, default=None,
                    help="Max filings to process")
    ap.add_argument("--include_amends", action="store_true",
                    help="Include */A amended forms")

    # Batch controls
    ap.add_argument("--filer", choices=["acquirer", "target", "both"],
                    default="acquirer", help="Which party to pull for each row (default: acquirer)")
    ap.add_argument("--window_days", dest="window_days", type=int,
                    default=5, help="Half-window around Announce Date (default: 5)")

    # Output
    ap.add_argument("--save-dir", default="EDGAR_EXHIBITS",
                    help="Root output folder")

    args = ap.parse_args()

    if args.input_xlsx:
        run_batch_from_excel(args)
    else:
        if not (args.ticker or args.cik or args.name):
            raise SystemExit(
                "❌ Provide either --input-xlsx (batch) or --ticker/--name/--cik (single).")
        if not (args.after and args.before):
            raise SystemExit(
                "❌ For single mode, provide --after and --before.")
        run_single(args)


if __name__ == "__main__":
    main()
