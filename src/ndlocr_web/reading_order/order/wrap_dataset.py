# Copyright (c) 2023, National Diet Library, Japan
#
# This software is released under the CC BY 4.0.
# https://creativecommons.org/licenses/by/4.0/


def wrap_ocr_dataset(xml_file):
    contents = open(xml_file, "r", encoding="utf-8").read()
    with open(xml_file, "w", encoding="utf-8") as fo:
        fo.write("<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
                 "<OCRDATASET>\n")
        fo.write(contents)
        fo.write("</OCRDATASET>\n")
