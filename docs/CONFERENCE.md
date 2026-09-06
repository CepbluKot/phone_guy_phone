# Asterisk listening demo

## Deployment ledger

All delivery targets only VM209 (`ubuntu@192.168.20.70`). The shared RVC
service, Frigate, DNS, VPS, firewall and Python virtual environment are out of
scope. Each failed candidate restored the prior HTTP image, native Caddyfile
and RVC health; no conference container remains running after a failed probe.

| UTC stamp | Outcome | Evidence |
| --- | --- | --- |
| `20260906T190601Z` | Interrupted before switch | Asterisk and controller images built; snapshot retained at `/opt/voice-conference/backups/20260906T190601Z`; no demo containers started. |
| `20260906T192000Z` | Rolled back | Asterisk failed before PID/ASTdb creation on a read-only root. Rollback exposed an HTTP readiness race and then restored Caddy and the original HTTP/RVC contour. |
| `20260906T193000Z` | Rolled back | Explicit Stasis config and temporary state directories were added, but mounting tmpfs over `/var/lib/asterisk` hid the Asterisk-installed XML documentation. Stasis therefore failed configuration initialization. |
| `20260906T193500Z` | Rolled back | Confirmed the same documentation-masking root cause in the released image. RVC returned `ready`, `active=false`, `running=false`, `queuedWindows=0`; legacy HTTP returned `status=ok`. |
| `20260906T194000Z` | Rolled back | The corrected docs layout reached ASTdb initialization, then failed because `/var/run/asterisk/astdb` was not created before SQLite opened `astdb.sqlite3`. Asterisk stayed restarting/unhealthy; module, ARI, browser and media probes were not run. The owned Asterisk container was stopped and removed; RVC again returned idle/ready and legacy HTTP `status=ok`. |
| `20260906T194500Z` | Rolled back | Asterisk finally became healthy, controller started, Caddy candidate validated, and `asterisk -V` returned `22.11.0`. The probe then received the correct HTTP 403 for a wrong Origin, but its client expected close code 1008 and raised `websockets.exceptions.InvalidStatus`. No media/browser acceptance ran; automatic rollback completed. |
| `20260906T195000Z` | Rolled back | Asterisk and controller both became healthy; controller `/healthz` returned `status=idle`, ARI responded `401 Authentication required` without credentials, and Caddy validated. A valid listener then received `{"type":"error","code":"upstream_unavailable"}` before media. Automatic rollback completed; no media/browser acceptance ran. |
| `20260906T201500Z` | Rolled back | ARI `POST /channels/create` reached Asterisk, which logged `Stasis dialplan application is not registered`; the following variable `GET` returned 404. The build, allowlist and health check were then corrected to include `app_stasis`. |
| `20260906T202000Z` | Isolated diagnosis | With `app_stasis` loaded, all four owned ARI media channels opened. The controller then failed before RVC input because root-created fixture files were unreadable by the unprivileged controller. No public route was left enabled. |
| `20260906T223500Z` | Rolled back | Audio flowed, but the smoke tool incorrectly treated intentional silent 20-ms PCM gaps as an error. The probe was corrected to require valid PCM plus sounding frames rather than sound in every frame. |
| `20260906T225200Z` | Accepted then deliberately rolled back | End-to-end browser-wire smoke passed. This release was used for the required scoped rollback check; the prior HTTP/RVC contour returned healthy. |
| `20260906T225700Z` | Superseded | Final redeploy and post-rollback smoke passed; it provided the five-minute acceptance run. |
| `20260906T231500Z` | Active | Updated idle-contour preflight and supported Stasis taskpool configuration; deployed over the owned idle contour and passed post-update smoke. |

The corrected layout keeps `/var/lib/asterisk` read-only so its bundled XML
documentation remains visible. Only `astdbdir` is redirected to
`/var/run/asterisk/astdb` on the bounded temporary filesystem. The next
candidate must prove Stasis, module health and private bindings before any
media acceptance claim.

## Current status and use

The demo is deployed on VM209 release `20260906T231500Z`.

- Listener page: `https://voice.lan.awesomeio.ru/conference/` (VPN/private DNS
  required). It is receive-only: its route sends `Permissions-Policy:
  microphone=()` and its client has no `getUserMedia` or audio upload path.
- Native media endpoint: `wss://vm-voice-1.lan.awesomeio.ru/ws/conference`.
  The first client message must be `{"type":"listen","version":1}`; only
  `{"type":"stop"}` is accepted afterwards.
