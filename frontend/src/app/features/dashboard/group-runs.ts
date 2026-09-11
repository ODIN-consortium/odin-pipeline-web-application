import { ContinuationEvidence, NanoporeRunStatus } from '../../core/models/discovery.model';

/** One row of the dashboard: a run on its own, or a group the dashboard offers to merge. */
export type DisplayItem =
  | { kind: 'single'; run: NanoporeRunStatus }
  | {
      kind: 'continuation-group';
      runs: NanoporeRunStatus[];
      confidence: 'likely' | 'possible';
      evidence: ContinuationEvidence;
      mergeDecision: boolean | null;
    }
  | {
      kind: 'sample-group';
      runs: NanoporeRunStatus[];
      sampleName: string;
      mergeDecision: boolean | null;
    };

/** Shown when a group has no evidence of its own; the UI still needs the shape. */
const NO_EVIDENCE: ContinuationEvidence = {
  flow_cell_id: 'unknown',
  time_gap_hours: 0,
  kit: null,
  run_name_match: null,
};

type Adjacency = Map<string, Set<string>>;

/**
 * Group discovered runs for display.
 *
 * A run may be linked to others in two unrelated ways: as a *continuation* (the same physical
 * sequencing run resumed on the same flow cell) or as *related* (a different run of the same
 * sample). Both are symmetric, both are transitive, and the answer is the connected components of
 * each link type — a run that continues another which continues a third belongs in one group of
 * three, not two groups of two.
 *
 * The two link types are kept in **separate adjacency maps on purpose**. Merged into one graph, a
 * single sample-level link would join two continuation chains into one component and the dashboard
 * would offer to merge sequencing runs that were never the same run. Continuation groups are
 * therefore formed first and their members withdrawn from consideration before sample groups are
 * formed from what is left.
 *
 * Pure: everything it needs is in its arguments, which is what lets the grouping be tested without
 * standing up the dashboard.
 */
export function groupRuns(runs: NanoporeRunStatus[], hideExcluded: boolean): DisplayItem[] {
  const visible = hideExcluded ? runs.filter((r) => !r.is_excluded) : runs;
  const known = new Set(visible.map((r) => r.run_accession));

  const continuationAdj = buildAdjacency(visible, known, (r) => r.continuation_run_accessions);
  const relatedAdj = buildAdjacency(visible, known, (r) => r.related_run_accessions);
  const byAccession = new Map(visible.map((r) => [r.run_accession, r]));

  const items: DisplayItem[] = [];
  const grouped = new Set<string>();

  for (const members of groupsOf(visible, continuationAdj)) {
    members.forEach((r) => grouped.add(r.run_accession));
    items.push({
      kind: 'continuation-group',
      runs: members,
      ...continuationEvidenceOf(members),
      mergeDecision: unanimousMergeDecision(members),
    });
  }

  const ungrouped = new Set([...known].filter((ra) => !grouped.has(ra)));
  for (const members of groupsOf(visible, relatedAdj, ungrouped)) {
    members.forEach((r) => grouped.add(r.run_accession));
    items.push({
      kind: 'sample-group',
      runs: members,
      sampleName: members.find((r) => r.sample_name)?.sample_name ?? 'Multiple runs',
      mergeDecision: unanimousMergeDecision(members),
    });
  }

  for (const run of visible) {
    if (!grouped.has(run.run_accession)) items.push({ kind: 'single', run });
  }

  return sortByEarliestMember(items, visible);

  function groupsOf(
    from: NanoporeRunStatus[],
    adj: Adjacency,
    onlyFrom?: Set<string>,
  ): NanoporeRunStatus[][] {
    return connectedComponents(from, adj, onlyFrom)
      .filter((component) => component.length >= 2)
      .map((component) => component.map((ra) => byAccession.get(ra)!).filter(Boolean))
      .filter((members) => members.length > 0);
  }
}

/** Symmetric adjacency over one kind of link, ignoring links to runs that are not present. */
function buildAdjacency(
  runs: NanoporeRunStatus[],
  known: Set<string>,
  linksOf: (run: NanoporeRunStatus) => string[],
): Adjacency {
  const adj: Adjacency = new Map();
  const link = (a: string, b: string) => {
    if (!adj.has(a)) adj.set(a, new Set());
    adj.get(a)!.add(b);
  };
  for (const run of runs) {
    for (const other of linksOf(run)) {
      // A link is mutual even when only one side declares it, and a link to a run that was not
      // discovered (or was excluded) is not a link at all.
      if (!known.has(other)) continue;
      link(run.run_accession, other);
      link(other, run.run_accession);
    }
  }
  return adj;
}

/** Breadth-first sweep, visiting runs in discovery order so components come out deterministically. */
function connectedComponents(
  runs: NanoporeRunStatus[],
  adj: Adjacency,
  onlyFrom?: Set<string>,
): string[][] {
  const visited = new Set<string>();
  const components: string[][] = [];

  for (const run of runs) {
    const start = run.run_accession;
    if (onlyFrom && !onlyFrom.has(start)) continue;
    if (!adj.has(start) || visited.has(start)) continue;

    const component: string[] = [];
    const queue = [start];
    while (queue.length > 0) {
      const current = queue.shift()!;
      if (visited.has(current)) continue;
      if (onlyFrom && !onlyFrom.has(current)) continue;
      visited.add(current);
      component.push(current);
      for (const neighbour of adj.get(current) ?? []) {
        if (!visited.has(neighbour)) queue.push(neighbour);
      }
    }
    if (component.length > 0) components.push(component);
  }
  return components;
}

/** `true`/`false` only when every run in the group agrees; anything else is undecided. */
function unanimousMergeDecision(runs: NanoporeRunStatus[]): boolean | null {
  if (runs.every((r) => r.auto_merge === true)) return true;
  if (runs.every((r) => r.auto_merge === false)) return false;
  return null;
}

/**
 * The group's confidence is the best any member claims: one `likely` run makes the whole group
 * likely, and its evidence is the evidence shown. Otherwise the first evidence found stands in.
 */
function continuationEvidenceOf(runs: NanoporeRunStatus[]): {
  confidence: 'likely' | 'possible';
  evidence: ContinuationEvidence;
} {
  let evidence: ContinuationEvidence | null = null;
  for (const run of runs) {
    if (!run.continuation_evidence) continue;
    if (run.continuation_confidence === 'likely') {
      return { confidence: 'likely', evidence: run.continuation_evidence };
    }
    evidence ??= run.continuation_evidence;
  }
  return { confidence: 'possible', evidence: evidence ?? NO_EVIDENCE };
}

/** Keep the discovery order the operator already sees: a group sits where its first run sat. */
function sortByEarliestMember(items: DisplayItem[], visible: NanoporeRunStatus[]): DisplayItem[] {
  const position = new Map(visible.map((r, i) => [r.run_accession, i]));
  const earliest = (item: DisplayItem): number => {
    const accessions =
      item.kind === 'single' ? [item.run.run_accession] : item.runs.map((r) => r.run_accession);
    return Math.min(...accessions.map((ra) => position.get(ra) ?? Number.MAX_SAFE_INTEGER));
  };
  return [...items].sort((a, b) => earliest(a) - earliest(b));
}
