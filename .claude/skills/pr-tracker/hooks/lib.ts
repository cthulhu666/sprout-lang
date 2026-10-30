// Pure helpers: parsing, matching and the pane's text. No `$`, so tests import it directly.
import type {
  PrTrackerCi,
  PrTrackerIssue,
  PrTrackerPr,
  PrTrackerRun,
  PrTrackerStatus,
} from '../types'

const num = (s: string | undefined): number | null =>
  s !== undefined && /^\d+$/.test(s) ? Number(s) : null

/** Parses `review_ledger.sh runs` output: one completed run per line, eleven columns. */
export function parseRuns(text: string): PrTrackerRun[] {
  const out: PrTrackerRun[] = []
  for (const line of text.split('\n')) {
    const f = line.replace(/\r$/, '').split('\t')
    const id = f[4] ?? ''
    if (f.length !== 11 || id === '') continue
    out.push({
      id,
      when: f[0] ?? '',
      branch: f[2] ?? '',
      head: f[3] ?? '',
      found: num(f[5]),
      confirmed: num(f[6]),
      effort: f[7] ?? '-',
      cleanups: num(f[8]),
      cleanupsConfirmed: num(f[9]),
      target: f[10] ?? '-',
    })
  }
  return out
}

/** PR numbers named by `pr:<n>` targets. */
export function prsFromRuns(runs: readonly PrTrackerRun[]): number[] {
  const out: number[] = []
  for (const r of runs) {
    const m = /^pr:(\d+)$/.exec(r.target)
    if (m !== null && !out.includes(Number(m[1]))) out.push(Number(m[1]))
  }
  return out
}

/**
 * Runs of one PR, newest first: by `pr:<n>` target, or, for a run that names no other PR,
 * by the PR's ledger branch (`PrTrackerPr.ledgerBranch`).
 */
export function runsForPr(
  runs: readonly PrTrackerRun[],
  pr: { number: number; ledgerBranch: string },
): PrTrackerRun[] {
  return runs
    .filter(r => {
      if (r.target === `pr:${pr.number}`) return true
      if (/^pr:\d+$/.test(r.target)) return false
      return pr.ledgerBranch !== '' && r.branch === pr.ledgerBranch
    })
    .sort((a, b) => (a.when < b.when ? 1 : a.when > b.when ? -1 : 0))
}

const STATUSES: readonly PrTrackerStatus[] = ['open', 'fixed', 'skipped', 'wontfix', 'refuted']

/** Parses `review_ledger.sh issues` output; malformed lines are dropped. */
export function parseIssues(stdout: string): PrTrackerIssue[] {
  const out: PrTrackerIssue[] = []
  for (const line of stdout.split('\n')) {
    const f = line.replace(/\r$/, '').split('\t')
    if (f.length < 7) continue
    const ns = (f[0] ?? '')
      .split(',')
      .map(s => Number(s))
      .filter(n => Number.isInteger(n) && n > 0)
    const track = f[1] === 'cleanup' ? 'cleanup' : 'bug'
    const severity = f[2] === 'high' || f[2] === 'medium' ? f[2] : 'low'
    const status = STATUSES.find(s => s === f[3])
    if (ns.length === 0 || status === undefined) continue
    out.push({
      ns,
      track,
      severity,
      status,
      sha: f[4] === undefined || f[4] === '-' || f[4] === '' ? null : f[4],
      where: f[5] ?? '',
      summary: f.slice(6).join('\t'),
    })
  }
  return out
}

type Check = {
  __typename?: string
  status?: string
  conclusion?: string
  state?: string
}

