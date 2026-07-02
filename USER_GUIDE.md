# linkedin-stats-v1 - User Guide

A friendly, step-by-step guide for **non-technical users**. By the end you'll be able to install the tool and produce a spreadsheet of the people who **liked, commented on and reposted** a LinkedIn company's posts.

No programming knowledge needed, just follow the steps in order and copy/paste the commands exactly.

> Developers: the technical README is in [README.md](README.md).

## 1. What this tool does

You give it:

- a **LinkedIn company page** (or a personal profile), and
- a **date range** (a start date and an end date).

It then visits that company's posts published in that period, looks at **who liked**, **who commented** and **who reposted** each post, and produces a single spreadsheet (`.csv`, opens in Excel) ranking people by how much they engaged.

The spreadsheet has one row per person, with these columns:

| Column                       | Meaning                                                            |
| ---------------------------- | ------------------------------------------------------------------ |
| `name`                       | The person's name                                                  |
| `profile_url`                | Link to their LinkedIn profile                                     |
| `headline`                   | Their job title / tagline (when available)                         |
| `likes_count`                | How many of the posts they liked                                   |
| `comments_count`             | How many comments they left                                        |
| `repost_count`               | How many of the posts they reposted **without** adding a comment   |
| `repost_with_comment_count`  | How many of the posts they reposted **with** their own comment     |

The two repost columns never overlap (a repost is either plain or with a comment), so adding them together gives the total number of times that person reshared the posts. People are still ranked by likes then comments; the repost columns are extra information shown alongside.

**Good to know up front:**

- It runs **on your own computer**, using **your own LinkedIn account**. Nothing is sent anywhere else.
- It is for **personal use**. Please respect LinkedIn's Terms of Service and don't run it aggressively.
- Dates are **approximate** (LinkedIn only shows "2 weeks ago", "3 months ago", etc.), so a post right on the edge of your range may be slightly off.
- It can be **slow** (it opens each post one by one). A couple of minutes for a dozen posts is normal.

## 2. What you'll need

- A **Windows computer** (this guide is written for Windows 11; Mac/Linux notes are included where commands differ).
- Your **LinkedIn login** (email + password, and your phone if LinkedIn asks for a code).
- About **30 minutes** for the one-time setup.
- Two free programs we'll install: **Google Chrome** and **Python**.

You only do the setup (sections 3–6) **once**. After that, producing a report is just one command (section 8).

## 3. Install Google Chrome

If you already have Google Chrome, skip this.

1. Go to **https://www.google.com/chrome/**
2. Click **Download Chrome** and run the installer.

(The tool uses Chrome because LinkedIn trusts it and logs in more reliably.)

## 4. Install Python (the engine the tool runs on)

1. Go to **https://www.python.org/downloads/windows/**
2. Download the **"Windows installer (64-bit)"** for **Python 3.12** or **3.13**. ⚠️ It **must be 64-bit**, the tool will not install on the 32-bit version.
3. Run the installer. **Very important:** on the first screen, tick the box **"Add python.exe to PATH"** at the bottom, then click **Install Now**.

To check it worked, open the **Start menu**, type **PowerShell**, open it, and type:

```powershell
python --version
```

You should see something like `Python 3.12.x` or `Python 3.13.x`. If you see an error or a different version, see **Troubleshooting** at the end.

> **Mac:** install Python 3.12/3.13 from python.org or with Homebrew
> (`brew install python@3.12`).
> **Linux (Debian/Ubuntu):** `sudo apt install python3.12 python3.12-venv`.

## 5. Download the project

The tool lives on GitHub at:

**https://github.com/LalbaAnthony/linkedin-stats-v1**

The simplest way to get it (no extra software needed) is to download it as a ZIP and unzip it:

