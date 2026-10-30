import type { On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'

const PANE = {
  plugin: 'pr-tracker',
  component: 'Pane',
  requestId: 'prs',
  props: {
    title: 'PRs',
    isFocused: false,
    bodyColumns: 90,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 30 },
    view: {},
  },
} as const

const BAND = {
  plugin: 'pr-tracker',
  component: 'AbovePrompt',
  props: {
    hasSurvey: false,
    isWorking: false,
    maxRows: 12,
    bodyColumns: 90,
    scroll: { offset: 0, bodyRows: 11 },
    view: {},
  },
} as const

const PR_782 = JSON.stringify({
  number: 782,
  title: 'review ledger',
  state: 'OPEN',
  headRefName: 'review-ledger-targets',
  headRefOid: 'abc',
  mergeStateStatus: 'CLEAN',
  baseRefName: 'master',
  statusCheckRollup: [
    { __typename: 'CheckRun', status: 'COMPLETED', conclusion: 'SUCCESS' },
    { __typename: 'CheckRun', status: 'COMPLETED', conclusion: 'FAILURE' },
    { __typename: 'CheckRun', status: 'IN_PROGRESS', conclusion: '' },
  ],
})

const CMD = {
  command: 'prs',
  origin: { kind: 'composer' },
  presentation: { isFullscreen: true, columns: 100 },
} as const

const T = '\t'
const RUNS = [
  ['2026-10-30T10:26:57Z', 'start', 'review-ledger-targets', 'e8c8f355', 'r1', '-', '-', '-', '-', '-', '-'],
  ['2026-10-30T10:30:59Z', 'done', 'review-ledger-targets', 'e8c8f355', 'r1', '2', '2', 'medium', '0', '0', '-'],
]
  .map(f => f.join(T))
  .join('\n')

const ISSUES = [
  ['1', 'bug', 'high', 'open', '-', 'a.sh:1', 'first problem'],
  ['2', 'cleanup', 'low', 'fixed', 'abc1234def', 'b.sh:2', 'second problem'],
]
  .map(f => f.join(T))
  .join('\n')

const LEDGER = '/repo/.git/claude-review'

// What lies outside the plugin: a test changes it between steps.
const world = () => ({
  /** mtimes of the files the plugin stats; any other path is missing. */
  mtimes: { [`${LEDGER}/items-r1.json`]: 1 } as Record<string, number>,
  /** The answer of `review_ledger.sh issues <id>`. */
  issues: (id: string): { exitCode: number; stdout: string } =>
    id === 'r1' ? { exitCode: 0, stdout: ISSUES } : { exitCode: 1, stdout: '' },
  calls: [] as { argv: readonly string[]; timeoutMs: number | undefined }[],
  descriptions: [] as string[],
  /** `git rev-parse` fails, as outside a repository. */
  isRepo: true,
  /** How many git calls reject first, as a timeout does. */
  gitRejects: 0,
  /** PR state `gh pr view` answers for 771-776; OPEN when absent. */
  states: {} as Record<number, string>,
})

const prJson = (n: number, branch: string, state = 'OPEN') =>
  JSON.stringify({
    number: n, title: 'older', state, headRefName: branch, headRefOid: 'abc',
    isCrossRepository: false, headRepositoryOwner: { login: 'o' }, statusCheckRollup: [],
  })

// `review_ledger.sh runs` prints the done rows only, padded to 11 columns.
const doneRows = (text: string) =>
  text
    .split('\n')
    .map(l => l.split(T))
    .filter(f => f[1] === 'done')
    .map(f => Array.from({ length: 11 }, (_, i) => f[i] ?? '-').join(T))
    .join('\n')

// The tests run on a real clock for the hooks' fire-and-forget work; the lib has no timer types.
declare const setTimeout: (fn: () => void, ms: number) => unknown
const settle = () => new Promise<void>(resolve => setTimeout(resolve, 25))

type Node = { type?: string; props?: { flexDirection?: string }; children?: unknown[] }

// The rows the list draws: each line of a PR block, and each other child of the list.
const rowsDrawn = async (ui: { findAll: (q: object) => Promise<readonly unknown[]> }) => {
  const [root] = (await ui.findAll({})) as Node[]
  let n = 0
  for (const c of root?.children ?? []) {
    if (typeof c !== 'object' || c === null) continue
    const el = c as Node
    n += el.type === 'Box' && el.props?.flexDirection === 'column' ? (el.children?.length ?? 0) : 1
  }
  return n
}

