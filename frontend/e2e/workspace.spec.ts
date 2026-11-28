import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

// Workspace flows against the real API. Projects are created through the API and deleted after.
const created: string[] = []

test.afterEach(async ({ page }) => {
  // page.request shares the browser's cookies, so this also works in visitor mode.
  while (created.length) await page.request.delete(`/api/projects/${created.pop()}`)
})

async function checkoutProject(request: APIRequestContext): Promise<string> {
  const res = await request.post('/api/projects', {
    data: {
      title: 'Workspace test',
      start: 'Checkout change approved',
      goal: 'Reliable launch',
      context: { domain: 'checkout' },
      tasks: [
        { id: 'tsk_wstest001', title: 'Confirm requirements', position: 0 },
        { id: 'tsk_wstest002', title: 'Build integration', position: 1 },
        { id: 'tsk_wstest003', title: 'QA and staged rollout', position: 2 },
      ],
      prerequisites: [
        { id: 'pre_wstest001', before_task_id: 'tsk_wstest001', after_task_id: 'tsk_wstest002' },
        { id: 'pre_wstest002', before_task_id: 'tsk_wstest002', after_task_id: 'tsk_wstest003' },
      ],
    },
  })
  expect(res.status()).toBe(201)
  const id = (await res.json()).plan.project.id as string
  created.push(id)
  return id
}

const mapNode = (page: Page, text: string) => page.locator('.map-node', { hasText: text })
const details = (page: Page) => page.getByRole('complementary', { name: 'Details' })

async function generate(page: Page, id: string, firstNode = 'Confirm requirements') {
  await page.goto(`/#/p/${id}`)
  await expect(page.getByRole('heading', { name: 'No scenario map yet' })).toBeVisible()
  await page.getByRole('button', { name: 'Generate map' }).first().click()
  await expect(page.getByText(/Map generated: \d+ added/)).toBeVisible()
  await expect(mapNode(page, firstNode)).toBeVisible()
}

test('generate a map, review a suggestion, edit a step and save', async ({ page }) => {
  const id = await checkoutProject(page.request)
  await generate(page, id)
  await expect(page.getByText('Ready to analyze')).toBeVisible()

  // Selecting a risk shows its details and highlights causes and effects on the map.
  await page.locator('.risk-title', { hasText: 'Release defect or failed rollback' }).click()
  await expect(details(page)).toContainText('A defect appears in production during rollout')
  await expect(page.locator('.map-node.role-cause').first()).toBeVisible()
  await expect(page.locator('.map-node.role-effect').first()).toBeVisible()
  await details(page).getByRole('button', { name: 'Accept and add branch' }).click()
  await expect(page.getByText('Accepted, and its branch is now on the map.')).toBeVisible()
  await expect(details(page)).toContainText('Branch is on the map')

  // Edit a step and add a review note, then save the map.
  await mapNode(page, 'Build integration').click()
  await details(page).getByLabel('Name').fill('Build payment integration')
  await details(page).getByRole('textbox', { name: 'New review note' }).fill('Pin the API version')
  await details(page).getByRole('textbox', { name: 'New review note' }).press('Enter')
  await expect(page.getByText('Unsaved map changes')).toBeVisible()
  await page.getByRole('button', { name: 'Save map' }).click()
  await expect(page.getByText('Map saved.')).toBeVisible()

  await page.reload()
  await expect(mapNode(page, 'Build payment integration')).toBeVisible()
  await mapNode(page, 'Build payment integration').click()
  await expect(details(page)).toContainText('Pin the API version')
  await page.getByRole('button', { name: 'Decided' }).click()
  await expect(page.locator('.risk-list')).toContainText('Release defect or failed rollback')
})

test('collapse hides side branches and expand brings them back', async ({ page }) => {
  const id = await checkoutProject(page.request)
  await generate(page, id)
  await expect(mapNode(page, 'Release defect needs rollback')).toBeVisible()
  await page.getByRole('button', { name: 'Collapse all' }).click()
  await expect(mapNode(page, 'Release defect needs rollback')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Expand \d+ hidden branch nodes/ }).first()).toBeVisible()
  await page.getByRole('button', { name: 'Expand all' }).click()
  await expect(mapNode(page, 'Release defect needs rollback')).toBeVisible()
})

test('a stale map save is refused and the newer map is kept', async ({ page, context }) => {
  const id = await checkoutProject(page.request)
  await generate(page, id)
  const other = await context.newPage()
  await other.goto(`/#/p/${id}`)
  await mapNode(other, 'Confirm requirements').click()
  await details(other).getByLabel('Name').fill('Confirm scope (other tab)')
  await other.getByRole('button', { name: 'Save map' }).click()
  await expect(other.getByText('Map saved.')).toBeVisible()

  await mapNode(page, 'Confirm requirements').click()
  await details(page).getByLabel('Name').fill('Confirm scope (stale tab)')
  await page.getByRole('button', { name: 'Save map' }).click()
  await expect(page.getByText('Someone saved this project after you opened it.')).toBeVisible()
  await page.getByRole('button', { name: /Load the latest version/ }).click()
  await expect(mapNode(page, 'Confirm scope (other tab)')).toBeVisible()
})