/** Counts a statusCheckRollup. SKIPPED and NEUTRAL count as ok: a skipped job is no failure. */
export function ciSummary(rollup: readonly Check[] | null | undefined): PrTrackerCi {
  const ci: PrTrackerCi = { ok: 0, fail: 0, pending: 0, total: 0 }
  for (const c of rollup ?? []) {
    ci.total++
    if (c.__typename === 'StatusContext' || (c.status === undefined && c.state !== undefined)) {
      if (c.state === 'SUCCESS') ci.ok++
      else if (c.state === 'PENDING' || c.state === 'EXPECTED') ci.pending++
      else ci.fail++
    } else if (c.status !== 'COMPLETED') {
      ci.pending++
    } else if (c.conclusion === 'SUCCESS' || c.conclusion === 'SKIPPED' || c.conclusion === 'NEUTRAL') {
      ci.ok++
    } else {
      ci.fail++
    }
  }
  return ci
}

/** The branch name `review_ledger.sh open --pr` files a PR's runs under; '' if unknowable. */
function ledgerBranchOf(j: Record<string, unknown>, head: string): string {
  if (j['isCrossRepository'] !== true) return head
  const owner = (j['headRepositoryOwner'] as { login?: unknown } | null)?.login
  return typeof owner === 'string' && owner !== '' ? `${owner}:${head}` : ''
}

/** What `gh pr view` is asked for; `parsePrView` reads these. */
export const PR_FIELDS =
  'number,title,state,headRefName,headRefOid,isCrossRepository,headRepositoryOwner,statusCheckRollup,mergeStateStatus,baseRefName,isDraft'

/** A PR not fetched yet. */
export const blank = (n: number): PrTrackerPr => ({
  number: n,
  title: '',
  state: '',
  branch: '',
  ledgerBranch: '',
  oid: '',
  mergeState: '',
  base: '',
  draft: false,
  ci: ciSummary(null),
  error: null,
})

/** A PR kept in the data atom; fields added since it was stored come from `blank`. */
export const storedPr = (n: number, stored: PrTrackerPr | undefined): PrTrackerPr => ({
  ...blank(n),
  ...stored,
})

/** Parses `gh pr view --json` with `PR_FIELDS`. */
export function parsePrView(stdout: string): PrTrackerPr | null {
  try {
    const j = JSON.parse(stdout) as Record<string, unknown>
    if (typeof j['number'] !== 'number') return null
    const head = String(j['headRefName'] ?? '')
    return {
      number: j['number'],
      title: String(j['title'] ?? ''),
      state: String(j['state'] ?? ''),
      branch: head,
      ledgerBranch: ledgerBranchOf(j, head),
      oid: String(j['headRefOid'] ?? ''),
      mergeState: String(j['mergeStateStatus'] ?? ''),
      base: String(j['baseRefName'] ?? ''),
      draft: j['isDraft'] === true,
      ci: ciSummary(j['statusCheckRollup'] as Check[] | null),
      error: null,
    }
  } catch {
    return null
  }
}

/** The PR number a `gh pr create` printed, or null. Only a `gh pr create` command counts. */
export function createdPrNumber(command: string, output: string): number | null {
  if (!/\bgh\s+pr\s+create\b/.test(command)) return null
  const all = [...output.matchAll(/https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/(\d+)/g)]
  const last = all[all.length - 1]
  return last?.[1] === undefined ? null : Number(last[1])
}

/** Commands after which the PR data is worth refetching at once. */
export const touchesPrs = (command: string): boolean =>
  /\bgit\s+push\b/.test(command) || /\bgh\s+pr\b/.test(command)

export type PrsCommand =
  | { kind: 'open' }
  | { kind: 'add' | 'remove'; numbers: number[] }
  | { kind: 'view'; view: PrsView }
  | { kind: 'error'; text: string }

export type PrsView = 'pane' | 'band'

