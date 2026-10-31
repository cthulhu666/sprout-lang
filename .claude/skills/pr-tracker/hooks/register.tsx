import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, ResolveInput, Timer } from 'claude-code'

import type { PrTrackerIssue, PrTrackerPr } from '../types'
import {
  PR_FIELDS,
  clock,
  createdPrNumber,
  emptyText,
  footerLines,
  headerSegs,
  issueSegs,
  issueWidths,
  layoutRows,
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
import type { PrsScope, PrsView, Seg } from './lib'

type Dollar = EngineInterface

const PANE = 'prs'
const VIEW_KEY = 'view'
const LONG_TIMEOUT_MS = 30_000
const SHORT_TIMEOUT_MS = 10_000
const prsA = atom({ plugin: 'pr-tracker', key: 'prs' } as const, [])
const dataA = atom({ plugin: 'pr-tracker', key: 'data' } as const, {})
const runsA = atom({ plugin: 'pr-tracker', key: 'runs' } as const, [])
const issuesA = atom({ plugin: 'pr-tracker', key: 'issues' } as const, {})
const updatedA = atom({ plugin: 'pr-tracker', key: 'updatedAt' } as const, 0)
const selectedA = atom({ plugin: 'pr-tracker', key: 'selected' } as const, 'auto')
const dismissedA = atom({ plugin: 'pr-tracker', key: 'dismissed' } as const, [])
const sinceA = atom({ plugin: 'pr-tracker', key: 'since' } as const, 0)
const viewA = atom({ plugin: 'pr-tracker', key: 'view' } as const, 'pane')
const mineA = atom({ plugin: 'pr-tracker', key: 'mine' } as const, [])
const scopeA = atom({ plugin: 'pr-tracker', key: 'scope' } as const, 'mine')

// Module variables are lost on a hot reload, along with the old timers, so
// every hook that may run first calls `ensureTimers`, which starts them again.
let timers: Timer[] | null = null
let isRefreshing = false
let isDirty = false
let ledgerFor: { cwd: string; ledger: Ledger | null } | null = null
// Last seen mtimes of a run's issue files, to skip spawning `issues` when nothing changed.
const seen = new Map<string, string>()

async function fetchPr(
  $: Dollar,
  cwd: string,
  n: number,
): Promise<{ n: number; pr: PrTrackerPr | null; error: string }> {
  try {
    const r = await $.process.run(
      ['gh', 'pr', 'view', String(n), '--json', PR_FIELDS],
      { cwd, timeoutMs: LONG_TIMEOUT_MS },
    )
    if (r.exitCode !== 0) {
      return { n, pr: null, error: (r.stderr.trim().split('\n')[0] ?? 'gh failed') || 'gh failed' }
    }
    const pr = parsePrView(r.stdout)
    return { n, pr, error: pr === null ? 'unreadable gh output' : '' }
  } catch (err) {
    return { n, pr: null, error: err instanceof Error ? err.message : 'gh failed' }
  }
}

/** Refetches every tracked PR, in parallel. A call during a fetch queues one more round. */
async function refreshPrs($: Dollar): Promise<void> {
  if (isRefreshing) {
    isDirty = true
    return
  }
  isRefreshing = true
  try {
    do {
      isDirty = false
      const cwd = await $.session.cwd()
      const prs = await read($, prsA)
      const results = await Promise.all(prs.map(n => fetchPr($, cwd, n)))
      await update($, dataA, old => {
        const next: Record<string, PrTrackerPr> = {}
        for (const r of results) {
          const prev = old[String(r.n)]
          next[String(r.n)] = r.pr ?? { ...storedPr(r.n, prev), error: r.error }
        }
        return next
      })
      await update($, updatedA, () => Date.now())
    } while (isDirty)
  } finally {
    isRefreshing = false
  }
}

/** The repo root, so the script resolves from a subdirectory, and the dirs it reads. */
type Ledger = { root: string; dirs: string[] }

/** Runs the ledger script from the repo root; null when it fails or times out. */
async function ledgerScript($: Dollar, root: string, args: string[], timeoutMs: number) {
  const r = await $.process
    .run(['bash', `${root}/scripts/review_ledger.sh`, ...args], { cwd: root, timeoutMs })
    .catch(() => null)
  return r !== null && r.exitCode === 0 ? r.stdout : null
}

/** The ledger; null outside a repository; undefined when a call failed or timed out. */
async function findLedger($: Dollar, cwd: string): Promise<Ledger | null | undefined> {
  const top = await $.process
    .run(['git', 'rev-parse', '--show-toplevel'], { cwd, timeoutMs: SHORT_TIMEOUT_MS })
    .catch(() => undefined)
  if (top === undefined) return undefined
  const root = top.exitCode === 0 ? top.stdout.trim() : ''
  if (root === '') return null
  const out = await ledgerScript($, root, ['dir'], SHORT_TIMEOUT_MS)
  const dirs = (out ?? '').split('\n').filter(d => d !== '')
  return dirs.length === 0 ? undefined : { root, dirs }
}

/** The ledger of the repo `cwd` is in; null outside one. Only a definite answer is kept. */
async function ledgerOf($: Dollar, cwd: string): Promise<Ledger | null> {
  if (ledgerFor?.cwd === cwd) return ledgerFor.ledger
  const ledger = await findLedger($, cwd)
  if (ledger === undefined) return null
  ledgerFor = { cwd, ledger }
  return ledger
}

/** Adds PRs to the list. Runs add only ones the user has not removed; a typed add overrides that. */
async function addPrs($: Dollar, numbers: readonly number[], isTyped: boolean): Promise<boolean> {
  const dismissed = await read($, dismissedA)
  const wanted = numbers.filter(n => isTyped || !dismissed.includes(n))
  if (isTyped) {
    await update($, dismissedA, list => without(list, numbers))
    await update($, mineA, list => withAll(list, numbers))
  }
  let isAdded = false
  await update($, prsA, list => {
    const next = withAll(list, wanted)
    isAdded = next.length !== list.length
    return next
  })
  return isAdded
}

async function removePrs($: Dollar, numbers: readonly number[]): Promise<void> {
  await update($, prsA, list => without(list, numbers))
  await update($, mineA, list => without(list, numbers))
  await update($, dismissedA, list => withAll(list, numbers))
  await update($, dataA, old => {
    const next = { ...old }
    for (const n of numbers) delete next[String(n)]
    return next
  })
}

/** Re-reads the ledger, adds PRs it names, and refreshes issues for the runs of tracked PRs. */
async function syncRuns($: Dollar, isForced: boolean): Promise<void> {
  const ledger = await ledgerOf($, await $.session.cwd())
  if (ledger === null) return
  const old = await read($, runsA)
  // A failed call leaves the runs as they were.
  const out = await ledgerScript($, ledger.root, ['runs'], SHORT_TIMEOUT_MS)
  const runs = out === null ? old : parseRuns(out)
  if (JSON.stringify(old) !== JSON.stringify(runs)) await update($, runsA, () => runs)

  const since = await read($, sinceA)
  const fresh = runs.filter(r => Date.parse(r.when) >= since)
  if (await addPrs($, prsFromRuns(fresh), false)) await refreshPrs($)

  await syncIssues($, ledger, isForced)
}

async function syncIssues($: Dollar, { root, dirs }: Ledger, isForced: boolean): Promise<void> {
  const prs = await read($, prsA)
  const data = await read($, dataA)
  const runs = await read($, runsA)
  const ids = new Set<string>()
  for (const n of prs) {
    for (const r of runsForPr(runs, { number: n, ledgerBranch: data[String(n)]?.ledgerBranch ?? '' })) {
      ids.add(r.id)
    }
  }

  const found = await Promise.all(
    [...ids].map(async (id): Promise<[string, PrTrackerIssue[] | null] | null> => {
      // The issues command joins items and status files; their mtimes say whether it would answer differently.
      const mtime = (p: string) => $.fs.stat(p).then(s => String(s.mtimeMs), () => '-')
      const items = await Promise.all(dirs.map(d => mtime(`${d}/items-${id}.json`)))
      const status = await Promise.all(dirs.map(d => mtime(`${d}/status-${id}.tsv`)))
      const stamp = [...items, ...status].join('|')
      if (!isForced && seen.get(id) === stamp) return null
      // No items file: the run has no issue data, and the script would only fail.
      if (items.every(m => m === '-')) {
        seen.set(id, stamp)
        return [id, null]
      }
      // A failed call stores no stamp, so the next round tries again; the issues shown stay.
      const out = await ledgerScript($, root, ['issues', id], LONG_TIMEOUT_MS)
      if (out === null) return null
      seen.set(id, stamp)
      return [id, parseIssues(out)]
    }),
  )
  const changed = found.filter((x): x is [string, PrTrackerIssue[] | null] => x !== null)
  if (changed.length === 0) return
  await update($, issuesA, old => {
    const next = { ...old }
    for (const [id, issues] of changed) {
      if (issues === null) delete next[id]
      else next[id] = issues
    }
    return next
  })
}

/** PR data first, since runs match PRs by branch; then the ledger and the issues. */
async function refreshAll($: Dollar, isForced: boolean): Promise<void> {
  await refreshPrs($)
  await syncRuns($, isForced)
}

/** Copies the stored view into the atom; state survives a reload, but not a new session. */
async function loadView($: Dollar): Promise<void> {
  const stored = await $.store.get(VIEW_KEY)
  if (stored === 'pane' || stored === 'band') await update($, viewA, () => stored)
}

/** Sets the view, keeps it across sessions, and moves the pane to match. */
async function setView($: Dollar, view: PrsView): Promise<void> {
  await $.store.set(VIEW_KEY, view)
  await update($, viewA, () => view)
  if (view === 'band') await $.ui.close({ id: PANE })
  else await $.ui.open({ id: PANE, title: 'PRs' })
}

const quiet = (p: Promise<unknown>): void => void p.catch(() => {})

const viewReply = (view: PrsView): string => (view === 'pane' ? 'PRs pane opened.' : 'PRs band shown.')

function ensureTimers($: Dollar): void {
  if (timers !== null) return
  timers = [
    $.clock.every(5_000, () => quiet(syncRuns($, false))),
    $.clock.every(60_000, () => quiet(refreshPrs($))),
  ]
  quiet(
    update($, sinceA, t => (t === 0 ? Date.now() : t)).then(() => refreshAll($, false)),
  )
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'prs',
      description:
        'Track PRs created or added here; all also shows PRs any session in this repo reviewed since this one started. With reviews and issues',
      argumentHint: '[add <n>... | remove <n>... | pane | band]',
    })
    await loadView($)
    if ((await read($, viewA)) === 'pane') void $.ui.open({ id: PANE, title: 'PRs' })
    ensureTimers($)

    return next(e)
  })

  on('command.run', { command: 'prs' }, async ($, e) => {
    ensureTimers($)
    const cmd = parsePrsArgs(e.args)
    if (cmd.kind === 'error') return { text: cmd.text }

    const view = await read($, viewA)
    let text = viewReply(view)
    if (cmd.kind === 'view') {
      await setView($, cmd.view)
      return { text: viewReply(cmd.view) }
    }
    if (cmd.kind === 'add') {
      await addPrs($, cmd.numbers, true)
      await refreshAll($, false)
      text = `Tracking ${cmd.numbers.map(n => '#' + n).join(' ')}.`
    } else if (cmd.kind === 'remove') {
      await removePrs($, cmd.numbers)
      text = `Removed ${cmd.numbers.map(n => '#' + n).join(' ')}.`
    }
    if (view === 'pane') await $.ui.open({ id: PANE, title: 'PRs' })

    return { text }
  })

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    ensureTimers($)
    const ran = await next(e)
    if (ran.deny !== undefined) return ran

    // Tracking must never break the Bash call it watches.
    try {
      const created = createdPrNumber(e.command, ran.text ?? '')
      if (created !== null) await addPrs($, [created], true)
      if (created !== null || touchesPrs(e.command)) {
        // Not awaited: the result goes to the model first. A timer outlives this dispatch.
        $.clock.after(0, () => void refreshAll($, false).catch(() => {}))
      }
    } catch {
      // Dropped; the next timer round catches up.
    }

    return ran
  }).catch(($, e, next) => next(e))

  on('ui.render', { component: 'Pane', requestId: PANE }, ($, e) => {
    // Inline, the frame fits the tree, so half the rows halves the pane; the dock keeps them all.
    const rows =
      e.props.placement === 'inline' ? Math.floor(e.props.scroll.bodyRows / 2) : e.props.scroll.bodyRows
    return drawPrs($, e, { columns: e.props.bodyColumns, rows })
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey || (await read($, viewA)) === 'pane') return next(e)
    // A frame sets the band apart from the transcript; it takes two rows and four columns.
    const { Box } = $.ui.resolve(e)
    return (
      <Box borderStyle="round" borderDimColor paddingX={1}>
        {await drawPrs($, e, { columns: e.props.bodyColumns - 4, rows: e.props.maxRows - 2 })}
      </Box>
    )
  })
}