test('estimates drive the probability panel: exact, then a range', async ({ page }) => {
  const res = await page.request.post('/api/projects', { data: { start: 'Ready', goal: 'Done' } })
  const id = (await res.json()).plan.project.id as string
  created.push(id)
  await generate(page, id, 'Ready')
  const panel = page.getByRole('region', { name: 'Probability' })
  await expect(panel).toContainText('Partial')

  await mapNode(page, 'Ready').click()
  await details(page).getByRole('button', { name: /Success to Done/ }).click()
  await details(page).getByLabel('Probability', { exact: true }).selectOption('point')
  await details(page).getByLabel('Percent (%)').fill('100')
  await details(page).getByLabel('Assumption', { exact: true }).fill('Nothing can stop it')
  await details(page).getByRole('button', { name: 'Apply estimate' }).click()
  await page.getByRole('button', { name: 'Save map' }).click()
  await expect(page.getByText('Map saved.')).toBeVisible()
  await expect(panel).toContainText('Exact')
  await expect(panel).toContainText('100.0%')

  await details(page).getByLabel('Probability', { exact: true }).selectOption('range')
  await details(page).getByLabel('Low (%)').fill('80')
  await details(page).getByLabel('High (%)').fill('90')
  await details(page).getByRole('button', { name: 'Apply estimate' }).click()
  await page.getByRole('button', { name: 'Save map' }).click()
  // A single 80–90% link leaves 10–20% of outcomes unmodeled: shown, never normalized away.
  await expect(panel).toContainText('Clean first-pass success80% – 90%')
  await expect(panel).toContainText('Eventual successat least 80% (partial)')
  await expect(panel).toContainText('10%–20% of outcomes are not modeled')
  await expect(page.locator('.react-flow__edge-text').first()).toContainText('80–90% hyp.')
})

test('worst cases: critical stays visible, cascades are traced, show on map', async ({ page }) => {
  const id = await checkoutProject(page.request)
  await generate(page, id)
  await page.locator('.risk-title', { hasText: 'Release defect or failed rollback' }).click()
  await details(page).getByRole('button', { name: 'Accept and add branch' }).click()
  await expect(page.getByText('Accepted, and its branch is now on the map.')).toBeVisible()

  await page.getByRole('button', { name: 'Worst cases', exact: true }).click()
  const first = page.locator('.worst-list > li').first()
  await expect(first).toContainText('#1')
  await expect(first).toContainText('Release defect or failed rollback')
  await expect(first).toContainText('critical')
  await expect(first.locator('.cascade')).toContainText('Release defect needs rollback')

  // A strict likelihood filter hides the rest, but never the critical risk.
  await page.getByLabel('Evidence at least').selectOption('strong')
  await expect(page.getByText(/critical kept visible despite the filters/)).toBeVisible()
  await expect(page.locator('.worst-list > li')).toHaveCount(1)
  await expect(page.getByText(/hidden by filters/)).toBeVisible()

  await first.getByRole('button', { name: 'Show on map' }).click()
  await expect(details(page)).toContainText('Release defect or failed rollback')
  await expect(page.locator('.map-node.role-effect').first()).toBeVisible()
})

test('save a baseline, apply the checklist as a variant, and see +8.55 pp', async ({ page }) => {
  // Copy the seeded launch example's baseline map into a fresh project.
  const projects = await (await page.request.get('/api/projects')).json() as { id: string; title: string }[]
  const seeded = projects.find((p) => p.title === 'Checkout launch (example)')
  expect(seeded, 'the checkout launch example should be seeded').toBeTruthy()
  const versions = await (await page.request.get(`/api/projects/${seeded!.id}/versions`)).json()
  const base = await (await page.request.get(`/api/projects/${seeded!.id}/versions/${versions[0].id}`)).json()
  const res = await page.request.post('/api/projects', { data: { start: base.plan.project.start, goal: base.plan.project.goal, title: 'Checklist demo' } })
  const created_ = await res.json()
  const id = created_.plan.project.id as string
  created.push(id)
  for (const n of base.graph.nodes) n.task_id = null
  expect((await page.request.put(`/api/projects/${id}/graph`, { data: { graph: base.graph, revision: 1 } })).status()).toBe(200)

  await page.goto(`/#/p/${id}`)
  await page.getByRole('button', { name: 'Versions', exact: true }).click()
  await page.getByRole('button', { name: 'Save current map as baseline' }).click()
  await expect(page.getByText(/Saved version 1/)).toBeVisible()

  await page.getByLabel('Variant name').fill('Requirements checklist')
  await page.getByLabel('Mitigation', { exact: true }).fill('Requirements checklist')
  await page.getByLabel('Expected effect').fill('Scope ambiguity drops from 15% to 5%')
  await page.getByLabel('Link 1').selectOption({ label: 'Confirm requirements → Build integration (Scope is clear)' })
  await page.getByLabel('New percent 1').fill('95')
  await page.getByLabel('Assumption 1').fill('Checklist used on every change')
  await page.getByRole('button', { name: '+ Another change' }).click()
  await page.getByLabel('Link 2').selectOption({ label: 'Confirm requirements → Scope ambiguity found (Scope is ambiguous)' })
  await page.getByLabel('New percent 2').fill('5')
  await page.getByLabel('Assumption 2').fill('Checklist used on every change')
  await page.getByRole('button', { name: 'Create variant' }).click()
  await expect(page.getByText(/Created variant v2/)).toBeVisible()

  const cmp = page.locator('.comparison')
  await expect(cmp.locator('.delta-card').first()).toContainText('+8.55 pp')
  await expect(cmp).toContainText('72.7% (exact 72.675%)')
  await expect(cmp).toContainText('81.2% (exact 81.225%)')
  await expect(cmp).toContainText('85% (hypothetical)')

  await page.reload()
  await page.getByRole('button', { name: 'Versions', exact: true }).click()
  await expect(page.locator('.version-list li')).toHaveCount(2)
  await expect(page.locator('.comparison .delta-card').first()).toContainText('+8.55 pp')
})