1. Open the page above in your web browser.
2. Click the green **`<> Code`** button, then **Download ZIP**.
3. Open your **Downloads** folder and find the file **`linkedin-stats-v1-main.zip`**.
4. **Unzip it:** right-click the file, choose **Extract All...**, pick a location that's easy to find (for example your **Documents** folder), then click **Extract**.
5. You now have a folder called **`linkedin-stats-v1-main`**. Rename it to **`linkedin-stats-v1`** (right-click it, choose **Rename**) so it matches the rest of this guide.

You should end up with a folder like:
`C:\Users\YourName\Documents\linkedin-stats-v1`

Open it and check you can see files such as `main.py` and `README.md` **directly inside** (not hidden in a second sub-folder of the same name). If they sit one level deeper, move them up so they are directly inside your `linkedin-stats-v1` folder.

> **Mac:** double-click the downloaded `.zip` to unzip it, then rename the resulting `linkedin-stats-v1-main` folder to `linkedin-stats-v1`.
> **Linux (Debian/Ubuntu):** unzip it from the file manager (right-click, Extract Here) or run `unzip linkedin-stats-v1-main.zip`, then rename the folder to `linkedin-stats-v1`.

> **Already comfortable with Git?** Instead of the ZIP, you can run
> `git clone https://github.com/LalbaAnthony/linkedin-stats-v1.git`, which creates the `linkedin-stats-v1` folder directly.

## 6. Open a terminal inside the folder

The "terminal" (also called PowerShell) is the blue/black window where you type commands.

1. Open **File Explorer** and go **into** the `linkedin-stats-v1` folder (you should see files like `main.py` and `README.md` inside).
2. Click once in the **address bar** at the top (where the folder path is shown).
3. Type **`powershell`** and press **Enter**.

A PowerShell window opens, already pointed at the folder. Keep it open for the next steps.

> **Mac/Linux:** open **Terminal**, then type `cd ` (with a space) and drag the folder onto the window, then press Enter.

## 7. One-time setup

Copy these commands **one at a time** into the PowerShell window (paste with a right-click), pressing **Enter** after each. They prepare a private workspace and download what the tool needs.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

- The first line is quick.
- The second downloads the tool's components (about a minute).
- The third downloads a backup browser (a bit larger; one or two minutes).

When the prompt (the `>` line) comes back with no red error, setup is done. You won't need to repeat this.

> **Mac/Linux:** replace `.\.venv\Scripts\python.exe` with `.venv/bin/python` in every command, here and below.

## 8. Produce your first report

### a) Find the company's LinkedIn address

1. In your normal browser, open the company's LinkedIn page.
2. Copy the web address from the address bar. It looks like:
   `https://www.linkedin.com/company/agoravita`

