# Copyright (c) 2023, National Diet Library, Japan
#
# This software is released under the CC BY 4.0.
# https://creativecommons.org/licenses/by/4.0/


import xml.etree.ElementTree as et


class IndexedTags:
    """
    Mark the order of each tag in a depth first order. This orders are used for
    an algorithm that requires unique keys to point each xml tag. The marks are
    removed after exiting from the `with` scope.
    """

    def __init__(self, xml):
        from uuid import uuid4
        self.xml = xml
        self.key = uuid4().hex
        for i, elem in enumerate(self.xml.iter("*")):
            elem.set(self.key, str(i))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        for elem in self.xml.iter("*"):
            if self.key in elem.attrib:
                del elem.attrib[self.key]


class ConstantNumberOfTags:
    """
    If an operation on xml does not change the number of tags before and after
    it (e.g. sorting tags), it is recommended that the operation is enclosed by
    this block. If the number of tags changes, an error is raised.
    """

    def __init__(self, xml):
        self.xml = xml
        self.num = sum(1 for _ in self.xml.findall(".//*"))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        num = sum(1 for _ in self.xml.findall(".//*"))
        if self.num != num:
            raise RuntimeError("Number of XML tags changed: %d -> %d" %
                               (self.num, num))


def insert_before(parent: et.Element, element: et.Element, anchor: et.Element):
    """
    Insert an element to the parent, right before the anchor.
    """
    for i, child in enumerate(parent):
        if child == anchor:
            parent.insert(i, element)
            break
    else:
        raise RuntimeError("Cannot find anchor tag: %s" % anchor.tag)


def insert_after(parent: et.Element, element: et.Element, anchor: et.Element):
    """
    Insert an element to the parent, right after the anchor.
    """
    for i, child in enumerate(parent):
        if child == anchor:
            parent.insert(i + 1, element)
            break
    else:
        raise RuntimeError("Cannot find anchor tag: %s" % anchor.tag)


def shuffle_xml(xml: et.Element):
    """
    Apply shuffling recursively
    """
    from random import shuffle
    for child in xml:
        shuffle_xml(child)
    if 1 < len(xml):
        shuffle(xml)


def shuffle_xml_file(input_path, output_path):
    """
    Randomly reorder tags in XML files. Each tag's parent-child relationships
    are preserved.
    """
    root = et.parse(input_path).getroot()
    shuffle_xml(root)
    et.ElementTree(root).write(
        output_path, encoding="utf-8", xml_declaration=True)
