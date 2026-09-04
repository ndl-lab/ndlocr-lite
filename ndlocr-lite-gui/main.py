import logging
# logging.basicConfig(filename='debug.log', encoding='utf-8', level=logging.DEBUG)
import asyncio
import flet as ft
from typing import List, Dict, Tuple
import sys
import os
from pathlib import Path
import threading

APP_DIR = Path(__file__).resolve().parent
SOURCE_DIR = APP_DIR / 'src'
if not SOURCE_DIR.is_dir():
    # Source checkout: CI/builds copy src beside main.py, while local development
    # uses the repository's top-level src directory directly.
    SOURCE_DIR = APP_DIR.parent / 'src'
sys.path.append(str(SOURCE_DIR))

import xml.etree.ElementTree as ET
import time
from concurrent.futures import ThreadPoolExecutor
import json
import shutil
import argparse
import yaml
import io
import glob
import ctypes
from io import BytesIO
from gui_config import build_default_config
from uicomponent.localelabel import TRANSLATIONS
from uicomponent.textdaisy import TextDaisyExporter
from collections import Counter
from optional_features import load_optional_features
from pdf_output_utils import should_output_pdf_pages_separately, write_pdf_page_outputs
from version import __version__

name = 'NDLOCR-Lite-GUI'

_feature_modules = load_optional_features()
DATA_POOL_FEATURE = (
    _feature_modules.data_pool.create_feature()
    if _feature_modules.data_pool is not None
    else None
)
LLM_FEATURE = (
    _feature_modules.llm.create_feature()
    if _feature_modules.llm is not None
    else None
)
TABLE_STRUCTURE_FEATURE = (
    _feature_modules.table_structure.create_feature()
    if _feature_modules.table_structure is not None
    else None
)

np = None
Image = None
ImageGrab = None
ocr = None
pypdfium2 = None
convert_tei = None
categories_org_name_index = None
_runtime_modules_lock = threading.Lock()
_runtime_modules_ready = False


def load_runtime_modules():
    """Load OCR/native dependencies after the first GUI frame is available."""
    global np, Image, ImageGrab, ocr, pypdfium2
    global convert_tei, categories_org_name_index
    global _runtime_modules_ready

    if _runtime_modules_ready:
        return
    with _runtime_modules_lock:
        if _runtime_modules_ready:
            return

        import numpy as numpy_module
        from PIL import Image as pil_image_module
        from PIL import ImageFile as pil_image_file_module
        from PIL import ImageGrab as pil_image_grab_module
        import ocr as ocr_module
        import pypdfium2 as pypdfium2_module
        from tools.ndlkoten2tei import convert_tei as convert_tei_function
        from ndl_parser import categories_org_name_index as category_mapping

        pil_image_file_module.MAXBLOCK = 1024 * 1024 * 128
        np = numpy_module
        Image = pil_image_module
        ImageGrab = pil_image_grab_module
        ocr = ocr_module
        pypdfium2 = pypdfium2_module
        convert_tei = convert_tei_function
        categories_org_name_index = category_mapping
        _runtime_modules_ready = True


def build_gui_result_markdown(page_result, page_tables=None):
    """Build ft.Markdown content in layout order for one OCR page."""
    table_fragments = (
        TABLE_STRUCTURE_FEATURE.markdown_fragments(page_tables)
        if TABLE_STRUCTURE_FEATURE is not None
        else []
    )

    rendered = ocr.build_markdown_text_from_xml(
        str(page_result.get('page_xml', '')),
        table_markdowns=table_fragments,
    )
    if rendered:
        return rendered
    return str(page_result.get('markdown_text') or page_result.get('text') or '')


ASSETS_DIR = APP_DIR / 'assets'
DEFAULT_IMAGE_SRC = str(ASSETS_DIR / 'dummy.dat')
APP_DATA_DIR = Path(os.environ.get('FLET_APP_STORAGE_DATA', os.getcwd())).resolve()
APP_TEMP_DIR = Path(
    os.environ.get('FLET_APP_STORAGE_TEMP', str(APP_DATA_DIR / 'tmp'))
).resolve()
PDFTMPPATH = str(APP_TEMP_DIR / '4ab7ecc3-53fb-b3e7-64e8-a809b5a483d2')
USER_CONFIG_PATH = APP_DATA_DIR / 'userconf.yaml'


def get_windows_scale_factor():
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return dpi / 96.0
    except Exception:
        return 1.0


class RecogLine:
    def __init__(self, npimg: 'np.ndarray', idx: float, pred_char_cnt: int, pred_str: str = ''):
        self.npimg = npimg
        self.idx = idx
        self.pred_char_cnt = pred_char_cnt
        self.pred_str = pred_str

    def __lt__(self, other):
        return self.idx < other.idx


