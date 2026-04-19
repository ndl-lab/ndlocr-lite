"""Serial 3-stage cascade recognition (no ThreadPoolExecutor)."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np


@dataclass
class RecogLine:
    npimg: np.ndarray | None
    idx: int
    pred_char_cnt: float
    pred_str: str = ""

    def __lt__(self, other: "RecogLine") -> bool:
        return self.idx < other.idx


def process_cascade(
    alllineobj: list[RecogLine],
    recognizer30,
    recognizer50,
    recognizer100,
    is_cascade: bool = True,
) -> list[str]:
    """Run 3-stage cascade recognition in serial order."""
    targetlist30: list[RecogLine] = []
    targetlist50: list[RecogLine] = []
    targetlist100: list[RecogLine] = []
    targetlist200: list[RecogLine] = []
    resultall: list[RecogLine] = []

    for lineobj in alllineobj:
        if lineobj.pred_char_cnt == 3 and is_cascade:
            targetlist30.append(lineobj)
        elif lineobj.pred_char_cnt == 2 and is_cascade:
            targetlist50.append(lineobj)
        else:
            targetlist100.append(lineobj)

    # Stage 30
    for lineobj in targetlist30:
        pred_str = recognizer30.read(lineobj.npimg)
        if len(pred_str) >= 25:
            targetlist50.append(lineobj)
        else:
            lineobj.pred_str = pred_str
            resultall.append(lineobj)

    # Stage 50
    for lineobj in targetlist50:
        pred_str = recognizer50.read(lineobj.npimg)
        if len(pred_str) >= 45:
            targetlist100.append(lineobj)
        else:
            lineobj.pred_str = pred_str
            resultall.append(lineobj)

    # Stage 100
    for lineobj in targetlist100:
        pred_str = recognizer100.read(lineobj.npimg)
        lineobj.pred_str = pred_str
        if len(pred_str) >= 98 and lineobj.npimg is not None and lineobj.npimg.shape[0] < lineobj.npimg.shape[1]:
            base = lineobj.npimg
            half = base.shape[1] // 2
            targetlist200.append(RecogLine(npimg=base[:, :half, :], idx=lineobj.idx, pred_char_cnt=100))
            targetlist200.append(RecogLine(npimg=base[:, half:, :], idx=lineobj.idx, pred_char_cnt=100))
        else:
            resultall.append(lineobj)

    # Stage 200 (split halves)
    for i in range(0, len(targetlist200) - 1, 2):
        left = targetlist200[i]
        right = targetlist200[i + 1]
        combined = recognizer100.read(left.npimg) + recognizer100.read(right.npimg)
        merged = RecogLine(npimg=None, idx=left.idx, pred_char_cnt=100, pred_str=combined)
        resultall.append(merged)

    resultall.sort()
    return [obj.pred_str for obj in resultall]
