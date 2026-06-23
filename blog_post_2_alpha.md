# deFrost Alpha: From Concept to Working Software

A few weeks ago I wrote about the FROST browser exploit and a tool I was
planning to build to counter it. The post ended with an honest admission: no
code existed yet, and I wanted to get the design right before writing a line.

That process is done. deFrost is now working software. Chrome and Firefox
profiles activate and deactivate cleanly. The RAM disk creates, fills with your
profile, and releases correctly. Your browser closes, moves to RAM, and
relaunches automatically. The protection works.

This post covers what it actually took to get there — the decisions made, the
problems that only showed up during real testing, and where the project is
headed next.

---

## What We Built

The final architecture is a small Python application with three visible parts:

A **system tray icon** that lives in the corner of your screen. Blue means
protected. Gray means not. It is the only persistent thing you see while deFrost
is running.

A **local web dashboard** at `127.0.0.1:7375` that you open in any browser.
Setup, status, activation controls — all here. It only responds to your own
machine. Nothing is accessible from your network.

A **native status window** — a small floating panel that appears during
activation and deactivation, showing you exactly what is happening step by step.
This one turned out to be more important than expected, and the reason why is
worth explaining.

---

## The Problem With Using Chrome to Control Chrome

The original design had activation and deactivation status displayed as an
overlay in the web dashboard. Clean, simple. Then we tested it.

When you activate Chrome protection, the very first thing deFrost does is close
Chrome. Which closes the tab showing the activation overlay. The status
disappears mid-process and you have no idea whether the copy is running,
finished, or failed.

The fix was to build a second status display that is completely independent of
any browser — a small native Python window using tkinter. It appears when
activation starts, shows a live scrolling log of every step as it happens, and
closes itself just before the browser relaunches. It survives browser
termination because it is not running inside any browser. The browser closes.
The status window stays. You watch the progress. Chrome comes back.

This is also why the browser relaunches directly to the deFrost dashboard rather
than to your home page. When Chrome reopens, you see immediately whether
activation succeeded. The dashboard is the result screen.

---

## The Cache Problem

Chrome's profile directory on a typical machine is enormous. Not because of
your bookmarks or history — those are small. Because of the cache.

The HTTP cache, the code cache, the GPU shader cache — these can add up to
three or four gigabytes on a machine that has been browsing for any length of
time. A three gigabyte RAM disk just to protect your browsing history is a hard
sell, and on a machine with eight gigabytes of RAM it becomes impractical.

The insight that resolved this was straightforward once we thought it through.
Cache is regenerable. It is just copies of public web content that Chrome
downloaded to speed up page loads. It contains nothing personal and nothing that
cannot be rebuilt from scratch. And here is the key part: once deFrost activates
and the browser profile is redirected to the RAM disk, any new cache entries
Chrome writes go directly to RAM anyway. The cache cold-starts empty when you
activate, warms up in RAM as you browse, and never touches the SSD during your
session.

FROST protection is complete because all browser reads happen in RAM. The fact
that the cache started empty is irrelevant — what matters is where Chrome is
reading from, and the answer is RAM.

The result: Chrome drops from 3.5GB to around 2GB after cache exclusion.
Firefox, which has always been more conservative about disk usage, comes in
well under 200MB. Both are manageable on modern hardware.

---

## Sizing the RAM Disk

Getting the RAM disk size right required solving a measurement problem.

The naive approach — scan the profile directory and total up the file sizes —
turns out to be unreliable when the application is not running with administrator
privileges. Windows locks several Chrome subdirectories against non-admin reads.
The scan returns a number, but it is wrong. On one test machine it was wrong by
a factor of more than a hundred, reporting 28MB for a profile that was actually
684MB. A RAM disk sized to that number would run out of space halfway through
the copy.

The fix uses Robocopy in list-only mode. Robocopy runs at the system level and
can enumerate every file regardless of access restrictions. The same tool that
does the actual copy can first report exactly what it will copy, giving a
precise size measurement before any RAM disk is created. The cache exclusions
apply to both the measurement and the copy so the numbers always match.

The RAM disk is then sized as the measured profile size plus a configurable
write buffer (200MB by default) plus overhead. The write buffer is what absorbs
new writes during your session before they flush to disk.

