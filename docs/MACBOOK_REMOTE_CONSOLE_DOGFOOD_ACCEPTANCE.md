# MacBook Remote Console Dogfood Acceptance

Status: real second-device transport and one-click Console rendering verified;
normal Chrome proxy path and authenticated workflow remain open

Date: 2026-07-18 (Asia/Shanghai)

## Topology

- The Mac mini remains the AgentOps MIS Host, model/Worker runtime, knowledge
  store and authoritative ledger.
- The MacBook Air is the real human Console device on the existing private
  Tailscale network.
- Tailscale Serve on the Host keeps HTTPS port `8443` mapped to the loopback
  Private Host. Funnel remains disabled.
- The MacBook uses the detached `screen` session `codex-resume` as an optional
  SSH-operated Codex execution channel. This is an internal dogfood mechanism,
  not a customer dependency or product authority.

Internal operators attach from the Mini with:

```bash
TERM=xterm-256color ssh -t agentops-macbook \
  'TERM=xterm-256color screen -xRR codex-resume'
```

This resumes the Codex task state in a fresh terminal; it does not take over an
old TTY. The MacBook GUI terminal and the SSH-attached screen are a single-writer
channel and must never submit input concurrently. Remote tasks must be bounded,
must write redacted receipt files, and must not return or ingest terminal
scrollback. This operating channel does not bypass Console authentication or
move MIS authority away from the Mini.

No Tailscale command, Serve mapping, Host database, Runtime credential or
customer record was changed during the source hardening work.

## Verified Evidence

From the MacBook:

- Tailscale peer ping reached the Host directly.
- TCP connection to the Host HTTPS port succeeded.
- `curl` with certificate verification returned HTTP `200` over both HTTP/1.1
  and HTTP/2.
- A fresh temporary Chrome profile with `--no-proxy-server` rendered the real
  AgentOps MIS Chinese account-and-access login surface at `1280x720`.
- `~/Applications/AgentOps MIS Console.app` now opens a separate Chrome app-mode
  profile with `--no-proxy-server`. Its isolated History contains only the
  expected Host `/workspace` URL with page title `AgentOps MIS`.
- No username, password, cookie, setup code or form submission was used.

The first normal-Chrome screenshot truthfully showed
`ERR_CONNECTION_CLOSED`. Investigation found that the MacBook system browser
was using a loopback HTTP/HTTPS/SOCKS proxy backed by Qiqi/mihomo, and its
bypass list omitted the Tailscale range and Host. The existing proxy settings
were preserved; only the Tailscale CGNAT range, `*.ts.net`, and the exact Host
name/address were appended to the Wi-Fi bypass list. A fresh normal Chrome
profile still returned `ERR_CONNECTION_CLOSED`, while the same fresh Chrome
with `--no-proxy-server` rendered the Workspace. The remaining defect is
therefore the normal Chrome/proxy integration, not Tailscale Serve or MIS.

All screenshots and receipts remain temporary local evidence under `/tmp` and
are not committed.

A fresh bounded read-only receipt repeated the installed-app path after the
Mini Worker recovery. It confirmed the app launch, dedicated Chrome profile,
`--no-proxy-server` process flag, `/workspace` path and `AgentOps MIS` title.
The MacBook `codex-resume` channel independently produced the same bounded
successful receipt without returning terminal scrollback.
The check copied only that dedicated profile's History database to a temporary
file, reduced the result to booleans, and omitted the private hostname and full
URL. It did not inspect the normal Chrome profile, credentials, cookies or
terminal scrollback, and it changed no network or repository state.

The Console app contains only the non-secret Host URL and browser launch flags.
It does not install AgentOps, Python, Node, Hermes, OpenClaw or another model on
the MacBook; all authority and execution remain on the Mini.

After the Mini upgraded to exact-package preview.33 and restarted the Host, a
new bounded MacBook Codex receipt launched the app but did not observe the path
or title through the dedicated profile's History database. A separate direct
read-only diagnostic showed that the app existed and launched, its dedicated
Chrome process used `--no-proxy-server`, and the Host was reachable; the History
database itself contained zero URL rows. The negative receipt was therefore an
invalid observation method, not proof that the page failed to render.

A fresh temporary no-account Chrome profile then used the exact URL already
embedded in the installed app and a loopback-only DevTools endpoint. Its
allowlisted target readback observed an HTTPS page, the `/workspace` path, and
the exact `AgentOps MIS` title. The private hostname and full URL were omitted;
no login, form submission, credential/cookie read, repository change, network
setting change, screenshot of the full desktop, or terminal scrollback read
occurred. This re-accepts the physical app entry/rendering path after the
preview.33 Host restart, but not an authenticated human workflow.

## Not Yet Accepted

- direct rendering in the user's ordinary proxy-managed Chrome profile;
- member pairing or Owner login from the MacBook;
- customer task creation, approval, evaluation, audit and artifact download;
- browser/Tailscale disconnect while a real Hermes/OpenClaw task continues;
- logout plus Owner device revocation;
- exact-release correlation with a packaged Host build.

This receipt therefore does not close the full physical second-computer gate.
It does prove that the installed Host, Tailscale Serve, certificate, and a real
Chrome engine can render the Workspace from the MacBook without a container.

## Safety

- Existing Tailscale remains installed and enabled as the advanced private
  network profile.
- No Funnel, router port forwarding, public tunnel or non-loopback Python bind
  was enabled.
- The remote Codex task was read-only and produced bounded receipt metadata;
  full terminal scrollback was not ingested.
- The temporary Chrome profile contains no user account or browsing history.