test('report, CSV export, sign-off, and an outcome turned into an approved estimate', async ({ page }) => {
  // Copy the seeded transaction example into a fresh project with a saved baseline.
  const projects = await (await page.request.get('/api/projects')).json() as { id: string; title: string }[]
  const seeded = projects.find((p) => p.title === 'Checkout transaction (example)')!
  const vs = await (await page.request.get(`/api/projects/${seeded.id}/versions`)).json()
  const base = await (await page.request.get(`/api/projects/${seeded.id}/versions/${vs[0].id}`)).json()
  const made = await (await page.request.post('/api/projects', { data: { start: base.plan.project.start, goal: base.plan.project.goal, title: 'Outcome demo' } })).json()
  const id = made.plan.project.id as string
  created.push(id)
  await page.request.put(`/api/projects/${id}/graph`, { data: { graph: base.graph, revision: 1 } })
  expect((await page.request.post(`/api/projects/${id}/versions`, { data: { label: 'Baseline', kind: 'baseline', revision: 2 } })).status()).toBe(201)

  // Draft report and CSV.
  await page.goto(`/#/p/${id}/report`)
  await expect(page.locator('.report-status')).toContainText('DRAFT')
  await expect(page.getByRole('img', { name: 'Scenario map snapshot' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Risk register' })).toBeVisible()
  await expect(page.locator('.report')).toContainText('81.1% (exact 81.144%)')
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('link', { name: 'Download risk register (CSV)' }).click(),
  ])
  expect(download.suggestedFilename()).toContain('draft-risk-register.csv')

  // Sign off the saved version: the report becomes "reviewed".
  await page.getByLabel('Report on').selectOption({ label: 'v1 · Baseline' })
  await page.getByLabel('Sign-off note').fill('Numbers checked')
  await page.getByRole('button', { name: 'Sign off as reviewed' }).click()
  await expect(page.locator('.report-status')).toContainText('REVIEWED')

  // Record an outcome, review the candidate, approve it with an explicit sibling change.
  await page.goto(`/#/p/${id}`)
  await page.getByRole('button', { name: 'Outcomes', exact: true }).click()
  await page.getByLabel('Branch observed').selectOption({ label: 'Payment authorized?: Authorize payment → Confirm order (Payment authorized)' })
  await page.getByLabel('Eligible runs (reached the decision)').fill('200')
  await page.getByLabel('Runs that took this branch').fill('185')
  await page.getByLabel('Context tags (comma separated)').fill('web')
  await page.getByRole('button', { name: 'Record outcome' }).click()
  await expect(page.getByText('Outcome recorded.')).toBeVisible()
  await expect(page.locator('.versions')).toContainText('185 of 200 eligible runs')

  await page.getByLabel('Branch to estimate').selectOption({ label: 'Payment authorized?: Authorize payment → Confirm order (Payment authorized)' })
  await page.getByLabel('Context tags to match').fill('web')
  await page.getByRole('button', { name: 'Check' }).click()
  await expect(page.locator('.proposal')).toContainText('Candidate')
  await expect(page.locator('.proposal')).toContainText('185 / 200')
  await page.getByLabel('New percent for Payment declined').fill('7.5')
  await page.getByLabel('Assumption for Payment declined').fill('Remainder of observed runs')
  await page.getByRole('button', { name: 'Approve and apply' }).click()
  await expect(page.getByText(/Estimate updated in a new revision/)).toBeVisible()
  await expect(page.getByRole('region', { name: 'Probability' })).toContainText('83.4%')
})

test('risk library suggests similar risks and adds one to review', async ({ page }) => {
  const id = await checkoutProject(page.request)
  await generate(page, id)
  const lib = page.getByRole('region', { name: 'From the risk library' })
  await expect(lib).toBeVisible()
  await expect(lib.locator('li')).toHaveCount(5)
  const title = (await lib.locator('li strong').first().textContent())!
  await lib.getByRole('button', { name: `Add to review: ${title}` }).click()
  await expect(page.getByText(`Added “${title}” to the review list.`)).toBeVisible()
  await expect(page.locator('.risk-list')).toContainText(title)
})
