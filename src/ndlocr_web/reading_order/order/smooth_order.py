# Copyright (c) 2023, National Diet Library, Japan
#
# This software is released under the CC BY 4.0.
# https://creativecommons.org/licenses/by/4.0/


import numpy as np
import networkx as nx


def find_minimum_hamiltonian_path(graph):
    """
    Return the minimum weight hamiltonian path in `graph`.
    """
    min_weight = 99999
    min_path = None
    num_nodes = len(graph.nodes())
    for path in nx.all_simple_paths(graph, source=0, target=num_nodes-1):
        if len(path) == num_nodes:
            path_weight = sum([graph[u][v]['weight']
                              for u, v in zip(path[:-1], path[1:])])
            if path_weight < min_weight:
                min_weight, min_path = path_weight, path
    return min_path, min_weight


def smooth_order_page(page):
    w = float(page.get("WIDTH"))
    h = float(page.get("HEIGHT"))
    diam = np.sqrt(w*w + h*h)

    def traverse(page_or_block):
        tobe_sorted = list()
        unsorted = list()
        for element in page_or_block:
            if "TEXTBLOCK" == element.tag:
                orders = list()
                is_first = True
                beg_pos = (None, None)
                end_pos = (None, None)
                for line in element.findall("./LINE"):
                    orders.append(float(line.get("ORDER", np.nan)))
                    x = float(line.get("X", np.nan))
                    y = float(line.get("Y", np.nan))
                    w = float(line.get("WIDTH", np.nan))
                    h = float(line.get("HEIGHT", np.nan))
                    if is_first:
                        beg_pos = (x + w/2, y)
                    end_pos = (x + w/2, y + h)
                if not orders:
                    continue
                order = orders[len(orders) // 2]
                tobe_sorted.append((order, beg_pos, end_pos, element))

            elif "LINE" == element.tag:
                order = float(element.get("ORDER", np.nan))
                x = float(element.get("X", np.nan))
                y = float(element.get("Y", np.nan))
                w = float(element.get("WIDTH", np.nan))
                h = float(element.get("HEIGHT", np.nan))
                beg_pos = (x + w/2, y)
                end_pos = (x + w/2, y + h)
                tobe_sorted.append((order, beg_pos, end_pos, element))

            elif "BLOCK" == element.tag:
                traverse(element)
                unsorted.append(element)

            else:
                unsorted.append(element)

        # Build graph.
        graph = nx.DiGraph()
        num = len(tobe_sorted)
        orders = [o for o, _, _, _ in tobe_sorted]
        if 0 < num:
            order_range = max(orders) - min(orders)

            def calc_weight(i, j):
                order_i, _, end, _ = tobe_sorted[i]
                order_j, beg, _, _ = tobe_sorted[j]
                order_d = abs(order_i - order_j) / order_range
                x0, y0 = end
                x1, y1 = beg
                dist = np.sqrt((x1-x0) ** 2 + (y1-y0) ** 2)
                return dist / diam + order_d
            for i in range(num):
                graph.add_node(i)
            max_step = 3 if num < 20 else 2
            for step in range(1, max_step):
                for i in range(num-step):
                    graph.add_edge(
                        i, i+step, weight=calc_weight(i, i+step))
                    graph.add_edge(
                        i+step, i, weight=calc_weight(i+step, i))
            if 0 < graph.number_of_nodes():
                min_path, _ = find_minimum_hamiltonian_path(graph)
                if min_path:
                    page_or_block[:] = [tobe_sorted[i][-1]
                                        for i in min_path] + unsorted

    traverse(page)


def smooth_order(root):
    if "PAGE" == root.tag:
        smooth_order_page(root)
    else:
        for page in root.findall(".//PAGE"):
            smooth_order_page(page)