/** `/prs`, `/prs add 1 2`, `/prs remove 3`, `/prs pane`, `/prs band`. Numbers may carry a `#` or a comma. */
export function parsePrsArgs(args: string): PrsCommand {
  const words = args.trim().split(/[\s,]+/).filter(w => w !== '')
  if (words.length === 0) return { kind: 'open' }
  const verb = words[0]
  if ((verb === 'pane' || verb === 'band') && words.length === 1) return { kind: 'view', view: verb }
  if (verb !== 'add' && verb !== 'remove') {
    return { kind: 'error', text: 'usage: /prs [add <n>... | remove <n>... | pane | band]' }
  }
  const numbers: number[] = []
  for (const w of words.slice(1)) {
    const n = /^#?(\d+)$/.exec(w)
    if (n === null) return { kind: 'error', text: `not a PR number: ${w}` }
    numbers.push(Number(n[1]))
  }
  if (numbers.length === 0) return { kind: 'error', text: `usage: /prs ${verb} <n>...` }
  return { kind: verb, numbers }
}

// --- text for the pane ---

/** Terminal cells of a string: emoji take two. */
export function cells(s: string): number {
  let w = 0
  for (const ch of s) w += (ch.codePointAt(0) ?? 0) >= 0x1f300 ? 2 : 1
  return w
}

/** Cuts to at most `width` cells, ending in an ellipsis when it cut. */
export function fit(s: string, width: number): string {
  if (cells(s) <= width) return s
  if (width <= 0) return ''
  let out = ''
  let w = 0
  for (const ch of s) {
    const cw = cells(ch)
    if (w + cw > width - 1) break
    out += ch
    w += cw
  }
  return out + '…'
}

export type Seg = {
  text: string
  color?: string
  dim?: boolean
  bold?: boolean
}

/** Cuts a row of segments to `width` cells: the segment that runs over is trimmed, the rest dropped. */
export function fitSegs(segs: readonly Seg[], width: number): Seg[] {
  const out: Seg[] = []
  let left = width
  for (const s of segs) {
    if (left <= 0) break
    const w = cells(s.text)
    if (w <= left) {
      out.push(s)
      left -= w
    } else {
      out.push({ ...s, text: fit(s.text, left) })
      break
    }
  }
  return out
}

export const pad = (s: string, width: number): string =>
  s + ' '.repeat(Math.max(0, width - cells(s)))

const STATE_COLOR: Record<string, string> = { OPEN: 'success', MERGED: 'merged', CLOSED: 'error' }

const MERGE_SEG: Record<string, Seg> = {
  CLEAN: { text: 'mergeable', color: 'success' },
  HAS_HOOKS: { text: 'mergeable', color: 'success' },
  BEHIND: { text: 'behind', color: 'warning' },
  BLOCKED: { text: 'blocked', color: 'warning' },
  UNSTABLE: { text: 'unstable', color: 'warning' },
  DIRTY: { text: 'conflicts', color: 'error' },
  DRAFT: { text: 'draft', dim: true },
}

/** Mergeability of an open PR, from GitHub's MergeStateStatus; null for a merged or closed one. */
export function mergeSeg(pr: PrTrackerPr): Seg | null {
  if (pr.state !== 'OPEN') return null
  if (pr.draft) return { text: 'draft', dim: true }
  return MERGE_SEG[pr.mergeState] ?? { text: 'merge ?', dim: true }
}

/** The PR the pane expands: the chosen one, or by default the newest open one. */
export function resolveExpanded(
  selected: number | 'auto' | 'none',
  prs: readonly number[],
  data: Readonly<Record<string, PrTrackerPr>>,
): number | null {
  if (selected === 'none') return null
  if (selected !== 'auto') return prs.includes(selected) ? selected : null
  const open = [...prs].filter(n => data[String(n)]?.state === 'OPEN').sort((a, b) => b - a)
  return open[0] ?? null
}

export type Layout = {
  /** The PRs drawn, in list order. */
  shown: number[]
  /** PRs left out; one `… k more PRs` line stands for them. */
  hidden: number
  /** The PRs that draw a totals line. */
  totals: Set<number>
  /** Rows left for the expanded PR's issues. */
  room: number
}

