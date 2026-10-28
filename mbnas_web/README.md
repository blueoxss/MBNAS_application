# mbnas_web — MBNAS Research Console (web)

React 18 + TypeScript + Vite UI for the public gateway API (`mbnas_server`, `/api/v1`). Seven screens (`src/screens`): NAS 아카이브 (Archive, the default entry), Run 비교 (Compare),
탐색 모니터링 (Monitor), 실험 설정 (Settings), 모델 계보 (Lineage), 실행 기록 (History), 서버 연결·실행 검토 (Server).
Types are generated from the canonical contract `../mbnas_contracts/src/mbnas_contracts/openapi/openapi.json`, **contract
1.5.0** (never edited here; `MBNAS_OPENAPI` overrides the path). `src/api/types.ts` only aliases generated components: no
hand-written field is added to a contract type.

> Installation, start/stop and the gateway/core/runner configuration are documented in `docs/public/INSTALL.md` and
> `docs/public/OPERATIONS.md`. This file covers the web console itself.

## Ports

| Port | What | When |
|---|---|---|
| **8710** | gateway (`mbnas_server`): the API under `/api/v1` **and** the built console `dist/` at `/` | production |
| **8720** | `vite preview` (or `vite` dev) of this folder, `/api` proxied to the gateway on 8710 | development only |

The core (8700) is never called by the browser: the gateway relays to it.

## Connect to a server

### Production: the gateway serves the console (same origin, no CORS)