/** The PRs the current scope draws. */
async function shownPrs($: Dollar): Promise<number[]> {
  return scopePrs(await read($, prsA), await read($, mineA), await read($, scopeA))
}

/** The PR list, for the pane and the band alike. */
async function drawPrs(
  $: Dollar,
  e: ResolveInput,
  size: { columns: number; rows: number },
) {
  const { Box, Text, Button } = $.ui.resolve(e)
  const tracked = await read($, prsA)
  const scope = await read($, scopeA)
  const prs = await shownPrs($)
  const data = await read($, dataA)
  const runs = await read($, runsA)
  const issues = await read($, issuesA)
  const updatedAt = await read($, updatedA)
  const selected = await read($, selectedA)

  const columns = Math.max(20, size.columns)
  const rows = size.rows
  const expanded = resolveExpanded(selected, prs, data)
  const sorted = [...prs].sort((a, b) => b - a)

  const toggle = (n: number) => async () => {
    const shown = await shownPrs($)
    const current = resolveExpanded(await read($, selectedA), shown, await read($, dataA))
    await update($, selectedA, () => (current === n ? 'none' : n))
  }
  const refresh = async () => {
    await refreshAll($, true)
  }
  const switchView = async () => {
    await setView($, (await read($, viewA)) === 'pane' ? 'band' : 'pane')
  }
  const switchScope = async () => {
    await update($, scopeA, s => (s === 'mine' ? 'all' : 'mine'))
  }
  const clearMerged = async () => {
    const shown = await shownPrs($)
    await removePrs($, mergedIn(shown, await read($, dataA)))
  }
  const merged = mergedIn(prs, data)
  const empty = emptyText(prs.length, tracked.length, scope)
  const view = await read($, viewA)

  // Segments to elements. A Text takes no key.
  const texts = (segs: readonly Seg[]) =>
    segs.map(s =>
      s.color === undefined && s.dim !== true && s.bold !== true ? (
        s.text
      ) : (
        <Text
          {...(s.color === undefined ? {} : { color: s.color })}
          {...(s.dim === true ? { dimColor: true } : {})}
          {...(s.bold === true ? { bold: true } : {})}
        >
          {s.text}
        </Text>
      ),
    )

  const stamp = updatedAt === 0 ? 'not updated yet' : `updated ${clock(updatedAt)}`
  const items = [
    { label: stamp, node: <Text dimColor>{stamp}</Text> },
    {
      label: 'refresh',
      node: (
        <Button key="refresh" onPress={() => quiet(refresh())}>
          refresh
        </Button>
      ),
    },
    {
      label: view === 'pane' ? 'band' : 'pane',
      node: (
        <Button key="view" onPress={() => quiet(switchView())}>
          {view === 'pane' ? 'band' : 'pane'}
        </Button>
      ),
    },
    {
      label: scope === 'mine' ? 'all' : 'mine',
      node: (
        <Button key="scope" onPress={() => quiet(switchScope())}>
          {scope === 'mine' ? 'all' : 'mine'}
        </Button>
      ),
    },
    ...(merged.length === 0
      ? []
      : [
          {
            label: `clear merged (${merged.length})`,
            node: (
              <Button key="clear" onPress={() => quiet(clearMerged())}>
                {`clear merged (${merged.length})`}
              </Button>
            ),
          },
        ]),
  ]
  let from = 0
  const footer = footerLines(items.map(i => i.label), columns).map(l => items.slice(from, (from += l.length)))

  // The expanded PR gets the rows the headers, totals and footer leave for its issues.
  // layoutRows counts one footer row; a wrapped footer takes more.
  const layout = layoutRows(rows - (footer.length - 1), sorted, expanded)
  const room = layout.room

  const blocks = layout.shown.map(n => {
    const stored = data[String(n)]
    const pr = stored === undefined ? undefined : storedPr(n, stored)
    const prRuns = runsForPr(runs, { number: n, ledgerBranch: pr?.ledgerBranch ?? '' })
    const all = prRuns.flatMap(r => issues[r.id] ?? [])
    const isExpanded = expanded === n
    const noData = prRuns.filter(r => issues[r.id] === undefined).length

    let lines: Seg[][] = []
    if (isExpanded) {
      const list = sortIssues(all)
      const w = issueWidths(list)
      const shown = list.length > room ? Math.max(0, room - 1) : list.length
      lines = list.slice(0, shown).map(i => issueSegs(i, w.ns, w.right, columns))
      if (shown < list.length && room > 0) {
        lines.push([{ text: `  … ${list.length - shown} more`, dim: true }])
      }
    }

    return (
      <Box flexDirection="column">
        <Button key={`pr-${n}`} plain onPress={() => quiet(toggle(n)())}>
          {texts(headerSegs(n, pr, prRuns, isExpanded, columns))}
        </Button>
        {layout.totals.has(n) && <Text>{texts(totalsSegs(all, prRuns.length, noData, columns))}</Text>}
        {lines.map(l => (
          <Text>{texts(l)}</Text>
        ))}
      </Box>
    )
  })

  return (
    <Box flexDirection="column">
      {empty !== null && <Text dimColor>{empty}</Text>}
      {blocks}
      {layout.hidden > 0 && <Text dimColor>{`… ${layout.hidden} more PRs`}</Text>}
      <Box flexDirection="column">
        {footer.map(line => (
          <Box gap={2}>{line.map(i => i.node)}</Box>
        ))}
      </Box>
    </Box>
  )
}
