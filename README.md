# AI in Campaigns

A dashboard of preserved Pangram analyses of 2026 congressional campaign policy text.

## Develop

```sh
npm ci
npm test
npm run build
npm run dev
```

The source repository contains application code and asset references. Campaign data, captured HTML, policy PDFs, submitted text, detector reports, and research logs live in Cloudflare R2. See [asset-links.json](asset-links.json) for the catalog location. A local static preview needs an R2 route proxy or the deployed catalog URL.

## Hosting and data

`wrangler.jsonc` deploys a Worker with static application assets and one R2 binding. `/research-assets/` exposes only the bucket's `public/` prefix. The `private/` preservation mirror has no public route. Archived HTML is served as an attachment with a sandbox policy, so campaign scripts cannot run on the dashboard origin.

The public catalog points to an immutable, content-addressed dataset and reports. Upload referenced objects before replacing the catalog. Previous datasets and original scans remain preserved. The published dataset retains missing-analysis statuses and separate campaign/document records. Word weighting excludes missing analyses.

```sh
wrangler whoami
npm run deploy
```

`CLOUDFLARE_ACCOUNT_ID` must match the configured account. R2 uploads are performed separately from source deployments; a code deployment does not overwrite data. No credentials belong in this repository.

## Verification

Tests cover weighted calculations, filter combinations, missing values, source-route isolation, conditional downloads and PDF byte-range responses. The original research directory retains capture and submission manifests and checksums.
