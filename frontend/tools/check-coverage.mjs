import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const report = JSON.parse(await readFile(path.join(root, 'coverage/coverage-summary.json'), 'utf8'));
const files = (await readdir(path.join(root, 'src'), { recursive: true }))
  .filter(name => /\.(ts|tsx)$/.test(name) && !/\.test\./.test(name) && !name.startsWith(`test${path.sep}`) && name !== 'types.ts');
const missing = [];
for (const name of files) {
  const filename = path.join(root, 'src', name);
  const entry = report[filename];
  if (!entry) { missing.push(`${name}: absent from coverage report`); continue; }
  const source = await readFile(filename, 'utf8');
  if (/\b(?:istanbul|v8|c8)\s+ignore\b/.test(source)) missing.push(`${name}: coverage exclusion`);
  for (const metric of ['lines', 'statements', 'functions', 'branches']) {
    const value = entry[metric];
    if (value.covered !== value.total || value.skipped) missing.push(`${name}: ${metric} ${value.covered}/${value.total}`);
  }
}
if (!files.length || missing.length) throw new Error(`Full client coverage required: ${missing.join('; ')}`);
console.log(`${files.length} runtime client files: 100% lines, statements, functions and branches; no skipped coverage.`);