def process_cascade(alllineobj: RecogLine, recognizer30, recognizer50, recognizer100, is_cascade=True):
    targetdflist30, targetdflist50, targetdflist100 = [], [], []
    for lineobj in alllineobj:
        if lineobj.pred_char_cnt == 3 and is_cascade:
            targetdflist30.append(lineobj)
        elif lineobj.pred_char_cnt == 2 and is_cascade:
            targetdflist50.append(lineobj)
        else:
            targetdflist100.append(lineobj)
    targetdflistall = []
    with ThreadPoolExecutor(thread_name_prefix='recognizer') as executor:
        jobs30 = [(lineobj, executor.submit(recognizer30.read, lineobj.npimg)) for lineobj in targetdflist30]
        jobs50 = [(lineobj, executor.submit(recognizer50.read, lineobj.npimg)) for lineobj in targetdflist50]
        jobs100 = [(lineobj, executor.submit(recognizer100.read, lineobj.npimg)) for lineobj in targetdflist100]

        for lineobj, future in jobs30:
            pred_str = future.result()
            if len(pred_str) >= 25:
                jobs50.append((lineobj, executor.submit(recognizer50.read, lineobj.npimg)))
            else:
                lineobj.pred_str = pred_str
                targetdflistall.append(lineobj)

        for lineobj, future in jobs50:
            pred_str = future.result()
            if len(pred_str) >= 45:
                jobs100.append((lineobj, executor.submit(recognizer100.read, lineobj.npimg)))
            else:
                lineobj.pred_str = pred_str
                targetdflistall.append(lineobj)

        targetdflist200 = []
        for lineobj, future in jobs100:
            pred_str = future.result()
            lineobj.pred_str = pred_str
            if len(pred_str) >= 98 and lineobj.npimg.shape[0] < lineobj.npimg.shape[1]:
                baseimg = lineobj.npimg
                tmplineobj_1 = RecogLine(npimg=baseimg[:, :baseimg.shape[1] // 2, :], idx=lineobj.idx, pred_char_cnt=100)
                tmplineobj_2 = RecogLine(npimg=baseimg[:, baseimg.shape[1] // 2:, :], idx=lineobj.idx, pred_char_cnt=100)
                targetdflist200.append(tmplineobj_1)
                targetdflist200.append(tmplineobj_2)
            else:
                targetdflistall.append(lineobj)

        jobs200 = [(lineobj, executor.submit(recognizer100.read, lineobj.npimg)) for lineobj in targetdflist200]
        resultlines200 = [future.result() for _, future in jobs200]
        for i in range(0, len(targetdflist200) - 1, 2):
            ia = targetdflist200[i]
            lineobj = RecogLine(npimg=None, idx=ia.idx, pred_char_cnt=100, pred_str=resultlines200[i] + resultlines200[i + 1])
            targetdflistall.append(lineobj)

    targetdflistall = sorted(targetdflistall)
    return [t.pred_str for t in targetdflistall]


def load_ocr_models(args):
    """Create the four independent ONNX sessions concurrently."""
    load_runtime_modules()
    if hasattr(ocr, '_load_charlist'):
        ocr._load_charlist(os.path.abspath(args.rec_classes))
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='model-init') as executor:
        detector_future = executor.submit(ocr.get_detector, args)
        recognizer100_future = executor.submit(ocr.get_recognizer, args)
        recognizer30_future = executor.submit(ocr.get_recognizer, args, args.rec_weights30)
        recognizer50_future = executor.submit(ocr.get_recognizer, args, args.rec_weights50)
        return (
            detector_future.result(),
            recognizer100_future.result(),
            recognizer30_future.result(),
            recognizer50_future.result(),
        )


def load_rgb_image_array(input_path: str) -> 'np.ndarray':
    load_runtime_modules()
    with Image.open(input_path) as pil_image:
        return np.asarray(pil_image.convert('RGB'))


def render_pdf_page_array(pdf_path: str, page_index: int, scale: float) -> 'np.ndarray':
    """Render one PDF page without sharing PDFium objects between threads."""
    load_runtime_modules()
    pdf_doc = pypdfium2.PdfDocument(pdf_path)
    try:
        rendered_pages = pdf_doc.render(
            pypdfium2.PdfBitmap.to_pil,
            page_indices=[page_index],
            scale=scale,
        )
        return np.asarray(next(iter(rendered_pages)).convert('RGB'))
    finally:
        pdf_doc.close()


class ImageSelector:
    def __init__(self, page: ft.Page, config_obj: Dict, detector=None, recognizer30=None, recognizer50=None, recognizer100=None, table_feature=None, table_recognizer=None, outputdirpath=None, width: int = 600, height: int = 600):
        self.cnt = 0
        self.page = page
        self.config_obj = config_obj
        self.langcode = config_obj['langcode']
        self.inputpathlist = []
        self.outputdirpath = outputdirpath

        self.image_src = DEFAULT_IMAGE_SRC
        self.dialog_width = width
        self.dialog_height = height
        self.page_index = 0
        self.detector = detector
        self.recognizer30 = recognizer30
        self.recognizer50 = recognizer50
        self.recognizer100 = recognizer100
        self.table_feature = table_feature
        self.table_recognizer = table_recognizer

        self.start_x = 0
        self.start_y = 0

        self.selection_box = ft.Container(
            left=0,
            top=0,
            width=0,
            height=0,
            border=ft.Border.all(2, ft.Colors.BLUE),
            bgcolor=ft.Colors.TRANSPARENT,
        )

        self.overlay = ft.GestureDetector(
            content=ft.Container(
                width=self.dialog_width,
                height=self.dialog_height,
                bgcolor=ft.Colors.TRANSPARENT,
            ),
            on_pan_start=self.pan_start,
            on_pan_update=self.pan_update,
            on_pan_end=self.pan_end,
        )
        self.img = ft.Image(src=self.image_src, width=self.dialog_width, height=self.dialog_height, fit=ft.BoxFit.CONTAIN)
        self.imgzm = ft.Image(src=self.image_src, width=self.dialog_width, height=self.dialog_height, fit=ft.BoxFit.CONTAIN)
        self.image_stack = ft.Stack(
            width=self.dialog_width,
            height=self.dialog_height,
            controls=[
                self.img,
                self.selection_box,
                self.overlay,
            ],
        )
        self.cropocr_btn = ft.Button(TRANSLATIONS['imageselector_cropocr_btn'][self.langcode], on_click=self.crop_region)
        self.dialog = ft.AlertDialog(
            modal=True,
            content=self.image_stack,
            actions=[
                ft.Button(TRANSLATIONS['imageselector_zoom_btn'][self.langcode], icon=ft.Icons.ZOOM_IN, on_click=self.open_zoom_page),
                ft.Button(TRANSLATIONS['imageselector_prev_btn'][self.langcode], on_click=self.prev_page),
                ft.Button(TRANSLATIONS['imageselector_next_btn'][self.langcode], on_click=self.next_page),
                self.cropocr_btn,
                ft.Button(TRANSLATIONS['common_cancel'][self.langcode], on_click=self.close_dialog),
            ],
        )
        zoom_img = ft.InteractiveViewer(
            min_scale=1,
            max_scale=10,
            boundary_margin=ft.Margin.all(20),
            content=self.imgzm,
        )

        self.zoom_dialog = ft.AlertDialog(
            modal=True,
            content=zoom_img,
            actions=[
                ft.Button(TRANSLATIONS['common_cancel'][self.langcode], on_click=self.close_zoom_page),
            ],
        )
        self.resulttext = ft.Markdown(
            value='',
            selectable=True,
            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
            soft_line_break=True,
            visible=bool(self.config_obj.get('result_markdown_display', False)),
        )
        self.result_plain_text = ft.Text(
            value='',
            selectable=True,
            visible=not bool(self.config_obj.get('result_markdown_display', False)),
        )
        self.result_view_switch = ft.Switch(
            label='整形表示（Markdown）',
            value=bool(self.config_obj.get('result_markdown_display', False)),
            on_change=self.change_result_view_mode,
        )

        self.crop_image = ft.Image(src=self.image_src, width=300, height=300, fit=ft.BoxFit.CONTAIN)
        crop_image_col = ft.Column(
            controls=[self.crop_image],
            width=300,
            height=300,
            expand=False,
        )
        self.crop_image_int = ft.InteractiveViewer(
            min_scale=1,
            max_scale=5,
            boundary_margin=ft.Margin.all(20),
            content=crop_image_col,
        )
        self.result_text_col = ft.Column(
            controls=[self.result_view_switch, self.result_plain_text, self.resulttext],
            scroll=ft.ScrollMode.ALWAYS,
            width=800,
            height=300,
            expand=False,
        )

        self.result_dialog = ft.AlertDialog(
            title=ft.Text(TRANSLATIONS['imageselector_result_title'][self.langcode]),
            modal=True,
            content=ft.Row([self.crop_image_int, self.result_text_col]),
            actions=[
                ft.Button('OK', on_click=self.close_result_page),
            ],
        )

    def change_result_view_mode(self, e=None):
        use_markdown = bool(self.result_view_switch.value)
        self.config_obj['result_markdown_display'] = use_markdown
        self.resulttext.visible = use_markdown
        self.result_plain_text.visible = not use_markdown
        self.page.update()

    def open_result_page(self):
        if self.dialog.open:
            self.page.pop_dialog()
        self.page.show_dialog(self.result_dialog)

    def close_result_page(self, e):
        if self.result_dialog.open:
            self.page.pop_dialog()
        self.page.show_dialog(self.dialog)

    def set_image(self, inputpathlist):
        self.cnt = 0
        self.inputpathlist = inputpathlist
        self.page_index = 0
        if not inputpathlist:
            return
        self.image_src = inputpathlist[self.page_index]
        self.img.src = inputpathlist[self.page_index]
        self.imgzm.src = inputpathlist[self.page_index]
        self.page.update()

    def set_outputdir(self, outputdirpath):
        self.outputdirpath = outputdirpath

    def open_zoom_page(self, e):
        if self.dialog.open:
            self.page.pop_dialog()
        self.page.show_dialog(self.zoom_dialog)

    def close_zoom_page(self, e):
        if self.zoom_dialog.open:
            self.page.pop_dialog()
        self.page.show_dialog(self.dialog)

    def pan_start(self, e: ft.DragStartEvent):
        self.start_x = e.local_position.x
        self.start_y = e.local_position.y
        self.selection_box.left = self.start_x
        self.selection_box.top = self.start_y
        self.selection_box.width = 0
        self.selection_box.height = 0
        self.page.update()

    def pan_update(self, e: ft.DragUpdateEvent):
        cur_x, cur_y = e.local_position.x, e.local_position.y
        left = min(self.start_x, cur_x)
        top = min(self.start_y, cur_y)
        width = abs(cur_x - self.start_x)
        height = abs(cur_y - self.start_y)
        self.selection_box.left = left
        self.selection_box.top = top
        self.selection_box.width = width
        self.selection_box.height = height
        self.page.update()

    def pan_end(self, e: ft.DragEndEvent):
        self.page.update()

    def open_dialog(self, e):
        if not self.dialog.open:
            self.page.show_dialog(self.dialog)

    def prev_page(self, e):
        if not self.inputpathlist:
            return
        if self.page_index > 0:
            self.page_index -= 1
        else:
            self.page_index = len(self.inputpathlist) - 1
        self.img.src = self.inputpathlist[self.page_index]
        self.imgzm.src = self.inputpathlist[self.page_index]
        self.page.update()

    def next_page(self, e):
        if not self.inputpathlist:
            return
        if self.page_index < len(self.inputpathlist) - 1:
            self.page_index += 1
        else:
            self.page_index = 0
        self.img.src = self.inputpathlist[self.page_index]
        self.imgzm.src = self.inputpathlist[self.page_index]
        self.page.update()

    def crop_region(self, e):
        pilimg = Image.open(self.img.src)
        pilimg = pilimg.convert('RGB')
        rwidth, rheight = pilimg.size
        if rheight < rwidth:
            window_h = self.dialog_height * rheight / rwidth
            window_w = self.dialog_width
            offset_h = (window_w - window_h) / 2
            offset_w = 0
        else:
            window_h = self.dialog_height
            window_w = self.dialog_width * rwidth / rheight
            offset_w = (window_h - window_w) / 2
            offset_h = 0
        hratio = rheight / window_h
        wratio = rwidth / window_w
        cropx = int((self.selection_box.left - offset_w) * wratio)
        cropy = int((self.selection_box.top - offset_h) * hratio)
        cropw = int(self.selection_box.width * wratio)
        croph = int(self.selection_box.height * hratio)
        if cropx > 0 and cropy > 0 and cropw > 10 and croph > 0:
            im_crop = pilimg.crop((cropx, cropy, cropx + cropw, cropy + croph))
        else:
            return
        buff = BytesIO()
        im_crop.save(buff, 'png')
        self.crop_image.src = buff.getvalue()
        self.outputcroppedpath = os.path.join(PDFTMPPATH, os.path.splitext(os.path.basename(self.image_src))[0] + '_cropped_{}.jpg'.format(self.cnt))
        self.page.run_task(self.mini_ocr, im_crop, self.outputcroppedpath)
        self.cnt += 1
        self.page.update()

    async def mini_ocr(self, im_crop, outputcroppedpath=None):
        self.cropocr_btn.disabled = True
        self.page.update()
        inputname = os.path.basename(outputcroppedpath or self.outputcroppedpath)

        npimg = np.array(im_crop)
        try:
            page_result = await asyncio.to_thread(
                ocr._run_ocr_on_image_array,
                detector=self.detector,
                recognizer30=self.recognizer30,
                recognizer50=self.recognizer50,
                recognizer100=self.recognizer100,
                inputname=inputname,
                img=npimg,
                outputpath=self.outputdirpath or os.getcwd(),
                save_viz=False,
            )
            page_tables = []
            if (
                self.table_feature is not None
                and self.config_obj.get('table_structure', True)
                and self.table_recognizer is not None
            ):
                page_result, page_tables = await asyncio.to_thread(
                    self.table_feature.infer,
                    npimg,
                    page_result,
                    self.table_recognizer,
                )
            alltextlist = [page_result['text']]
            display_markdown = build_gui_result_markdown(page_result, page_tables)
            if page_result['line_count'] == 0 or (
                page_result['line_count'] > 0 and page_result['vertical_line_count'] / page_result['line_count'] > 0.5
            ):
                alltextlist = alltextlist[::-1]
            os.makedirs(self.outputdirpath or os.getcwd(), exist_ok=True)
            if page_tables:
                tables_path = os.path.join(
                    self.outputdirpath or os.getcwd(),
                    os.path.splitext(os.path.basename(inputname))[0] + '_tables.html',
                )
                with open(tables_path, 'w', encoding='utf-8') as wf:
                    wf.write(self.table_feature.build_html(inputname, page_tables))
            with open(os.path.join(self.outputdirpath or os.getcwd(), os.path.splitext(os.path.basename(inputname))[0] + '.txt'), 'w', encoding='utf-8') as wtf:
                wtf.write('\n'.join(alltextlist))
            self.result_plain_text.value = page_result['text']
            self.resulttext.value = display_markdown
        except Exception as ex:
            self.result_plain_text.value = f'エラーが発生しました: {ex}'
            self.resulttext.value = f'エラーが発生しました: {ex}'
        finally:
            self.cropocr_btn.disabled = False
            self.open_result_page()
            self.page.update()

    def close_dialog(self, e):
        if self.dialog.open:
            self.page.pop_dialog()


class CaptureTool:
    def __init__(self, page: ft.Page, config_obj: Dict, detector=None, recognizer30=None, recognizer50=None, recognizer100=None, table_feature=None, table_recognizer=None, width: int = 400, height: int = 300):
        self.page = page
        self.config_obj = config_obj
        self.langcode = config_obj['langcode']
        self.detector = detector
        self.recognizer30 = recognizer30
        self.recognizer50 = recognizer50
        self.recognizer100 = recognizer100
        self.table_feature = table_feature
        self.table_recognizer = table_recognizer
        self.dialog_width = width
        self.dialog_height = height
        self.im_crop = None
        self.img_str = ''
        self.result_jsonstr = ''
        self.outputdirpath = os.getcwd()
        self.scale_factor = get_windows_scale_factor()
        self.start_x = 0
        self.start_y = 0
        self.current_x = 0
        self.current_y = 0

        self.original_width = 0
        self.original_height = 0
        self.original_left = 0
        self.original_top = 0
        self.original_bgcolor = None

        self.selection_box = ft.Container(
            border=ft.Border.all(2, ft.Colors.RED),
            bgcolor=ft.Colors.with_opacity(0.2, ft.Colors.RED),
            visible=False,
        )
        self.img_control = ft.Image(
            src=b'',
            width=self.dialog_width,
            height=self.dialog_height,
            fit=ft.BoxFit.CONTAIN,
            gapless_playback=True,
        )
        self.retry_btn = ft.Button(TRANSLATIONS['capturetool_retry_btn'][self.langcode], on_click=self.start_capture)
        self.cboc_fixed = ft.Checkbox(label=TRANSLATIONS['capturetool_fixregion'][self.langcode], value=False, disabled=True)
        self.cb_table_structure = (
            self.table_feature.create_capture_control(self.config_obj)
            if self.table_feature is not None
            else None
        )
        self.ocr_btn = ft.Button(TRANSLATIONS['capturetool_ocr_button'][self.langcode], on_click=self.mini_ocr)

        self.errorlog = ft.Text('')
        self.dialog_content = ft.Column(
            controls=[
                self.errorlog,
                ft.Container(
                    content=self.img_control,
                    border=ft.Border.all(1, ft.Colors.GREY),
                    alignment=ft.Alignment.CENTER,
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        )

        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(TRANSLATIONS['capturetool_result_title'][self.langcode]),
            content=self.dialog_content,
            actions=[
                ft.Row([
                    self.retry_btn,
                    ft.Column(
                        controls=[
                            self.cboc_fixed,
                            *(
                                [self.cb_table_structure]
                                if self.cb_table_structure is not None
                                else []
                            ),
                        ],
                        spacing=0,
                        tight=True,
                    ),
                    self.ocr_btn,
                    ft.Button(TRANSLATIONS['common_close'][self.langcode], on_click=self.close_dialog),
                ]),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.resulttext = ft.Markdown(
            value='',
            selectable=True,
            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
            soft_line_break=True,
            visible=bool(self.config_obj.get('result_markdown_display', False)),
        )
        self.result_plain_text = ft.Text(
            value='',
            selectable=True,
            visible=not bool(self.config_obj.get('result_markdown_display', False)),
        )
        self.result_view_switch = ft.Switch(
            label='整形表示（Markdown）',
            value=bool(self.config_obj.get('result_markdown_display', False)),
            on_change=self.change_result_view_mode,
        )
        self.resultsmessage = ft.Text(value='', selectable=True)
        self.result_crop_image = ft.Image(src=b'', width=300, height=300, fit=ft.BoxFit.CONTAIN)

        self.crop_image_int = ft.InteractiveViewer(
            min_scale=1,
            max_scale=5,
            boundary_margin=ft.Margin.all(20),
            content=ft.Column([self.result_crop_image], width=300, height=300),
        )

        self.result_text_col = ft.Column(
            controls=[self.result_view_switch, self.result_plain_text, self.resulttext],
            scroll=ft.ScrollMode.ALWAYS,
            width=600,
            height=300,
        )
        self.result_dialog = ft.AlertDialog(
            title=ft.Text(TRANSLATIONS['capturetool_resultocr_title'][self.langcode]),
            modal=True,
            content=ft.Row(
                controls=[self.crop_image_int, self.result_text_col],
                alignment=ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            actions=[
                self.resultsmessage,
                ft.Button(TRANSLATIONS['common_close'][self.langcode], on_click=self.close_result_page),
            ],
        )
        self.overlay_stack = ft.Stack(
            controls=[
                ft.GestureDetector(
                    on_pan_start=self._on_pan_start,
                    on_pan_update=self._on_pan_update,
                    on_pan_end=self._on_pan_end,
                    drag_interval=10,
                ),
                self.selection_box,
            ],
            expand=True,
            visible=False,
        )

        self.page.overlay.append(self.overlay_stack)

    def start_capture(self, e=None):
        if self.dialog.open:
            self.close_dialog(e)
        if self.cboc_fixed.value:
            self._capture_and_restore(self.x1_phys, self.y1_phys, self.x2_phys, self.y2_phys)
            return
        self.scale_factor = get_windows_scale_factor()
        self.original_width = self.page.window.width
        self.original_height = self.page.window.height
        self.original_left = self.page.window.left
        self.original_top = self.page.window.top
        self.original_bgcolor = self.page.bgcolor

        self.page.window.maximized = True
        self.page.window.title_bar_hidden = True
        self.page.window.title_bar_buttons_hidden = True
        self.page.window.always_on_top = True
        self.page.window.opacity = 0.3
        self.page.window.bgcolor = ft.Colors.TRANSPARENT
        self.page.bgcolor = ft.Colors.with_opacity(0.3, ft.Colors.BLACK)

        self.overlay_stack.visible = True
        self.page.update()

    def _on_pan_start(self, e: ft.DragStartEvent):
        self.start_x = e.local_position.x
        self.start_y = e.local_position.y
        self.current_x = e.local_position.x
        self.current_y = e.local_position.y
        self.selection_box.visible = True
        self.selection_box.left = self.start_x
        self.selection_box.top = self.start_y
        self.selection_box.width = 0
        self.selection_box.height = 0
        self.page.update()

    def _on_pan_update(self, e: ft.DragUpdateEvent):
        self.current_x = e.local_position.x
        self.current_y = e.local_position.y

        left = min(self.start_x, self.current_x)
        top = min(self.start_y, self.current_y)
        width = abs(self.current_x - self.start_x)
        height = abs(self.current_y - self.start_y)

        self.selection_box.left = left
        self.selection_box.top = top
        self.selection_box.width = width
        self.selection_box.height = height
        self.page.update()

    def _on_pan_end(self, e: ft.DragEndEvent):
        x1_local = min(self.start_x, self.current_x)
        y1_local = min(self.start_y, self.current_y)
        x2_local = max(self.start_x, self.current_x)
        y2_local = max(self.start_y, self.current_y)

        offset_x = self.page.window.left or 0
        offset_y = self.page.window.top or 0

        x1_global = x1_local + offset_x
        y1_global = y1_local + offset_y
        x2_global = x2_local + offset_x
        y2_global = y2_local + offset_y

        self.x1_phys = int(x1_global * self.scale_factor)
        self.y1_phys = int(y1_global * self.scale_factor)
        self.x2_phys = int(x2_global * self.scale_factor)
        self.y2_phys = int(y2_global * self.scale_factor)

        self._capture_and_restore(self.x1_phys, self.y1_phys, self.x2_phys, self.y2_phys)

    def _capture_and_restore(self, x1, y1, x2, y2):
        self.page.window.opacity = 0
        self.page.update()
        time.sleep(0.2)

        if (x2 - x1) > 5 and (y2 - y1) > 5:
            try:
                self.im_crop = ImageGrab.grab(bbox=(x1, y1, x2, y2)).convert('RGB')
                self.cboc_fixed.disabled = False
                buffered = io.BytesIO()
                self.im_crop.save(buffered, format='png')
                image_bytes = buffered.getvalue()
                self.img_control.src = image_bytes
                self.result_crop_image.src = image_bytes
                self.page.show_dialog(self.dialog)
            except Exception as ex:
                print(f'Capture failed: {ex}')

        self.overlay_stack.visible = False
        self.selection_box.visible = False

        self.page.window.opacity = 1
        self.page.window.maximized = False
        self.page.window.title_bar_hidden = False
        self.page.window.title_bar_buttons_hidden = False
        self.page.window.always_on_top = False
        self.page.window.bgcolor = ft.Colors.WHITE
        self.page.bgcolor = self.original_bgcolor
        self.page.update()
        time.sleep(0.2)

        self.page.window.width = self.original_width
        self.page.window.height = self.original_height
        self.page.window.left = self.original_left
        self.page.window.top = self.original_top
        self.page.update()

    def change_result_view_mode(self, e=None):
        use_markdown = bool(self.result_view_switch.value)
        self.config_obj['result_markdown_display'] = use_markdown
        self.resulttext.visible = use_markdown
        self.result_plain_text.visible = not use_markdown
        self.page.update()

    async def mini_ocr(self, e):
        if self.im_crop is None:
            return
        self.ocr_btn.disabled = True
        self.resultsmessage.value = ''
        self.page.update()
        try:
            allstart = time.time()
            filename_base = 'captureimg'
            npimg = np.array(self.im_crop)

            page_result = await asyncio.to_thread(
                ocr._run_ocr_on_image_array,
                detector=self.detector,
                recognizer30=self.recognizer30,
                recognizer50=self.recognizer50,
                recognizer100=self.recognizer100,
                inputname=filename_base,
                img=npimg,
                outputpath=self.outputdirpath,
                save_viz=False,
            )
            page_tables = []
            if (
                self.table_feature is not None
                and self.cb_table_structure is not None
                and self.cb_table_structure.value
                and self.table_recognizer is not None
            ):
                page_result, page_tables = await asyncio.to_thread(
                    self.table_feature.infer,
                    npimg,
                    page_result,
                    self.table_recognizer,
                )

            final_text = build_gui_result_markdown(page_result, page_tables)
            self.resultsmessage.value = '{:.2f} sec'.format(time.time() - allstart)
            self.result_plain_text.value = page_result['text']
            self.resulttext.value = final_text
            self.result_jsonstr = json.dumps(page_result['json_lines'], ensure_ascii=False)
            self.open_result_page()

        except Exception as e:
            print(f'OCR Error: {e}')
            self.result_plain_text.value = f'エラーが発生しました: {e}'
            self.resulttext.value = f'エラーが発生しました: {e}'
            self.open_result_page()
        finally:
            self.ocr_btn.disabled = False
            self.page.update()

    def open_dialog(self, e=None):
        self.start_capture()

    def close_dialog(self, e):
        if self.dialog.open:
            self.page.pop_dialog()

    def open_result_page(self):
        if self.dialog.open:
            self.page.pop_dialog()
        self.page.show_dialog(self.result_dialog)

    def close_result_page(self, e):
        if self.result_dialog.open:
            self.page.pop_dialog()
        self.page.show_dialog(self.dialog)

def main(page: ft.Page):
    parser = argparse.ArgumentParser(description='Argument for Inference using ONNXRuntime')
    parser.add_argument('--det-weights', type=str, required=False, help='Path to rtmdet onnx file', default=str(SOURCE_DIR / 'model' / 'deim-s-1024x1024.onnx'))
    parser.add_argument('--det-classes', type=str, required=False, help='Path to list of class in yaml file', default=str(SOURCE_DIR / 'config' / 'ndl.yaml'))
    parser.add_argument('--det-score-threshold', type=float, required=False, default=0.2)
    parser.add_argument('--det-conf-threshold', type=float, required=False, default=0.25)
    parser.add_argument('--det-iou-threshold', type=float, required=False, default=0.2)
    parser.add_argument('--rec-weights30', type=str, required=False, help='Path to parseq-tiny onnx file', default=str(SOURCE_DIR / 'model' / 'parseq-ndl-24x256-30-tiny-189epoch-tegaki3-r8data-202604.onnx'))
    parser.add_argument('--rec-weights50', type=str, required=False, help='Path to parseq-tiny onnx file', default=str(SOURCE_DIR / 'model' / 'parseq-ndl-24x384-50-tiny-300epoch-tegaki3-r8data-202604.onnx'))
    parser.add_argument('--rec-weights', type=str, required=False, help='Path to parseq-tiny onnx file', default=str(SOURCE_DIR / 'model' / 'parseq-ndl-24x768-100-tiny-153epoch-tegaki3-r8data-202604.onnx'))
    parser.add_argument('--rec-classes', type=str, required=False, help='Path to list of class in yaml file', default=str(SOURCE_DIR / 'config' / 'NDLmoji.yaml'))
    parser.add_argument('--device', type=str, required=False, help='Device use (cpu or cuda)', choices=['cpu', 'cuda'], default='cpu')
    if TABLE_STRUCTURE_FEATURE is not None:
        TABLE_STRUCTURE_FEATURE.add_arguments(parser, SOURCE_DIR)
    # Flet and test runners may append their own process arguments. Keep the
    # GUI's OCR options while leaving unrelated host arguments untouched.
    args, _ = parser.parse_known_args()

    page.title = 'NDLOCR-Lite-GUI v{}'.format(__version__)
    page.theme_mode = ft.ThemeMode.SYSTEM
    page.window.icon = str(ASSETS_DIR / 'icon.png')
    page.window.width = 1024
    page.window.height = 900
    page.window.min_width = 1024
    page.window.min_height = 900
    default_config = build_default_config()
    for feature in (DATA_POOL_FEATURE, LLM_FEATURE, TABLE_STRUCTURE_FEATURE):
        if feature is not None:
            default_config.update(feature.default_config())
    load_obj = {}
    if USER_CONFIG_PATH.exists():
        with USER_CONFIG_PATH.open(encoding='utf-8') as f:
            load_obj = yaml.safe_load(f)
        if load_obj is None:
            load_obj = {}

    config_obj = default_config | load_obj

    page.locale_configuration = ft.LocaleConfiguration(
        supported_locales=[
            ft.Locale('ja', 'JP'),
            ft.Locale('en', 'US'),
        ],
        current_locale=ft.Locale('ja', 'JP') if config_obj['langcode'] == 'ja' else ft.Locale('en', 'US'),
    )

    def save_config():
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with USER_CONFIG_PATH.open('w', encoding='utf-8') as wf:
            yaml.dump(config_obj, wf, default_flow_style=False, allow_unicode=True)

    def handle_locale_change(e):
        index = e.control.selected_index
        if index == 0:
            page.locale_configuration.current_locale = ft.Locale('ja', 'JP')
        elif index == 1:
            page.locale_configuration.current_locale = ft.Locale('en', 'US')
        config_obj['langcode'] = page.locale_configuration.current_locale.language_code
        save_config()
        page.update()
        renderui()

    origin_detector = None
    origin_recognizer = None
    origin_recognizer30 = None
    origin_recognizer50 = None
    origin_table_recognizer = None
    model_state = {'loading': False, 'error': None, 'table_error': None, 'elapsed': None}

    def renderui():
        page.clean()
        page.overlay.clear()
        page.services.clear()
        page.update()
        inputpathlist = []
        visualizepathlist = []
        outputtxtlist = []
        outputplaintextlist = []
        ocr_cancel_event=threading.Event()
        pdf_job_list = []

        def create_pdf_func(outputpath: str, img: object, bboxlistobj: list, viztxtflag: bool, resolution: int = 300):
            from reportlab.pdfgen import canvas
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            from reportlab.lib.utils import ImageReader
            from reportlab.lib.colors import blue

            img_h, img_w = img.shape[:2]
            dpi = max(float(resolution), 1.0)
            page_w = img_w * 72.0 / dpi
            page_h = img_h * 72.0 / dpi

            pdfmetrics.registerFont(UnicodeCIDFont('HeiseiMin-W3', isVertical=True))
            pdfmetrics.registerFont(UnicodeCIDFont('HeiseiKakuGo-W5', isVertical=False))

            c = canvas.Canvas(outputpath, pagesize=(page_w, page_h))

            pilimg_data = io.BytesIO()
            pilimg=Image.fromarray(img)
            if pilimg.mode in ("RGBA", "LA", "P"):
                pilimg = pilimg.convert("RGB")
            #Image.fromarray(img).save(pilimg_data, format='PNG')
            try:
                pilimg.save(pilimg_data, format='JPEG')
            except:
                pilimg.save(pilimg_data, format='PNG')
            pilimg_data.seek(0)

            c.drawImage(
                ImageReader(pilimg_data),
                0,
                0,
                width=page_w,
                height=page_h,
                preserveAspectRatio=False,
                mask=None,
            )

            if viztxtflag:
                c.setFillAlpha(1)
                c.setFillColor(blue)
            else:
                c.setFillAlpha(0)

            for bboxobj in bboxlistobj:
                text = bboxobj.get('text', '')
                if not text:
                    continue
                bbox = bboxobj['boundingBox']
                xmin = bbox[0][0]
                ymin = bbox[0][1]
                line_w = bbox[2][0] - bbox[0][0]
                line_h = bbox[1][1] - bbox[0][1]
                line = {
                    'x': xmin,
                    'y': ymin,
                    'width': line_w,
                    'height': line_h,
                    'text': text,
                    'is_vertical': line_h > line_w,
                }
                ocr._draw_text_layer_line(
                    canvas_obj=c,
                    line=line,
                    img_width=img_w,
                    img_height=img_h,
                    page_width=page_w,
                    page_height=page_h,
                    visible=viztxtflag,
                )
            c.save()

        def is_pdf_tmp_path(path: str) -> bool:
            try:
                tmp_dir = os.path.abspath(PDFTMPPATH)
                return os.path.abspath(path).startswith(tmp_dir + os.sep)
            except Exception:
                return False

        def render_pdf_preview(pdf_path: str, filestem: str):
            load_runtime_modules()
            os.makedirs(PDFTMPPATH, exist_ok=True)
            doc = pypdfium2.PdfDocument(pdf_path)
            try:
                pdfarray = doc.render(
                    pypdfium2.PdfBitmap.to_pil,
                    page_indices=[i for i in range(len(doc))],
                    scale=100 / 72,
                )
                for ix, image in enumerate(list(pdfarray)):
                    outputtmppath = os.path.join(PDFTMPPATH, '{}_{:05}.jpg'.format(filestem, ix))
                    inputpathlist.append(outputtmppath)
                    image = image.convert('RGB')
                    image.save(outputtmppath)
            finally:
                doc.close()

        def models_are_ready():
            return all((
                origin_detector,
                origin_recognizer,
                origin_recognizer30,
                origin_recognizer50,
            ))

        def parts_control(flag:bool,allow_ocr_cancel:bool=False):
            file_upload_btn.disabled = flag
            directory_upload_btn.disabled = flag
            directory_output_btn.disabled = flag
            chkbx_visualize.disabled = flag
            customize_btn.disabled = flag
            preview_prev_btn.disabled = flag or not outputtxtlist
            preview_next_btn.disabled = flag or not outputtxtlist
            has_input = bool(inputpathlist or pdf_job_list)
            has_output = bool(selected_output_path.value)
            ocr_btn.disabled = flag or not (models_are_ready() and has_input and has_output)
            crop_btn.disabled = flag or not (models_are_ready() and inputpathlist and has_output)
            cap_btn.disabled = flag or not models_are_ready()
            localebutton.disabled = flag or model_state['loading']
            stop_ocr_btn.disabled=not (flag and allow_ocr_cancel)
            if chkbx_table_structure is not None:
                chkbx_table_structure.disabled = flag or origin_table_recognizer is None

        async def process_pdf_with_original_layer(pdf_path: str, outputpath: str, alljsonobjlist: list, pdf_outpath_list: list, allsum: int):
            pdf_path_obj = Path(pdf_path)
            output_stem = pdf_path_obj.stem
            render_dpi = max(float(pdf_resolution_textfield.value), 1.0)
            render_scale = render_dpi / 72.0

            pdf_doc = pypdfium2.PdfDocument(str(pdf_path_obj))
            page_results = []
            all_json_contents = []
            page_infos = []
            all_text_pages = []
            all_markdown_pages = []
            all_page_xml = []
            all_table_results = []
            tei_jsonobjlist = []

            try:
                page_count = len(pdf_doc)
                separate_page_outputs = should_output_pdf_pages_separately(
                    page_count,
                    config_obj.get('pdf_page_output', True),
                )
                for page_index in range(page_count):
                    if ocr_cancel_event.is_set():
                        return page_index, False, tei_jsonobjlist
                    page_name = f'{output_stem}_{page_index + 1:05}.png'
                    progressmessage.value = f'{pdf_path_obj.name} page {page_index + 1}/{page_count}'
                    progressmessage.update()

                    img = await asyncio.to_thread(
                        render_pdf_page_array,
                        str(pdf_path_obj),
                        page_index,
                        render_scale,
                    )

                    page_result = await asyncio.to_thread(
                        ocr._run_ocr_on_image_array,
                        detector=origin_detector,
                        recognizer30=origin_recognizer30,
                        recognizer50=origin_recognizer50,
                        recognizer100=origin_recognizer,
                        inputname=page_name,
                        img=img,
                        outputpath=outputpath,
                        save_viz=False,
                    )

                    page_tables = []
                    if (
                        TABLE_STRUCTURE_FEATURE is not None
                        and chkbx_table_structure is not None
                        and chkbx_table_structure.value
                        and origin_table_recognizer is not None
                    ):
                        page_result, page_tables = await asyncio.to_thread(
                            TABLE_STRUCTURE_FEATURE.infer,
                            img,
                            page_result,
                            origin_table_recognizer,
                            args.table_crop_padding,
                        )
                        for table_item in page_tables:
                            table_item['page_index'] = page_index
                        all_table_results.extend(page_tables)

                    page_results.append(page_result)
                    all_json_contents.append(page_result['json_lines'])
                    page_infos.append({
                        'page_index': page_index,
                        'img_width': page_result['img_width'],
                        'img_height': page_result['img_height'],
                        'img_name': page_result['img_name'],
                    })
                    all_text_pages.append(page_result['text'])
                    page_markdown = build_gui_result_markdown(page_result, page_tables)
                    all_markdown_pages.append(page_markdown)
                    outputplaintextlist.append(page_result['text'])
                    outputtxtlist.append(page_markdown)
                    all_page_xml.append(page_result['page_xml'])

                    page_json_obj = write_pdf_page_outputs(
                        outputpath,
                        output_stem,
                        page_index,
                        page_result,
                        source_path=str(pdf_path_obj),
                        write_json=separate_page_outputs and chkbx_json.value,
                        write_xml=separate_page_outputs and chkbx_xml.value,
                        write_txt=separate_page_outputs and chkbx_txt.value,
                        write_tei=separate_page_outputs and chkbx_tei.value,
                        convert_tei_func=convert_tei if chkbx_tei.value else None,
                        include_xml_in_json=True,
                    )
                    alljsonobjlist.append(page_json_obj)
                    if not separate_page_outputs:
                        tei_jsonobjlist.append(page_json_obj)

                    if chkbx_visualize.value:
                        viz_path = os.path.join(outputpath, f'viz_{page_name}')
                        await asyncio.to_thread(
                            origin_detector.drawxml_detections,
                            npimg=img,
                            xmlstr='<OCRDATASET>\n' + page_result['page_xml'] + '\n</OCRDATASET>',
                            categories=categories_org_name_index,
                            outputimgpath=viz_path,
                        )
                        if os.path.exists(viz_path):
                            visualizepathlist.append(viz_path)

                    progressbar.value += 1 / max(allsum, 1)
                    page.update()
            finally:
                pdf_doc.close()

            if chkbx_xml.value and not separate_page_outputs:
                with open(os.path.join(outputpath, output_stem + '.xml'), 'w', encoding='utf-8') as wf:
                    wf.write('<OCRDATASET>\n')
                    wf.write('\n'.join(all_page_xml))
                    wf.write('\n</OCRDATASET>')

            if chkbx_txt.value and not separate_page_outputs:
                with open(os.path.join(outputpath, output_stem + '.txt'), 'w', encoding='utf-8') as wf:
                    wf.write('\n\n'.join(all_text_pages))

            if chkbx_markdown.value:
                with open(os.path.join(outputpath, output_stem + '.md'), 'w', encoding='utf-8') as wf:
                    wf.write('\n\n---\n\n'.join(all_markdown_pages))

            pdf_json_obj = {
                'contents': all_json_contents,
                'pdfinfo': {
                    'pdf_path': str(pdf_path_obj),
                    'pdf_name': pdf_path_obj.name,
                    'page_count': len(page_results),
                    'render_dpi': render_dpi,
                },
                'pages': page_infos,
            }

            if chkbx_json.value and not separate_page_outputs:
                with open(os.path.join(outputpath, output_stem + '.json'), 'w', encoding='utf-8') as wf:
                    wf.write(json.dumps(pdf_json_obj, ensure_ascii=False, indent=2))

            if all_table_results:
                tables_html_path = os.path.join(outputpath, output_stem + '_tables.html')
                tables_html = TABLE_STRUCTURE_FEATURE.build_html(
                    pdf_path_obj.name,
                    all_table_results,
                )
                with open(tables_html_path, 'w', encoding='utf-8') as wf:
                    wf.write(tables_html)

            if chkbx_pdf.value:
                output_pdf = os.path.join(outputpath, output_stem + '_text.pdf')
                ocr.embed_text_layer_pdf(
                    input_pdf=str(pdf_path_obj),
                    output_pdf=output_pdf,
                    page_results=page_results,
                    visible_text=chkbx_pdf_viztxt.value,
                )
                pdf_outpath_list.append(output_pdf)

            return page_count, True, tei_jsonobjlist

        def request_ocr_cancel(e):
            if ocr_cancel_event.is_set():
                return
            ocr_cancel_event.set()
            stop_ocr_btn.disabled=True
            progressmessage.value=TRANSLATIONS["main_ocr_cancel_requested"][config_obj["langcode"]]
            page.update()

        async def ocr_button_result(e):
            ocr_cancel_event.clear()
            progressbar.value = 0
            outputpath = selected_output_path.value
            nonlocal inputpathlist, outputtxtlist, visualizepathlist, preview_index, args
            nonlocal origin_recognizer, origin_recognizer30, origin_recognizer50
            nonlocal origin_detector

            if not outputpath:
                progressmessage.value = 'Output directory is not selected.'
                progressmessage.update()
                return

            preview_index = 0
            parts_control(True,allow_ocr_cancel=True)
            page.update()
            progressmessage.value = 'Start'
            progressmessage.update()

            try:
                allstart = time.time()
                progressbar.value = 0
                progressbar.update()
                outputtxtlist.clear()
                outputplaintextlist.clear()
                visualizepathlist.clear()
                alljsonobjlist = []
                tei_jsonobjlist = []
                pdf_outpath_list = []
                completed_count=0
                cancelled=False
                standalone_inputpathlist = [p for p in inputpathlist if not is_pdf_tmp_path(p)]

                allsum = len(standalone_inputpathlist)
                for pdf_path in pdf_job_list:
                    try:
                        pdf_doc_tmp = pypdfium2.PdfDocument(pdf_path)
                        allsum += len(pdf_doc_tmp)
                        pdf_doc_tmp.close()
                    except Exception:
                        allsum += 1

                if allsum == 0:
                    progressmessage.value = 'Images are not found.'
                    progressmessage.update()
                    return

                for pdf_path in pdf_job_list:
                    processed_pages, pdf_completed, pdf_tei_jsonobjlist = await process_pdf_with_original_layer(
                        pdf_path,
                        outputpath,
                        alljsonobjlist,
                        pdf_outpath_list,
                        allsum,
                    )
                    tei_jsonobjlist.extend(pdf_tei_jsonobjlist)
                    completed_count += processed_pages
                    if not pdf_completed:
                        cancelled = True
                        break

                for idx, inputpath in enumerate(standalone_inputpathlist):
                    if ocr_cancel_event.is_set():
                        cancelled=True
                        break
                    progressmessage.value = inputpath
                    progressmessage.update()
                    img = await asyncio.to_thread(load_rgb_image_array, inputpath)
                    start = time.time()
                    img_h, img_w = img.shape[:2]
                    imgname = os.path.basename(inputpath)

                    page_result = await asyncio.to_thread(
                        ocr._run_ocr_on_image_array,
                        detector=origin_detector,
                        recognizer30=origin_recognizer30,
                        recognizer50=origin_recognizer50,
                        recognizer100=origin_recognizer,
                        inputname=imgname,
                        img=img,
                        outputpath=outputpath,
                        save_viz=False,
                    )

                    page_tables = []
                    if (
                        TABLE_STRUCTURE_FEATURE is not None
                        and chkbx_table_structure is not None
                        and chkbx_table_structure.value
                        and origin_table_recognizer is not None
                    ):
                        page_result, page_tables = await asyncio.to_thread(
                            TABLE_STRUCTURE_FEATURE.infer,
                            img,
                            page_result,
                            origin_table_recognizer,
                            args.table_crop_padding,
                        )

                    allxmlstr = '<OCRDATASET>\n' + page_result['page_xml'] + '\n</OCRDATASET>'
                    alltextlist = [page_result['text']]
                    resjsonarray = page_result['json_lines']
                    page_markdown = build_gui_result_markdown(page_result, page_tables)
                    outputplaintextlist.append(page_result['text'])
                    outputtxtlist.append(page_markdown)

                    alljsonobj = {
                        'contents': [resjsonarray],
                        'imginfo': {
                            'img_width': img_w,
                            'img_height': img_h,
                            'img_path': inputpath,
                            'img_name': os.path.basename(inputpath),
                        },
                        'xml': allxmlstr,
                    }
                    alljsonobjlist.append(alljsonobj)
                    tei_jsonobjlist.append(alljsonobj)

                    output_stem = os.path.splitext(os.path.basename(inputpath))[0]
                    if page_tables:
                        tables_html_path = os.path.join(outputpath, output_stem + '_tables.html')
                        tables_html = TABLE_STRUCTURE_FEATURE.build_html(
                            os.path.basename(inputpath),
                            page_tables,
                        )
                        with open(tables_html_path, 'w', encoding='utf-8') as wf:
                            wf.write(tables_html)

                    if chkbx_xml.value:
                        with open(os.path.join(outputpath, output_stem + '.xml'), 'w', encoding='utf-8') as wf:
                            wf.write(allxmlstr)

                    if chkbx_visualize.value:
                        output_vizpath = os.path.join(outputpath, 'viz_' + os.path.basename(inputpath))
                        if output_vizpath.split('.')[-1] == 'jp2':
                            output_vizpath = output_vizpath[:-4] + '.jpg'
                        visualizepathlist.append(output_vizpath)
                        await asyncio.to_thread(
                            origin_detector.drawxml_detections,
                            npimg=img,
                            xmlstr=allxmlstr,
                            categories=categories_org_name_index,
                            outputimgpath=output_vizpath,
                        )

                    if chkbx_json.value:
                        with open(os.path.join(outputpath, output_stem + '.json'), 'w', encoding='utf-8') as wf:
                            wf.write(json.dumps(alljsonobj, ensure_ascii=False, indent=2))

                    if chkbx_txt.value:
                        with open(os.path.join(outputpath, output_stem + '.txt'), 'w', encoding='utf-8') as wtf:
                            wtf.write('\n'.join(alltextlist))

                    if chkbx_markdown.value:
                        with open(os.path.join(outputpath, output_stem + '.md'), 'w', encoding='utf-8') as wf:
                            wf.write(page_markdown)

                    if chkbx_pdf.value:
                        pdf_outpath = os.path.join(outputpath, output_stem + '.pdf')
                        await asyncio.to_thread(
                            create_pdf_func,
                            pdf_outpath,
                            img,
                            resjsonarray,
                            chkbx_pdf_viztxt.value,
                            resolution=int(pdf_resolution_textfield.value),
                        )
                        pdf_outpath_list.append(pdf_outpath)

                    print('Total calculation time (Detection + Recognition):', time.time() - start)
                    completed_count+=1
                    progressbar.value += 1 / max(allsum, 1)
                    preview_prev_btn.disabled = False
                    preview_next_btn.disabled = False
                    if outputtxtlist:
                        preview_plain_text.value = outputplaintextlist[preview_index] if preview_index < len(outputplaintextlist) else ''
                        preview_text.value = outputtxtlist[preview_index]
                    if len(visualizepathlist) > 0:
                        preview_image.src = visualizepathlist[min(preview_index, len(visualizepathlist) - 1)]
                    elif inputpathlist:
                        preview_image.src = inputpathlist[min(preview_index, len(inputpathlist) - 1)]
                    if inputpathlist:
                        current_visualizeimgname.value = os.path.basename(inputpathlist[min(preview_index, len(inputpathlist) - 1)])
                    preview_image.update()
                    page.update()
                    if cancelled:
                        break
                if cancelled:
                    progressmessage.value=TRANSLATIONS["main_ocr_cancelled"][config_obj["langcode"]].format(
                        completed=completed_count,total=allsum,elapsed=time.time()-allstart
                    )
                elif config_obj['langcode'] == 'ja':
                    progressmessage.value = '{} 件OCR完了 / 所要時間 {:.2f} 秒'.format(allsum, time.time() - allstart)
                else:
                    progressmessage.value = '{} items completed / Total time {:.2f} sec'.format(allsum, time.time() - allstart)
                progressmessage.update()

                if chkbx_tei.value and len(tei_jsonobjlist)>0:
                    tei_base_path = inputpathlist[0] if inputpathlist else tei_jsonobjlist[0]['imginfo']['img_name']
                    with open(os.path.join(outputpath, os.path.splitext(os.path.basename(tei_base_path))[0] + '_tei.xml'), 'wb') as wf:
                        allxmlstrtei = convert_tei(tei_jsonobjlist)
                        wf.write(allxmlstrtei)

                if chkbx_text_daisy.value and alljsonobjlist:
                    first_name = alljsonobjlist[0].get('imginfo', {}).get('img_name', 'ocr')
                    book_stem = os.path.splitext(os.path.basename(first_name))[0] or 'ocr'
                    text_daisy_result = TextDaisyExporter(
                        title=book_stem,
                        language=config_obj['langcode'],
                        producer='NDLOCR-Lite',
                    ).export(
                        alljsonobjlist,
                        outputpath,
                        folder_name=book_stem + '_textdaisy',
                        make_zip=True,
                    )
                    daisy_path = text_daisy_result.zip_path or text_daisy_result.directory
                    progressmessage.value += f' / Text DAISY: {os.path.basename(daisy_path)}'

                if chkbx_pdf.value and chkbx_pdf_merge.value and len(pdf_outpath_list) > 1:
                    from pypdf import PdfReader, PdfWriter

                    writer = PdfWriter()
                    for p in pdf_outpath_list:
                        reader = PdfReader(p)
                        for page_obj in reader.pages:
                            writer.add_page(page_obj)

                    merged_pdf_path = os.path.join(
                        outputpath,
                        os.path.splitext(os.path.basename(inputpathlist[0]))[0] + '_merged.pdf',
                    )
                    with open(merged_pdf_path, 'wb') as wf:
                        writer.write(wf)

                    for p in pdf_outpath_list:
                        try:
                            os.remove(p)
                        except OSError:
                            pass

                if outputtxtlist:
                    preview_plain_text.value = outputplaintextlist[0] if outputplaintextlist else ''
                    preview_text.value = outputtxtlist[0]
                if len(visualizepathlist) > 0:
                    preview_image.src = visualizepathlist[0]
                    current_visualizeimgname.value = os.path.basename(visualizepathlist[0])
                elif inputpathlist:
                    preview_image.src = inputpathlist[0]
                    current_visualizeimgname.value = os.path.basename(inputpathlist[0])
                preview_image.update()
                preview_plain_text.update()
                preview_text.update()

            except Exception as e:
                print(e)
                progressmessage.value = str(e)
                progressmessage.update()
            finally:
                ocr_cancel_event.clear()
                parts_control(False)
                page.update()

        async def pick_files_result(e):
            files = await file_picker.pick_files(allow_multiple=False)
            if files and files[0].path:
                selected_file_path = files[0].path
                selected_input_path.value = selected_file_path
                nonlocal inputpathlist, outputtxtlist, pdf_job_list
                inputpathlist.clear()
                outputtxtlist.clear()
                outputplaintextlist.clear()
                pdf_job_list.clear()
                ext = selected_file_path.split('.')[-1].lower()
                if ext == 'pdf':
                    pdf_job_list.append(selected_file_path)
                    filestem = os.path.basename(selected_file_path)[:-4]
                    if config_obj['langcode'] == 'ja':
                        progressmessage.value = 'pdfファイルの前処理中…… {} '.format(selected_file_path)
                    else:
                        progressmessage.value = 'preprocessing pdf…… {} '.format(selected_file_path)
                    parts_control(True)
                    page.update()
                    for p in glob.glob(os.path.join(PDFTMPPATH, '*.jpg')):
                        if os.path.isfile(p):
                            os.remove(p)
                    os.makedirs(PDFTMPPATH, exist_ok=True)
                    await asyncio.to_thread(render_pdf_preview, selected_file_path, filestem)
                    if config_obj['langcode'] == 'ja':
                        progressmessage.value = 'pdfファイルの前処理完了'
                    else:
                        progressmessage.value = 'Preprocessing of pdf complete'
                    parts_control(False)
                    page.update()
                else:
                    inputpathlist.append(selected_file_path)
                selector.set_image(inputpathlist)
                if selected_output_path.value is not None:
                    parts_control(False)
            selected_input_path.update()
            page.update()

        async def pick_directory_result(e):
            selected_directory = await file_picker.get_directory_path()
            if selected_directory:
                selected_input_path.value = selected_directory
                nonlocal inputpathlist, outputtxtlist, pdf_job_list
                inputpathlist.clear()
                outputtxtlist.clear()
                outputplaintextlist.clear()
                pdf_job_list.clear()

                for p in glob.glob(os.path.join(PDFTMPPATH, '*.jpg')):
                    if os.path.isfile(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
                os.makedirs(PDFTMPPATH, exist_ok=True)

                parts_control(True)
                crop_btn.disabled = True
                ocr_btn.disabled = True
                page.update()

                all_files_to_process = []
                pdf_filename_counter = Counter()

                for root, dirs, files in os.walk(selected_directory):
                    dirs.sort()
                    files.sort()
                    for filename in files:
                        full_path = os.path.join(root, filename)
                        ext = filename.split('.')[-1].lower()

                        if ext in ['jpg', 'png', 'tiff', 'jp2', 'tif', 'jpeg', 'bmp', 'webp']:
                            all_files_to_process.append((full_path, 'image'))
                        elif ext == 'pdf':
                            all_files_to_process.append((full_path, 'pdf'))
                            pdf_filename_counter[filename] += 1

                all_files_to_process.sort(key=lambda x: x[0])

                for inputpath, filetype in all_files_to_process:
                    if filetype == 'image':
                        inputpathlist.append(inputpath)
                    elif filetype == 'pdf':
                        pdf_job_list.append(inputpath)
                        filename = os.path.basename(inputpath)
                        if pdf_filename_counter[filename] > 1:
                            rel_path = os.path.relpath(inputpath, start=selected_directory)
                            filestem = os.path.splitext(rel_path)[0].replace(os.sep, '-')
                        else:
                            filestem = os.path.splitext(filename)[0]

                        if config_obj['langcode'] == 'ja':
                            progressmessage.value = 'pdfファイルの前処理中…… {} '.format(inputpath)
                        else:
                            progressmessage.value = 'preprocessing pdf…… {} '.format(inputpath)
                        page.update()

                        try:
                            await asyncio.to_thread(render_pdf_preview, inputpath, filestem)
                        except Exception as err:
                            print(f'Error processing {inputpath}: {err}')

                if config_obj['langcode'] == 'ja':
                    progressmessage.value = '処理完了'
                else:
                    progressmessage.value = 'Processing complete'

                selector.set_image(inputpathlist)

                if len(inputpathlist) > 0:
                    parts_control(False)

            selected_input_path.update()
            page.update()

        async def pick_output_result(e):
            nonlocal inputpathlist
            selected_directory = await file_picker.get_directory_path()
            if selected_directory:
                selected_output_path.value = selected_directory
                selected_output_path.update()
                config_obj['selected_output_path'] = selected_directory
                save_config()
                selector.set_outputdir(selected_directory)
                capture_tool.outputdirpath = selected_directory
                if len(inputpathlist) > 0:
                    parts_control(False)
            page.update()

        preview_index = 0

        def next_image(e):
            nonlocal inputpathlist, outputtxtlist, preview_index
            if not outputtxtlist:
                return
            if preview_index < min(len(inputpathlist) - 1, len(outputtxtlist) - 1):
                preview_index += 1
            else:
                preview_index = 0

            if len(visualizepathlist) > 0:
                preview_image.src = visualizepathlist[min(preview_index, len(visualizepathlist) - 1)]
                current_visualizeimgname.value = os.path.basename(preview_image.src)
            elif 0 <= preview_index < len(inputpathlist):
                preview_image.src = inputpathlist[preview_index]
                current_visualizeimgname.value = os.path.basename(inputpathlist[preview_index])
            if 0 <= preview_index < len(outputtxtlist):
                preview_plain_text.value = outputplaintextlist[preview_index] if preview_index < len(outputplaintextlist) else ''
                preview_text.value = outputtxtlist[preview_index]
            preview_image.update()
            preview_plain_text.update()
            preview_text.update()
            page.update()

        def prev_image(e):
            nonlocal inputpathlist, outputtxtlist, preview_index
            if not outputtxtlist:
                return
            if preview_index > 0:
                preview_index -= 1
            else:
                preview_index = min(len(inputpathlist) - 1, len(outputtxtlist) - 1)

            if len(visualizepathlist) > 0:
                preview_image.src = visualizepathlist[min(preview_index, len(visualizepathlist) - 1)]
                current_visualizeimgname.value = os.path.basename(preview_image.src)
            elif 0 <= preview_index < len(inputpathlist):
                preview_image.src = inputpathlist[preview_index]
                current_visualizeimgname.value = os.path.basename(inputpathlist[preview_index])
            if 0 <= preview_index < len(outputtxtlist):
                preview_plain_text.value = outputplaintextlist[preview_index] if preview_index < len(outputplaintextlist) else ''
                preview_text.value = outputtxtlist[preview_index]
            preview_image.update()
            preview_plain_text.update()
            preview_text.update()
            page.update()

        def change_preview_view_mode(e=None):
            use_markdown = bool(preview_view_switch.value)
            config_obj['result_markdown_display'] = use_markdown
            save_config()
            preview_text.visible = use_markdown
            preview_plain_text.visible = not use_markdown
            # Keep the crop/capture result viewers on the same mode when available.
            for viewer in (selector, capture_tool):
                viewer.result_view_switch.value = use_markdown
                viewer.resulttext.visible = use_markdown
                viewer.result_plain_text.visible = not use_markdown
            page.update()

        def handle_customize_dlg_modal_close(e):
            updated_config = {
                'json': chkbx_json.value,
                'txt': chkbx_txt.value,
                'markdown': chkbx_markdown.value,
                'xml': chkbx_xml.value,
                'tei': chkbx_tei.value,
                'pdf': chkbx_pdf.value,
                'pdf_viztxt': chkbx_pdf_viztxt.value,
                'pdf_merge': chkbx_pdf_merge.value,
                'pdf_page_output': chkbx_pdf_page_output.value,
                'pdf_resolution': int(pdf_resolution_textfield.value),
                'text_daisy': chkbx_text_daisy.value,
            }
            if chkbx_table_structure is not None:
                updated_config['table_structure'] = chkbx_table_structure.value
            config_obj.update(updated_config)
            save_config()
            if customize_dlg_modal.open:
                page.pop_dialog()

        def change_pdfstatus(e):
            chkbx_pdf_viztxt.disabled = not chkbx_pdf.value
            chkbx_pdf_viztxt.update()
            chkbx_pdf_merge.disabled = not chkbx_pdf.value
            chkbx_pdf_merge.update()
            pdf_resolution_textfield.disabled = not chkbx_pdf.value
            pdf_resolution_textfield.update()

        preview_image = ft.Image(src=DEFAULT_IMAGE_SRC, width=400, height=300, gapless_playback=True)
        preview_text = ft.Markdown(
            value='',
            selectable=True,
            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
            soft_line_break=True,
            visible=bool(config_obj.get('result_markdown_display', False)),
        )
        preview_plain_text = ft.Text(
            value='',
            selectable=True,
            visible=not bool(config_obj.get('result_markdown_display', False))
        )
        preview_view_switch = ft.Switch(
            label='整形表示（Markdown）',
            value=bool(config_obj.get('result_markdown_display', False)),
            on_change=change_preview_view_mode,
        )

        file_picker = ft.FilePicker()
        page.services.append(file_picker)
        progressbar = ft.ProgressBar(width=200, value=0)
        selected_input_path = ft.Text(selectable=True)
        selected_output_path = ft.Text(config_obj['selected_output_path'], selectable=True)
        current_visualizeimgname = ft.Text(selectable=True)
        progressmessage = ft.Text(width=400, selectable=True)
        chkbx_visualize = ft.Checkbox(label=TRANSLATIONS['main_visualize_label'][config_obj['langcode']], value=True, key='visualize')
        chkbx_json = ft.Checkbox(label='JSON形式', value=config_obj['json'], key='output_json')
        chkbx_txt = ft.Checkbox(label='TXT形式', value=config_obj['txt'], key='output_txt')
        chkbx_markdown = ft.Checkbox(label='Markdown形式（.md）', value=config_obj.get('markdown', False), key='output_markdown')
        chkbx_xml = ft.Checkbox(label='XML形式', value=config_obj['xml'], key='output_xml')
        chkbx_tei = ft.Checkbox(label='TEI形式', value=config_obj['tei'], key='output_tei')
        chkbx_pdf = ft.Checkbox(label='透明テキスト付PDF', value=config_obj['pdf'], on_change=change_pdfstatus, key='output_pdf')
        chkbx_pdf_viztxt = ft.Checkbox(label='PDFに青色で文字を重ねる', value=config_obj['pdf_viztxt'], disabled=not chkbx_pdf.value, key='output_pdf_visible_text')
        chkbx_pdf_merge = ft.Checkbox(label='出力ファイルを1つのpdfにまとめる', value=config_obj['pdf_merge'], disabled=not chkbx_pdf.value, key='output_pdf_merge')
        chkbx_pdf_page_output = ft.Checkbox(label='複数ページPDFを入力とする時はTXT/JSON/XML/TEIをページ毎に出力する', value=config_obj.get('pdf_page_output', True), key='output_pdf_page_output')
        pdf_resolution_textfield = ft.TextField(label='pdfの出力解像度を指定する', value=str(config_obj['pdf_resolution']), width=200, disabled=not chkbx_pdf.value, key='output_pdf_resolution')
        chkbx_text_daisy = ft.Checkbox(
            label='テキストDAISY形式（DAISY 3）',
            value=config_obj['text_daisy'],
            key='output_text_daisy',
        )
        chkbx_table_structure = (
            TABLE_STRUCTURE_FEATURE.create_output_control(config_obj)
            if TABLE_STRUCTURE_FEATURE is not None
            else None
        )
        file_upload_btn = ft.Button(
            TRANSLATIONS['main_file_upload_btn'][config_obj['langcode']],
            icon=ft.Icons.UPLOAD_FILE,
            on_click=pick_files_result,
            key='select_input_file',
        )
        directory_upload_btn = ft.Button(
            TRANSLATIONS['main_directory_upload_btn'][config_obj['langcode']],
            icon=ft.Icons.FOLDER_OPEN,
            on_click=pick_directory_result,
            key='select_input_directory',
        )
        directory_output_btn = ft.Button(
            TRANSLATIONS['main_directory_output_btn'][config_obj['langcode']],
            on_click=pick_output_result,
            key='select_output_directory',
        )
        ocr_btn = ft.Button(
            content='OCR',
            on_click=ocr_button_result,
            style=ft.ButtonStyle(
                padding=30,
                shape=ft.RoundedRectangleBorder(radius=10),
            ),
            disabled=True,
            key='run_ocr',
        )
        stop_ocr_btn=ft.Button(
            content=TRANSLATIONS["main_stop_ocr_btn"][config_obj["langcode"]],
            on_click=request_ocr_cancel,
            disabled=True,
            key='stop_ocr')
        preview_image_col = ft.Column(
            controls=[preview_image],
            width=400,
            height=300,
            expand=False,
        )

        preview_image_int = ft.InteractiveViewer(
            min_scale=1,
            max_scale=10,
            boundary_margin=ft.Margin.all(20),
            content=preview_image_col,
        )
        preview_text_col = ft.Column(
            #controls=[preview_view_switch, preview_plain_text, preview_text],
            controls=[preview_plain_text, preview_text],
            scroll=ft.ScrollMode.ALWAYS,
            height=300,
            expand=True,
        )
        preview_prev_btn = ft.Button(content=TRANSLATIONS['main_prev_btn'][config_obj['langcode']], on_click=prev_image, disabled=True)
        preview_next_btn = ft.Button(content=TRANSLATIONS['main_next_btn'][config_obj['langcode']], on_click=next_image, disabled=True)
        customize_btn = ft.Button(
            TRANSLATIONS['main_customize_btn'][config_obj['langcode']],
            on_click=lambda e: page.show_dialog(customize_dlg_modal),
            key='customize_output',
        )
        customize_dlg_modal = ft.AlertDialog(
            modal=True,
            title=ft.Text(TRANSLATIONS['customize_dlg_modal_title'][config_obj['langcode']]),
            content=ft.Text(TRANSLATIONS['customize_dlg_modal_explain'][config_obj['langcode']]),
            actions=[
                ft.Row([chkbx_txt,chkbx_json]),
                #chkbx_markdown,
                ft.Row([chkbx_xml, chkbx_tei]),
                ft.Row([chkbx_pdf, chkbx_pdf_viztxt]),
                ft.Row([chkbx_pdf_merge, pdf_resolution_textfield]),
                chkbx_pdf_page_output,
                #chkbx_text_daisy,
                *([chkbx_table_structure] if chkbx_table_structure is not None else []),
                ft.TextButton('OK', on_click=handle_customize_dlg_modal_close, key='customize_output_ok'),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            key='customize_output_dialog',
        )
        selector = ImageSelector(
            page,
            config_obj,
            detector=origin_detector,
            recognizer30=origin_recognizer30,
            recognizer50=origin_recognizer50,
            recognizer100=origin_recognizer,
            table_feature=TABLE_STRUCTURE_FEATURE,
            table_recognizer=origin_table_recognizer,
            outputdirpath=selected_output_path.value,
        )

        capture_tool = CaptureTool(
            page,
            config_obj,
            detector=origin_detector,
            recognizer30=origin_recognizer30,
            recognizer50=origin_recognizer50,
            recognizer100=origin_recognizer,
            table_feature=TABLE_STRUCTURE_FEATURE,
            table_recognizer=origin_table_recognizer,
        )
        if selected_output_path.value:
            capture_tool.outputdirpath = selected_output_path.value

        llm_capture_extension = (
            LLM_FEATURE.attach_capture_tool(capture_tool, config_obj, save_config)
            if LLM_FEATURE is not None
            else None
        )
        datapool_capture_extension = (
            DATA_POOL_FEATURE.attach_capture_tool(
                capture_tool,
                llm_capture_extension,
                config_obj,
                Path(PDFTMPPATH),
            )
            if DATA_POOL_FEATURE is not None
            else None
        )
        datapool_header_controls = (
            DATA_POOL_FEATURE.build_auth_controls(
                page,
                config_obj,
                save_config,
                datapool_capture_extension,
            )
            if DATA_POOL_FEATURE is not None
            else []
        )

        crop_btn = ft.Button(
            content='Crop&OCR',
            on_click=selector.open_dialog,
            style=ft.ButtonStyle(
                padding=10,
                shape=ft.RoundedRectangleBorder(radius=10),
            ),
            disabled=True,
            key='crop_ocr',
        )
        cap_btn = ft.Button(
            content=TRANSLATIONS['main_cap_btn'][config_obj['langcode']],
            on_click=capture_tool.start_capture,
            style=ft.ButtonStyle(
                padding=10,
                shape=ft.RoundedRectangleBorder(radius=10),
            ),
            disabled=True,
            key='capture_ocr',
        )
        explain_label = ft.Text(TRANSLATIONS['main_explain'][config_obj['langcode']])
        localebutton = ft.CupertinoSlidingSegmentedButton(
            selected_index=0 if config_obj['langcode'] == 'ja' else 1,
            thumb_color=ft.Colors.BLUE_400,
            on_change=handle_locale_change,
            controls=[ft.Text('日本語'), ft.Text('English')],
            key='locale',
        )
        page.add(
            ft.Row([
                localebutton,
                *datapool_header_controls,
            ]),
            ft.Row([
                explain_label,
                cap_btn,
            ]),
            ft.Divider(),
            ft.Row([
                file_upload_btn,
                directory_upload_btn,
                ft.Text(TRANSLATIONS['main_target_label'][config_obj['langcode']]),
                selected_input_path,
            ]),
            ft.Divider(),
            ft.Row([
                directory_output_btn,
                ft.Text(TRANSLATIONS['main_output_label'][config_obj['langcode']]),
                selected_output_path,
            ]),
            ft.Divider(),
            ft.Row([
                ocr_btn,
                stop_ocr_btn,
                crop_btn,
                ft.Column([
                    chkbx_visualize,
                    ft.Row([
                        customize_btn,
                        ft.Column([progressmessage, progressbar], width=400),
                    ]),
                ]),
            ]),
            ft.Divider(),
            ft.Row([ft.Text(TRANSLATIONS['main_preview_label'][config_obj['langcode']]), preview_prev_btn, preview_next_btn, current_visualizeimgname]),
            ft.Row([preview_image_int, preview_text_col]),
        )
        page.update()

        async def initialize_models():
            nonlocal origin_detector, origin_recognizer
            nonlocal origin_recognizer30, origin_recognizer50
            nonlocal origin_table_recognizer

            started_at = time.perf_counter()
            try:
                (
                    origin_detector,
                    origin_recognizer,
                    origin_recognizer30,
                    origin_recognizer50,
                ) = await asyncio.to_thread(load_ocr_models, args)
                origin_table_recognizer = None
                model_state['table_error'] = None
                if TABLE_STRUCTURE_FEATURE is not None:
                    try:
                        table_recognizer = TABLE_STRUCTURE_FEATURE.create_recognizer(args)
                        await asyncio.to_thread(table_recognizer.load)
                        origin_table_recognizer = table_recognizer
                    except Exception as table_ex:
                        model_state['table_error'] = table_ex
                        print(f'[WARN] Table structure initialization failed: {table_ex}')

                model_state['elapsed'] = time.perf_counter() - started_at
                model_state['error'] = None

                for tool in (selector, capture_tool):
                    tool.detector = origin_detector
                    tool.recognizer30 = origin_recognizer30
                    tool.recognizer50 = origin_recognizer50
                    tool.recognizer100 = origin_recognizer
                    tool.table_recognizer = origin_table_recognizer

                progressbar.value = 0
                if config_obj['langcode'] == 'ja':
                    if TABLE_STRUCTURE_FEATURE is None:
                        progressmessage.value = f'OCRモデル準備完了 ({model_state["elapsed"]:.1f}秒)'
                    elif model_state.get('table_error') is None:
                        progressmessage.value = f'OCR / 表推定モデル準備完了 ({model_state["elapsed"]:.1f}秒)'
                    else:
                        progressmessage.value = f'OCRモデル準備完了 / 表推定初期化失敗 ({model_state["elapsed"]:.1f}秒)'
                else:
                    if TABLE_STRUCTURE_FEATURE is None:
                        progressmessage.value = f'OCR models ready ({model_state["elapsed"]:.1f} sec)'
                    elif model_state.get('table_error') is None:
                        progressmessage.value = f'OCR / TableInfer models ready ({model_state["elapsed"]:.1f} sec)'
                    else:
                        progressmessage.value = f'OCR models ready / TableInfer models unavailable ({model_state["elapsed"]:.1f} sec)'
            except Exception as ex:
                model_state['error'] = ex
                progressbar.value = 0
                progressmessage.value = f'OCR model initialization failed: {ex}'
            finally:
                model_state['loading'] = False
                parts_control(False)
                page.update()

        if models_are_ready():
            parts_control(False)
            if model_state['elapsed'] is not None:
                if config_obj['langcode'] == 'ja':
                    if TABLE_STRUCTURE_FEATURE is None:
                        progressmessage.value = f'OCRモデル準備完了 ({model_state["elapsed"]:.1f}秒)'
                    elif model_state.get('table_error') is None:
                        progressmessage.value = f'OCR / 表推定モデル準備完了 ({model_state["elapsed"]:.1f}秒)'
                    else:
                        progressmessage.value = f'OCRモデル準備完了 / 表推定初期化失敗 ({model_state["elapsed"]:.1f}秒)'
                else:
                    if TABLE_STRUCTURE_FEATURE is None:
                        progressmessage.value = f'OCR models ready ({model_state["elapsed"]:.1f} sec)'
                    elif model_state.get('table_error') is None:
                        progressmessage.value = f'OCR / TableInfer models ready ({model_state["elapsed"]:.1f} sec)'
                    else:
                        progressmessage.value = f'OCR models ready / TableInfer models unavailable ({model_state["elapsed"]:.1f} sec)'
            page.update()
        elif not model_state['loading']:
            model_state['loading'] = True
            progressbar.value = None
            if config_obj['langcode'] == 'ja':
                progressmessage.value = 'OCRモデルを読み込み中…'
            else:
                progressmessage.value = 'Loading OCR models…'
            localebutton.disabled = True
            page.update()
            page.run_task(initialize_models)

    renderui()


if __name__ == '__main__':
    ft.run(main, assets_dir=str(ASSETS_DIR))
