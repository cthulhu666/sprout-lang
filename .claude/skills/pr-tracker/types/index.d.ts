// What the pane draws from. The draw only reads these; timers and hooks write them.

/** CI summary of one PR, from its statusCheckRollup. */
export type PrTrackerCi = { ok: number; fail: number; pending: number; total: number }

/** One PR as `gh pr view` last answered. `error` is set when the latest fetch failed;
 *  the other fields then hold the last good answer (or blanks if there was none). */
export type PrTrackerPr = {
  number: number
  title: string
  state: string
  /** The head branch, `headRefName`. */
  branch: string
  /** The branch name the ledger files this PR's runs under: `<owner>:<branch>` for a
   *  fork, else `branch`; empty when unknown. */
  ledgerBranch: string
  oid: string
  ci: PrTrackerCi
  error: string | null
}

/** One finished review run, a line of `review_ledger.sh runs`. */
export type PrTrackerRun = {
  id: string
  when: string
  branch: string
  head: string
  found: number | null
  confirmed: number | null
  effort: string
  cleanups: number | null
  cleanupsConfirmed: number | null
  /** `pr:<n>`, `branch:<name>` or `-`. */
  target: string
}

export type PrTrackerStatus = 'open' | 'fixed' | 'skipped' | 'wontfix' | 'refuted'

/** One line of `review_ledger.sh issues <run-id>`. */
export type PrTrackerIssue = {
  ns: number[]
  track: 'bug' | 'cleanup'
  severity: 'low' | 'medium' | 'high'
  status: PrTrackerStatus
  sha: string | null
  /** `file:line` */
  where: string
  summary: string
}

declare module 'claude-code' {
  interface PluginState {
    'pr-tracker': {
      /** PR numbers tracked: added here, or named by a run any session in this repo finished
       *  after this session started. */
      prs: number[]
      /** Latest `gh pr view` answer per PR, keyed by the number as a string. */
      data: Record<string, PrTrackerPr>
      /** Finished runs from `review_ledger.sh runs`, all PRs and branches. */
      runs: PrTrackerRun[]
      /** Issues per run id; `[]` for a clean review. A run with no items file has no key. */
      issues: Record<string, PrTrackerIssue[]>
      /** Epoch ms of the last PR fetch; 0 before the first. */
      updatedAt: number
      /** PRs removed by hand; runs that name them do not add them back. */
      dismissed: number[]
      /** Epoch ms the mod first ran in this session; only later runs add PRs; 0 before. */
      since: number
      /** The expanded PR: `auto` is the newest open one, `none` collapses all. */
      selected: number | 'auto' | 'none'
      /** Where the list shows: the `Pane` or the `AbovePrompt` band. */
      view: 'pane' | 'band'
    }
  }
}
