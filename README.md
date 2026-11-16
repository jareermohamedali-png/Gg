# Lola → Claude course exporter

Downloads all your course material from **[Lola](https://lola.hec.uliege.be)** (HEC Liège's Moodle):
course pages, chapters and books, PDFs and slides, folders, assignment instructions and announcements.
You get **one tidy file per course**, ready to upload to Claude so it can help you study.

**Your password stays with you.** A normal browser window opens and *you* log in with your ULiège
account. The tool then only **reads**. It never submits anything, and it never opens quizzes,
lessons, grades, participant lists, edit mode or the logout link.

## 1. Install (once)

You need [Python 3.9+](https://www.python.org/downloads/). On Windows, tick **"Add Python to PATH"** in the installer.
Then download this repo (green **Code** button → *Download ZIP*) and unzip it.

## 2. Choose your courses (optional)

Open **`my_courses.txt`** in Notepad. It lists the courses to export, one per line, either as a Lola
course link or as a name. Only those courses are exported. Names ignore accents and capitals, and `|`
means "or" (e.g. `english | anglais`). Delete all the lines, or run with `--all-courses`, to choose
from every course instead.

## 3. Run it

- **Windows:** double-click `run_windows.bat`
- **Windows with Opera GX:** double-click `run_windows_operagx.bat` (uses your Opera GX, so there's no extra browser to download)
- **Mac:** open Terminal in the folder and run `bash run_mac_linux.sh`

(Or by hand: `pip install -r requirements.txt`, `playwright install chromium`, `python lola_export.py`.)

1. A browser window opens on Lola. **Log in** and wait until you see your dashboard.
2. Go back to the terminal window and press **Enter**.
3. It shows which courses from `my_courses.txt` it found (`OK`) and any it couldn't find (`??`), then starts.
4. Wait while it works. Each page and file is printed as it is saved. Press `Ctrl+C` to pause at any
   time; run it again to continue from where it stopped.

## 4. Study with Claude

The results are in the `lola_export/` folder:

| Folder | What's inside |
|---|---|
| `CLAUDE_UPLOAD/` | **Upload these.** One `.md` file per course (e.g. `01_Finance_dentreprise.md`), containing every page, chapter, announcement and the text of the PDFs, plus `00_TABLE_OF_CONTENTS.md` |
| `files/` | The original PDFs, slides and documents |
| `pages/` | Each page as its own file (you don't normally need these) |

**Recommended:** on claude.ai, create one **Project per course**, open *Project knowledge*, and add that
course's `.md` file. For slides with important diagrams, also add the original PDF from `files/`.
Every chat in the project then knows your course. Try:

- *"Résume le chapitre 3 et donne-moi les définitions clés."*
- *"Make 25 flashcards covering the whole course."*
- *"Quiz me on NPV and capital budgeting, one question at a time, and correct me."*
- *"What did the announcements say about the exam? Make me a revision plan until then."*

## Options

| Command | What it does |
|---|---|
| `python lola_export.py --course Finance` | Only courses whose name contains "Finance" (repeatable) |
| `python lola_export.py --no-forums` | Skip forums/announcements |
| `python lola_export.py --depth 4` | Look deeper inside courses (default 3) |
| `python lola_export.py --fresh` | Forget earlier progress and start over |
| `python lola_export.py --bundle-only` | Rebuild the upload files without downloading again |
| `python lola_export.py --opera` | Use your installed Opera GX |
| `python lola_export.py --cdp-browser "C:\path\to\browser.exe"` | Use any other Chrome-like browser (Brave, Edge, Opera, Vivaldi...) |
| `python lola_export.py --channel chrome` | Use your installed Google Chrome instead of downloading Chromium |
| `python lola_export.py --start https://other-moodle/my/` | Use another Moodle site |

Run `python lola_export.py --help` to see all options.

## Good to know

- **Re-running later in the semester:** use `--fresh` to pick up newly added material.
- **"Session expired":** progress is saved. Run it again, log in, and it carries on.
- **Viewing counts as viewing:** Moodle records that you opened each page or file, as if you had clicked
  it yourself. Activities that are set to "complete when viewed" may get ticked off.
- **Not exported:** videos (Claude can't watch them), quizzes, and external links such as YouTube.
- **Scanned PDFs** (photos of pages) have no text to extract. Upload those from `files/` directly; Claude can read them.
- **Opera GX:** the tool opens a *separate* Opera GX window with its own clean profile, so your normal
  tabs, passwords and extensions aren't touched. You log in to Lola once in that window. If it says it
  can't connect, fully quit Opera GX (including from the system tray) and try again.
- Your login is kept in `.browser-profile/` so next time you may not need to log in. Delete that folder to sign out.
- This is for your **own study**. Course material belongs to HEC Liège and your lecturers, so don't share it.
