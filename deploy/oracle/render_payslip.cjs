// Renders the repo's synthetic payslip (HTML, no real data) into a real PDF for the live extraction check.
// node render_payslip.cjs <repo-frontend-dir> <out.pdf>
const path = require('path')
const { pathToFileURL } = require('url')
const { chromium } = require(path.join(process.argv[2], 'node_modules', 'playwright'))
;(async () => {
  const browser = await chromium.launch()
  const page = await browser.newPage()
  await page.goto(pathToFileURL(path.join(process.argv[2], 'e2e', 'fixtures', 'payslips', 'synthetic_full_payslip.html')).href)
  await page.pdf({ path: process.argv[3], format: 'A4', printBackground: true })
  await browser.close()
  console.log('pdf ok')
})().catch((e) => { console.error(e.message); process.exit(1) })