- Only the controller and Asterisk containers were added. ARI is host-loopback
  port 8092; controller port 8091 is host-loopback; Caddy publishes only the
  HTTPS WebSocket route. No SIP, firewall, DNS, VPS, Frigate or RVC service
  configuration changed.
- Runtime limits are 256 MiB, 0.5 CPU and 128 PIDs per new container. During
  the accepted run Asterisk used 25.69 MiB and the controller 46.39 MiB.

The synthetic room is created only while at least one listener is connected;
after the last listener leaves, its four owned channels and the single RVC
connection are released. Containers remain intentionally running so the page
can be used later. Do not stop `voice-rvc.service`, do not share its single
session with another client during a listen run, and never delete old
`/opt/voice-conference/backups/<stamp>` directories without a separate
retention decision.

## Live acceptance evidence

- Five-minute private WSS run: 14,976 valid 1,920-byte PCM frames, 7,782
  sounding frames, maximum observed receive gap 188.761 ms; the 10-second
  reconnect also passed.
- Wrong Origin was rejected. A deliberately occupied RVC session caused the
  listener to receive `preparing`, `error/busy`, `stopped`; no raw-C fallback
  was sent. Afterwards RVC reported
  `ready, active=false, running=false, queuedWindows=0`.
- One scoped rollback of release `20260906T225200Z` restored the prior HTTP,
  Caddy and RVC contour; `20260906T225700Z` then redeployed and passed its
  post-rollback smoke. The active `20260906T231500Z` was later deployed over
  that owned idle contour and passed the same smoke.

For a new operator: first open the listener page while connected to the VPN,
press **Listen**, and use the browser's normal audio output. If it reports an
error, capture the visible code and inspect only the named conference
containers plus `voice-rvc.service`; do not restart unrelated homelab
services. The release script is the supported operation path:

```bash
cd /home/oleg/Documents/voice-changer/.worktrees/rvc-streaming
bash deploy/deploy-conference.sh
```

## Bounded operator checks and recovery

Set the active release once, then use only these scoped checks. They neither
change Frigate nor restart the RVC worker:

```bash
release=20260906T231500Z
ssh ubuntu@192.168.20.70 "sudo docker compose -p voice-conference \
  -f /opt/voice-conference/releases/$release/deploy/compose.conference.yaml \
  --env-file /opt/voice-conference/releases/$release/.env ps"
ssh ubuntu@192.168.20.70 'sudo docker stats --no-stream \
  voice-conference-asterisk-1 voice-conference-controller-1; \
  curl -fsS http://127.0.0.1:8090/healthz'
```

To reproduce the listener acceptance from the laptop, run the private probe;
it checks wrong Origin, valid 1,920-byte PCM, sounding output, last-listener
cleanup and reconnect. Start it in the background, then inspect the room
before waiting for it: it must report exactly four members (A, B, converted C
and listener).

```bash
CONFERENCE_URL=wss://vm-voice-1.lan.awesomeio.ru/ws/conference \
CONFERENCE_ORIGIN=https://voice.lan.awesomeio.ru \
.venv/bin/python tests/live-conference.py --seconds 300 > /tmp/conference-live.json &
probe_pid=$!
sleep 5
ssh ubuntu@192.168.20.70 \
  "sudo docker exec voice-conference-asterisk-1 asterisk -rx 'confbridge list phoneguy-demo'"
wait "$probe_pid" && cat /tmp/conference-live.json
```

The unit test `test_only_converted_c_reaches_asterisk_and_mix_fans_out` is the
deterministic route proof: before the model supplies an output block, it
verifies that raw C was sent only to the model and never to channel C; it then
verifies that only the model block reaches that channel. The following live
fail-closed experiment occupies the real RVC and proves that the public
conference returns `busy` rather than injecting any raw-C fallback. It stores
no audio; do not run it while a real listener is using the one shared session.

```bash
.venv/bin/python tests/live-conference-busy.py
ssh ubuntu@192.168.20.70 'curl -fsS http://127.0.0.1:8090/healthz'
```

If a new release must be rolled back, use the release's scoped rollback entry
point, then check the restored HTTP and idle RVC state before doing anything
else:

```bash
ssh ubuntu@192.168.20.70 'sudo bash -s -- --remote-rollback 20260906T231500Z' \
  < deploy/deploy-conference.sh
ssh ubuntu@192.168.20.70 \
  'curl -ksSf https://voice.lan.awesomeio.ru/healthz; \
   curl -fsS http://127.0.0.1:8090/healthz'
```
