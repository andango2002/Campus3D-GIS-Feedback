# -*- coding: utf-8 -*-
"""Pure helpers for Phase 4 visual A/B comparison (not numerical pairing)."""
import math
import os


def key(entry):
    return tuple(entry[:3])


def role(layer):
    return os.path.basename(layer.source().split('|', 1)[0].replace('\\', '/')).lower()


def validate_pair(a, b):
    if a is None or b is None:
        raise ValueError('AとBの解析結果を選択してください。')
    if key(a) == key(b):
        raise ValueError('AとBには異なる解析結果を選んでください。')
    if a[0] != b[0] or a[0] not in ('mrt', 'surface'):
        raise ValueError('同じ種類（MRT同士、またはSurface同士）を選んでください。')
    for record in (a,b):
        if not record[3] or not all(l.isValid() for l in record[3]):
            raise ValueError('無効または欠落したレイヤがあります。')
        roles = [role(l) for l in record[3]]
        if len(roles) != len(set(roles)):
            raise ValueError('同じ出力が重複読込されています。重複レイヤを除いてください。')
    ra, rb = {role(l) for l in a[3]}, {role(l) for l in b[3]}
    if ra != rb:
        raise ValueError('A/Bで読込済みの面種別が違います。対応するGROUND/ROOF/WALLを読み込んでください。')
    return a[0]


def common_limits(low, high):
    if not (math.isfinite(low) and math.isfinite(high)) or low > high:
        raise ValueError('有効な温度範囲がありません。')
    if high-low < .001:
        low -= 1.; high += 1.
    # Outward rounding; ensure valid data remains within renderer bounds.
    lo, hi = math.floor(low*10)/10, math.ceil(high*10)/10
    if lo >= hi:
        lo -= .1; hi += .1
    return lo, hi


def matching_cuts(a,b):
    return bool(a and b and len(a)==len(b) and all(
        abs(x[0]-y[0]) < 1e-7 and abs(x[1]-y[1]) < 1e-7 and x[2].lower()==y[2].lower()
        for x,y in zip(a,b)))


def visible_side(a_ids, b_ids, visible_ids, all_temperature_ids):
    """Label A/B only when exactly the full selected temperature set is visible."""
    seen=set(visible_ids)&set(all_temperature_ids)
    if a_ids and seen==set(a_ids): return 'A'
    if b_ids and seen==set(b_ids): return 'B'
    return ''
