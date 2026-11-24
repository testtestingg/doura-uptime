# Doura public uptime checks

Independent, read-only monitoring of https://doura.tn and https://www.doura.tn.
This repository contains no private application source, customer records or server credentials.

The workflow runs every five minutes, offset from the top of the hour. It checks both domains with certificate validation, the app shell, its current compiled JavaScript/CSS, major public routes, sitemap XML, the published listings RPC, catalogue search and the public Auth settings API. The public Supabase configuration is read from the public homepage at runtime and never printed or saved.

A failure is retried once after two seconds. The first confirmed failure opens an issue assigned to and mentioning `testtestingg`; an existing incident is not spammed. Recovery comments on and closes the incident. GitHub delivers notifications according to the owner's notification settings; this is not an SMS or paging service and does not guarantee email delivery. Workflow failure notifications provide an additional signal if the monitor itself fails.

Standard GitHub-hosted Actions runners on public repositories are free. No Vercel/Supabase credentials or production write access are available to this workflow. It never redeploys, modifies DNS or changes data.

## Verification

Run `python3 -m unittest -v` and `python3 monitor.py` locally. The latter has no notification side effects without `GH_TOKEN`. Use the manual workflow input `alert_test: true` to send a clearly labelled test issue and close it with a recovery message; this does not pretend production is down.

## Limits

GitHub scheduled runs can be delayed or dropped under load, so five minutes is a target interval, not an immediate paging SLA. Public repository schedules can be disabled after 60 days without repository activity: check the Actions tab periodically and keep the workflow maintained. Successful HTTP/API checks do not prove every browser interaction or every Tunisian ISP works. Use an external one-minute multi-region paging provider if a stricter SLA is needed.

No `/api/health` check is used: this application currently returns the SPA fallback at that nonexistent endpoint, which would falsely look healthy.
