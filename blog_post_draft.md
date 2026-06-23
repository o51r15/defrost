# Introducing deFrost: A Simple Tool to Protect Your Browser from a Tracking Attack Nobody Is Fixing

There is a new browser exploit making the rounds in security research circles.
It does not require you to download anything malicious. It does not need admin
access to your machine. It runs entirely inside a browser tab, in plain
JavaScript, and it can figure out what websites you have open in other browsers
and what applications you are running on your computer — with accuracy that
should make you uncomfortable.

It is called FROST. And as of right now, no browser vendor is doing anything
meaningful about it.

So I decided to build something that does.

---

## What FROST Actually Does

Researchers at Graz University of Technology published a paper this year
describing an attack they named FROST — Fingerprinting Remotely using
OPFS-based SSD Timing. It is scheduled for formal presentation at the DIMVA
security conference in July 2026, but the details are already public and the
technique is real.

Here is the short version of how it works.

Modern browsers support something called the Origin Private File System, or
OPFS. It was introduced in 2023 to let web applications — things like
in-browser code editors, video editors, and productivity tools — store files
locally on your machine without asking for permission. It is a legitimate
feature used by legitimate software. Google, Microsoft, and Adobe are all
building applications that rely on it.

The problem is that OPFS lets a webpage create files directly on your SSD and
read them back at high speed. And SSDs have a measurable property that turns
out to be very useful for an attacker: when multiple things are reading from or
writing to the drive at the same time, they slow each other down. That slowdown
is called contention, and it is detectable from JavaScript running in a browser
tab.

FROST works by having a malicious page create a large file using OPFS and then
hammering it with continuous random reads while carefully timing how long each
read takes. When your browser loads a website, it reads various files from your
browser profile stored on the same SSD. That causes contention. The timing
changes. The malicious page notices.

A machine learning classifier trained on those timing patterns can then identify
which website you loaded with roughly 89% accuracy across the top 50 sites
tested. It can identify which desktop application you launched with roughly 96%
accuracy. And it works across browsers — a malicious page open in Chrome can
fingerprint what you are doing in Firefox, because both browsers are reading
from the same physical drive.

The researchers disclosed their findings to Google, Mozilla, and Apple before
publishing. Google said it does not consider fingerprinting a security
vulnerability. Apple said it was out of scope. Mozilla acknowledged it and
shipped nothing. The attack surface remains fully open in every major desktop
browser today.

---

## Why This Matters More Than a Typical Lab Demo

Most security research papers describe attacks that are clever in theory but
impractical in the real world. FROST sits in a more uncomfortable position.

The accuracy numbers are high enough to be genuinely useful for surveillance.
The attack runs in a browser tab with no special permissions. It works
remotely — the attacker does not need to be on your machine or your network.
And the underlying mechanism is not a bug that can be patched with a software
update. It is a consequence of how SSDs work physically and how browsers are
increasingly given access to hardware-level APIs.

There is no confirmed exploitation in the wild yet. But the technique is
documented, the code to implement it is straightforward for anyone who has read
the paper, and the browser vendors have collectively decided it is not their
problem to solve urgently.

That gap between a working attack and a vendor fix is exactly where users are
most exposed.

---

## The Fix Is Simpler Than You Might Think

The attack depends entirely on the browser reading your profile data from your
SSD. That is what creates the contention signal FROST measures.

If your browser profile lives in RAM instead of on the SSD, there is no SSD
read to measure. No contention. No timing signal. No fingerprinting. The attack
channel disappears completely.

This is not a novel idea. Security researchers including the FROST authors
themselves noted that moving the browser profile to RAM eliminates the attack.
But there is no easy way for a regular user to do that on Windows today. It
requires understanding RAM disks, junction points, and profile path redirection
— none of which are things most people should have to know about.

That is the gap deFrost is built to fill.

---

## What deFrost Is

deFrost is a small, portable Windows utility that makes moving your browser
profile to RAM a one-click operation.

You run it, it detects which browsers you have installed, you pick a browser
and a profile, and it handles everything else. It calculates how much RAM is
needed, creates a RAM disk, copies your profile into it, and redirects your
browser to use the in-RAM copy. Your browser runs normally. Websites load
normally. You do not notice anything different — except that FROST now has
nothing to measure.

When you are done browsing and want your changes saved back to disk, you
deactivate it. It syncs everything back and releases the RAM. Clean in, clean
out.

