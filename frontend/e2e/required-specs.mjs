// CI guard: the representative and email browser tests must run and pass, never be skipped quietly for a missing
// setting. Reads Playwright's JSON report and fails unless every spec below ran exactly its number of tests, all
// passed (a retry that passed counts as passed, "flaky"), and none was skipped.
// Usage: node e2e/required-specs.mjs <report.json>
import { readFileSync } from 'node:fs'

const REQUIRED = { 'rep-flow.spec.ts': 3, 'rep-inquiry.spec.ts': 2, 'qa-2026-10-29.spec.ts': 9, 'rep-inquiry-ux.spec.ts': 4 }

const report = JSON.parse(readFileSync(process.argv[2], 'utf8'))
const seen = Object.fromEntries(Object.keys(REQUIRED).map((f) => [f, { passed: 0, other: [] }]))

function walk(suite) {
  for (const spec of suite.specs ?? []) {
    const file = (spec.file ?? suite.file ?? '').split(/[\\/]/).pop()
    if (!(file in seen)) continue
    for (const t of spec.tests ?? []) {
      if (t.status === 'expected' || t.status === 'flaky') seen[file].passed++
      else seen[file].other.push(`${spec.title}: ${t.status}`)
    }
  }
  for (const child of suite.suites ?? []) walk(child)
}
for (const suite of report.suites ?? []) walk(suite)

let ok = true
for (const [file, want] of Object.entries(REQUIRED)) {
  const { passed, other } = seen[file]
  const line = `${file}: ${passed}/${want} passed${other.length ? `; not passed: ${other.join(' | ')}` : ''}`
  if (passed !== want || other.length) { ok = false; console.error(`FAIL ${line}`) } else console.log(`ok   ${line}`)
}
if (!ok) {
  console.error('The representative / email browser tests did not all run and pass (a skip counts as a failure here).')
  process.exit(1)
}
