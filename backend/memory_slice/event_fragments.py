"""Lossless event fragments independent of conversation injection."""
import hashlib
import re

def split_body(body, target=1500, maximum=3000):
    if not 0 < target <= maximum:
        raise ValueError('片段目标必须大于零且不超过硬上限')
    # Keep structural sections independent so appending a new section preserves old IDs.
    boundaries = [0] + [m.start() for m in re.finditer(r'(?m)^#{1,6} ', body) if m.start()] + [len(body)]
    result = []
    for left, right in zip(boundaries, boundaries[1:]):
        section_first = len(result)
        start = left
        while start < right:
            end = min(right, start + maximum)
            if right - start > target:
                # Prefer a paragraph ending near the target, then a sentence/line boundary.
                for pattern in (r'\n\s*\n', r'[。！？.!?](?:\s|$)|\n'):
                    cuts = [start + m.end() for m in re.finditer(pattern, body[start:end])
                            if m.end() >= target // 2]
                    if cuts:
                        end = min(cuts, key=lambda x: (abs(x - start - target), x))
                        break
            text = body[start:end]
            fid = 'part_' + hashlib.sha256((str(start) + ':' + text).encode()).hexdigest()[:20]
            result.append(dict(id=fid, start=start, end=end, text=text, section_first=section_first))
            start = end
    return result



def semantic_fragment_score(part, match):
    """Combine overlapping hit ranges without double-counting their coverage."""
    hits = [h for h in (match or {}).get("fragment_hits", [])
            if h['end'] > part['start'] and h['start'] < part['end'] and h['end'] > h['start']]
    if not hits or part['end'] <= part['start']:
        return 0
    boundaries = sorted({part['start'], part['end']} |
                        {max(part['start'], h['start']) for h in hits} |
                        {min(part['end'], h['end']) for h in hits})
    weighted = sum((right-left) * max((h['score'] for h in hits
                   if h['start'] <= left and h['end'] >= right), default=0)
                   for left,right in zip(boundaries,boundaries[1:]))
    span = min(part['end']-part['start'], max(h['end']-h['start'] for h in hits))
    return min(max(h['score'] for h in hits), weighted / span)
