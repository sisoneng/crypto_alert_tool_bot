# sec-sentinel

Polls official SEC EDGAR feeds and sends prioritized, deduplicated crypto-relevant filing alerts to Telegram. Runs free on GitHub Actions every 5 minutes.

Detectors:
- **Latest-filings feed** (primary): one EDGAR Atom feed per form type. Watchlist tickers are resolved to CIKs, keywords are matched against filer names, and 8-K item numbers are read from each entry. The company submissions JSON is used as a fallback for a watchlist 8-K whose entry lists no items.
- **Full-text search** (secondary): searches filing text for each keyword over the last 3 days, for the forms in `fulltext_forms`. It lags by minutes to hours. A filing alerts at most once, whichever detector sees it first.
- **SEC press releases**: the official press release RSS feed (enforcement and policy actions), matched on headline and summary.
- **Federal Register** (`federal_register: true`): SEC documents on federalregister.gov, the official US government journal (not an SEC endpoint). This covers exchange **19b-4** filings to list crypto ETFs: notices, proceedings, approvals and disapprovals. They are published days after the exchange files them.

ETF-related forms are watched too: **8-A12B** (registration right before trading starts), **N-1A / 485APOS** (1940-Act funds) and **424B3** (prospectus). For 485APOS, N-1A and 8-A12B the filer is often an umbrella trust ("ETF Opportunities Trust"), so the crypto fund's name only shows up through full-text search.

Keywords match whole words, case-insensitively, plus a plural "s" ("crypto asset" matches "Crypto Assets"; "ether" does not match "Aether").

Tiers: 🔴 HIGH for a watchlist 8-K with item 1.01, 2.01, 7.01 or 8.01; an S-1, S-1/A or 8-A12B with a keyword match; a press release with a keyword match; or a Federal Register order approving or disapproving an exchange listing. 🟡 MEDIUM for any other watchlist filing, keyword match or Federal Register match. Everything else is logged only.

## Run

```bash
pip install -r requirements.txt
export SEC_USER_AGENT="sec-sentinel you@example.com"
python -m sec_sentinel --once --dry-run      # print instead of sending
python -m sec_sentinel --once                # needs TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
```

The first run with no `state/seen.json` marks every current filing (and full-text match) as seen and sends only a "sec-sentinel armed" message. Delete `state/seen.json` to re-arm. A state file from an older version keeps working: any detector it hasn't seen before seeds itself silently on its first run. Changing `forms`, `fulltext_forms` or `keywords` later doesn't flood the chat either: items that are only newly covered by the change are marked as seen silently (one "config changed" message), while genuinely new filings still alert.

## Test

```bash
pip install -r requirements-dev.txt && pytest -q
```

## Setup (GitHub Actions)

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the token.
2. Send any message to your new bot (or add it to a group or channel). Then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `result[].message.chat.id`. Group and channel IDs are negative.
3. In the repo, go to **Settings → Secrets and variables → Actions** and add `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `SEC_USER_AGENT` (e.g. `sec-sentinel you@example.com`).
4. Under **Settings → Actions → General → Workflow permissions**, select "Read and write permissions" so the workflow can commit `state/seen.json`.
5. Open the **Actions** tab, select **sentinel**, and click **Run workflow** to arm it.

## Run on a VPS (`--loop`)

```bash
python -m sec_sentinel --loop --interval 60    # minimum 30
```

Each cycle runs the same code as `--once`. A failed cycle is logged and retried with backoff (2x, 4x, … up to 15 minutes). The chat gets one message after 3 failures in a row and one on recovery. The daily heartbeat still applies. SIGINT, SIGTERM or Ctrl+Break stops it within about a second.

Run either the VPS loop or the GitHub workflow, not both: each keeps its own `state/seen.json`, so running both sends every alert twice. To switch to the VPS, disable the **sentinel** workflow in the Actions tab and copy the latest `state/seen.json` to the server.

Example systemd unit (`/etc/systemd/system/sec-sentinel.service`):

```ini
[Unit]
Description=sec-sentinel
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/sec-sentinel
# .env holds SEC_USER_AGENT, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
EnvironmentFile=/opt/sec-sentinel/.env
ExecStart=/opt/sec-sentinel/.venv/bin/python -m sec_sentinel --loop --interval 60
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Then run `sudo systemctl enable --now sec-sentinel` and follow the logs with `journalctl -u sec-sentinel -f`.
