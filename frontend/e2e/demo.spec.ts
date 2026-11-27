import { expect, test, type Page } from '@playwright/test'

// The end-to-end demo script from docs/demo.md, step by step. Steps 2–6 run on a copy of
// the seeded checkout launch so the seeded example stays untouched for people trying it.

const created: string[] = []
test.afterEach(async ({ page }) => {
  while (created.length) await page.request.delete(`/api/projects/${created.pop()}`)
})

const details = (page: Page) => page.getByRole('complementary', { name: 'Details' })
const mapNode = (page: Page, text: string) => page.locator('.map-node', { hasText: text })

test('demo script: create, review, inspect, compare, persist, export and log an outcome', async ({ page }) => {
  test.setTimeout(120_000)

  // 1. A new project with only a start and a goal, and its initial map.
  await page.goto('/#/new')
  await page.getByLabel('Starting point').fill('Team agrees to try PathGuard')
  await page.getByLabel('Goal', { exact: true }).fill('First pilot completed')
  await page.getByRole('button', { name: 'Create project' }).click()
  await expect(page).toHaveURL(/#\/p\/prj_[A-Za-z0-9]+\/edit$/)
  created.push(new URL(page.url()).hash.replace('#/p/', '').replace('/edit', ''))
  await page.getByRole('link', { name: 'Open workspace' }).click()
  await page.getByRole('button', { name: 'Generate map' }).first().click()
  await expect(mapNode(page, 'Team agrees to try PathGuard')).toBeVisible()
  await expect(mapNode(page, 'First pilot completed')).toBeVisible()

  // 2. The seeded checkout launch (a copy): review a suggested risk and trace its effects.
  const projects = await (await page.request.get('/api/projects')).json() as { id: string; title: string }[]
  const seeded = projects.find((p) => p.title === 'Checkout launch (example)')!
  const graph = (await (await page.request.get(`/api/projects/${seeded.id}/graph`)).json()).graph
  for (const n of graph.nodes) n.task_id = null
  const copy = await (await page.request.post('/api/projects', { data: {
    title: 'Checkout launch (demo copy)', start: 'Checkout change approved',
    goal: 'Reliable launch with verified checkout behavior' } })).json()
  const id = copy.plan.project.id as string
  created.push(id)
  expect((await page.request.put(`/api/projects/${id}/graph`, { data: { graph, revision: 1 } })).status()).toBe(200)

  await page.goto(`/#/p/${id}`)
  await page.locator('.risk-title', { hasText: 'Unclear requirements' }).click()
  await expect(details(page)).toContainText('Scope or acceptance criteria are ambiguous')
  await expect(page.locator('.map-node.role-cause').first()).toBeVisible()
  await expect(page.locator('.map-node.role-effect').first()).toBeVisible()
  await details(page).getByRole('button', { name: 'Accept', exact: true }).click()
  await expect(page.getByText('Suggestion accepted.')).toBeVisible()

  // 3. An unknown estimate and a hypothetical estimate.
  await mapNode(page, 'Waiting for payment provider approval').click()
  await details(page).getByRole('button', { name: /Delay from Build integration/ }).click()
  await expect(details(page).getByLabel('Probability', { exact: true })).toHaveValue('unknown')
  await mapNode(page, 'Confirm requirements').click()
  await details(page).getByRole('button', { name: /Success to Build integration/ }).click()
  await expect(details(page).getByLabel('Source', { exact: true })).toHaveValue('hypothetical')
  await expect(details(page).getByLabel('Assumption', { exact: true })).toHaveValue(/scope ambiguity/i)
  await expect(page.locator('.react-flow__edge-text', { hasText: '85% hyp.' })).toHaveCount(1)

  // 5 (part). Add a review note so persistence covers notes too.
  await mapNode(page, 'Build integration').click()
  await details(page).getByRole('textbox', { name: 'New review note' }).fill('Pin the provider API version')
  await details(page).getByRole('textbox', { name: 'New review note' }).press('Enter')
  await page.getByRole('button', { name: 'Save map' }).click()
  await expect(page.getByText('Map saved.')).toBeVisible()

  // 4. Save the baseline, apply the checklist to a variant, verify the delta.
  await page.getByRole('button', { name: 'Versions', exact: true }).click()
  const center = page.getByRole('region', { name: 'Scenario map' })
  await center.getByRole('button', { name: 'Save current map as baseline' }).click()
  await expect(page.getByText(/Saved version 1/)).toBeVisible()
  await center.getByLabel('Variant name').fill('Requirements checklist')
  await center.getByLabel('Mitigation', { exact: true }).fill('Requirements checklist')
  await center.getByLabel('Expected effect').fill('Scope ambiguity drops from 15% to 5%')
  await center.getByLabel('Owner').fill('Product owner')
  await center.getByLabel('Link 1').selectOption({ label: 'Confirm requirements → Build integration (Scope is clear)' })
  await center.getByLabel('New percent 1').fill('95')
  await center.getByLabel('Assumption 1').fill('Checklist used on every change')
  await page.getByRole('button', { name: '+ Another change' }).click()
  await center.getByLabel('Link 2').selectOption({ label: 'Confirm requirements → Scope ambiguity found (Scope is ambiguous)' })
  await center.getByLabel('New percent 2').fill('5')
  await center.getByLabel('Assumption 2').fill('Checklist used on every change')
  await page.getByRole('button', { name: 'Create variant' }).click()
  await expect(page.locator('.delta-card').first()).toContainText('+8.55 pp')

  // 5. Reload: graph, notes and both versions persist.
  await page.reload()
  await expect(mapNode(page, 'Build integration')).toBeVisible()
  await mapNode(page, 'Build integration').click()
  await expect(details(page)).toContainText('Pin the provider API version')
  await page.getByRole('button', { name: 'Versions', exact: true }).click()
  await expect(page.locator('.version-list li')).toHaveCount(2)
  await expect(page.locator('.delta-card').first()).toContainText('+8.55 pp')

  // 6. A readable report and risk register, then an observed outcome.
  await page.goto(`/#/p/${id}/report`)
  await expect(page.locator('.report-status')).toContainText('DRAFT')
  for (const h of ['Scenario map', 'Results', 'Risk register', 'Mitigations and owners', 'Estimate sources', 'Open evidence gaps']) {
    await expect(page.getByRole('heading', { name: new RegExp(`^${h}`) })).toBeVisible()
  }
  const [csv] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('link', { name: 'Download risk register (CSV)' }).click(),
  ])
  expect(csv.suggestedFilename()).toMatch(/risk-register\.csv$/)

  await page.goto(`/#/p/${id}`)
  await page.getByRole('button', { name: 'Outcomes', exact: true }).click()
  await page.getByRole('region', { name: 'Scenario map' }).getByLabel('Version').selectOption({ label: 'v1 · Baseline' })
  await page.getByRole('region', { name: 'Scenario map' }).getByLabel('Branch observed').selectOption({ label: 'Scope confirmed?: Confirm requirements → Build integration (Scope is clear)' })
  await page.getByRole('region', { name: 'Scenario map' }).getByLabel('Eligible runs (reached the decision)').fill('40')
  await page.getByRole('region', { name: 'Scenario map' }).getByLabel('Runs that took this branch').fill('36')
  await page.getByRole('region', { name: 'Scenario map' }).getByLabel('Context tags (comma separated)').fill('checkout')
  await page.getByRole('button', { name: 'Record outcome' }).click()
  await expect(page.getByText('Outcome recorded.')).toBeVisible()
  await expect(page.locator('.versions')).toContainText('36 of 40 eligible runs')
})