// Each hook stands for the engine: it answers a call on `$` with { value } or { deny }.
const wire = (on: On, runs = RUNS, stored: Record<string, unknown> = {}, cwd = '/work', w = world()) => {
  const clock = mock.clock(on)
  on('store.get', (_, e) => ({ value: stored[e.key] }))
  on('store.set', (_, e) => {
    stored[e.key] = e.value
    return { value: undefined }
  })
  // Beneath the plugins: a band no plugin draws stays empty.
  on('ui.render', { component: 'AbovePrompt' }, ($, e) => {
    const { Box } = $.ui.resolve(e)
    return <Box />
  })
  on('command.register', (_, e) => {
    w.descriptions.push(e.description ?? '')
    return { value: { command: 'prs' } }
  })
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.close', () => ({ value: undefined }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.cwd', () => ({ value: cwd }))
  on('fs.stat', (_, e) => {
    const mtimeMs = w.mtimes[e.path]
    return mtimeMs === undefined
      ? { deny: 'ENOENT' }
      : { value: { kind: 'file', size: 1, mtimeMs, isLink: false } }
  })
  on('process.run', (_, e) => {
    w.calls.push({ argv: e.argv, timeoutMs: e.init?.timeoutMs })
    const [a, b] = e.argv
    const out = (exitCode: number, stdout: string, stderr = '') => ({
      value: { exitCode, stdout, stderr, isStdoutTruncated: false, isStderrTruncated: false },
    })
    const ok = (stdout: string) => out(0, stdout)
    if (a === 'git' && w.gitRejects > 0) {
      w.gitRejects--
      return { deny: 'timed out' }
    }
    if (a === 'git' && !w.isRepo) return out(128, '', 'fatal: not a git repository\n')
    if (a === 'git' && b === 'rev-parse' && e.argv.includes('--show-toplevel')) return ok('/repo\n')
    if (a === 'gh' && b === 'pr' && e.argv[3] === '782') return ok(PR_782)
    if (a === 'gh' && b === 'pr' && /^77[1-6]$/.test(e.argv[3] ?? '')) {
      return ok(prJson(Number(e.argv[3]), `old-${e.argv[3]}`, w.states[Number(e.argv[3])]))
    }
    if (a === 'gh') return out(1, '', 'no pull requests found\n')
    if (a === 'bash' && b === '/repo/scripts/review_ledger.sh') {
      if (e.argv[2] === 'dir') return ok(`${LEDGER}\n`)
      if (e.argv[2] === 'runs') return ok(doneRows(runs))
      if (e.argv[2] === 'issues') {
        const r = w.issues(e.argv[3] ?? '')
        return out(r.exitCode, r.stdout)
      }
    }
    return { deny: 'unexpected argv' }
  })
  return clock
}

test('/prs add fills the pane: header, totals, issues, and an error marker per failed PR', async ($, on) => {
  wire(on)
  on('tool.call', () => ({ result: {} }))
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  const out = await $.command.run({ ...CMD, args: 'add 782 999' })
  expect(out.text).toBe('Tracking #782 #999.')

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    const header = await ui.find({ type: 'Button', key: 'pr-782' })
    expect(header?.text).toBe('▾ #782 review-ledger-targets  OPEN  mergeable  CI 1/3 ✗1 …1  1 review (medium)')
    const broken = await ui.find({ type: 'Button', key: 'pr-999' })
    expect(broken?.text).toContain('⚠ no pull requests found')
    expect(await ui.find({ type: 'Text', text: /2 issues: 1 fixed · 1 open/ })).toBeDefined()
    // Open issues come before fixed ones.
    const rows = await ui.findAll({ type: 'Text', text: /problem/ })
    const at = (word: string) => rows.findIndex(r => r.text?.includes(word))
    expect(at('first')).toBeGreaterThanOrEqual(0)
    expect(at('first')).toBeLessThan(at('second'))
    await ui.unmount()
  }
})

test('issues run the ledger script from the repo root when the session starts in a subdirectory', async ($, on) => {
  wire(on, RUNS, {}, '/repo/sub/dir')
  await $.session.start({ cwd: '/repo/sub/dir', surface: 'terminal', isInteractive: true })
  await $.command.run({ ...CMD, args: 'add 782' })

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /first problem/ })).toBeDefined()
  await ui.unmount()
})

test('the header button toggles the expanded PR', async ($, on) => {
  wire(on)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  await $.command.run({ ...CMD, args: 'add 782' })

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /first problem/ })).toBeDefined()
  await ui.press({ key: 'pr-782' })
  expect(await ui.find({ type: 'Text', text: /first problem/ })).toBeUndefined()
  await ui.press({ key: 'pr-782' })
  expect(await ui.find({ type: 'Text', text: /first problem/ })).toBeDefined()
  await ui.unmount()
})

