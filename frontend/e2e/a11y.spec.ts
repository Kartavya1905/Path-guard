import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// Automated accessibility scan (axe-core, WCAG 2.1 A and AA rules) of every main screen.
// Automated checks find only part of the issues; a manual screen-reader pass is still open.

const created: string[] = []
test.afterEach(async ({ page }) => {
  while (created.length) await page.request.delete(`/api/projects/${created.pop()}`)
})

async function scan(page: Page, name: string) {
  const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
  const summary = result.violations.map((v) =>
    `${name}: ${v.id} (${v.impact}) ${v.help} -> ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`)
  expect(summary, summary.join('\n')).toEqual([])
}

test('main screens have no automatically detectable WCAG 2.1 AA violations', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto('/#/')
  await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()
  await scan(page, 'projects')

  await page.goto('/#/new')
  await page.getByRole('button', { name: 'Create project' }).click() // show validation messages too
  await scan(page, 'new project')

  // A copy of the seeded launch, so every workspace view has content.
  const projects = await (await page.request.get('/api/projects')).json() as { id: string; title: string }[]
  const seeded = projects.find((p) => p.title === 'Checkout launch (example)')!
  const graph = (await (await page.request.get(`/api/projects/${seeded.id}/graph`)).json()).graph
  for (const n of graph.nodes) n.task_id = null
  const copy = await (await page.request.post('/api/projects', { data: { title: 'A11y copy', start: 'Checkout change approved', goal: 'Reliable launch' } })).json()
  const id = copy.plan.project.id as string
  created.push(id)
  await page.request.put(`/api/projects/${id}/graph`, { data: { graph, revision: 1 } })
  await page.request.post(`/api/projects/${id}/versions`, { data: { label: 'Baseline', kind: 'baseline', revision: 2 } })

  await page.goto(`/#/p/${id}/edit`)
  await expect(page.getByLabel('Starting point')).toBeVisible()
  await scan(page, 'plan form')

  await page.goto(`/#/p/${id}`)
  await expect(page.locator('.map-node').first()).toBeVisible()
  await page.locator('.risk-title').first().click()
  await scan(page, 'workspace map with a risk selected')
  await page.locator('.map-node', { hasText: 'Confirm requirements' }).click()
  await page.getByRole('complementary', { name: 'Details' }).getByRole('button', { name: /Success to Build integration/ }).click()
  await scan(page, 'workspace with a link and its estimate editor')

  for (const view of ['Worst cases', 'Versions', 'Outcomes']) {
    await page.getByRole('button', { name: view, exact: true }).click()
    await page.waitForTimeout(300)
    await scan(page, view)
  }

  await page.goto(`/#/p/${id}/report`)
  await expect(page.getByRole('heading', { name: 'Risk register' })).toBeVisible()
  await scan(page, 'report')
})

test('narrow screens: no sideways scrolling, and the workspace stacks its panels', async ({ page }) => {
  const projects = await (await page.request.get('/api/projects')).json() as { id: string; title: string }[]
  const seeded = projects.find((p) => p.title === 'Checkout launch (example)')!
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)

  await page.setViewportSize({ width: 375, height: 812 })
  for (const path of ['/#/', '/#/new', `/#/p/${seeded.id}/edit`, `/#/p/${seeded.id}/report`]) {
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    expect(await overflow(), path).toBeLessThanOrEqual(1)
  }

  await page.setViewportSize({ width: 768, height: 1024 })
  await page.goto(`/#/p/${seeded.id}`)
  await expect(page.locator('.map-node').first()).toBeVisible()
  const map = await page.getByRole('region', { name: 'Scenario map' }).boundingBox()
  const details = await page.getByRole('complementary', { name: 'Details' }).boundingBox()
  expect(map!.width).toBeGreaterThan(700) // the map gets the full width
  expect(details!.y).toBeGreaterThan(map!.y + map!.height - 1) // details sit below it
  await page.locator('.risk-title').first().click()
  await expect(page.getByRole('complementary', { name: 'Details' })).toContainText('Trigger')
})