/**
 * Fits the list into `budget` rows (the footer included), whenever that is three or more.
 * First every PR draws header and totals; then only the expanded one keeps its totals; then
 * the newest PRs that fit are drawn, the expanded one always, and a line counts the rest.
 */
export function layoutRows(budget: number, sorted: readonly number[], expanded: number | null): Layout {
  const n = sorted.length
  const hasExpanded = expanded !== null && sorted.includes(expanded)
  if (1 + 2 * n <= budget) {
    return { shown: [...sorted], hidden: 0, totals: new Set(sorted), room: budget - 1 - 2 * n }
  }
  const headersOnly = 1 + n + (hasExpanded ? 1 : 0)
  if (headersOnly <= budget) {
    return {
      shown: [...sorted],
      hidden: 0,
      totals: new Set(hasExpanded ? [expanded] : []),
      room: budget - headersOnly,
    }
  }
  let left = budget - 2 // the footer and the "more" line
  const keep = new Set<number>()
  const totals = new Set<number>()
  if (hasExpanded) {
    keep.add(expanded)
    if (left >= 2) totals.add(expanded)
    left -= totals.size === 1 ? 2 : 1
  }
  for (const pr of sorted) {
    if (left <= 0 && keep.size > 0) break
    if (keep.has(pr)) continue
    keep.add(pr)
    left--
  }
  const shown = sorted.filter(pr => keep.has(pr))
  const hidden = n - shown.length
  return { shown, hidden, totals, room: Math.max(0, hidden === 0 ? left + 1 : left) }
}

/** `#n branch [→ base]  STATE  [merge state]  CI ok/total ✗fail …pending  k reviews (effort)` */
export function headerSegs(
  n: number,
  pr: PrTrackerPr | undefined,
  runs: readonly PrTrackerRun[],
  isExpanded: boolean,
  columns: number,
): Seg[] {
  const marker = isExpanded ? '▾ ' : '▸ '
  if (pr === undefined) {
    return [{ text: marker }, { text: `#${n}`, bold: true }, { text: '  …', dim: true }]
  }
  const ci = pr.ci
  const latest = runs[0]
  const effort = latest !== undefined && latest.effort !== '-' && latest.effort !== '' ? ` (${latest.effort})` : ''
  const merge = mergeSeg(pr)
  const baseFull = pr.base !== '' && pr.base !== 'master' ? ` → ${pr.base}` : ''
  const tail: Seg[] = [
    { text: '  ' },
    { text: pr.state === '' ? '?' : pr.state, color: STATE_COLOR[pr.state] },
    ...(merge === null ? [] : [{ text: '  ' }, merge]),
    { text: `  CI ${ci.ok}/${ci.total}` },
    ...(ci.fail > 0 ? [{ text: ` ✗${ci.fail}`, color: 'error' }] : []),
    ...(ci.pending > 0 ? [{ text: ` …${ci.pending}`, color: 'warning' }] : []),
    { text: `  ${runs.length} ${runs.length === 1 ? 'review' : 'reviews'}${effort}` },
    ...(pr.error !== null ? [{ text: `  ⚠ ${fit(pr.error, 24)}`, color: 'error' }] : []),
  ]
  // Branch and base share the room the tail leaves: the base takes at most half, or what the
  // branch does not need. A part with no room for a few cells is dropped.
  const fixed = cells(marker) + cells(`#${n}`) + tail.reduce((w, s) => w + cells(s.text), 0)
  const avail = Math.max(0, columns - fixed)
  const branchFull = pr.branch === '' ? 0 : cells(pr.branch) + 1
  const baseW = cells(baseFull)
  let baseRoom = Math.min(baseW, Math.max(Math.floor(avail / 2), avail - branchFull))
  const branchRoom = avail - baseRoom
  const branch: Seg[] = branchRoom > 1 && pr.branch !== '' ? [{ text: ' ' + fit(pr.branch, branchRoom - 1) }] : []
  const base: Seg[] = baseRoom > 3 ? [{ text: fit(baseFull, baseRoom), dim: true }] : []
  return fitSegs([{ text: marker }, { text: `#${n}`, bold: true }, ...branch, ...base, ...tail], columns)
}

