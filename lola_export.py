#!/usr/bin/env python3
"""
lola_export.py - Export your course content from a university portal (e.g. Lola)
so you can upload it to Claude and study with it.

How it works
  1. Opens a real browser window on your university's site.
  2. YOU log in yourself (password, 2FA, SSO - whatever your uni uses).
     Your password is never seen or stored by this script.
  3. You press Enter in the terminal. The script then visits every course page
     it can reach from the start page (same website only), saves each page as
     Markdown, and downloads the attached files (PDF, slides, docs...).
  4. It builds ready-to-upload files in the output folder:
       CLAUDE_UPLOAD/ALL_CONTENT_part1.md ...  (all text, organised by course)
       files/                                   (original PDFs, slides, etc.)

The crawler only *reads*: it never clicks buttons or submits forms, and it skips
links that look like logout / delete / submit / edit actions.

Usage
  python lola_export.py --start "https://<your-lola-site>/my/courses"
  python lola_export.py --start URL --max-pages 2000 --depth 4 --out my_export
Run with --help for all options.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse, unquote, parse_qsl, urlencode

try:
    from playwright.sync_api import sync_playwright, Error as PlaywrightError
except ImportError:
    sys.exit("Missing dependency. Run:  pip install -r requirements.txt  &&  playwright install chromium")

try:
    import html2text
except ImportError:
    html2text = None

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


# Links we never follow while logged in - they could change your account or log you out.
_B = r"(?:^|[/_?&=.\-])"   # word boundary inside a URL
_E = r"(?:$|[/_?&=.\-])"
DEFAULT_EXCLUDE = [
    r"log_?out", r"sign_?out", r"log_?off", r"sesskey=", r"mailto:", r"tel:", r"javascript:",
    _B + r"(delete|remove|unenrol|unenroll|drop|withdraw|submit|edit|update|upload|settings|preferences|admin|password)" + _E,
    r"attempt\.php", r"startattempt", r"processattempt",
    r"/calendar/", r"/message", r"/notifications", r"/grade/report", r"/badges", r"/user/profile", r"\.ics$",
]

FILE_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".csv", ".txt",
    ".rtf", ".odt", ".odp", ".ods", ".md", ".zip", ".epub",
    ".png", ".jpg", ".jpeg", ".gif", ".py", ".java", ".c", ".cpp", ".ipynb",
}
SKIP_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".mp3", ".wav", ".m4a", ".webm",
                   ".css", ".js", ".woff", ".woff2", ".ttf", ".ico", ".svg"}
TEXT_FILE_EXTENSIONS = {".txt", ".md", ".csv", ".py", ".java", ".c", ".cpp"}

# Parts of a page that are navigation chrome, not course content.
CHROME_SELECTORS = [
    "header", "footer", "nav", "script", "style", "noscript", "iframe",
    "[role=navigation]", "[role=banner]", "[role=contentinfo]",
    "#nav-drawer", ".navbar", "#page-footer", ".breadcrumb", ".skip-link",
    ".sr-only", "[aria-hidden=true]",
]
# Preferred containers for the main content (first match wins).
MAIN_SELECTORS = ["#region-main", "main", "[role=main]", "#content", "#main", "article", ".content", "body"]


def log(msg: str) -> None:
    print(msg, flush=True)


def normalize_url(url: str) -> str:
    """Drop #fragments and sort query params so the same page isn't visited twice."""
    p = urlparse(url)
    query = urlencode(sorted(parse_qsl(p.query, keep_blank_values=True)))
    return urlunparse((p.scheme, p.netloc.lower(), p.path or "/", "", query, ""))


def slugify(text: str, max_len: int = 80) -> str:
    text = re.sub(r"[^\w\s.-]", "", text, flags=re.UNICODE).strip()
    text = re.sub(r"[\s]+", "_", text)
    return (text[:max_len] or "untitled").strip("._") or "untitled"


def short_hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:8]