The interface is a simple web page that opens in your browser, served locally
from the app itself. A small icon in your system tray shows whether protection
is active. That is the entire user-facing experience.

---

## How the Write-Back Works

One of the more interesting design decisions involved figuring out when to sync
changes back to disk while protection is active.

The obvious approach is a timer — sync every 15 minutes or every hour. But a
timer is arbitrary and creates a predictable write pattern on the SSD, which
has its own minor implications and requires answering uncomfortable questions
like "what if the user is mid-session when the timer fires?"

The approach deFrost uses instead is a size threshold. The RAM disk is
allocated as the profile size plus a configurable write buffer — say 200MB by
default. The app monitors how much new data has been written to the RAM disk
since the last sync. When that delta reaches the buffer threshold, it flushes
only the changed files back to disk and resets the counter.

This means syncs happen when there is actually something meaningful to write,
not on a schedule. A light browsing session might never trigger a sync. A heavy
session with lots of downloads and cache activity will sync naturally as needed.
And critically, only changed files are written — not the entire profile — so
the operation is fast and the SSD impact is minimal.

The protection holds throughout. Browser reads always come from RAM. The
periodic write-back generates bulk sequential writes on a fixed threshold, which
is a completely different I/O signature from the read contention FROST looks
for. The timing channel stays dead.

---

## Browser Support

The initial release targets Chrome and Firefox, which covers the majority of
desktop browser users.

Chrome and the broader Chromium family — Edge, Brave, DuckDuckGo, Vivaldi —
all use the same profile folder structure, so adding them requires minimal
additional work and they follow close behind in the development roadmap.

Firefox is architecturally different. It uses a profile manager with
randomized folder names, and finding the active profile requires parsing a
configuration file rather than looking in a predictable location. That extra
step is handled automatically — the user just sees a dropdown of their Firefox
profiles with sensible names.

---

## What deFrost Is Not

Being clear about scope is important for any security tool.

deFrost solves one specific problem: it eliminates the SSD timing channel that
FROST exploits. It does not replace a VPN. It does not encrypt your browsing
data. It does not block other browser fingerprinting techniques like canvas
fingerprinting, WebGL fingerprinting, or font enumeration. It does not protect
your traffic at the network level.

It does one thing, it does it completely, and it does it in a way that any
Windows user can operate without understanding what an SSD timing side-channel
is.

---

## The Technical Choices

A few decisions worth explaining for anyone who wants to look under the hood
or contribute.

deFrost is built in Python, using Flask for the local web interface and pystray
for the system tray icon. The RAM disk is managed through ImDisk, an open
source RAM disk driver bundled with the application. File syncing uses
Robocopy, which is built into Windows, called as a subprocess with flags that
handle incremental sync efficiently.

The application is fully portable — no installer, no registry writes, no
AppData footprint. Drop the folder anywhere and run it. Everything including
configuration is stored in the same directory as the executable.

The Flask server binds exclusively to 127.0.0.1. It is not accessible from
other machines on the network under any circumstances.

The project will be open source. For a privacy and security tool, open source
is the only credible option. Users should be able to read exactly what it does
to their browser profile and their RAM.

---

## Current Status

deFrost is in early development. The architecture is defined, the execution
plan is written, and the project directory is set up. No code exists yet —
which is intentional. Getting the design right before writing a line is the
correct order of operations for a tool that touches your browser profile.

The development plan breaks into ten phases: project scaffold, browser
detection, RAM disk management, delta sync, web UI, system tray, config
management, packaging, testing, and documentation. Chrome and Firefox come
first. Edge and DuckDuckGo follow. The rest of the Chromium-based browsers
come after that.

There is no release date yet. This is a side project built because the problem
is real and the vendor response has been inadequate. It will be released when
it is reliable enough to trust with your browser profile — which is a higher
bar than most software ships to.

---

## Following Along

When there is something to share — early builds, a GitHub repository, updates
on progress — it will be posted here. If you work in security research, browser
development, or Windows systems programming and want to contribute, reach out.

In the meantime, the most effective thing you can do right now is close browser
tabs you are not actively using. FROST needs an open malicious tab for the
duration of its measurement. No open tab, no attack. It is not a complete
solution, but it costs nothing and works today.

A proper solution is coming.

---

*deFrost is an independent open source project. It is not affiliated with the
FROST researchers at Graz University of Technology, or with any browser vendor.*
