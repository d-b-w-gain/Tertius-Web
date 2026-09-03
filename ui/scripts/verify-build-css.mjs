import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const outputRoot = resolve('dist')
const indexHtml = readFileSync(resolve(outputRoot, 'index.html'), 'utf8')
const stylesheetMatch = indexHtml.match(/href="(\/assets\/index-[^"]+\.css)"/)

if (!stylesheetMatch) {
  throw new Error('Production index.html does not reference the generated application CSS')
}

const stylesheetPath = resolve(outputRoot, stylesheetMatch[1].replace(/^\//, ''))
const stylesheet = readFileSync(stylesheetPath, 'utf8')
const requiredUtilities = ['.flex{', '.hidden{', '.h-screen{', '.w-screen{', '.fixed{']
const missingUtilities = requiredUtilities.filter((utility) => !stylesheet.includes(utility))

if (missingUtilities.length > 0) {
  throw new Error(
    `Generated application CSS is missing required Tailwind utilities: ${missingUtilities.join(', ')}`,
  )
}

console.log(`Verified generated Tailwind utilities in ${stylesheetMatch[1]}`)