test('an empty list shows the hint', async ($, on) => {
  wire(on)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /No PRs yet/ })).toBeDefined()
  await ui.unmount()
})

test('a gh pr create in Bash adds the PR it printed', async ($, on) => {
  wire(on)
  on('tool.call', () => ({
    result: { stdout: '', stderr: '', interrupted: false },
    text: 'https://github.com/cthulhu666/sprout-lang/pull/782\n',
  }))
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  await $.tool.call({ tool: 'Bash', command: 'gh pr create --fill' })

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Button', key: 'pr-782' })).toBeDefined()
  await ui.unmount()
})

test('a run that targets a PR adds it on the next ledger read, unless the PR was removed', async ($, on) => {
  const later = ['2099-01-01T00:00:00Z', 'done', 'other', 'abc', 'r9', '0', '0', 'low', '-', '-', 'pr:900'].join(T)
  const clock = wire(on, `${RUNS}\n${later}`)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  await $.command.run({ ...CMD, args: '' })
  await clock.advance(5_000)

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'scope' })
  await settle()
  expect(await ui.find({ type: 'Button', key: 'pr-900' })).toBeDefined()

  await $.command.run({ ...CMD, args: 'remove 900' })
  await clock.advance(10_000)
  expect(await ui.find({ type: 'Button', key: 'pr-900' })).toBeUndefined()
  await ui.unmount()
})

test('pane view: the AbovePrompt band passes through', async ($, on) => {
  wire(on)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  await $.command.run({ ...CMD, args: 'add 782' })

  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await ui.find({ type: 'Button', key: 'pr-782' })).toBeUndefined()
  await ui.unmount()
})

test('band view draws the PR list in AbovePrompt, and yields to a survey', async ($, on) => {
  wire(on, RUNS, { view: 'band' })
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  await $.command.run({ ...CMD, args: 'add 782' })

  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  const header = await ui.find({ type: 'Button', key: 'pr-782' })
  expect(header?.text).toContain('#782 review-ledger-targets')
  expect(await ui.find({ type: 'Text', text: /first problem/ })).toBeDefined()
  await ui.unmount()

  const survey = await $.ui.mount({ ...BAND, props: { ...BAND.props, hasSurvey: true }, surface: 'terminal' })
  expect(await survey.find({ type: 'Button', key: 'pr-782' })).toBeUndefined()
  await survey.unmount()
})

test('/prs band and /prs pane switch the view and keep it in the store', async ($, on) => {
  const stored: Record<string, unknown> = {}
  wire(on, RUNS, stored)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  await $.command.run({ ...CMD, args: 'add 782' })

  const out = await $.command.run({ ...CMD, args: 'band' })
  expect(out.text).toBe('PRs band shown.')
  expect(stored.view).toBe('band')
  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await band.find({ type: 'Button', key: 'pr-782' })).toBeDefined()
  await band.unmount()

  const bare = await $.command.run({ ...CMD, args: '' })
  expect(bare.text).toBe('PRs band shown.')

  const back = await $.command.run({ ...CMD, args: 'pane' })
  expect(back.text).toBe('PRs pane opened.')
  expect(stored.view).toBe('pane')
  const gone = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(await gone.find({ type: 'Button', key: 'pr-782' })).toBeUndefined()
  await gone.unmount()
})

test('the footer button flips the view', async ($, on) => {
  wire(on)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await pane.find({ type: 'Button', key: 'view' }))?.text).toBe('band')
  await pane.press({ key: 'view' })
  await pane.unmount()

  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect((await band.find({ type: 'Button', key: 'view' }))?.text).toBe('pane')
  await band.press({ key: 'view' })
  await band.unmount()

  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await again.find({ type: 'Button', key: 'view' }))?.text).toBe('band')
  await again.unmount()
})

test('the command description says whose PRs the list holds', async ($, on) => {
  const w = world()
  wire(on, RUNS, {}, '/work', w)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  expect(w.descriptions[0]).toContain('any session in this repo')
  expect(w.descriptions[0]).not.toContain("this session's")
})

test('every call out to git, gh and the ledger script has a timeout; gh asks for the fork fields', async ($, on) => {
  const w = world()
  wire(on, RUNS, {}, '/work', w)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  await $.command.run({ ...CMD, args: 'add 782' })
  await settle()

  const kinds = new Set(w.calls.map(c => (c.argv[0] === 'bash' ? `bash ${c.argv[2]}` : c.argv[0])))
  expect([...kinds].sort()).toEqual(['bash dir', 'bash issues', 'bash runs', 'gh', 'git'])
  for (const c of w.calls) expect(c.timeoutMs).toBeGreaterThan(0)
  const gh = w.calls.find(c => c.argv[0] === 'gh')
  expect(gh?.argv.join(' ')).toContain('isCrossRepository,headRepositoryOwner')
  expect(gh?.argv.join(' ')).toContain('mergeStateStatus,baseRefName,isDraft')
})

