# Lola → Claude course exporter

Saves everything from your university portal (Lola) to your computer: course pages, chapters,
announcements, and attached files such as PDFs and slides. The output is ready to upload to Claude
so it can help you study.

**Your password stays with you.** The tool opens a normal browser window and *you* log in
yourself (SSO and 2FA work as usual). After that it only **reads** pages. It never submits forms and
it skips links like *log out*, *edit*, *delete*, *submit*, *unenrol* and quiz attempts.

## 1. Install (once)

You need [Python 3.9+](https://www.python.org/downloads/). On Windows, tick *"Add Python to PATH"* in the installer.

- **Windows:** double-click `run_windows.bat`
- **Mac / Linux:** run `./run_mac_linux.sh` in a terminal

Or install it manually:

```bash
pip install -r requirements.txt
playwright install chromium
```

## 2. Export

```bash
python lola_export.py --start "https://YOUR-LOLA-ADDRESS/my/courses"
```

1. A browser window opens. Log in to Lola.
2. Open the page that lists your courses/subjects.
3. Go back to the terminal and press **Enter**. The tool works through every course, chapter
   and file it can reach. Press `Ctrl+C` at any time to stop; run the same command again to resume.

## 3. Use it with Claude

The tool creates a `lola_export/` folder:

| Folder | What's inside |
|---|---|
| `CLAUDE_UPLOAD/` | **Upload these.** `00_TABLE_OF_CONTENTS.md` plus `ALL_CONTENT_part1.md`, `part2`… All text, grouped by course, with the text of your PDFs included |
| `files/` | The original PDFs, slides and documents (upload any that you want Claude to see exactly, e.g. slides with diagrams) |
| `pages/` | Each page saved as its own Markdown file |

Recommended setup: on claude.ai, create a **Project** (for example "Semester 1"), then add the
files from `CLAUDE_UPLOAD/` to its **Project knowledge**. Every chat in that project can then use
your material. Example prompts:

- *"Summarise chapter 3 of Biology 101 and list the key terms."*
- *"Make 20 flashcards from the Chemistry week 1–4 content."*
- *"Quiz me on Lecture 5, one question at a time, and explain what I get wrong."*
- *"Make a revision plan for my exam on the 12th covering all chapters."*

For a lot of material, a separate Project for each course works best.

## Options

| Option | Use |
|---|---|
| `--depth 4` | Follow links deeper (default 3) if chapters are nested deep inside courses |
| `--max-pages 3000` | Allow more pages (default 1500) |
| `--include course --include mod/` | Only follow URLs containing these words (keeps it focused on course content) |
| `--exclude forum` | Skip URLs matching a pattern (repeatable) |
| `--allow-domain files.myuni.edu` | Also download from another domain your uni uses for files |
| `--channel chrome` | Use your installed Google Chrome instead of downloading Chromium |
| `--fresh` | Ignore earlier progress and start over |
| `--bundle-only` | Rebuild the `CLAUDE_UPLOAD` files without crawling again |
| `--delay 1.5` | Wait longer between pages (default 0.7s), to go easy on the server |

## Tips & troubleshooting

- **It exported only 1 page / the login page:** make sure you were fully logged in before pressing
  Enter, and that `--start` is a page you can see when logged in.
- **It's crawling forums or other pages you don't need:** add `--exclude forum` or use `--include`.
- **Videos** are skipped (Claude can't watch them). Lecture recordings with transcripts on the page are saved as text.
- **Scanned PDFs** (photos of pages) have no text to extract. Upload those from `files/` directly; Claude can read them.
- Your login is stored in `.browser-profile/` so you don't need to log in again next time. Delete that folder to log out.
- This tool is for your own personal study. Don't share exported material that your university or your lecturers own.