`npm run build` writes `dist/`; the gateway (`mbnas_server`, default port 8710) serves that directory at `/` with an SPA
fallback (`MBNAS_GW_WEB_DIST`, default `<install root>/mbnas_web/dist`). The release ZIP already contains the built
`dist/`, so Node is needed only to rebuild the bundle. Open `http://<gateway host>:8710/`, go to the **Server** screen
(서버 연결·실행 검토), leave *API origin* empty (same origin), paste the bearer token (the gateway's `MBNAS_GW_API_TOKEN`)
and press **Connect / Health check**. No API URL is compiled into the bundle.

### Development

```bash
npm run dev -- --host 127.0.0.1 --port 8720 --strictPort       # live reload; /api proxied to http://127.0.0.1:8710 (MBNAS_DEV_API overrides)
npm run build && npx vite preview --host 127.0.0.1 --port 8720 --strictPort   # the built bundle, same proxy
```

### After connecting

Connecting checks `/health`, loads the server presets (`GET /api/v1/protocols/presets`) and, while the policy editor is still
empty, applies the server preset `DEFAULT_BASELINE14` (label "server preset DEFAULT_BASELINE14 (design example)", badge
"auto-applied on connect"). The bundle embeds no protocol template: before connecting the editor is empty and Settings
says so. Anything typed before connecting, a chosen preset or a loaded run is never replaced. Then:
1. For a smoke run choose `PILOT_SMOKE` (Settings preset picker, or **Use in editor** in the Server presets table).
2. **Validate** shows the resolved diff and the immutable `protocol_hash`.
3. **Run** creates the run and loads it.
4. Follow it on **Monitor**: progress rows, points, generation lines, and the candidate dialog with lineage and
   GT/TP/FN/FP images.
5. Use Pause / Resume / Cancel / Export on the Server screen. After a reload, enter the token again and use
   **Load by id**.

## Model configuration: upload, revisions, diff, download (contract 1.5.0)

**Upload (Settings → 개별 모델 학습, 모델 출처 = 사용자 지정 모델).** Pick a `.yaml` / `.yml` file (at most 1 MiB; the
browser refuses other names, empty files and larger files before sending anything, the gateway stays authoritative). Its
bytes go unchanged as a raw body to `POST /api/v1/uploads/model-config?filename=<name>`; the gateway stores them
content-addressed and answers `{upload_id, source_sha256, bytes}` (never a server path). The panel shows the upload id, the
server's `source_sha256` and the sha256 the browser computed over the same bytes (WebCrypto: HTTPS or localhost), with a
match / mismatch verdict. **구조 상세 → 서버 CPU 구조 검사 실행** then sends `POST /api/v1/structure-check {upload_id,
model_name}` and **이 모델 학습** sends `POST /api/v1/training-runs` with `source {kind: user, upload_id, model_name}`.
Rejections are shown by their code: `UPLOAD_TYPE_UNSUPPORTED`, `PAYLOAD_TOO_LARGE`, `UPLOAD_EMPTY`, `UPLOAD_NOT_UTF8`,
`UPLOAD_INVALID`, `STORE_UNAVAILABLE`, `UPLOAD_NOT_FOUND`, `UPLOAD_CORRUPT` (with the error's `location`).
The legacy server path (`model_path`, a YAML under the gateway's allowed roots; owner single-host deployments) exists only
behind the **고급: 서버 경로 사용 (model_path)** toggle and is exclusive with the upload.

**"구성" tab of the candidate view.** `GET …/candidates/{cid}/config-revisions` lists the revisions oldest first:
`kind` (original / selected / executed / generated), `source_sha256` (hash of the stored **bytes**), `canonical_hash`
(the `yaml_hash` of the canonical YAML dict: a different definition, so a different value is normal), `architecture_hash`
(null before a build), `created_at`, the parent revision and the store `artifact_id`.
- **Diff**: choose any two revisions (default: selected ↔ executed, else the last two). Both sides are fetched from
  `GET …/config-revisions/{revision_id}/yaml`, hashed again in the browser and compared with `X-Artifact-Sha256` and the
  recorded `source_sha256`; the line diff marks removed (−) and added (+) lines with their line numbers, and reports a
  CRLF / final-newline difference.
- **원문 바이트 다운로드** saves exactly those stored bytes (file `<candidate>-<kind>-<revision>.yaml`) and shows the hash
  verdict. The older **화면에 표시한 구성 YAML 다운로드** of the candidate view saves the displayed (canonical) text of the
  detail document; the two are labelled differently because their bytes can differ.
- **Export**: the run export ZIP (Server screen, `GET /runs/{run_id}/export`) carries the executed configurations
  as `architectures/*.yaml` bytes; the console does not inspect the ZIP, compare the sha256 yourself.
- A gateway without these routes (contract < 1.5.0) answers 404: the tab is hidden and a note says why
  (`RUN_NOT_FOUND` / `CANDIDATE_NOT_FOUND`: no record for this candidate). A 5xx keeps the tab with the error and a retry.

**Files by id, never by path.** The candidate view names the executed architecture by its artifact id + sha256 and the
checkpoints by checkpoint id (`ck-…`) + sha256 + store `artifact_id`; downloads use `GET /runs/{run_id}/artifacts/{artifact_id}`
and check the bytes against the recorded sha256. A failed download (and a diagnostic image) is classified by its
`Error.code`: `ARTIFACT_NOT_READY` → NOT_READY, `LOAD_FAILED` → LOAD_FAILED (stored bytes no longer match), `NOT_FOUND`
→ MISSING, `ACCESS_DENIED` / `AUTH_REQUIRED` → ACCESS_DENIED; the HTTP status decides only for an unknown code.

**Lineage** lists the generated candidates of an evaluation-OFF run after the evaluated rows, each with the
**UNTRAINED · UNEVALUATED** badge and no fitness ("미평가"); they are never shown as measured rows.

### Remote API on another origin (explicit HTTPS origin, CORS)

1. Serve the built `dist/` from any static host / reverse proxy (HTTPS recommended).
2. On the gateway set `MBNAS_GW_CORS_ORIGINS` to the **exact** origin of this page (scheme + host + port) and restart it.
3. In the Server screen enter *API origin* `https://api.example.org[:port]` (no path) and the bearer token.
   `http://` is accepted only for `localhost` / `127.0.0.1`. Authenticated requests and the event stream use
   `Authorization: Bearer ...`. The browser sends no cookies (`credentials: omit`).
4. A CORS or network failure shows up as *Cannot reach the server ...* in the connection panel.

## Build and check

```bash
npm install                 # versions are locked in package-lock.json (Node 22)
npm run gen:types     # regenerate src/api/types.gen.ts from the contract
npm run check:types   # regenerate to a temp file and diff; exit 1 on drift
npm run typecheck     # tsc --noEmit
npm run build         # typecheck + vite build -> dist/
npm run check:bundle  # grep dist/ for host facts, the removed policy embeds and removed private vocabulary (7 required + 21 extra needles; exit 1 on a hit, 2 without dist/)
```

## Final retraining flow

Final retraining is a separate, explicit phase that never runs inside a search run.

1. Server screen: load a search run (`kind: SEARCH`). When its status is `COMPLETED` the **Final Retrain** panel appears
   (it is hidden for running, failed, cancelled and for `FINAL_RETRAIN` runs).
2. Enter `top_k_per_arm_per_seed` (prefilled from the run's resolved `final_retraining`; empty = server default),
   `budget_gpu_hours` (required, > 0, separate from the search budget) and an optional reason, then start.
   The page sends `POST /api/v1/runs/{id}/final-retrain` with `idempotency_key` in the body and the same value in the
   `Idempotency-Key` header. Like Run, the key is reused for a retry after a timeout, network error or 5xx, and released
   after a 2xx, 409 or 422.
3. On success the panel shows the child run id and `finalist_count`; **Load child run** opens the `FINAL_RETRAIN` run.
   Finalists are chosen by the server's frozen rule from L2 rows only. 409 answers are shown distinctly:
   `NO_FINALIST` ("L2 finalist 없음 (하위 rung 대체 없음)": reported, no lower rung is ever substituted),
   `INVALID_STATE`, `BUDGET_INVALID`, `IDEMPOTENCY_CONFLICT`.
4. A loaded `FINAL_RETRAIN` run shows its kind, a **Load parent run** link and the finalist table (candidate, source
   evaluation, arm, seed, rung, AP, latency, selection rule) on the Server and Monitor screens. Final rows
   (comparison group `final`, rung `FINAL`, arm `Final`) form their own cohort and are never drawn together with search
   cohorts. In Lineage / the candidate dialog their operation is `final` with the finalist source candidate as the single
   parent. History lists parent/child runs from explicit server facts only (`parent_run_id`, the run list, run events).

Settings additions: GA `crossover_rate` (0-1, empty = server default 0.5) and the benchmark postprocess operating point
`conf` / `iou` / `max_det` ("latency operating point; AP evaluation floor stays 0.001"). Empty optional fields are removed
from the protocol (never sent as null). The Validate diff table marks values the server defaulted ("default applied").
The Workers table uses the status vocabulary `idle | busy | offline | lost` as text plus a glyph with a legend.

## Behaviour notes

- Run is enabled only after a valid **Validate** of the unchanged editor state; it sends the *resolved*
  protocol, `protocol_hash`, `validation_id` and an `Idempotency-Key` (header = body key). After a timeout,
  network error or 5xx the same key is reused; it is released only on 2xx or 409/422.
- While the loaded run is non-terminal (QUEUED, RUNNING, PAUSE_REQUESTED, PAUSED, RESUMING, CANCEL_REQUESTED)
  the Settings screen is locked; *Fork as new version* detaches the run and copies its resolved protocol.
- Charts plot only EVALUATED rows with finite valid metrics of ONE cohort at a time (cohort guard). Dotted
  generation lines need complete + planning_closed + pending 0 + counts invariant; solid lines join
  representatives of consecutive complete generations (highest fitness, lower latency, stable id) and break on gaps.
  Lines are never called a Pareto frontier.
- Events: fetch-streaming SSE, dedupe by seq, other runs ignored, gap or `snapshot.required` -> authoritative
  snapshot replaces state, reconnect with backoff from `after_seq = lastSeq`, snapshot polling fallback.
- Artifacts: `signed_url` -> plain `<img>` (no credentials); `bearer_fetch` -> Authorization only if the URL
  origin equals the API origin, shown via a Blob URL that is revoked on close/change.

## What is never persisted

The bearer token lives only in React state: never in `localStorage`, `sessionStorage`, cookies, the URL,
exports, logs or the policy JSON export. Nothing else is written to browser storage. The URL hash may contain
the screen name and a run id (not a secret) so a reload can reload the run after you re-enter the token.

## Accessibility and design tokens

Normal-text contrast >= 4.5:1 for the documented token pairs (defined in
`src/styles.css`); sidebar `#0b3d2e`/`#114b38`, page `#f6f8f6`. Mobile (<768px): menu disclosure
(`aria-expanded`, inert when closed), stacked cards, 44px touch targets, 16px inputs; `prefers-reduced-motion`
respected. Chart points are focusable buttons (Enter / Space / click) with an equivalent sortable table.
Korean labels need a CJK-capable system font; headless Chromium without one renders them as boxes.
