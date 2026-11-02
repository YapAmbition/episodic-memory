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