const ORDER: readonly PrTrackerStatus[] = ['fixed', 'skipped', 'wontfix', 'refuted', 'open']

/** All issues of a PR's runs, counted by status. */
export function issueCounts(issues: readonly PrTrackerIssue[]): Record<PrTrackerStatus, number> {
  const c: Record<PrTrackerStatus, number> = { open: 0, fixed: 0, skipped: 0, wontfix: 0, refuted: 0 }
  for (const i of issues) c[i.status]++
  return c
}

/** `9 issues: 7 fixed · 1 skipped · 1 open`; `noData` counts the runs with no items file. */
export function totalsSegs(
  issues: readonly PrTrackerIssue[],
  runCount: number,
  noData: number,
  columns: number,
): Seg[] {
  if (runCount === 0) return [{ text: '  no reviews yet', dim: true }]
  if (issues.length === 0) return [{ text: noData > 0 ? '  no issue data' : '  0 issues', dim: true }]
  const c = issueCounts(issues)
  const segs: Seg[] = [{ text: `  ${issues.length} ${issues.length === 1 ? 'issue' : 'issues'}: ` }]
  const present = ORDER.filter(s => c[s] > 0)
  present.forEach((s, k) => {
    if (k > 0) segs.push({ text: ' · ' })
    segs.push({ text: `${c[s]} ${s}`, color: s === 'open' ? 'warning' : undefined })
  })
  return fitSegs(segs, columns)
}

const SEV_RANK = { high: 0, medium: 1, low: 2 } as const
const CHIP = { high: '🔴', medium: '🟠', low: '🟡' } as const
export const isClosed = (s: PrTrackerStatus): boolean => s !== 'open'

/** Open first, then by severity, then by issue number. Stable across runs (newest run first). */
export function sortIssues(issues: readonly PrTrackerIssue[]): PrTrackerIssue[] {
  return issues
    .map((i, k) => ({ i, k }))
    .sort(
      (a, b) =>
        Number(isClosed(a.i.status)) - Number(isClosed(b.i.status)) ||
        SEV_RANK[a.i.severity] - SEV_RANK[b.i.severity] ||
        (a.i.ns[0] ?? 0) - (b.i.ns[0] ?? 0) ||
        a.k - b.k,
    )
    .map(x => x.i)
}

/** One issue row: `<ns>  <chip>  <summary>  <status> [<sha>]`, exactly `columns` cells at most. */
export function issueSegs(
  issue: PrTrackerIssue,
  nsWidth: number,
  rightWidth: number,
  columns: number,
): Seg[] {
  const dim = isClosed(issue.status)
  const right = pad(issue.status + (issue.sha === null ? '' : ' ' + issue.sha.slice(0, 7)), rightWidth)
  const lead = '  ' + pad(issue.ns.join(','), nsWidth) + ' '
  const chip = CHIP[issue.severity]
  const budget = Math.max(8, columns - cells(lead) - cells(chip) - 1 - 1 - rightWidth)
  return fitSegs(
    [
      { text: lead, dim },
      { text: chip + ' ' },
      { text: pad(fit(issue.summary, budget), budget), dim },
      { text: ' ' + right, color: dim ? undefined : 'warning', dim },
    ],
    columns,
  )
}

/** Widths the issue rows of one list share, so the columns line up. */
export function issueWidths(issues: readonly PrTrackerIssue[]): { ns: number; right: number } {
  let ns = 1
  let right = 1
  for (const i of issues) {
    ns = Math.max(ns, i.ns.join(',').length)
    right = Math.max(right, i.status.length + (i.sha === null ? 0 : 8))
  }
  return { ns, right }
}

export function clock(ms: number): string {
  const d = new Date(ms)
  const two = (n: number): string => String(n).padStart(2, '0')
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`
}