test('a failed issues call is retried on the next round, and keeps the issues already shown', async ($, on) => {
  const w = world()
  let isFailing = true
  w.issues = () => (isFailing ? { exitCode: 1, stdout: '' } : { exitCode: 0, stdout: ISSUES })
  const clock = wire(on, RUNS, {}, '/work', w)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  await $.command.run({ ...CMD, args: 'add 782' })

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await settle()
  expect(await ui.find({ type: 'Text', text: /first problem/ })).toBeUndefined()

  isFailing = false
  await clock.advance(5_000)
  await settle()
  expect(await ui.find({ type: 'Text', text: /first problem/ })).toBeDefined()

  // The run's files change and the call fails: what is shown stays.
  isFailing = true
  w.mtimes[`${LEDGER}/items-r1.json`] = 2
  const before = w.calls.filter(c => c.argv[2] === 'issues').length
  await clock.advance(5_000)
  await settle()
  expect(w.calls.filter(c => c.argv[2] === 'issues').length).toBeGreaterThan(before)
  expect(await ui.find({ type: 'Text', text: /first problem/ })).toBeDefined()
  await ui.unmount()
})

test('an items file with no issues is a clean review', async ($, on) => {
  const w = world()
  w.issues = () => ({ exitCode: 0, stdout: '' })
  wire(on, RUNS, {}, '/work', w)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  await $.command.run({ ...CMD, args: 'add 782' })

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await settle()
  expect(await ui.find({ type: 'Text', text: /0 issues/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /no issue data/ })).toBeUndefined()
  await ui.unmount()
})

test('a run with no items file has no issue data, and the ledger script is not asked', async ($, on) => {
  const w = world()
  w.mtimes = {}
  wire(on, RUNS, {}, '/work', w)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  await $.command.run({ ...CMD, args: 'add 782' })

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await settle()
  expect(await ui.find({ type: 'Text', text: /no issue data/ })).toBeDefined()
  expect(w.calls.filter(c => c.argv[2] === 'issues')).toEqual([])
  await ui.unmount()
})

test('outside a repository git is asked once, not every round', async ($, on) => {
  const w = world()
  w.isRepo = false
  const clock = wire(on, RUNS, {}, '/nowhere', w)
  await $.session.start({ cwd: '/nowhere', surface: 'terminal', isInteractive: true })
  for (let i = 0; i < 3; i++) {
    await clock.advance(5_000)
    await settle()
  }
  expect(w.calls.filter(c => c.argv[0] === 'git')).toHaveLength(1)
  expect(w.calls.filter(c => c.argv[0] === 'bash')).toEqual([])
})

test('a git call that times out is retried, not taken as outside a repository', async ($, on) => {
  const w = world()
  w.gitRejects = 1
  const clock = wire(on, RUNS, {}, '/work', w)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  for (let i = 0; i < 2; i++) {
    await clock.advance(5_000)
    await settle()
  }
  expect(w.calls.filter(c => c.argv[0] === 'git')).toHaveLength(2)
  expect(w.calls.some(c => c.argv[2] === 'runs')).toBe(true)
})

