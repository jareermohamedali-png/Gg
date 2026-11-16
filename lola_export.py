#!/usr/bin/env python3
"""
lola_export.py - Export your course material from Lola (HEC Liège's Moodle,
https://lola.hec.uliege.be) so you can upload it to Claude and study with it.

  1. Opens a real browser window on Lola. YOU log in yourself (ULiège ID / SSO);
     the script never sees or stores your password.
  2. You press Enter. It asks Lola for the list of courses you're enrolled in and
     lets you pick some or all of them.
  3. For each course it saves the course page, pages, books (whole book at once),
     folders, files (PDF, slides...), assignment instructions and announcements.
  4. It writes one upload-ready Markdown file per course into CLAUDE_UPLOAD/,
     with the text of your PDFs included, plus the original files in files/.

It only reads: it never submits anything, and never opens quizzes, lessons,
grades, participant lists, edit mode or logout links.
Works on other Moodle sites too (--start https://other-moodle/my/), and falls back
to a generic link-following mode on non-Moodle sites (or with --generic).

Usage:   python lola_export.py
         python lola_export.py --course Finance --course Marketing
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


LOLA_URL = "https://lola.hec.uliege.be/my/"

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

# Moodle pages worth exporting: course sections, files, pages, books, folders, assignment
# instructions, glossaries, wikis, announcements/forums. Quizzes, lessons, SCORM, grades,
# participants etc. are never visited.
MOODLE_FOLLOW = re.compile(
    r"/(course/view\.php$|course/section\.php$|pluginfile\.php/|"
    r"mod/(resource|page|book|folder|assign|glossary|wiki|forum|data|imscp|subsection|label)/view\.php$|"
    r"mod/book/tool/print/index\.php$|mod/forum/discuss\.php$)")


class SessionExpired(Exception):
    pass


# Parts of a page that are navigation chrome, not course content.
CHROME_SELECTORS = [
    "header", "footer", "nav", "script", "style", "noscript", "iframe",
    "[role=navigation]", "[role=banner]", "[role=contentinfo]",
    "#nav-drawer", ".navbar", "#page-footer", ".breadcrumb", ".skip-link",
    ".sr-only", "[aria-hidden=true]",
    # Moodle extras
    "#page-header", ".activity-header [data-region=activity-information]", ".completion-info",
    ".automatic-completion-conditions", ".activity-completion", "[data-region=drawer]", ".drawer",
    ".courseindex", "#course-index", ".btn-footer-popover", ".footer-popover", ".modal",
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
        self.pending: list[list] = []   # [url, depth, parent, course] still to visit (for resume)
        self.saved_course_ids: list[int] = []
        self.course_ids: set[int] = set()
        self.file_hashes: set[str] = set()
        self.moodle = False
        self._load_state()

    # ---------- resume support ----------
    def _load_state(self) -> None:
        if self.state_path.exists() and not self.args.fresh:
            data = json.loads(self.state_path.read_text())
            self.visited = set(data.get("visited", []))
            self.records = data.get("records", [])
            self.pending = data.get("pending", [])
            self.saved_course_ids = data.get("course_ids", [])
            self.file_hashes = set(data.get("file_hashes", []))
            log(f"Resuming: {len(self.visited)} URLs already done (use --fresh to start over).")

    def _save_state(self) -> None:
        self.state_path.write_text(json.dumps({"visited": sorted(self.visited), "records": self.records,
                                                 "pending": [list(x) for x in self.queue],
                                                 "course_ids": sorted(self.course_ids),
                                                 "file_hashes": sorted(self.file_hashes)}, indent=1))

    # ---------- link filtering ----------
    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        if p.netloc.lower() not in self.allowed_hosts:
            return False
        if url_extension(url) in SKIP_EXTENSIONS:
            return False
        is_file = "/pluginfile.php/" in p.path
        # File downloads are read-only, so file *names* containing e.g. "edit" are fine.
        if not is_file and any(rx.search(url) for rx in self.exclude):
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
            conv.ignore_links = True  # Claude can't open your private Lola links anyway; keeps text clean
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
    def save_file(self, url: str, body: bytes, headers: dict, parent: str, course: str = "") -> None:
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
        digest = hashlib.sha1(body).hexdigest()
        if digest in self.file_hashes:
            log(f"   (already have {name})")
            return
        self.file_hashes.add(digest)
        dest = self.files_dir / f"{slugify(stem)}_{short_hash(url)}{ext}"
        dest.write_bytes(body)
        text = self.extract_file_text(dest)
        self.records.append({"type": "file", "url": url, "title": name, "path": str(dest.relative_to(self.out)),
                             "text": text, "parent": parent, "course": course})
        log(f"   [file] {name}  ({len(body)//1024} KB)")

    def save_page(self, url: str, title: str, md: str, parent: str, course: str = "") -> None:
        dest = self.pages_dir / f"{len(self.records):04d}_{slugify(title)}.md"
        dest.write_text(f"# {title}\n\nSource: {url}\n\n{md}\n")
        self.records.append({"type": "page", "url": url, "title": title, "path": str(dest.relative_to(self.out)),
                             "text": md, "parent": parent, "course": course})
        log(f"   [page] {title}  ({len(md)} chars)")

    # ---------- Moodle (Lola) support ----------
    def is_moodle(self, page) -> bool:
        try:
            return bool(page.evaluate("() => !!(window.M && M.cfg && M.cfg.wwwroot)"))
        except PlaywrightError:
            return False

    def moodle_courses(self, page) -> list[dict]:
        """Ask Moodle for the courses you are enrolled in (same call the 'My courses' page makes)."""
        root = page.evaluate("() => M.cfg.wwwroot")
        if "/my" not in page.url:
            page.goto(root + "/my/courses.php", wait_until="domcontentloaded")
        try:
            courses = page.evaluate(r"""async () => {
                const call = async (cls) => {
                    const fn = 'core_course_get_enrolled_courses_by_timeline_classification';
                    const r = await fetch(M.cfg.wwwroot + '/lib/ajax/service.php?sesskey=' + M.cfg.sesskey + '&info=' + fn, {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify([{index: 0, methodname: fn,
                              args: {offset: 0, limit: 0, classification: cls, sort: 'fullname'}}])});
                    const j = await r.json();
                    if (!j[0] || j[0].error) throw new Error('ajax failed');
                    return j[0].data.courses;
                };
                try { return await call('allincludinghidden'); } catch (e) { return await call('all'); }
            }""")
            out = [{"id": int(c["id"]), "name": c.get("fullname") or c.get("shortname") or str(c["id"]),
                    "url": c.get("viewurl") or f"{root}/course/view.php?id={c['id']}"} for c in courses]
            if out:
                return out
        except Exception as e:
            log(f"(couldn't ask Moodle for your course list directly: {str(e)[:80]} - reading the page instead)")
        # Fallback: collect course links from the dashboard pages.
        found: dict[int, dict] = {}
        for u in (root + "/my/courses.php", root + "/my/"):
            try:
                page.goto(u, wait_until="domcontentloaded")
                page.wait_for_load_state("networkidle", timeout=10000)
            except PlaywrightError:
                pass
            for href, text in page.evaluate("""() => Array.from(document.querySelectorAll('a[href*="course/view.php?id="]'))
                                               .map(a => [a.href, a.innerText.trim()])"""):
                m = re.search(r"[?&]id=(\d+)", href)
                if m and int(m.group(1)) > 1:
                    cid = int(m.group(1))
                    if text and (cid not in found or len(text) > len(found[cid]["name"])):
                        found[cid] = {"id": cid, "name": text.splitlines()[-1].strip() or text, "url": href}
        return list(found.values())

    def choose_courses(self, courses: list[dict]) -> list[dict]:
        a = self.args
        if a.course:
            rx = [re.compile(p, re.I) for p in a.course]
            courses = [c for c in courses if any(r.search(c["name"]) for r in rx)]
        log(f"\nFound {len(courses)} course(s):")
        for i, c in enumerate(courses, 1):
            log(f"  {i:2d}. {c['name']}")
        if a.no_wait or a.course or not courses:
            return courses
        ans = input("\nPress ENTER to export ALL of them, or type the numbers you want (e.g. 1,3,5): ").strip()
        if not ans:
            return courses
        picks = {int(x) for x in re.findall(r"\d+", ans)}
        return [c for i, c in enumerate(courses, 1) if i in picks]

    def moodle_rewrite(self, url: str) -> str | None:
        """Map a Moodle link to the URL we actually want to fetch, or None to skip it."""
        p = urlparse(url)
        path = p.path
        q = dict(parse_qsl(p.query))
        if not MOODLE_FOLLOW.search(path):
            return None
        if "action" in q or "edit" in q or "sesskey" in q:
            return None
        if path.endswith("/course/view.php") or path.endswith("/course/section.php"):
            if path.endswith("/course/view.php") and int(q.get("id", "0") or 0) not in self.course_ids:
                return None
            return url
        if path.endswith("/mod/book/view.php"):  # whole book on one page
            return urlunparse(p._replace(path=path.replace("/mod/book/view.php", "/mod/book/tool/print/index.php"),
                                         query=urlencode({"id": q.get("id", "")})))
        if path.endswith("/mod/resource/view.php"):  # go straight to the file
            return urlunparse(p._replace(query=urlencode({"id": q.get("id", ""), "redirect": 1})))
        if not self.args.forums and "/mod/forum/" in path:
            return None
        return url

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
                log(" 2. Wait until you see your dashboard / course list.")
                log(" 3. Come back here and press ENTER to start exporting.")
                log("=" * 70)
                input()
            self.allowed_hosts.add(urlparse(page.url).netloc.lower())

            self.moodle = not a.generic and self.is_moodle(page)
            if self.moodle and "/login/" in page.url:
                log("It looks like you're not logged in yet (still on the login page). Run again and log in first.")
                ctx.close()
                return

            if self.pending:
                queue = self.queue = deque(tuple(x) for x in self.pending)
                self.course_ids = set(self.saved_course_ids)
            elif self.moodle:
                log("Detected a Moodle site (like Lola) - exporting course by course.")
                courses = self.choose_courses(self.moodle_courses(page))
                if not courses:
                    log("No courses found. Make sure you're logged in, or try --generic.")
                    ctx.close()
                    return
                self.course_ids = {c["id"] for c in courses}
                queue = self.queue = deque((c["url"], 0, "", c["name"]) for c in courses)
            else:
                seeds = [self.start] + ([page.url] if normalize_url(page.url) != normalize_url(self.start) else [])
                queue = self.queue = deque((s, 0, "", "") for s in seeds)
            queued = {normalize_url(x[0]) for x in queue} | self.visited
            count = 0
            try:
                while queue and count < a.max_pages:
                    url, depth, parent, course = queue.popleft()
                    key = normalize_url(url)
                    if key in self.visited:
                        continue
                    self.visited.add(key)
                    count += 1
                    log(f"[{count}/{a.max_pages}] {course[:40] + ' | ' if course else ''}{url}")
                    try:
                        new_links = self.process(ctx, page, url, parent, course)
                    except SessionExpired:
                        self.visited.discard(key)
                        queue.appendleft((url, depth, parent, course))
                        log("\nYour Lola session seems to have expired (got sent to the login page).")
                        log("Progress is saved - just run the same command again, log in, and it will continue.")
                        break
                    except PlaywrightError as e:
                        log(f"   ! skipped ({str(e).splitlines()[0][:120]})")
                        new_links = []
                    if depth < a.depth:
                        for link in new_links:
                            if self.moodle:
                                link = self.moodle_rewrite(link) if self.allowed(link) else None
                                if not link:
                                    continue
                            elif not self.allowed(link):
                                continue
                            nk = normalize_url(link)
                            if nk not in queued:
                                queued.add(nk)
                                queue.append((link, depth + 1, url, course))
                    if count % 10 == 0:
                        self._save_state()
                    time.sleep(a.delay)
            except KeyboardInterrupt:
                log("\nStopped by you - saving what we have so far (run again to continue)...")
            finally:
                self._save_state()
                ctx.close()

    def process(self, ctx, page, url: str, parent: str, course: str = "") -> list[str]:
        """Fetch one URL. Saves files directly; renders HTML pages in the browser."""
        resp = ctx.request.get(url, timeout=a_timeout(self.args), max_redirects=10)
        ctype = resp.headers.get("content-type", "").lower()
        if self.moodle and "/login/" in urlparse(resp.url).path:
            raise SessionExpired()
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
            self.save_file(resp.url, body, resp.headers, parent, course)
            return []
        page.goto(url, wait_until="domcontentloaded", timeout=a_timeout(self.args))
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightError:
            pass
        if self.moodle and "/login/" in urlparse(page.url).path:
            raise SessionExpired()
        if self.args.expand and not self.moodle:  # Moodle already has collapsed content in the page
            self.expand_sections(page)
        title, md, links = self.page_to_markdown(page)
        if len(md) >= self.args.min_chars:
            self.save_page(page.url, title, md, parent, course)
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
        by_url = {r["url"]: r for r in self.records}

        def course_of(rec: dict) -> str:
            if rec.get("course"):
                return rec["course"]
            # Generic sites: use the top-level page this was found from (usually the course page).
            seen, cur, chain = set(), rec, [rec]
            while cur.get("parent") and cur["parent"] in by_url and cur["parent"] not in seen:
                seen.add(cur["parent"])
                cur = by_url[cur["parent"]]
                chain.append(cur)
            node = chain[-2] if len(chain) >= 2 else chain[-1]
            return node["title"] or node["url"]

        groups: dict[str, list[dict]] = {}
        for r in self.records:
            groups.setdefault(course_of(r), []).append(r)

        for old in self.upload_dir.glob("*.md"):
            old.unlink()
        limit = self.args.split_chars
        toc = ["# My university course material - table of contents\n",
               "Each course below has its own file in this folder.\n"]
        n_files_written = 0
        for n, (course, recs) in enumerate(groups.items(), 1):
            header = (f"# {course}\n\n"
                      "Course material exported from my university learning platform (Lola, HEC Liège) "
                      "so you (Claude) can help me study it.\n"
                      "Each section below is one page or file from the course, with where it came from.\n"
                      "Please use it to explain concepts, summarise chapters, make flashcards and quiz me.\n\n"
                      "Contents of this course:\n" +
                      "".join(f"- {r['title']}{' (file)' if r['type'] == 'file' else ''}\n" for r in recs) + "\n")
            body = []
            for r in recs:
                if r["type"] == "page":
                    body.append(f"\n\n{'-' * 60}\n## {r['title']}\nSource: {r['url']}\n\n{r['text']}\n")
                else:
                    txt = r["text"] or "(the text of this file couldn't be extracted - the original is in the files/ folder)"
                    body.append(f"\n\n{'-' * 60}\n## FILE: {r['title']}\nOriginal saved as: {r['path']}\n\n{txt}\n")
            content = header + "".join(body)
            chunks = [content[i:i + limit] for i in range(0, len(content), limit)] or [content]
            base = f"{n:02d}_{slugify(course, 60)}"
            names = []
            for i, chunk in enumerate(chunks, 1):
                name = f"{base}.md" if len(chunks) == 1 else f"{base}_part{i}.md"
                if i > 1:
                    chunk = f"# {course} (continued, part {i})\n\n" + chunk
                (self.upload_dir / name).write_text(chunk)
                names.append(name)
            n_files_written += len(names)
            toc.append(f"\n## {course}\nFile(s): {', '.join(names)}")
            toc.extend(f"- {r['title']}{' (file)' if r['type'] == 'file' else ''}" for r in recs)
        (self.upload_dir / "00_TABLE_OF_CONTENTS.md").write_text("\n".join(toc) + "\n")

        n_pages = sum(r["type"] == "page" for r in self.records)
        n_files = len(self.records) - n_pages
        no_text = [r for r in self.records if r["type"] == "file" and not r["text"]]
        log("\n" + "=" * 70)
        log(f" Done! {n_pages} pages and {n_files} files exported from {len(groups)} course(s).")
        log(f" Upload these to Claude:   {self.upload_dir.resolve()}")
        log(f"   - one .md file per course ({n_files_written} files) + 00_TABLE_OF_CONTENTS.md")
        if no_text:
            log(f"   - plus {len(no_text)} original file(s) (slides/docs/scans) from {self.files_dir.resolve()}")
            log("     whose text couldn't be read automatically - upload those directly, Claude can read them.")
        log("=" * 70)


def a_timeout(args) -> int:
    return int(args.timeout * 1000)


def main() -> None:
    ap = argparse.ArgumentParser(description="Export course content from your university portal for studying with Claude.")
    ap.add_argument("--start", default=LOLA_URL, help=f"site / page to start from (default: {LOLA_URL})")
    ap.add_argument("--course", action="append", default=[], help="only export courses whose name matches this (repeatable), e.g. --course Finance")
    ap.add_argument("--no-forums", dest="forums", action="store_false", help="skip forums / announcements")
    ap.add_argument("--generic", action="store_true", help="don't use Moodle mode; just follow links like a normal website")
    ap.add_argument("--out", default="lola_export", help="output folder (default: lola_export)")
    ap.add_argument("--depth", type=int, default=3, help="how many clicks deep to follow links from each course (default 3)")
    ap.add_argument("--max-pages", type=int, default=1500, help="stop after this many URLs (default 1500)")
    ap.add_argument("--delay", type=float, default=0.7, help="seconds to wait between requests (be polite; default 0.7)")
    ap.add_argument("--timeout", type=float, default=45, help="seconds before a page load is abandoned")
    ap.add_argument("--include", action="append", default=[], help="regex; only follow URLs matching it (repeatable), e.g. --include course")
    ap.add_argument("--exclude", action="append", default=[], help="regex; never follow URLs matching it (repeatable)")
    ap.add_argument("--allow-domain", action="append", default=[], help="extra domain to follow, e.g. a file server (repeatable)")
    ap.add_argument("--max-file-mb", type=float, default=100, help="skip files bigger than this (default 100)")
    ap.add_argument("--min-chars", type=int, default=20, help="don't save pages with less text than this")
    ap.add_argument("--split-chars", type=int, default=1_500_000, help="max characters per upload file before it is split (default 1.5M)")
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
