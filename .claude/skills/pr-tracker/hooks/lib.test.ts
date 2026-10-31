import { expect, test } from 'claude-code/testing'

import {
  blank,
  cells,
  ciSummary,
  createdPrNumber,
  fit,
  footerLines,
  fitSegs,
  headerSegs,
  issueSegs,
  mergeSeg,
  layoutRows,
  emptyText,
  mergedIn,
  parseIssues,
  parsePrView,
  parsePrsArgs,
  parseRuns,
  prsFromRuns,
  resolveExpanded,
  runsForPr,
  scopePrs,
  sortIssues,
  storedPr,
  totalsSegs,
  touchesPrs,
  withAll,
  without,
} from './lib'

const T = '\t'
const row = (...f: string[]) => f.join(T)

// `review_ledger.sh runs` lines: eleven columns, an older row padded with '-'.
const R7 = row('2026-10-10T15:33:10Z', 'done', 'old-branch', '2da6baad', 'r7', '2', '0', '-', '-', '-', '-')
const R10 = row('2026-10-30T09:35:49Z', 'done', 'feat', '608a8e9f', 'r10', '3', '3', 'medium', '6', '6', '-')
const R11 = row('2026-10-30T10:30:59Z', 'done', 'review-ledger-targets', 'e8c8f355', 'r11', '4', '4', 'high', '6', '5', 'pr:782')

test('runs output: 11-column lines parse; "-" counts are null', () => {
  const runs = parseRuns([R7, R10, R11, ''].join('\n'))
  expect(runs.map(r => r.id)).toEqual(['r7', 'r10', 'r11'])
  expect(runs[0]).toEqual({
    id: 'r7', when: '2026-10-10T15:33:10Z', branch: 'old-branch', head: '2da6baad',
    found: 2, confirmed: 0, effort: '-', cleanups: null, cleanupsConfirmed: null, target: '-',
  })
  expect(runs[1]?.effort).toBe('medium')
  expect(runs[1]?.cleanups).toBe(6)
  expect(runs[1]?.target).toBe('-')
  expect(runs[2]?.target).toBe('pr:782')
  expect(runs[2]?.cleanupsConfirmed).toBe(5)
})

test('runs output: a line that is not 11 columns, or has no id, is dropped', () => {
  const noId = row('2026-10-30T10:30:59Z', 'done', 'b', 'abc', '', '1', '1', '-', '-', '-', '-')
  const runs = parseRuns(
    [row('a', 'done', 'b'), R11 + T + 'extra', row('2026-10-10T15:33:10Z', 'done', 'x', 'y', 'r1', '2', '0'), noId, R11].join('\n'),
  )
  expect(runs.map(r => r.id)).toEqual(['r11'])
})

const run = (id: string, when: string, branch: string, target: string) =>
  parseRuns(row(when, 'done', branch, 'abc', id, '1', '1', '-', '-', '-', target))[0]!

test('a run belongs to a PR by its pr: target, or by the ledger branch when it names no other PR', () => {
  const runs = parseRuns([R7, R10, R11].join('\n'))
  const byTarget = runsForPr(runs, { number: 782, ledgerBranch: '' })
  expect(byTarget.map(r => r.id)).toEqual(['r11'])
  const byBranch = runsForPr(runs, { number: 779, ledgerBranch: 'feat' })
  expect(byBranch.map(r => r.id)).toEqual(['r10'])
  const both = runsForPr(runs, { number: 782, ledgerBranch: 'old-branch' })
  expect(both.map(r => r.id)).toEqual(['r11', 'r7'])
  expect(runsForPr(runs, { number: 1, ledgerBranch: 'none' })).toEqual([])
  expect(prsFromRuns(runs)).toEqual([782])
})

test('a run that targets another PR is not matched by branch name', () => {
  const runs = [
    run('a', '2026-10-01T00:00:00Z', 'feat', 'pr:10'),
    run('b', '2026-10-02T00:00:00Z', 'feat', '-'),
    run('c', '2026-10-03T00:00:00Z', 'feat', 'branch:feat'),
    run('d', '2026-10-04T00:00:00Z', 'feat', 'pr:11'),
  ]
  expect(runsForPr(runs, { number: 11, ledgerBranch: 'feat' }).map(r => r.id)).toEqual(['d', 'c', 'b'])
  expect(runsForPr(runs, { number: 10, ledgerBranch: 'feat' }).map(r => r.id)).toEqual(['c', 'b', 'a'])
})

