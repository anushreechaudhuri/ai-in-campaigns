# Run campaign research in the cloud

The current application already runs without a laptop: a Cloudflare Worker serves the bundled dashboard, with research objects in the existing `ai-in-campaigns-data` R2 bucket. The mutable public catalog points to a versioned dashboard dataset. The Worker exposes `public/` only; it does not expose `private/`. This change adds research job entrypoints around that architecture. It does not replace the dashboard or publish new data.

The repository contains no original crawler, extraction pipeline, or detector submission code. Those may exist in the private research directory mentioned in the README. Import those existing scripts if available before adding another analysis implementation. The commands below deliberately make no paid detector requests.

## Execution and persistence

Use a GitHub-hosted runner or a disposable cloud checkout for computation. Use the existing R2 bucket for durable inputs, source bodies, manifests, logs, and outputs. Git remains source code only. A run does not depend on Safari, a laptop process, or a mounted local directory. A stopped ChatGPT turn alone is not a durable worker: a job must actually be dispatched on Actions or another cloud executor.

Three workflow definitions are included:

* `check.yml`: dashboard tests/build and offline research tests.
* `research.yml`: manual read-only audit of the **published dataset only**; no credentials. Its artifacts contain only public dashboard data and an audit receipt, retained seven days.
* `private-research.yml`: manual full-inventory audit or up to 100 queued captures. It reads the canonical inventory/manifest from R2 and saves run outputs under `private/cloud-runs/github-RUN_ID-ATTEMPT/`. It never uploads private data to public Actions artifacts. A completion manifest is uploaded last; interrupted runs must be inspected before reuse.

These files have not been pushed or run remotely. Private R2 transfer is covered by mocked tests and has not been tested against this account. After approval, configure the GitHub `research` environment with `RESEARCH_R2_API_TOKEN`, and environment variables `CANDIDATE_INVENTORY_KEY` and `PRESERVATION_MANIFEST_KEY`, each pointing to an explicit `private/` object. The token must be limited to the existing account/bucket and the read/write permissions actually needed. Cloudflare API token permissions may be broader than a prefix; the tool's private-prefix restriction is application logic, not a substitute for server-side credential scope. No credential is required for offline audits, tests, staging, or public snapshots.

