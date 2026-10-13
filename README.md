# Archive of the live Exo website, 2026-10-13

Snapshot of https://exo.askaconsult.com taken on 2026-10-13, before it was replaced.

- Vercel project: exo-site (team a-4677s-projects, id team_FaoexnZzdTcrVOAXDutsVakN)
- Production deployment id: dpl_4KfesQaBivfpQv1vVNj7xrEji3dy
- Immutable URL: https://exo-site-podvxw3iy-a-4677s-projects.vercel.app
- Deployed: 2026-10-12 07:53:51 UTC (CLI deploy, no git metadata)
- Source commit: 8d54b55 (full SHA below), "docs: whitepaper v0.2, up to date through D-033", on main.
  Matched by SHA-1 of all 22 files in the deployment file list against `site/` at that commit: 22 of 22 identical.
  Later commits that touched site/ (d3550a7, 9c87439, 11b1b04, a0fc85e) differ and were not live.

## Layout

- `mirror/` the live site as served: /, /app/, /cli/, /whitepaper/ and all assets (byte-identical to the deployment, links relative). Open `mirror/index.html` offline. Each file was fetched from the live domain and its SHA-1 verified against the Vercel deployment file list.
- `source/` the exact `site/` tree from the source commit.

## Restore

1. Redeploy the source: check out the source commit in aska-digital/exo, copy this branch's `source/` over `site/` (or just check out the commit), then `scripts/deploy-site.sh --prod`.
2. Or serve `mirror/` as a static site: copy `mirror/` over `site/` in a checkout at that commit and run `scripts/deploy-site.sh --prod`.
3. Quick roll back without a rebuild: promote deployment dpl_4KfesQaBivfpQv1vVNj7xrEji3dy in Vercel while it still exists (project retention: 30 days, keep last 10).

Not captured: /robots.txt, /sitemap.xml, /404.html do not exist on the live site (404).
Full source SHA: 8d54b55cd7710a24150816f3d4c19746bdab200a