test('a fork PR matches its owner:branch runs, not a local branch of the same name', () => {
  const runs = [
    run('local', '2026-10-01T00:00:00Z', 'fix', '-'),
    run('fork', '2026-10-02T00:00:00Z', 'alice:fix', '-'),
  ]
  expect(runsForPr(runs, { number: 5, ledgerBranch: 'alice:fix' }).map(r => r.id)).toEqual(['fork'])
  expect(runsForPr(runs, { number: 6, ledgerBranch: 'fix' }).map(r => r.id)).toEqual(['local'])
})

const ISSUES = [
  row('1', 'bug', 'low', 'fixed', '7d6348d9', 'scripts/review_ledger.sh:268', 'items accepts a bug'),
  row('5,6', 'cleanup', 'medium', 'open', '-', 'stdlib/json.sprout:40', 'term_interned_word is generic'),
  row('10', 'cleanup', 'low', 'skipped', '-', 'scripts/review_ledger.sh:312', 'differs on purpose'),
  row('11', 'bug', 'high', 'open', '-', 'a.c:1', 'crash'),
  row('12', 'bug', 'high', 'refuted', '-', 'b.c:2', 'not real'),
  'garbage line',
  row('x', 'bug', 'low', 'open', '-', 'f:1', 'bad ns'),
  row('13', 'bug', 'low', 'weird', '-', 'f:1', 'bad status'),
].join('\n')

test('issues output parses; ns split on commas, - sha is null, bad lines drop', () => {
  const issues = parseIssues(ISSUES)
  expect(issues.map(i => i.ns)).toEqual([[1], [5, 6], [10], [11], [12]])
  expect(issues[0]).toEqual({
    ns: [1], track: 'bug', severity: 'low', status: 'fixed', sha: '7d6348d9',
    where: 'scripts/review_ledger.sh:268', summary: 'items accepts a bug',
  })
  expect(issues[1]?.sha).toBe(null)
  expect(issues[1]?.track).toBe('cleanup')
  expect(parseIssues('')).toEqual([])
})

test('issues sort open first, then by severity', () => {
  const sorted = sortIssues(parseIssues(ISSUES)).map(i => i.ns[0])
  expect(sorted).toEqual([11, 5, 12, 1, 10])
})

test('issue totals line', () => {
  const issues = parseIssues(ISSUES)
  const text = totalsSegs(issues, 1, 0, 80).map(s => s.text).join('')
  expect(text).toBe('  5 issues: 1 fixed · 1 skipped · 1 refuted · 2 open')
  expect(totalsSegs([], 0, 0, 80)[0]?.text).toBe('  no reviews yet')
  expect(totalsSegs([], 2, 2, 80)[0]?.text).toBe('  no issue data')
  expect(totalsSegs([], 2, 1, 80)[0]?.text).toBe('  no issue data')
  expect(totalsSegs([], 2, 0, 80)[0]?.text).toBe('  0 issues')
})

test('CI summary counts success, failure and pending', () => {
  const rollup = [
    { __typename: 'CheckRun', status: 'COMPLETED', conclusion: 'SUCCESS' },
    { __typename: 'CheckRun', status: 'COMPLETED', conclusion: 'SKIPPED' },
    { __typename: 'CheckRun', status: 'COMPLETED', conclusion: 'FAILURE' },
    { __typename: 'CheckRun', status: 'COMPLETED', conclusion: 'CANCELLED' },
    { __typename: 'CheckRun', status: 'IN_PROGRESS', conclusion: '' },
    { __typename: 'CheckRun', status: 'QUEUED', conclusion: '' },
    { __typename: 'StatusContext', state: 'SUCCESS' },
    { __typename: 'StatusContext', state: 'PENDING' },
    { __typename: 'StatusContext', state: 'ERROR' },
  ]
  expect(ciSummary(rollup)).toEqual({ ok: 3, fail: 3, pending: 3, total: 9 })
  expect(ciSummary([])).toEqual({ ok: 0, fail: 0, pending: 0, total: 0 })
  expect(ciSummary(null)).toEqual({ ok: 0, fail: 0, pending: 0, total: 0 })
})