test('the list never draws more rows than its budget', async ($, on) => {
  wire(on)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  await $.command.run({ ...CMD, args: 'add 771 772 773 774 775 776 782' })
  await settle()
  const mount = (bodyRows: number) =>
    $.ui.mount({ ...PANE, props: { ...PANE.props, scroll: { offset: 0, bodyRows } }, surface: 'terminal' })

  // Room for every header and totals line, and the issues.
  const roomy = await mount(40)
  expect(await rowsDrawn(roomy)).toBeLessThanOrEqual(40)
  expect(await roomy.findAll({ type: 'Button', text: /^[▾▸] #/ })).toHaveLength(7)
  expect(await roomy.find({ type: 'Text', text: /more PRs/ })).toBeUndefined()
  expect(await roomy.find({ type: 'Text', text: /second problem/ })).toBeDefined()
  await roomy.unmount()

  // Collapsed PRs lose their totals line; the expanded PR's issues get what is left.
  const tight = await mount(10)
  expect(await rowsDrawn(tight)).toBeLessThanOrEqual(10)
  expect(await tight.findAll({ type: 'Button', text: /^[▾▸] #/ })).toHaveLength(7)
  expect(await tight.find({ type: 'Text', text: /no reviews yet/ })).toBeUndefined()
  expect(await tight.find({ type: 'Text', text: /… 2 more$/ })).toBeDefined()
  expect(await tight.find({ type: 'Button', key: 'refresh' })).toBeDefined()
  await tight.unmount()

  // Too many for headers: the newest fit, the expanded one among them, and the rest are counted.
  const few = await mount(6)
  expect(await rowsDrawn(few)).toBeLessThanOrEqual(6)
  expect(await few.find({ type: 'Button', key: 'pr-782' })).toBeDefined()
  expect(await few.find({ type: 'Button', key: 'pr-771' })).toBeUndefined()
  expect(await few.find({ type: 'Text', text: /… 4 more PRs/ })).toBeDefined()
  expect(await few.find({ type: 'Button', key: 'refresh' })).toBeDefined()
  await few.unmount()

  // Down to three rows: the footer, the count, and the expanded PR's header.
  const least = await mount(3)
  expect(await rowsDrawn(least)).toBeLessThanOrEqual(3)
  expect(await least.find({ type: 'Button', key: 'pr-782' })).toBeDefined()
  expect(await least.find({ type: 'Text', text: /… 6 more PRs/ })).toBeDefined()
  expect(await least.find({ type: 'Button', key: 'refresh' })).toBeDefined()
  await least.unmount()
})

const LATER = (n: number) =>
  ['2099-01-01T00:00:00Z', 'done', 'other', 'abc', `r${n}`, '0', '0', 'low', '-', '-', `pr:${n}`].join(T)

test('default scope shows PRs added by hand or created here, and hides ones a run named', async ($, on) => {
  const clock = wire(on, `${RUNS}\n${LATER(900)}`)
  on('tool.call', () => ({
    result: { stdout: '', stderr: '', interrupted: false },
    text: 'https://github.com/cthulhu666/sprout-lang/pull/776\n',
  }))
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  await $.command.run({ ...CMD, args: 'add 782' })
  await $.tool.call({ tool: 'Bash', command: 'gh pr create --fill' })
  await clock.advance(5_000)
  await settle()

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Button', key: 'pr-782' })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'pr-776' })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'pr-900' })).toBeUndefined()
  await ui.unmount()
})

test('the scope button switches between this session\'s PRs and all of them', async ($, on) => {
  const clock = wire(on, `${RUNS}\n${LATER(900)}`)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  await $.command.run({ ...CMD, args: 'add 782' })
  await clock.advance(5_000)
  await settle()

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Button', key: 'scope' }))?.text).toBe('all')
  expect(await ui.find({ type: 'Button', key: 'pr-900' })).toBeUndefined()
  await ui.press({ key: 'scope' })
  await settle()
  expect((await ui.find({ type: 'Button', key: 'scope' }))?.text).toBe('mine')
  expect(await ui.find({ type: 'Button', key: 'pr-900' })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'pr-782' })).toBeDefined()
  await ui.press({ key: 'scope' })
  await settle()
  expect(await ui.find({ type: 'Button', key: 'pr-900' })).toBeUndefined()
  await ui.unmount()
})

test('mine scope with only hidden PRs says how many all shows', async ($, on) => {
  const clock = wire(on, `${RUNS}\n${LATER(900)}`)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  await clock.advance(5_000)
  await settle()

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /No PRs from this session\. all shows 1/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /No PRs yet/ })).toBeUndefined()
  await ui.unmount()
})

test('clear merged shows with merged PRs drawn, removes them, and a later run does not bring them back', async ($, on) => {
  const w = world()
  w.states[771] = 'MERGED'
  // The ledger names 771 throughout.
  const clock = wire(on, `${RUNS}\n${LATER(771)}`, {}, '/work', w)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  await $.command.run({ ...CMD, args: 'add 772' })

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await settle()
  expect(await ui.find({ type: 'Button', key: 'clear' })).toBeUndefined()

  await $.command.run({ ...CMD, args: 'add 771' })
  await settle()
  expect((await ui.find({ type: 'Button', key: 'clear' }))?.text).toBe('clear merged (1)')
  await ui.press({ key: 'clear' })
  await settle()
  expect(await ui.find({ type: 'Button', key: 'pr-771' })).toBeUndefined()
  expect(await ui.find({ type: 'Button', key: 'pr-772' })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'clear' })).toBeUndefined()

  // Later rounds see the run again; the PR stays gone, in either scope.
  await clock.advance(10_000)
  await settle()
  await ui.press({ key: 'scope' })
  await settle()
  expect(await ui.find({ type: 'Button', key: 'pr-771' })).toBeUndefined()
  await ui.unmount()
})