def url_extension(url: str) -> str:
    path = unquote(urlparse(url).path).lower()
    m = re.search(r"(\.[a-z0-9]{1,5})$", path)
    return m.group(1) if m else ""


class Exporter:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.start = args.start
        self.out = Path(args.out)
        self.pages_dir = self.out / "pages"
        self.files_dir = self.out / "files"
        self.upload_dir = self.out / "CLAUDE_UPLOAD"
        for d in (self.pages_dir, self.files_dir, self.upload_dir):
            d.mkdir(parents=True, exist_ok=True)

        start_host = urlparse(self.start).netloc.lower()
        self.allowed_hosts = {start_host} | {h.lower() for h in args.allow_domain}
        self.exclude = [re.compile(p, re.I) for p in DEFAULT_EXCLUDE + args.exclude]
        self.include = [re.compile(p, re.I) for p in args.include]

        self.state_path = self.out / "state.json"
        self.queue: deque = deque()
        self.visited: set[str] = set()
        self.records: list[dict] = []   # one entry per saved page/file, in crawl order
        self.pending: list[list] = []   # [url, depth, parent] still to visit (for resume)
        self._load_state()

    # ---------- resume support ----------
    def _load_state(self) -> None:
        if self.state_path.exists() and not self.args.fresh:
            data = json.loads(self.state_path.read_text())
            self.visited = set(data.get("visited", []))
            self.records = data.get("records", [])
            self.pending = data.get("pending", [])
            log(f"Resuming: {len(self.visited)} URLs already done (use --fresh to start over).")

    def _save_state(self) -> None:
        self.state_path.write_text(json.dumps({"visited": sorted(self.visited), "records": self.records,
                                                 "pending": [list(x) for x in self.queue]}, indent=1))

    # ---------- link filtering ----------
    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        if p.netloc.lower() not in self.allowed_hosts:
            return False
        if url_extension(url) in SKIP_EXTENSIONS:
            return False
        if any(rx.search(url) for rx in self.exclude):
            return False
        if self.include and not any(rx.search(url) for rx in self.include):
            # Always allow direct file links even when an include filter is set.
            return url_extension(url) in FILE_EXTENSIONS or "pluginfile.php" in url
        return True

    # ---------- content extraction ----------
    def page_to_markdown(self, page) -> tuple[str, str, list[str]]:
        title = (page.title() or "").strip()
        if not title:
            title = page.evaluate("() => { const h = document.querySelector('h1, h2'); return h ? h.innerText.trim() : ''; }")
        title = title or urlparse(page.url).path.strip("/") or page.url
        html = page.evaluate(
            """([chrome, mains]) => {
                let root = null;
                for (const s of mains) { root = document.querySelector(s); if (root) break; }
                root = (root || document.body).cloneNode(true);
                for (const s of chrome) root.querySelectorAll(s).forEach(e => e.remove());
                return root.innerHTML;
            }""",
            [CHROME_SELECTORS, MAIN_SELECTORS],
        )
        links = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]'))
                     .map(a => a.href).filter(Boolean)"""
        )
        if html2text:
            conv = html2text.HTML2Text()
            conv.body_width = 0
            conv.ignore_images = True
            conv.ignore_emphasis = False
            conv.baseurl = page.url
            md = conv.handle(html)
        else:
            md = page.evaluate("(h) => { const d=document.createElement('div'); d.innerHTML=h; return d.innerText; }", html)
        md = re.sub(r"\n{3,}", "\n\n", md).strip()
        return title, md, links

    def extract_file_text(self, path: Path) -> str:
        ext = path.suffix.lower()
        try:
            if ext == ".pdf" and PdfReader:
                reader = PdfReader(str(path))
                parts = []
                for i, pg in enumerate(reader.pages, 1):
                    t = (pg.extract_text() or "").strip()
                    if t:
                        parts.append(f"[page {i}]\n{t}")
                return "\n\n".join(parts)
            if ext in TEXT_FILE_EXTENSIONS:
                return path.read_text(errors="replace")
        except Exception as e:  # damaged or encrypted files
            log(f"   (couldn't read text from {path.name}: {str(e)[:80]})")
        return ""

    # ---------- saving ----------
    def save_file(self, url: str, body: bytes, headers: dict, parent: str) -> None:
        name = ""
        cd = headers.get("content-disposition", "")
        m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)|filename=\"?([^\";]+)\"?", cd, re.I)
        if m:
            name = unquote((m.group(1) or m.group(2)).strip().strip('"'))
        if not name:
            name = unquote(Path(urlparse(url).path).name) or "file"
        stem, ext = Path(name).stem, Path(name).suffix
        if not ext:
            ct = headers.get("content-type", "").split(";")[0]
            ext = {"application/pdf": ".pdf", "text/plain": ".txt"}.get(ct, "")
        dest = self.files_dir / f"{slugify(stem)}_{short_hash(url)}{ext}"
        dest.write_bytes(body)
        text = self.extract_file_text(dest)
        self.records.append({"type": "file", "url": url, "title": name, "path": str(dest.relative_to(self.out)),
                             "text": text, "parent": parent})
        log(f"   [file] {name}  ({len(body)//1024} KB)")

    def save_page(self, url: str, title: str, md: str, parent: str) -> None:
        dest = self.pages_dir / f"{len(self.records):04d}_{slugify(title)}.md"
        dest.write_text(f"# {title}\n\nSource: {url}\n\n{md}\n")
        self.records.append({"type": "page", "url": url, "title": title, "path": str(dest.relative_to(self.out)),
                             "text": md, "parent": parent})
        log(f"   [page] {title}  ({len(md)} chars)")

    # ---------- crawl ----------
    def crawl(self) -> None:
        a = self.args
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(Path(a.profile).resolve()),
                headless=a.headless,
                accept_downloads=False,
                executable_path=a.browser_path or None,
                channel=a.channel or None,
                viewport={"width": 1280, "height": 900},
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(self.start)
            if not a.headless and not a.no_wait:
                log("\n" + "=" * 70)
                log(" A browser window has opened.")
                log(" 1. Log in to your university account in that window.")
                log(" 2. Go to the page that lists your courses / subjects")
                log("    (or leave it on the start page).")
                log(" 3. Come back here and press ENTER to start exporting.")
                log("=" * 70)
                input()
            # Use whatever page the user landed on as an extra seed (handles SSO redirects).
            seeds = [self.start]
            if normalize_url(page.url) != normalize_url(self.start):
                seeds.append(page.url)
                self.allowed_hosts.add(urlparse(page.url).netloc.lower())

            if self.pending:
                queue = self.queue = deque(tuple(x) for x in self.pending)
            else:
                queue = self.queue = deque((s, 0, "") for s in seeds)
            queued = {normalize_url(x[0]) for x in queue} | self.visited
            count = 0
            try:
                while queue and count < a.max_pages:
                    url, depth, parent = queue.popleft()
                    key = normalize_url(url)
                    if key in self.visited:
                        continue
                    self.visited.add(key)
                    count += 1
                    log(f"[{count}/{a.max_pages}] depth {depth}: {url}")
                    try:
                        new_links = self.process(ctx, page, url, parent)
                    except PlaywrightError as e:
                        log(f"   ! skipped ({str(e).splitlines()[0][:120]})")
                        new_links = []
                    if depth < a.depth:
                        for link in new_links:
                            nk = normalize_url(link)
                            if nk not in queued and nk not in self.visited and self.allowed(link):
                                queued.add(nk)
                                queue.append((link, depth + 1, url))
                    if count % 10 == 0:
                        self._save_state()
                    time.sleep(a.delay)
            except KeyboardInterrupt:
                log("\nStopped by you - saving what we have so far...")
            finally:
                self._save_state()
                ctx.close()

    def process(self, ctx, page, url: str, parent: str) -> list[str]:
        """Fetch one URL. Saves files directly; renders HTML pages in the browser."""
        resp = ctx.request.get(url, timeout=a_timeout(self.args), max_redirects=10)
        ctype = resp.headers.get("content-type", "").lower()
        if resp.status >= 400:
            log(f"   ! HTTP {resp.status}")
            return []
        if "text/html" not in ctype and "xhtml" not in ctype:
            final_ext = url_extension(resp.url) or url_extension(url)
            if final_ext in SKIP_EXTENSIONS or ctype.startswith(("video/", "audio/")):
                log("   (skipping media)")
                return []
            body = resp.body()
            if len(body) > self.args.max_file_mb * 1024 * 1024:
                log(f"   (skipping file larger than {self.args.max_file_mb} MB)")
                return []
            self.save_file(resp.url, body, resp.headers, parent)
            return []
        page.goto(url, wait_until="domcontentloaded", timeout=a_timeout(self.args))
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightError:
            pass
        if self.args.expand:
            self.expand_sections(page)
        title, md, links = self.page_to_markdown(page)
        if len(md) >= self.args.min_chars:
            self.save_page(page.url, title, md, parent)
        # Also follow iframes' sources (some LMSs embed content/PDF viewers).
        frames = page.evaluate("() => Array.from(document.querySelectorAll('iframe[src],embed[src],object[data]')).map(e => e.src || e.data)")
        return links + [f for f in frames if f]

    @staticmethod
    def expand_sections(page) -> None:
        """Open collapsed sections (accordions / 'show more') - read-only UI toggles."""
        try:
            page.evaluate("""() => {
                document.querySelectorAll('details:not([open])').forEach(d => d.open = true);
                document.querySelectorAll('[aria-expanded="false"]').forEach(el => {
                    const t = (el.innerText || '').toLowerCase();
                    if (el.tagName === 'A' && el.getAttribute('href') && !el.getAttribute('href').startsWith('#')) return;
                    if (/log ?out|sign ?out|delete|remove|submit/.test(t)) return;
                    try { el.click(); } catch (e) {}
                });
            }""")
            page.wait_for_timeout(500)
        except PlaywrightError:
            pass

    # ---------- bundle for Claude ----------
    def build_bundle(self) -> None:
        if not self.records:
            log("Nothing was exported. Check that you were logged in and the start URL is right.")
            return
        # Group content under the top-level page it was found from (usually the course page).
        by_url = {r["url"]: r for r in self.records}

        def course_of(rec: dict) -> str:
            seen = set()
            cur = rec
            chain = [rec]
            while cur.get("parent") and cur["parent"] in by_url and cur["parent"] not in seen:
                seen.add(cur["parent"])
                cur = by_url[cur["parent"]]
                chain.append(cur)
            # chain[-1] is the root (dashboard); the course is the one just below it.
            node = chain[-2] if len(chain) >= 2 else chain[-1]
            return node["title"] or node["url"]

        groups: dict[str, list[dict]] = {}
        for r in self.records:
            groups.setdefault(course_of(r), []).append(r)

        sections: list[str] = []
        toc = ["# Table of contents\n"]
        for course, recs in groups.items():
            toc.append(f"\n## {course}")
            block = [f"\n\n{'=' * 80}\n# COURSE / SECTION: {course}\n{'=' * 80}\n"]
            for r in recs:
                toc.append(f"- {r['title']}  ({r['type']})")
                if r["type"] == "page":
                    block.append(f"\n\n## {r['title']}\nSource: {r['url']}\n\n{r['text']}\n")
                else:
                    body = r["text"] or "(no text could be extracted - upload the original file from the files/ folder)"
                    block.append(f"\n\n## FILE: {r['title']}\nSaved as: {r['path']}\n\n{body}\n")
            sections.append("".join(block))

        header = (
            "# My university course material\n\n"
            "This file was exported from my university learning portal so you (Claude) can help me study.\n"
            "It is organised by course. Each section has the page/file title and where it came from.\n"
            "Please use it to explain concepts, summarise chapters, make flashcards and quiz me.\n\n"
        )
        (self.upload_dir / "00_TABLE_OF_CONTENTS.md").write_text(header + "\n".join(toc) + "\n")

        # Split into parts so each upload stays under size limits.
        limit = self.args.split_chars
        for old in self.upload_dir.glob("ALL_CONTENT_part*.md"):
            old.unlink()
        parts, cur = [], header
        for s in sections:
            while len(s) > limit:  # a single huge course
                room = max(limit - len(cur), 1)
                cur += s[:room]
                parts.append(cur)
                cur, s = "(continued)\n", s[room:]
            if len(cur) + len(s) > limit and cur.strip():
                parts.append(cur)
                cur = ""
            cur += s
        if cur.strip():
            parts.append(cur)
        for i, p in enumerate(parts, 1):
            (self.upload_dir / f"ALL_CONTENT_part{i}.md").write_text(p)

        n_pages = sum(r["type"] == "page" for r in self.records)
        n_files = len(self.records) - n_pages
        no_text = [r for r in self.records if r["type"] == "file" and not r["text"]]
        log("\n" + "=" * 70)
        log(f" Done! {n_pages} pages and {n_files} files exported across {len(groups)} sections.")
        log(f" Upload these to Claude:   {self.upload_dir.resolve()}")
        log(f"   - {len(parts)} ALL_CONTENT_part*.md file(s) + 00_TABLE_OF_CONTENTS.md")
        if no_text:
            log(f"   - plus {len(no_text)} original files (slides/docs/images) from {self.files_dir.resolve()}")
            log("     whose text couldn't be read automatically - Claude can read those directly.")
        log("=" * 70)


def a_timeout(args) -> int:
    return int(args.timeout * 1000)


def main() -> None:
    ap = argparse.ArgumentParser(description="Export course content from your university portal for studying with Claude.")
    ap.add_argument("--start", required=True, help="URL to start from, e.g. your 'My courses' / dashboard page")
    ap.add_argument("--out", default="lola_export", help="output folder (default: lola_export)")
    ap.add_argument("--depth", type=int, default=3, help="how many clicks deep to follow links (default 3)")
    ap.add_argument("--max-pages", type=int, default=1500, help="stop after this many URLs (default 1500)")
    ap.add_argument("--delay", type=float, default=0.7, help="seconds to wait between requests (be polite; default 0.7)")
    ap.add_argument("--timeout", type=float, default=45, help="seconds before a page load is abandoned")
    ap.add_argument("--include", action="append", default=[], help="regex; only follow URLs matching it (repeatable), e.g. --include course")
    ap.add_argument("--exclude", action="append", default=[], help="regex; never follow URLs matching it (repeatable)")
    ap.add_argument("--allow-domain", action="append", default=[], help="extra domain to follow, e.g. a file server (repeatable)")
    ap.add_argument("--max-file-mb", type=float, default=100, help="skip files bigger than this (default 100)")
    ap.add_argument("--min-chars", type=int, default=80, help="don't save pages with less text than this")
    ap.add_argument("--split-chars", type=int, default=1_500_000, help="max characters per ALL_CONTENT part (default 1.5M)")
    ap.add_argument("--no-expand", dest="expand", action="store_false", help="don't auto-open collapsed sections")
    ap.add_argument("--profile", default=".browser-profile", help="folder that keeps your login between runs")
    ap.add_argument("--channel", default="", help="use an installed browser instead of Playwright's: chrome or msedge")
    ap.add_argument("--browser-path", default="", help="path to a Chrome/Chromium executable to use")
    ap.add_argument("--headless", action="store_true", help="no browser window (only works if already logged in via --profile)")
    ap.add_argument("--no-wait", action="store_true", help="don't pause for login")
    ap.add_argument("--fresh", action="store_true", help="ignore previous progress and start over")
    ap.add_argument("--bundle-only", action="store_true", help="just rebuild CLAUDE_UPLOAD from a previous run")
    args = ap.parse_args()

    exp = Exporter(args)
    if not args.bundle_only:
        exp.crawl()
    exp.build_bundle()


if __name__ == "__main__":
    main()