test('gh pr view output parses into a PR', () => {
  const json = JSON.stringify({
    number: 782, title: 'ledger', state: 'OPEN', headRefName: 'review-ledger-targets', headRefOid: 'abc',
    isCrossRepository: false, headRepositoryOwner: { login: 'cthulhu666' },
    statusCheckRollup: [{ __typename: 'CheckRun', status: 'COMPLETED', conclusion: 'SUCCESS' }],
  })
  expect(parsePrView(json)).toEqual({
    number: 782, title: 'ledger', state: 'OPEN', branch: 'review-ledger-targets',
    ledgerBranch: 'review-ledger-targets', oid: 'abc',
    mergeState: '', base: '', draft: false,
    ci: { ok: 1, fail: 0, pending: 0, total: 1 }, error: null,
  })
  const merge = JSON.stringify({
    number: 6, mergeStateStatus: 'BEHIND', baseRefName: 'stack-base', isDraft: true,
  })
  expect(parsePrView(merge)).toMatchObject({ mergeState: 'BEHIND', base: 'stack-base', draft: true })
  // The ledger files a fork PR's run as <owner>:<branch>.
  const fork = JSON.stringify({
    number: 5, headRefName: 'fix', isCrossRepository: true, headRepositoryOwner: { login: 'alice' },
  })
  expect(parsePrView(fork)?.branch).toBe('fix')
  expect(parsePrView(fork)?.ledgerBranch).toBe('alice:fix')
  const noOwner = JSON.stringify({ number: 5, headRefName: 'fix', isCrossRepository: true, headRepositoryOwner: null })
  expect(parsePrView(noOwner)?.ledgerBranch).toBe('')
  expect(parsePrView('not json')).toBe(null)
  expect(parsePrView('{"title":"x"}')).toBe(null)
})

test('a created PR is found in gh pr create output', () => {
  const url = 'https://github.com/cthulhu666/sprout-lang/pull/801'
  expect(createdPrNumber('gh pr create --base master --fill', `Creating...\n${url}\n`)).toBe(801)
  expect(createdPrNumber('cd x && gh pr create --fill', url)).toBe(801)
  expect(createdPrNumber('gh pr view 5', url)).toBe(null)
  expect(createdPrNumber('gh pr create --fill', 'no url here')).toBe(null)
  expect(createdPrNumber('gh pr create', 'a https://github.com/o/r/pull/7 and https://github.com/o/r/pull/9')).toBe(9)
  expect(touchesPrs('git push -u origin x')).toBe(true)
  expect(touchesPrs('gh pr merge 3')).toBe(true)
  expect(touchesPrs('git status')).toBe(false)
})

test('/prs arguments', () => {
  expect(parsePrsArgs('')).toEqual({ kind: 'open' })
  expect(parsePrsArgs('add 776 779, #781 782')).toEqual({ kind: 'add', numbers: [776, 779, 781, 782] })
  expect(parsePrsArgs(' remove 4 ')).toEqual({ kind: 'remove', numbers: [4] })
  expect(parsePrsArgs('band')).toEqual({ kind: 'view', view: 'band' })
  expect(parsePrsArgs(' pane ')).toEqual({ kind: 'view', view: 'pane' })
  expect(parsePrsArgs('band 3').kind).toBe('error')
  expect(parsePrsArgs('add').kind).toBe('error')
  expect(parsePrsArgs('add x').kind).toBe('error')
  expect(parsePrsArgs('drop 1').kind).toBe('error')
})

test('expansion defaults to the newest open PR', () => {
  const pr = (number: number, state: string) => ({ ...blank(number), state })
  const data = { '1': pr(1, 'OPEN'), '2': pr(2, 'MERGED'), '3': pr(3, 'OPEN') }
  expect(resolveExpanded('auto', [1, 2, 3], data)).toBe(3)
  expect(resolveExpanded('auto', [2], data)).toBe(null)
  expect(resolveExpanded(2, [1, 2, 3], data)).toBe(2)
  // A selection that is not in the list (hidden by scope) falls back to the auto rule.
  expect(resolveExpanded(9, [1, 2, 3], data)).toBe(3)
  expect(resolveExpanded('none', [1, 2, 3], data)).toBe(null)
})

// Rows drawn by a layout: the footer, the "more" line, a header and a totals line per PR.
const rowsOf = (l: ReturnType<typeof layoutRows>) =>
  1 + (l.hidden > 0 ? 1 : 0) + l.shown.length + l.totals.size

