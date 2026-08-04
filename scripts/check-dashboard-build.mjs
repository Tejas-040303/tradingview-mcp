#!/usr/bin/env node
/**
 * Assert the built dashboard is actually servable.
 *
 * `vite build` exiting 0 is not the same as a working page. The failure this
 * guards against is a wrong `base` path: the bundle builds fine, but index.html
 * points at asset URLs the bridge does not serve, and the browser shows a blank
 * page with 404s in the console. That is invisible to a build-succeeded check
 * and is exactly the kind of thing CI should catch.
 *
 * So: parse the emitted HTML, resolve every asset reference the way bridge.py
 * would, and confirm the file is really there.
 *
 *   node scripts/check-dashboard-build.mjs
 */
import { existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const DASHBOARD = join(ROOT, 'mt5-bridge', 'dashboard');
const OUT = join(DASHBOARD, 'app');
const BASE = '/dashboard/app/';

const problems = [];
const note = message => console.log(`  ${message}`);

const indexPath = join(OUT, 'index.html');
if (!existsSync(indexPath)) {
  console.error(`No build output at ${indexPath}`);
  console.error('Run: npm run build:dashboard');
  process.exit(1);
}

const html = readFileSync(indexPath, 'utf8');
note(`index.html  ${statSync(indexPath).size} bytes`);

// Every src/href the page will ask the bridge for.
const refs = [...html.matchAll(/(?:src|href)="([^"]+)"/g)]
  .map(m => m[1])
  .filter(url => !/^(https?:)?\/\//.test(url) && !url.startsWith('data:'));

if (!refs.length) problems.push('index.html references no local assets at all');

let scripts = 0;
let styles = 0;
for (const ref of refs) {
  if (!ref.startsWith(BASE)) {
    // A relative or root-relative path here means the page would request a URL
    // the bridge does not map to this directory.
    problems.push(`asset "${ref}" is not under ${BASE} — vite base is wrong`);
    continue;
  }
  // Resolve the way _serve_dashboard does: strip /dashboard, join to the
  // dashboard directory.
  const onDisk = join(DASHBOARD, ref.slice('/dashboard/'.length));
  if (!existsSync(onDisk)) {
    problems.push(`asset "${ref}" is referenced but missing at ${onDisk}`);
    continue;
  }
  if (ref.endsWith('.js')) scripts += 1;
  if (ref.endsWith('.css')) styles += 1;
  note(`${ref}  ${statSync(onDisk).size} bytes`);
}

if (!scripts) problems.push('no JavaScript bundle is referenced');
if (!styles) problems.push('no stylesheet is referenced — Tailwind did not emit');

// The app is useless without a mount point for React to render into.
if (!/<div id="root">/.test(html)) problems.push('index.html has no #root element');

if (problems.length) {
  console.error('\nDashboard build is not servable:');
  problems.forEach(p => console.error(`  - ${p}`));
  process.exit(1);
}

console.log(`\nDashboard build OK — ${scripts} script(s), ${styles} stylesheet(s), all resolvable.`);
