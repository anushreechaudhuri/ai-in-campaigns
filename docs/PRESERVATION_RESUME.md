# Resume source preservation

Source capture, candidate identity, policy scope and detector scoring are separate stages. These commands do not deploy the dashboard or call a detector.

## Existing R2 access

Keep the existing Wrangler login in its authorized environment. Refresh it with `wrangler whoami` if needed; never copy its token into Actions, Git, terminal output or a document.

```sh
wrangler whoami
python3 scripts/r2-inventory.py --all \
  --wrangler-config /path/to/existing/wrangler/config/default.toml \
  --out /private/new-inventory.json
```

The inventory follows every cursor. Its object/byte counts describe the actual listing responses, and explicitly note when hosted jobs are still adding objects.

An explicit private transfer plan contains `account_id`, `bucket`, `proposed_new_prefix`, `completion_marker_key` and `objects`, each with `filename`, `key`, `bytes` and `sha256`.

```sh
python3 scripts/r2-transfer.py \
  --plan /private/verified-transfer-plan.json \
  --source-root /private/downloads \
  --out /private/transfer-receipts \
  --wrangler-config /path/to/existing/wrangler/config/default.toml \
  --execute
```

All local hashes are checked before writes. Existing matching remote objects are reused; mismatches stop the transfer. An uncertain upload is read before retrying. Every object is downloaded and rehashed before the completion marker is written. No original object is deleted or intentionally replaced.

## Hosted execution

The public repository contains collection code only. Run private inventories and source bytes in a private execution repository. Its workflows can use GitHub's short-lived job identity with the separate `preservation/bridge.js` Worker and the existing R2 binding. No Cloudflare API credential is exported. Configure exact private repository IDs, workflow/ref/commit bindings, input keys, a fresh private output prefix and an expiry. The bridge has no delete or overwrite operation, and enforces object and total storage limits.

The directory collector validates the matched profile's displayed name, FEC ID, state, chamber/district and the 2026 cycle before following an explicit campaign website. A directory's paraphrased summaries are retained as reference evidence and never substituted for campaign text. Previously captured, attempted or actively reserved URLs are skipped. Robots rules, origin pauses and source budgets remain active.

```sh
python3 scripts/cloud-directory-policy.py \
  --bridge https://YOUR-TEMPORARY-BRIDGE \
  --input-key private/EXPLICIT-INPUT/input.json \
  --input-sha256 EXPECTED_SHA256 \
  --fallback-input /private/hash-identical-input.json \
  --write-prefix private/cloud-runs/NEW-RUN-PREFIX/ \
  --max-seconds 6000 --out runs/directory
```

Sequential hosted jobs restore the preceding private artifact to the same output directory and add `--resume`. Resume verifies saved body hashes, the input/code/workflow identity and the exact completed prefix. It carries prior source restrictions, attempted URLs and lifetime request/byte charges forward. Independent private Git backups remain useful when R2 authentication or transport is temporarily unavailable. A completed job or successful artifact upload alone does not prove candidate or policy coverage.

## Restore, review and merge

Restore and verify the previously packaged private corpus with its supplied `verify-downloads.py` before merging. Retrieve new hosted outputs with `gh run download RUN_ID --repo OWNER/PRIVATE_REPO --dir /private/new-run`. Preserve originals and use distinct run labels.

```sh
python3 scripts/merge-preservation.py \
  --inventory /private/canonical-inventory.json \
  --run prior=/private/restored-corpus \
  --run new=/private/new-capture-run \
  --out /private/new-merged-corpus

python3 scripts/review-policy-content.py \
  --corpus /private/new-merged-corpus \
  --inventory /private/canonical-inventory.json \
  --out /private/new-policy-text-review
```

Policy URL/link signals, saved substantive text candidates, human-reviewed PDF attribution and complete policy coverage remain distinct. Text/topic matching is triage. Archived bodies, original receipts and source-control history remain authoritative. Keep policy images/OCR, unexecuted app assets and reference documents separate from accepted campaign HTML/PDFs. Do not lift an access hold merely because another executor has different network access.