(You can also use a **person's** profile address, which looks like `https://www.linkedin.com/in/firstname-lastname`.)

### b) Choose your dates

Dates are written **year-month-day**, like `2025-01-01` (1 January 2025). The start date must be **on or before** the end date.

### c) Run the command

In the PowerShell window (still inside the folder), type the command below, **replace the address and the two dates** with yours:

```powershell
.\.venv\Scripts\python.exe main.py --author "https://www.linkedin.com/company/agoravita" --start 2025-01-01 --end 2025-03-31
```

Tip: keep the quotation marks `"` around the address.

### d) Log in (first run only)

The very first time, a **Chrome window opens on the LinkedIn login page**:

1. **Log in** in that window (email, password, and a phone code if LinkedIn asks).
2. Once you can see your normal LinkedIn feed, **go back to the PowerShell window and press Enter**.

The tool saves your session, so on later runs it **won't ask you to log in again**.

> While it works, the Chrome window will scroll and open posts **by itself** that's normal. Don't click inside it or close it until it's finished.

### e) Wait for it to finish

When it's done, you'll see a short summary, like:

```
Posts analysed: 13
Posts skipped: 0
Likes collected: 104
Comments collected: 18
Reposts collected: 7
Reposts with comment collected: 2
Unique people: 26
CSV generated: ...\output\results.csv
```

## 9. Open your results

The spreadsheet is created inside the project folder at:

```
linkedin-stats-v1\output\results.csv
```

Double-click it to open it in **Excel** (or Google Sheets). Accented names (é, à, …) display correctly. People are sorted from most likes to least.

> If you re-run the tool, this file is **overwritten**. To keep a copy, rename it (e.g. `results-january.csv`) or add `--output output/my-report.csv` to the command to choose a different file name.

## 10. Doing it again (new company or new dates)

You only need section **8c** again. Open PowerShell in the folder (section 6) and run the command with the new address and/or dates. No login, no setup, unless your LinkedIn session has expired (then it will ask you to log in once more).

## 11. Tips and good-to-knows

- **It's normal for it to take a while.** Each post is opened individually and its comments are loaded.
- **Looking far back in time?** If your date range is more than a few months ago and the report seems to miss posts, see "missing older posts" in Troubleshooting.
- **Approximate dates:** a post shown as "2 months ago" is estimated, so results near the very edges of your range can be slightly imprecise.
- **One LinkedIn account:** use the tool with your own account and at a reasonable pace, in line with LinkedIn's Terms of Service.

## 12. Troubleshooting

| What you see                                                                         | What to do                                                                                                                                                                                                                                                                |
| ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `python` opens the **Microsoft Store**, or `python --version` errors                 | Python isn't installed correctly. Reinstall from python.org and **tick "Add python.exe to PATH"** (section 4). On Windows 11 you may also need to turn off the Store shortcut: Start → search **"Manage app execution aliases"** → turn **off** the two "python" entries. |
| `... is not recognized` after a command                                              | You're probably not inside the project folder, or setup wasn't finished. Redo section 6, then section 7.                                                                                                                                                                  |
| Red error mentioning **"Could not find a version" / no `pandas`/`playwright`**       | Your Python is likely **32-bit** (or version 3.14). Install **64-bit Python 3.12 or 3.13** (section 4), delete the `.venv` folder, and redo section 7.                                                                                                                    |
| `running scripts is disabled on this system`                                         | You don't need to "activate" anything, just always start commands with `.\.venv\Scripts\python.exe` as shown in this guide.                                                                                                                                               |
| It keeps asking me to log in / times out                                             | Log in **inside the Chrome window the tool opened** (not your usual browser), reach your feed, then press **Enter** in PowerShell. Make sure you completed any phone/2FA step.                                                                                            |
| **0 likes / 0 comments**, or it seems to miss things                                 | LinkedIn occasionally changes its website, which can break the tool. Re-run the exact command but add **`--debug`** at the end, then send the project's `output\debug_*.html` files to whoever maintains the tool so they can fix it.                                     |
| **Repost columns are all `0`** (but you can see reposts on LinkedIn)                  | Reposts are read from the feed and LinkedIn may show that part differently for your account. Re-run with **`--debug`** and send the `output\debug_reposts_*.html` and `output\debug_repost_card.html` files to whoever maintains the tool so the repost reader can be tuned. The tool never reposts anything itself. |
| **Missing older posts** (report has fewer posts than expected for an old date range) | LinkedIn loads newest posts first. Create a file named `.env` in the folder containing one line, `LINKEDIN_MAX_FEED_SCROLLS=400`, then re-run. Increase the number further if needed.                                                                                     |
| A Chrome window stays open after it finishes                                         | You can close it normally.                                                                                                                                                                                                                                                |

## 13. Where things are (quick reference)

- **The tool:** the `linkedin-stats-v1` folder.
- **Your report:** `linkedin-stats-v1\output\results.csv`.
- **Your saved login:** `linkedin-stats-v1\sessions\linkedin.json` (delete this file if you want to force a fresh login).
- **The command to make a report:** `.\.venv\Scripts\python.exe main.py --author "<address>" --start <YYYY-MM-DD> --end <YYYY-MM-DD>`

If you get stuck, send a screenshot of the PowerShell window to the person who gave you the tool, the messages there usually explain what went wrong.