test('layout: everything fits, then collapsed totals go, then PRs are left out', () => {
  const all = layoutRows(40, [3, 2, 1], 3)
  expect(all).toEqual({ shown: [3, 2, 1], hidden: 0, totals: new Set([3, 2, 1]), room: 33 })

  const noTotals = layoutRows(5, [3, 2, 1], 3)
  expect(noTotals).toEqual({ shown: [3, 2, 1], hidden: 0, totals: new Set([3]), room: 0 })

  const subset = layoutRows(7, [9, 8, 7, 6, 5, 4, 3, 2, 1], 5)
  expect(subset).toEqual({ shown: [9, 8, 7, 5], hidden: 5, totals: new Set([5]), room: 0 })

  expect(layoutRows(4, [3, 2, 1], 3)).toEqual({ shown: [3], hidden: 2, totals: new Set([3]), room: 0 })
  expect(layoutRows(3, [3, 2, 1], 3)).toEqual({ shown: [3], hidden: 2, totals: new Set(), room: 0 })
  expect(layoutRows(3, [3, 2, 1], null)).toEqual({ shown: [3], hidden: 2, totals: new Set(), room: 0 })
  expect(layoutRows(10, [], null)).toEqual({ shown: [], hidden: 0, totals: new Set(), room: 9 })
})

test('layout: at least three rows of budget are never overrun', () => {
  const prs = [9, 8, 7, 6, 5, 4, 3, 2, 1]
  for (let budget = 3; budget <= 25; budget++) {
    for (const expanded of [null, 9, 5, 1]) {
      const l = layoutRows(budget, prs, expanded)
      expect(rowsOf(l) + l.room).toBeLessThanOrEqual(budget)
      if (expanded !== null) expect(l.shown).toContain(expanded)
    }
  }
})

test('rows fit the width: fit, fitSegs, header and issue rows', () => {
  expect(fit('abcdef', 6)).toBe('abcdef')
  expect(fit('abcdef', 4)).toBe('abc…')
  expect(cells('🔴 a')).toBe(4)
  const segs = fitSegs([{ text: 'aaaa' }, { text: 'bbbb', dim: true }, { text: 'cc' }], 6)
  expect(segs.map(s => s.text)).toEqual(['aaaa', 'b…'])

  const pr = {
    ...blank(782), state: 'OPEN', branch: 'a-very-long-branch-name-indeed',
    ledgerBranch: 'a-very-long-branch-name-indeed', mergeState: 'CLEAN',
    ci: { ok: 5, fail: 2, pending: 3, total: 10 },
  }
  const [run] = parseRuns(R11)
  const wide = headerSegs(782, pr, run === undefined ? [] : [run], true, 100).map(s => s.text).join('')
  expect(wide).toBe('▾ #782 a-very-long-branch-name-indeed  OPEN  mergeable  CI 5/10 ✗2 …3  1 review (high)')
  for (const w of [30, 45, 60]) {
    expect(cells(headerSegs(782, pr, [], false, w).map(s => s.text).join(''))).toBeLessThanOrEqual(w)
  }
  const stacked = { ...pr, base: 'parent-branch' }
  const stackedWide = headerSegs(782, stacked, [], false, 120).map(s => s.text).join('')
  expect(stackedWide).toContain('a-very-long-branch-name-indeed → parent-branch  OPEN  mergeable')
  for (const w of [30, 45, 60, 80]) {
    const line = headerSegs(782, stacked, [], false, w).map(s => s.text).join('')
    expect(cells(line)).toBeLessThanOrEqual(w)
    if (w >= 60) expect(line).toContain('→ p')
  }
  const longBase = { ...pr, base: 'some-long-parent-feature-branch-name' }
  for (const w of [45, 60, 80]) {
    const line = headerSegs(782, longBase, [], false, w).map(s => s.text).join('')
    expect(cells(line)).toBeLessThanOrEqual(w)
    expect(line).toContain('OPEN')
    expect(line).toContain('mergeable')
    expect(line).toContain('CI ')
  }
  const issue = parseIssues(ISSUES)[1]!
  for (const w of [30, 50, 80]) {
    const line = issueSegs(issue, 3, 6, w).map(s => s.text).join('')
    expect(cells(line)).toBeLessThanOrEqual(w)
  }
  expect(issueSegs(issue, 3, 6, 80).map(s => s.text).join('')).toContain('🟠 term_interned_word is generic')
})

test('a stored PR from before the merge fields existed gets them from blank', () => {
  const old = { number: 9, title: 't', state: 'OPEN', branch: 'b', ledgerBranch: 'b', oid: 'x', ci: ciSummary(null), error: null }
  const pr = storedPr(9, old as never)
  expect(pr).toMatchObject({ mergeState: '', base: '', draft: false, branch: 'b' })
  const line = headerSegs(9, pr, [], false, 100).map(s => s.text).join('')
  expect(line).not.toContain('undefined')
  expect(line).not.toContain('→')
  expect(storedPr(9, undefined)).toEqual(blank(9))
})

