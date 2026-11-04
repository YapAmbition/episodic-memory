import type { MemoryEvent } from './types';

export const GRAPH_LIMIT = 400;
export const eventDate = (event: MemoryEvent) => (event.occurred_at || event.learned_at).slice(0, 10);
export const memoryColor = (e: MemoryEvent) => e.frozen ? '#6f96bc' : e.memory_status === 'future' ? '#a28ab9' : e.memory_status === 'active' ? '#6c927a' : e.memory_status === 'quiet' ? '#bd9c66' : '#a9b4ae';

/** Search retains immediate neighbours; no relationships are inferred. Never mutate API records. */
export function prepareMemories(events: MemoryEvent[], query = '', limit = GRAPH_LIMIT) {
  const ordered = [...events].sort((a,b) => b.learned_at.localeCompare(a.learned_at) || a.id.localeCompare(b.id));
  const term = query.trim().toLowerCase();
  const matches = new Set(ordered.filter(e => !term || `${e.title} ${e.body}`.toLowerCase().includes(term)).map(e => e.id));
  const neighbours = new Set<string>();
  if (term) ordered.forEach(e => e.links.forEach(l => {
    if (matches.has(e.id)) neighbours.add(l.target_id);
    if (matches.has(l.target_id)) neighbours.add(e.id);
  }));
  const candidates = term ? [...ordered.filter(e=>matches.has(e.id)), ...ordered.filter(e=>!matches.has(e.id) && neighbours.has(e.id))] : ordered;
  const visible = candidates.slice(0, limit);
  const lookup = new Map(visible.map(e => [e.id, e]));
  const degree = new Map(visible.map(e => [e.id, 0]));
  const edges = visible.flatMap(source => source.links.flatMap((link, i) => {
    if (!lookup.has(link.target_id) || link.target_id === source.id) return [];
    degree.set(source.id, degree.get(source.id)! + 1);
    degree.set(link.target_id, degree.get(link.target_id)! + 1);
    return [{id: `${source.id}:link:${i}`, source: source.id, target: link.target_id, relation: link.relation, strength: link.strength}];
  }));
  const dates = [...new Set(visible.map(eventDate))].sort();
  const rows = new Map<string, number>();
  const nodes = [...visible].sort((a,b)=>eventDate(a).localeCompare(eventDate(b)) || (a.occurred_at || a.learned_at).localeCompare(b.occurred_at || b.learned_at) || a.id.localeCompare(b.id)).map((event, i) => {
    const date = eventDate(event), row = rows.get(date) || 0;
    rows.set(date, row + 1);
    return {event, degree: degree.get(event.id)!, matched: matches.has(event.id),
      x: Math.cos(i * 2.39996) * Math.sqrt(i + 1) * 48,
      y: Math.sin(i * 2.39996) * Math.sqrt(i + 1) * 48,
      timeX: dates.indexOf(date) * 290, timeY: row * 122};
  });
  return {nodes, edges, dates, matches: term ? matches.size : 0, omitted: Math.max(0, candidates.length - visible.length)};
}
