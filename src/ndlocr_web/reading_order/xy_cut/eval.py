# Copyright (c) 2023, National Diet Library, Japan
#
# This software is released under the CC BY 4.0.
# https://creativecommons.org/licenses/by/4.0/


import numpy as np
import re
import xml.etree.ElementTree as ET


from ..order.reorder import sort_lines
from ..utils.logger import get_logger
from ..utils.time import TimeKeeper
from .block_xy_cut import solve


def eval_xml(root, time_keeper=None, logger=None, plot_path=None, line_width_scale=1.0, smoothing=True, **_):
    time_keeper = time_keeper or TimeKeeper()
    logger = logger or get_logger(__name__)
    num = 0
    for i, page in enumerate(root.findall(".//PAGE")):
        with time_keeper.measure_time("sorting page"):
            lines = np.array([[
                int(line.get("X")),
                int(line.get("Y")),
                int(line.get("X")) + int(line.get("WIDTH")),
                int(line.get("Y")) + int(line.get("HEIGHT")),
            ] for line in page.findall(".//LINE")])
            new_plot_path = plot_path.with_suffix(
                ".%d.jpg" % i) if plot_path else None
            ranks = solve(lines, plot_path=new_plot_path,
                          logger=logger, scale=line_width_scale)
            for line, rank in zip(page.findall(".//LINE"), ranks):
                line.set("ORDER", str(rank))
            sort_lines(page, smoothing=smoothing)
            num += 1
    return num


def eval_path(xml_path, out_path, logger=None, time_keeper=None, plot_partition=False, line_width_scale=1.0, smoothing=False, **_):
    time_keeper = time_keeper or TimeKeeper()
    logger = logger or get_logger(__name__)

    # Parse xml
    xml_str = open(xml_path, encoding="utf-8").read()
    root = ET.fromstring(re.sub("xmlns=[\"'].*?[\"']", "", xml_str, count=1))

    plot_path = xml_path.with_suffix(
        ".xy-block.jpg") if plot_partition else None
    num = eval_xml(root, time_keeper=time_keeper, logger=logger,
                   plot_path=plot_path, line_width_scale=line_width_scale, smoothing=smoothing)
    ET.ElementTree(root).write(
        out_path, encoding="utf-8", xml_declaration=True)
    return num