test('merge segment: label and color per state, draft first, open PRs only', () => {
  const base = { ...blank(1), state: 'OPEN', branch: 'b', ledgerBranch: 'b' }
  const seg = (mergeState: string, extra: object = {}) => mergeSeg({ ...base, mergeState, ...extra })
  expect(seg('CLEAN')).toEqual({ text: 'mergeable', color: 'success' })
  expect(seg('HAS_HOOKS')).toEqual({ text: 'mergeable', color: 'success' })
  expect(seg('BEHIND')).toEqual({ text: 'behind', color: 'warning' })
  expect(seg('BLOCKED')).toEqual({ text: 'blocked', color: 'warning' })
  expect(seg('UNSTABLE')).toEqual({ text: 'unstable', color: 'warning' })
  expect(seg('DIRTY')).toEqual({ text: 'conflicts', color: 'error' })
  expect(seg('DRAFT')).toEqual({ text: 'draft', dim: true })
  expect(seg('UNKNOWN')).toEqual({ text: 'merge ?', dim: true })
  expect(seg('')).toEqual({ text: 'merge ?', dim: true })
  expect(seg('SOMETHING_NEW')).toEqual({ text: 'merge ?', dim: true })
  expect(seg('DIRTY', { draft: true })).toEqual({ text: 'draft', dim: true })
  expect(seg('CLEAN', { state: 'MERGED' })).toBe(null)
  expect(seg('CLEAN', { state: 'CLOSED' })).toBe(null)

  const line = (extra: object) =>
    headerSegs(1, { ...base, ...extra }, [], false, 100).map(s => s.text).join('')
  expect(line({ mergeState: 'DIRTY' })).toContain('OPEN  conflicts  CI')
  expect(line({ state: 'MERGED', mergeState: 'DIRTY' })).not.toContain('conflicts')
  expect(line({ base: 'master' })).not.toContain('→')
  expect(line({ base: 'topic' })).toContain('b → topic  OPEN')
})

test('scopePrs: mine keeps only this session\'s PRs, all keeps everything', () => {
  expect(scopePrs([3, 1, 2], [2, 9], 'mine')).toEqual([2])
  expect(scopePrs([3, 1, 2], [2, 9], 'all')).toEqual([3, 1, 2])
  expect(scopePrs([], [], 'mine')).toEqual([])
})

test('emptyText: hint when nothing is tracked, count of the hidden ones in mine scope', () => {
  expect(emptyText(0, 0, 'mine')).toBe('No PRs yet. /prs add <n>')
  expect(emptyText(0, 3, 'mine')).toBe('No PRs from this session. all shows 3')
  expect(emptyText(0, 0, 'all')).toBe('No PRs yet. /prs add <n>')
  expect(emptyText(2, 3, 'mine')).toBeNull()
})

test('mergedIn: the shown PRs whose stored state is MERGED', () => {
  const data = {
    '1': { ...blank(1), state: 'MERGED' },
    '2': { ...blank(2), state: 'OPEN' },
    '3': { ...blank(3), state: 'MERGED' },
  }
  expect(mergedIn([1, 2, 3, 4], data)).toEqual([1, 3])
  expect(mergedIn([2, 4], data)).toEqual([])
})

test('footerLines: packs items greedily into lines no wider than the columns', () => {
  expect(footerLines(['aa', 'bb', 'cc'], 20)).toEqual([['aa', 'bb', 'cc']])
  // 'aa  bb' is 6 cells; adding '  cc' makes 10.
  expect(footerLines(['aa', 'bb', 'cc'], 8)).toEqual([['aa', 'bb'], ['cc']])
  expect(footerLines(['aa', 'bb', 'cc'], 8, 3)).toEqual([['aa', 'bb'], ['cc']])
  expect(footerLines(['aa', 'bb', 'cc'], 6, 3)).toEqual([['aa'], ['bb'], ['cc']])
  expect(footerLines([], 10)).toEqual([])
})

test('footerLines: an item wider than the columns gets its own line', () => {
  expect(footerLines(['a', 'wider than ten', 'b'], 10)).toEqual([['a'], ['wider than ten'], ['b']])
})

test('withAll appends the missing numbers in order; without filters them out', () => {
  expect(withAll([1, 2], [2, 3, 4, 3])).toEqual([1, 2, 3, 4])
  expect(withAll([], [5])).toEqual([5])
  expect(without([1, 2, 3, 4], [2, 4, 9])).toEqual([1, 3])
  expect(without([1], [])).toEqual([1])
})
