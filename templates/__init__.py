# -*- coding: utf-8 -*-
from . import boxed_header, underline_header

TEMPLATES = [
    ("Boxed Header (bordered section boxes, black & white)", boxed_header.build),
    ("Underline Header (stacked contact lines, black & white)", underline_header.build),
]