The account ID and bucket name are reused from `asset-links.json`; no new bucket, subscription, hosting service, secret, or schedule is created. Private jobs consume existing R2 storage/operations and Actions runner capacity. Check the account's quotas/billing before enabling larger or recurring runs. There is no Pangram charge from these tools. The small-file REST adapter uses Cloudflare's documented [Get Object](https://developers.cloudflare.com/api/resources/r2/subresources/buckets/subresources/objects/methods/get/) and [Upload Object](https://developers.cloudflare.com/api/resources/r2/subresources/buckets/subresources/objects/methods/upload/) endpoints; each file is capped at 16 MiB. Larger corpora should use the existing S3-compatible tooling rather than raising limits without review.

## Audit the intended inventory

Python 3.12+ is sufficient; no Python packages are needed. Node 24 and `npm ci` are needed for dashboard tests/build and staging.

```sh
npm ci
npm test
npm run test:research
npm run build

python3 scripts/research.py snapshot --out runs/current/public
python3 scripts/research.py audit \
  --inventory data/candidates.csv \
  --dataset runs/current/public/dataset.json \
  --manifest data/preservation.jsonl \
  --corpus-root archive \
  --expected-candidates 4319 \
  --out runs/current/audit
```

An offline inventory-only audit is also useful when the public catalog is unavailable: omit `--dataset`. The private workflow uses that fallback and persists diagnostic outputs even when a later research step fails. Without `--inventory`, the denominator is explicitly `published_dataset_only`, never the 4,319 intended candidates. A mismatching expected denominator or duplicate canonical identities fails the run and preserves diagnostic outputs. Snapshot failures leave a receipt with the failure state; no empty dataset is substituted. Use a fresh snapshot directory for each run; existing dataset bytes are never overwritten.

The canonical CSV/JSON fields are:

| Field | Meaning |
| --- | --- |
| `candidate_id` | Canonical Ballotpedia Candidate URL; required and unique. `Candidate URL` is also accepted. |
| `name`, `state`, `chamber`, `district` | Public identity fields. |
| `website` | Campaign website; `campaign_website` and `Campaign Website` also accepted. |
| `status`, `primary` | Advancement/primary outcome, used only for preservation priority. |
| `status_as_of` | Optional date of the outcome snapshot; retain this when known. |
| `dashboard_id` | Optional explicitly verified link to the published candidate ID. |

Keep a source export with its retrieval time, outcome snapshot date, and original column names privately. Do not substitute the Ballotpedia Candidate URL for a campaign website. Sparse campaign website metadata establishes an enrichment gap; it says nothing by itself about how many campaign sites are already archived. Refresh stale election outcomes before treating queue priorities as current. These tools record the supplied outcome basis and do not claim to verify election results.

Dashboard joins use a canonical URL or explicit dashboard ID first, then an exact unique `name/state/chamber/district` tuple. The join method is recorded. Ambiguous or unmatched rows stay unmatched, with null detector counts and archive pointers; an absent match is not evidence of no analysis. The summary records whether a published dataset was supplied. Review identity joins before research conclusions. No fuzzy name matching or guessed URL is used.

Capture manifests are JSONL, one receipt per captured response:

```json
{"candidate_id":"https://ballotpedia.org/Example","source_url":"https://campaign.example/issues","final_url":"https://campaign.example/issues","kind":"campaign_page","status":"captured","captured_at":"2026-10-30T18:00:00+00:00","sha256":"64 lowercase hex characters","path":"objects/HASH.html","bytes":1234,"content_type":"text/html"}
```

`path` is relative to `--corpus-root`. Repeat `--manifest` for more than one batch. Original manifests should be adapted into this format with a documented mapping; do not invent source URLs, timestamps, hashes, or candidate links. Remote `object_key` references can be retained as extra metadata, but are **manifest only** until the object is downloaded and its bytes verified. Use explicit private R2 pulls to retrieve known objects; no bulk enumeration or whole-bucket download occurs automatically.

The audit writes `summary.json`, `coverage.json`, `coverage.csv`, and `priority-queue.csv`. Each candidate is `verified_source_bytes`, `manifest_only`, or `unknown`. Hash failures, missing objects, invalid timestamps, and incomplete receipts are visible. Verified counts distinguish campaign pages, unreviewed campaign PDFs, and reviewed policy PDFs. An unreviewed PDF remains preservation evidence without asserting policy scope. A detector report PDF is never counted as a policy PDF; a Wayback link is recorded as a pointer, not verified preservation. No live Wayback availability check is implicit.

Priority 1 covers known-site preservation or object verification for losing/non-advancing campaigns; priority 2 covers their missing website enrichment; priorities 3–4 cover the same tasks for other candidates; priority 5 reviews scope/completeness of existing verified captures. A captured homepage alone does not establish policy coverage. None of these statuses establishes complete site preservation or that an absent input means an absent archive.

## Capture a small reviewed batch

```sh
python3 scripts/research.py capture \
  --queue runs/current/audit/priority-queue.csv \
  --limit 5 --delay 2 --out runs/current/capture

python3 scripts/research.py audit \
  --inventory data/candidates.csv \
  --manifest runs/current/capture/captures.jsonl \
  --corpus-root runs/current/capture \
  --expected-candidates 4319 --out runs/current/post-capture-audit
```

For a manually prepared source queue, use `candidate_id,source_url,kind` columns. `kind=policy_pdf` asserts that a human has reviewed that URL's policy scope. An unreviewed PDF is `campaign_pdf`; it counts as source preservation but does not count as a policy PDF. Discovered same-origin PDF links are emitted for review in `discovered-pdfs.json`; they are not fetched recursively. Rows with `review_required=true` are skipped. Convert approved discoveries to CSV after reviewing scope.

The collector accepts public HTTPS only, refuses private network targets/credentialed URLs, checks robots before each origin including redirects, observes crawl delays/request rates, and spaces requests by at least two seconds. Network connections use an IP from the vetted DNS answer while retaining the original hostname for TLS certificate verification. If a configured HTTPS proxy is required for a public URL, the public fetch fails closed because a generic proxy cannot guarantee that vetted destination; it never bypasses that proxy. Use an approved cloud runner with suitable egress rather than working around a proxy denial. Fixed Cloudflare API requests retain standard proxy handling and never follow redirects.

Robots matching supports longest-path precedence, Allow ties, merged applicable groups, wildcard/end anchors, and percent normalization; ambiguous source paths require manual review. Only a 404 means absent robots; auth, challenge HTML, redirects, throttling, and other robot errors fail closed. No browser automation, login, scripts, CAPTCHA handling, or access-control workaround is attempted. Failed requests retain an error receipt. Captures preserve single response bodies, not embedded assets or a rendered/interactive website. Large files, JavaScript-only content, source block pages, and policy completeness require review. No archive submission, detector request, or remote upload is performed by `capture` itself.

## Preserve outputs and resume

For full-seed policy acquisition, use the public-only seed or a private canonical inventory:

```sh
python3 scripts/research.py collect \
  --inventory seeds/candidates-public.json \
  --workers 4 --max-pages 6 --max-pdfs 4 \
  --max-mib 1024 --max-requests 30000 --out runs/policy
```

Add `--dataset runs/current/public/dataset.json` when a published snapshot is available; only exact unique-name matches supply additional website references. Missing websites are discovered from explicit campaign-website anchors on canonical candidate pages. Failed robots access is cached per origin for the run, so a blocked reference site is not repeatedly requested for every candidate. Per-origin request locks and spacing apply across the four worker threads. Statuses distinguish failed discovery, missing website links, capture failure, no policy found in the bounded scan, successful/partial policy captures, and exhausted budgets. A homepage-only capture is not claimed as full policy coverage; automatically selected PDFs remain unreviewed policy candidates. Each completed candidate has an atomic checkpoint, and every accepted source body is rehashed before its receipt is recorded. Resuming the same output directory skips checkpoints; `--retry-failures` permits one further attempt for failed or partial candidates. Byte budgets may exceed the limit by at most the bounded responses already in flight before workers stop.

Raw artifacts must be held in a private repository or private object store. Public-repository Actions artifacts inherit public repository read access and are unsuitable for this corpus. The temporary acquisition workflow uses an isolated `campaign-preservation` branch of the owner's existing private `kb` repository; its main branch and existing project files are unchanged. Private artifacts have seven-day retention, so retrieve/mirror them before expiry. No new credentials or access grants are created by that workflow.

The following commands use credentials supplied to the process, never stored in Git. Uploads are explicit writes and should be run only after authorization for that destination.

```sh
python3 scripts/research.py r2 pull \
  --key private/inputs/candidates.csv --file data/candidates.csv

python3 scripts/research.py upload-run \
  --root runs/current \
  --key-prefix private/cloud-runs/UNIQUE_RUN_ID
```

Private jobs upload under a new run ID. They do not overwrite the canonical inventory or merge manifests automatically. Before another collection batch, reconcile the new capture manifest into the private canonical index with its run prefix/object keys. Audit all relevant manifests together; otherwise a later job may revisit older work. Preserve prior receipts and original objects. For multiple local corpus roots, normalize paths into one private mirror without changing source receipts, or audit each corpus separately; a single audit accepts one root. A partial upload has no completion marker and must not be treated as a complete durable run. The marker confirms upload completion only; inspect the audit/capture receipts for research success or failures. Code/tool checkpoints and remote object checks are separate from a scientific claim about capture completeness.

## Analysis and dashboard updates

Source preservation and detector classification are separate steps. Preserve original HTML/PDF, extracted policy text, the exact detector submission, detector/model version, response, report PDF, word count, hashes, and timestamps before joining a score into a dashboard dataset. The original extraction/submission code and private submission manifest are needed to reproduce the pilot. This patch does not infer missing scores, re-score the pilot, or claim classification proves AI authorship.

A reviewed existing-format dataset can be validated and staged locally:

```sh
node scripts/stage-dataset.mjs data/reviewed-dashboard.json runs/staged-dashboard
```

This reuses `src/stats.js` for weighted summaries, keeps missing analyses null with zero analyzed words, checks IDs/scores/report metadata, retains exact input bytes, and writes a content-addressed dataset plus `proposed-catalog.json` and `validation.json`. Complete score triplets must sum to 100 within one percentage point, permitting whole-percent rounding. Single-report scores must agree with the campaign aggregate within that tolerance. Multi-report aggregates are checked when every report supplies an explicit positive integer `words` count; otherwise the candidate ID appears in `candidateReportConsistencyUnchecked`. That list must be reviewed, not treated as proven aggregate consistency. It does not verify remote report/source object availability. The proposed catalog contains only the dataset pointer; merge it with any retained catalog fields during review. Schema validation has been exercised with fixtures, not the inaccessible current live dataset. Review it against the actual public data before publishing.

The private R2 helper intentionally cannot write `public/`. Use the existing approved publishing tooling after reviewing the public dataset and report/source objects. Upload all referenced immutable public objects first; only then replace the public catalog. Keep its prior contents for rollback. Code deployment remains the existing `npm run deploy` flow. Neither Actions research workflow deploys a Worker, changes the public catalog, or changes the live dashboard.

## Remaining inputs for a complete preservation audit

The required evidence is a canonical machine-readable 4,319-row candidate export, the original capture/submission manifest(s), and read-only access to the corresponding R2 objects or a cloud mirror with paths intact. The public catalog/dataset is also needed to reconcile the pilot. The container's public catalog requests returned HTTP 403 during this implementation, so no full or sampled corpus-coverage rate was computed. If original manifests exist only on a laptop, copy that specific research directory to the private cloud bucket once; do not rely on the laptop as the production runner. Do not infer that the corpus is laptop-only: the repository states a private R2 mirror exists, but it has not been accessed here.
