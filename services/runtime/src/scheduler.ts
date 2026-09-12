import { parseContract, type DAG, type Node } from '@jarvis/contracts';

export function validateDAG(value: unknown): DAG {
  const dag = parseContract('DAG', value);
  const nodes = new Map(dag.nodes.map(node => [node.id, node]));
  if (nodes.size !== dag.nodes.length) throw new Error('DAG contains duplicate node IDs');
  if (new Set(dag.nodes.map(node => node.action.id)).size !== dag.nodes.length) throw new Error('DAG contains duplicate action IDs');
  const visited = new Set<string>();
  const visiting = new Set<string>();
  function visit(id: string) {
    if (visited.has(id)) return;
    if (visiting.has(id)) throw new Error('DAG contains a cycle');
    const node = nodes.get(id);
    if (!node) throw new Error(`DAG references missing dependency ${id}`);
    visiting.add(id);
    for (const dependency of node.dependsOn) visit(dependency);
    visiting.delete(id); visited.add(id);
  }
  for (const node of dag.nodes) visit(node.id);
  return dag;
}
/** Runs only ready nodes, joins all started work on failure, and never schedules after abort. */
export async function runDAG(value: unknown, execute: (node: Node, signal: AbortSignal) => Promise<void>, externalSignal: AbortSignal): Promise<{ completed: string[]; failed: string[]; cancelled: boolean }> {
  const dag = validateDAG(value);
  const controller = new AbortController();
  const cancel = () => controller.abort(externalSignal.reason);
  if (externalSignal.aborted) cancel(); else externalSignal.addEventListener('abort', cancel, { once: true });
  const completed = new Set<string>();
  const failed = new Set<string>();
  const pending = new Map(dag.nodes.map(node => [node.id, node]));
  const active = new Map<string, Promise<void>>();
  try {
    while (pending.size || active.size) {
      if (!controller.signal.aborted) {
        for (const [id, node] of pending) {
          if (active.size >= dag.maxConcurrency) break;
          if (!node.dependsOn.every(dependency => completed.has(dependency))) continue;
          pending.delete(id);
          const work = Promise.resolve().then(() => { controller.signal.throwIfAborted(); return execute(node, controller.signal); }).then(() => { completed.add(id); }, () => { failed.add(id); controller.abort(new Error(`Node ${id} failed`)); }).finally(() => { active.delete(id); });
          active.set(id, work);
        }
      }
      if (!active.size) break;
      await Promise.race(active.values());
    }
    return { completed: [...completed], failed: [...failed], cancelled: externalSignal.aborted };
  } finally { externalSignal.removeEventListener('abort', cancel); await Promise.allSettled(active.values()); }
}
export function abortableDelay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(signal.reason); return; }
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, ms);
    signal.addEventListener('abort', abort, { once: true });
  });
}
