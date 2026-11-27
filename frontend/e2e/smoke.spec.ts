import { expect, test, type Page } from '@playwright/test'

// Every project this file creates is deleted afterwards, so the demo database stays clean.
const created: string[] = []

test.afterEach(async ({ page }) => {
  // page.request shares the browser's cookies, so this also works in visitor mode.
  while (created.length) await page.request.delete(`/api/projects/${created.pop()}`)
})

async function createProject(page: Page, start: string, goal: string): Promise<string> {
  await page.goto('/#/new')
  await page.getByLabel('Starting point').fill(start)
  await page.getByLabel('Goal', { exact: true }).fill(goal)
  await page.getByRole('button', { name: 'Create project' }).click()
  await expect(page).toHaveURL(/#\/p\/prj_[A-Za-z0-9]+\/edit$/)
  const id = new URL(page.url()).hash.replace('#/p/', '').replace('/edit', '')
  created.push(id)
  await expect(page.getByText(/Saved · revision \d+/)).toBeVisible()
  return id
}

const task = (page: Page, n: number) => page.getByRole('group', { name: `Task ${n}`, exact: true })

test('start and goal are required, tasks are optional, and the draft survives a reload', async ({ page }) => {
  await page.goto('/#/new')
  await page.getByRole('button', { name: 'Create project' }).click()
  await expect(page.getByText('Describe where the project starts.')).toBeVisible()
  await expect(page.getByText('Describe the goal.')).toBeVisible()

  // Only start and goal: a valid project with zero tasks.
  await createProject(page, 'Legacy checkout converts 72% of carts', 'Ship the new checkout safely')
  await expect(page.getByText('No tasks yet.')).toBeVisible()

  // Optional tasks, reordered, with an explicit prerequisite.
  await page.getByRole('button', { name: '+ Add task' }).click()
  await task(page, 1).getByLabel('Title').fill('Run payment sandbox tests')
  await page.getByRole('button', { name: '+ Add task' }).click()
  await task(page, 2).getByLabel('Title').fill('Agree rollout plan')
  await task(page, 2).getByLabel('Days').fill('2')
  await page.getByRole('button', { name: 'Move task 2 up' }).click()
  await expect(task(page, 1).getByLabel('Title')).toHaveValue('Agree rollout plan')

  await page.getByLabel('Must finish first').selectOption({ label: 'Agree rollout plan' })
  await page.getByLabel('Before this can start').selectOption({ label: 'Run payment sandbox tests' })
  await page.getByRole('button', { name: 'Add prerequisite' }).click()
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText(/Saved \(revision \d+\)/)).toBeVisible()

  await page.reload()
  await expect(page.getByLabel('Starting point')).toHaveValue('Legacy checkout converts 72% of carts')
  await expect(page.getByLabel('Goal', { exact: true })).toHaveValue('Ship the new checkout safely')
  await expect(task(page, 1).getByLabel('Title')).toHaveValue('Agree rollout plan')
  await expect(task(page, 1).getByLabel('Days')).toHaveValue('2')
  await expect(task(page, 2).getByLabel('Title')).toHaveValue('Run payment sandbox tests')
  await expect(page.getByRole('list', { name: 'Prerequisites' }))
    .toContainText('Agree rollout plan must finish before Run payment sandbox tests')
})

test('a dependency cycle is saved and reported as a plan check', async ({ page }) => {
  await createProject(page, 'Two teams', 'One release')
  for (const title of ['Build API', 'Build UI']) {
    await page.getByRole('button', { name: '+ Add task' }).click()
    await page.getByRole('group', { name: /^Task \d$/ }).last().getByLabel('Title').fill(title)
  }
  for (const [first, then] of [['Build API', 'Build UI'], ['Build UI', 'Build API']] as const) {
    await page.getByLabel('Must finish first').selectOption({ label: first })
    await page.getByLabel('Before this can start').selectOption({ label: then })
    await page.getByRole('button', { name: 'Add prerequisite' }).click()
  }
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText(/Saved \(revision \d+\)/)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Plan checks' })).toBeVisible()
  await expect(page.locator('.issues')).toContainText(/Build (API|UI) -> Build (UI|API) -> Build (API|UI)/)
})

test('a stale save is refused and the newer version is kept', async ({ page, context }) => {
  const id = await createProject(page, 'Old start', 'Old goal')
  const other = await context.newPage()
  await other.goto(`/#/p/${id}/edit`)
  await other.getByLabel('Goal', { exact: true }).fill('Goal saved in the other tab')
  await other.getByRole('button', { name: 'Save' }).click()
  await expect(other.getByText(/Saved \(revision \d+\)/)).toBeVisible()

  await page.getByLabel('Goal', { exact: true }).fill('Goal from the stale tab')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Someone saved this project after you opened it.')).toBeVisible()
  await page.getByRole('button', { name: /Load the latest version/ }).click()
  await expect(page.getByLabel('Goal', { exact: true })).toHaveValue('Goal saved in the other tab')
})
