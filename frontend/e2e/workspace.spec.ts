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
