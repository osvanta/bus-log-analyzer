# Security Policy

## Supported Versions

Only the latest release receives security fixes.

|Version|Supported|
|-|-|
|Latest|✅ Yes|
|Older|❌ No|

## Reporting a Vulnerability

If you discover a security issue (e.g. a malicious BLF, ASC, MF4, MDF, CSV, DBC,
ARXML or LDF file causing arbitrary code execution, or the application leaving a
copy of a measurement behind), please **do not open a public GitHub issue**.

Instead, report it privately via GitHub's
[Security Advisories](https://github.com/osvanta/bus-log-analyzer/security/advisories)
feature, or email the maintainer directly.

Please include:

* A description of the vulnerability
* Steps to reproduce (a minimal test case if possible — no proprietary data)
* The potential impact

You can expect an acknowledgement within 72 hours.

## Scope

The tool reads measurement files (BLF, ASC, MF4, MDF, CSV) and databases (DBC,
ARXML, LDF) from the local filesystem. It opens no network connection of its
own: the links in the Help menu and About dialog open a page in the user's
browser, and only when clicked. It sends no telemetry and checks for no updates.
The primary attack surface is **malformed or malicious input files**, including
configuration files that carry calculated-signal formulas.

What the application writes to disk, and where, is described in
[Security and data handling](https://osvanta.github.io/bus-log-analyzer/security-and-data/)
in the user guide. A difference between that page and what the application
actually does is in scope for this policy.
