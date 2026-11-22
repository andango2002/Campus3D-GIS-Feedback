"""Pure numeric helpers for spatial evaluation (no interpolation)."""
import math
from .point_evaluation_core import finite_number


def weighted_summary(records, threshold):
    """Records: (temperature, positive weight). None temperatures omitted.
    Unit weights for MRT, planar intersection area for surface.
    Population SD describes available values, not inferential uncertainty.
    """
    valid=[]
    for temp, weight in records:
        t,w=finite_number(temp),finite_number(weight)
        if t is not None and w is not None and w>0:
            valid.append((t,w))
    total=math.fsum(w for t,w in valid)
    if not valid:
        return dict(count=0,weight=0.,mean=None,minimum=None,maximum=None,sd=None,hot_weight=0.,hot_percent=None)
    mean=math.fsum(t*w for t,w in valid)/total
    hot=math.fsum(w for t,w in valid if t>=threshold)
    return dict(count=len(valid),weight=total,mean=mean,minimum=min(t for t,w in valid),
                maximum=max(t for t,w in valid),sd=math.sqrt(math.fsum(w*(t-mean)**2 for t,w in valid)/total),
                hot_weight=hot,hot_percent=100*hot/total)


def ratio(numerator, denominator):
    return 100*numerator/denominator if denominator>0 else None