---

## The Write Buffer

This is the design decision I am most satisfied with.

The obvious approach to syncing changes back to disk while protection is active
is a timer. Sync every fifteen minutes. Simple, predictable, easy to implement.

It is also wrong for a few reasons. A timer is arbitrary — fifteen minutes might
mean syncing after two seconds of browsing or missing thirty minutes of changes.
A timer creates a predictable write pattern on the SSD, which has implications
for the attack we are trying to prevent. And a timer requires answering awkward
questions about what happens when it fires mid-session.

deFrost instead uses a size threshold. The buffer fills as you browse — cookies
update, history grows, extensions write state. When the accumulated delta
reaches the threshold, only the changed files flush to disk and the counter
resets. A light session might never trigger a flush. A heavy session flushes
proportionally to actual activity.

From a security perspective this is cleaner too. The flush events are bulk
sequential writes triggered by volume, not timing. That is a completely
different I/O signature from the read contention pattern FROST measures. An
attacker trying to use the flush pattern as a timing signal would find nothing
useful.

---

## Elevation and the Two-Process Problem

deFrost needs administrator privileges. Creating RAM disks and NTFS junction
points are kernel-level operations. There is no way around this.

Browsers, on the other hand, refuse to run elevated. Chrome in particular will
not start if it detects it is running with administrator rights.

This creates a problem. deFrost runs elevated. It closes the browser. It needs
to relaunch the browser. But it cannot just spawn the browser as a child process
because the child would inherit its elevated token.

The solution is `ShellExecuteW` called without the `runas` verb. This launches
the browser through the Windows shell at the user's normal privilege level
rather than the elevated level. The browser starts normally, deFrost continues
running elevated, and both coexist without conflict.

The ImDisk driver has a similar consideration. The driver needs to be installed
as a Windows service, which requires elevation — already satisfied. But the
driver files do not ship with deFrost. On first activation, if the driver is
not present, deFrost downloads and installs it automatically. Users never touch
ImDisk directly.

---

## What Works Today

Chrome and Firefox activation and deactivation are working end to end. The
profile moves to RAM on activation, the browser relaunches and behaves normally,
changes sync back to disk when the buffer fills, and clean deactivation restores
everything.

The status window works. The confirm dialogs work. The dashboard shows correct
status. The size measurement is accurate. The cache exclusion is correct.

Edge behaved mostly correctly — deactivation worked cleanly, and the profile
detection found it properly. There is a known issue with the activation
close-browser step for Edge that is on the list to investigate.

The project is open source at `github.com/o51r15/defrost` and the code is
currently in an alpha state. It is functional but has rough edges.

---

## What Is Coming

There are a handful of known issues being worked through. The sync button on
the dashboard is currently returning a failure that needs investigation. The
profile size measurement step is slower than it should be for large profiles.
The dashboard does not yet correctly handle the case where you open it in a
different browser than the one being protected — it should show that browser's
status rather than a global status, and eventually allow protecting multiple
browsers simultaneously with independent RAM disks.

Beyond the bug list, the bigger project is the thing deFrost deliberately does
not address: the rest of the browser fingerprinting landscape. Canvas
fingerprinting, WebGL, font enumeration, AudioContext signatures — these are
the other vectors that identify you across sessions and across sites, and none
of them are fixed by moving your profile to RAM.

A second project is in early design. The working title is Phantom. The core
idea is a local proxy that injects calibrated session-varied noise into
JavaScript API responses before the browser processes them. Not blocking — that
is detectable. Not spoofing a different identity — that is fragile. Just making
every measurement slightly different every time so no stable fingerprint ever
forms.

The goal is a unified privacy suite: deFrost handling the SSD timing channel,
Phantom handling the JavaScript fingerprinting vectors, one install, one
dashboard, both independently toggleable.

Neither project is affiliated with the FROST researchers at Graz University of
Technology or with any browser vendor.

---

*deFrost is available at github.com/o51r15/defrost. It requires Windows 10 or
later and administrator privileges. Python 3.10 or later is required to run
from source. A packaged release is planned when the known issues are resolved.*
