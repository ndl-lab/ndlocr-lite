import logging
# logging.basicConfig(filename='debug.log', encoding='utf-8', level=logging.DEBUG)
import flet as ft
from typing import List, Dict, Tuple
import sys
import os
import numpy as np
from PIL import Image, ImageFile, ImageGrab
ImageFile.MAXBLOCK = 1024 * 1024 * 128
from pathlib import Path

sys.path.append(os.path.join('.', 'src'))

name = 'NDLOCR-Lite-GUI'

try:
    import ocr
    from tools.ndlkoten2tei import convert_tei
except (ImportError, OSError) as import_err:
    # onnxruntime の DLL ロード失敗などで OCR エンジンを読み込めない場合に、
    # 無言で終了（白画面・一瞬だけ枠が表示される等）せず原因と対処を案内する
    _IMPORT_ERROR_MESSAGE = (
        'OCRエンジンの読み込みに失敗しました。\n'
        'Microsoft Visual C++ 再頒布可能パッケージがインストールされていない可能性があります。\n'
        '以下からインストールしてから再度お試しください。\n'
        'https://aka.ms/vs/17/release/vc_redist.x64.exe\n\n'
        'Failed to load the OCR engine.\n'
        'The Microsoft Visual C++ Redistributable may not be installed.\n'
        'Please install it from the URL above and try again.\n\n'
        '詳細 / Details: {}'
    ).format(import_err)
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, _IMPORT_ERROR_MESSAGE, name, 0x10)
    else:
        print(_IMPORT_ERROR_MESSAGE, file=sys.stderr)
    sys.exit(1)
import xml.etree.ElementTree as ET
import time
from concurrent.futures import ThreadPoolExecutor
import json
import shutil
import threading
import argparse
import yaml
import io
import glob
import pypdfium2
import base64
import ctypes
from io import BytesIO
from uicomponent.localelabel import TRANSLATIONS
from collections import Counter

from reading_order.xy_cut.eval import eval_xml
from ndl_parser import convert_to_xml_string3
from ndl_parser import categories_org_name_index


PDFTMPPATH = '4ab7ecc3-53fb-b3e7-64e8-a809b5a483d2'


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
    def __init__(self, npimg: np.ndarray, idx: float, pred_char_cnt: int, pred_str: str = ''):
        self.npimg = npimg
        self.idx = idx
        self.pred_char_cnt = pred_char_cnt
        self.pred_str = pred_str

    def __lt__(self, other):
        return self.idx < other.idx


def process_cascade(alllineobj: RecogLine, recognizer30, recognizer50, recognizer100, is_cascade=True):
    targetdflist30, targetdflist50, targetdflist100, targetdflist200 = [], [], [], []
    for lineobj in alllineobj:
        if lineobj.pred_char_cnt == 3 and is_cascade:
            targetdflist30.append(lineobj)
        elif lineobj.pred_char_cnt == 2 and is_cascade:
            targetdflist50.append(lineobj)
        else:
            targetdflist100.append(lineobj)
    targetdflistall = []
    with ThreadPoolExecutor(thread_name_prefix='thread') as executor:
        resultlines30, resultlines50, resultlines100, resultlines200 = [], [], [], []
        if len(targetdflist30) > 0:
            resultlines30 = executor.map(recognizer30.read, [t.npimg for t in targetdflist30])
            resultlines30 = list(resultlines30)
        for i in range(len(targetdflist30)):
            pred_str = resultlines30[i]
            lineobj = targetdflist30[i]
            if len(pred_str) >= 25:
                targetdflist50.append(lineobj)
            else:
                lineobj.pred_str = pred_str
                targetdflistall.append(lineobj)
        if len(targetdflist50) > 0:
            resultlines50 = executor.map(recognizer50.read, [t.npimg for t in targetdflist50])
            resultlines50 = list(resultlines50)
        for i in range(len(targetdflist50)):
            pred_str = resultlines50[i]
            lineobj = targetdflist50[i]
            if len(pred_str) >= 45:
                targetdflist100.append(lineobj)
            else:
                lineobj.pred_str = pred_str
                targetdflistall.append(lineobj)
        if len(targetdflist100) > 0:
            resultlines100 = executor.map(recognizer100.read, [t.npimg for t in targetdflist100])
            resultlines100 = list(resultlines100)
        for i in range(len(targetdflist100)):
            pred_str = resultlines100[i]
            lineobj = targetdflist100[i]
            lineobj.pred_str = pred_str
            if len(pred_str) >= 98 and lineobj.npimg.shape[0] < lineobj.npimg.shape[1]:
                baseimg = lineobj.npimg
                tmplineobj_1 = RecogLine(npimg=baseimg[:, :baseimg.shape[1] // 2, :], idx=lineobj.idx, pred_char_cnt=100)
                tmplineobj_2 = RecogLine(npimg=baseimg[:, baseimg.shape[1] // 2:, :], idx=lineobj.idx, pred_char_cnt=100)
                targetdflist200.append(tmplineobj_1)
                targetdflist200.append(tmplineobj_2)
            else:
                targetdflistall.append(lineobj)
        if len(targetdflist200) > 0:
            resultlines200 = executor.map(recognizer100.read, [t.npimg for t in targetdflist200])
            resultlines200 = list(resultlines200)
            for i in range(0, len(targetdflist200) - 1, 2):
                ia = targetdflist200[i]
                lineobj = RecogLine(npimg=None, idx=ia.idx, pred_char_cnt=100, pred_str=resultlines200[i] + resultlines200[i + 1])
                targetdflistall.append(lineobj)
        targetdflistall = sorted(targetdflistall)
        resultlinesall = [t.pred_str for t in targetdflistall]
    return resultlinesall


class ImageSelector:
    def __init__(self, page: ft.Page, config_obj: Dict, detector=None, recognizer30=None, recognizer50=None, recognizer100=None, outputdirpath=None, width: int = 600, height: int = 600):
        self.cnt = 0
        self.page = page
        self.config_obj = config_obj
        self.langcode = config_obj['langcode']
        self.inputpathlist = []
        self.outputdirpath = outputdirpath

        self.image_src = 'dummy.dat'
        self.dialog_width = width
        self.dialog_height = height
        self.page_index = 0
        self.detector = detector
        self.recognizer30 = recognizer30
        self.recognizer50 = recognizer50
        self.recognizer100 = recognizer100

        self.start_x = 0
        self.start_y = 0

        self.selection_box = ft.Container(
            left=0,
            top=0,
            width=0,
            height=0,
            border=ft.border.all(2, ft.Colors.BLUE),
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
        self.img = ft.Image(src=self.image_src, width=self.dialog_width, height=self.dialog_height, fit=ft.ImageFit.CONTAIN)
        self.imgzm = ft.Image(src=self.image_src, width=self.dialog_width, height=self.dialog_height, fit=ft.ImageFit.CONTAIN)
        self.image_stack = ft.Stack(
            width=self.dialog_width,
            height=self.dialog_height,
            controls=[
                self.img,
                self.selection_box,
                self.overlay,
            ],
        )
        self.cropocr_btn = ft.ElevatedButton(TRANSLATIONS['imageselector_cropocr_btn'][self.langcode], on_click=self.crop_region)
        self.dialog = ft.AlertDialog(
            modal=True,
            content=self.image_stack,
            actions=[
                ft.ElevatedButton(TRANSLATIONS['imageselector_zoom_btn'][self.langcode], icon=ft.Icons.ZOOM_IN, on_click=self.open_zoom_page),
                ft.ElevatedButton(TRANSLATIONS['imageselector_prev_btn'][self.langcode], on_click=self.prev_page),
                ft.ElevatedButton(TRANSLATIONS['imageselector_next_btn'][self.langcode], on_click=self.next_page),
                self.cropocr_btn,
                ft.ElevatedButton(TRANSLATIONS['common_cancel'][self.langcode], on_click=self.close_dialog),
            ],
        )
        zoom_img = ft.InteractiveViewer(
            min_scale=1,
            max_scale=10,
            boundary_margin=ft.margin.all(20),
            content=self.imgzm,
        )

        self.zoom_dialog = ft.AlertDialog(
            modal=True,
            content=zoom_img,
            actions=[
                ft.ElevatedButton(TRANSLATIONS['common_cancel'][self.langcode], on_click=self.close_zoom_page),
            ],
        )
        self.resulttext = ft.Text(value='', selectable=True)

        self.crop_image = ft.Image(src=self.image_src, width=300, height=300, fit=ft.ImageFit.CONTAIN)
        crop_image_col = ft.Column(
            controls=[self.crop_image],
            width=300,
            height=300,
            expand=False,
        )
        self.crop_image_int = ft.InteractiveViewer(
            min_scale=1,
            max_scale=5,
            boundary_margin=ft.margin.all(20),
            content=crop_image_col,
        )
        self.result_text_col = ft.Column(
            controls=[self.resulttext],
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
                ft.ElevatedButton('OK', on_click=self.close_result_page),
            ],
        )

    def open_result_page(self):
        self.dialog.open = False
        self.result_dialog.open = True
        self.page.overlay.append(self.result_dialog)
        self.page.update()

    def close_result_page(self, e):
        self.result_dialog.open = False
        self.dialog.open = True
        self.page.update()

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
        self.dialog.open = False
        if self.zoom_dialog not in self.page.overlay:
            self.page.overlay.append(self.zoom_dialog)
        self.zoom_dialog.open = True
        self.page.update()

    def close_zoom_page(self, e):
        self.zoom_dialog.open = False
        self.dialog.open = True
        self.page.update()

    def pan_start(self, e: ft.DragStartEvent):
        self.start_x = e.local_x
        self.start_y = e.local_y
        self.selection_box.left = self.start_x
        self.selection_box.top = self.start_y
        self.selection_box.width = 0
        self.selection_box.height = 0
        self.page.update()

    def pan_update(self, e: ft.DragUpdateEvent):
        cur_x, cur_y = e.local_x, e.local_y
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
        self.page.overlay.append(self.dialog)
        self.dialog.open = True
        self.page.update()

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
        self.crop_image.src_base64 = base64.b64encode(buff.getvalue()).decode('utf-8')
        self.outputcroppedpath = os.path.join(os.getcwd(), PDFTMPPATH, os.path.splitext(os.path.basename(self.image_src))[0] + '_cropped_{}.jpg'.format(self.cnt))
        self.mini_ocr(im_crop)
        self.cnt += 1
        self.page.update()

    def mini_ocr(self, im_crop):
        self.cropocr_btn.disabled = True
        self.page.update()
        inputname = os.path.basename(self.outputcroppedpath)

        self.crop_image.src = im_crop
        npimg = np.array(im_crop)
        try:
            page_result = ocr._run_ocr_on_image_array(
                detector=self.detector,
                recognizer30=self.recognizer30,
                recognizer50=self.recognizer50,
                recognizer100=self.recognizer100,
                inputname=inputname,
                img=npimg,
                outputpath=self.outputdirpath or os.getcwd(),
                save_viz=False,
            )
            alltextlist = [page_result['text']]
            if page_result['line_count'] == 0 or (
                page_result['line_count'] > 0 and page_result['vertical_line_count'] / page_result['line_count'] > 0.5
            ):
                alltextlist = alltextlist[::-1]
            os.makedirs(self.outputdirpath or os.getcwd(), exist_ok=True)
            with open(os.path.join(self.outputdirpath or os.getcwd(), os.path.splitext(os.path.basename(inputname))[0] + '.txt'), 'w', encoding='utf-8') as wtf:
                wtf.write('\n'.join(alltextlist))
            self.resulttext.value = '\n'.join(alltextlist)
        except Exception as ex:
            self.resulttext.value = f'エラーが発生しました: {ex}'
        finally:
            self.cropocr_btn.disabled = False
            self.open_result_page()
            self.page.update()

    def close_dialog(self, e):
        self.dialog.open = False
        self.page.update()


class CaptureTool:
    def __init__(self, page: ft.Page, config_obj: Dict, detector=None, recognizer30=None, recognizer50=None, recognizer100=None, width: int = 400, height: int = 300):
        self.page = page
        self.config_obj = config_obj
        self.langcode = config_obj['langcode']
        self.detector = detector
        self.recognizer30 = recognizer30
        self.recognizer50 = recognizer50
        self.recognizer100 = recognizer100
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
            border=ft.border.all(2, ft.Colors.RED),
            bgcolor=ft.Colors.with_opacity(0.2, ft.Colors.RED),
            visible=False,
        )
        self.img_control = ft.Image(
            src_base64=None,
            src=None,
            width=self.dialog_width,
            height=self.dialog_height,
            fit=ft.ImageFit.CONTAIN,
            gapless_playback=True,
        )
        self.retry_btn = ft.ElevatedButton(TRANSLATIONS['capturetool_retry_btn'][self.langcode], on_click=self.start_capture)
        self.cboc_fixed = ft.Checkbox(label=TRANSLATIONS['capturetool_fixregion'][self.langcode], value=False, disabled=True)
        self.ocr_btn = ft.ElevatedButton(TRANSLATIONS['capturetool_ocr_button'][self.langcode], on_click=self.mini_ocr)

        self.errorlog = ft.Text('')
        self.dialog_content = ft.Column(
            controls=[
                self.errorlog,
                ft.Container(
                    content=self.img_control,
                    border=ft.border.all(1, ft.Colors.GREY),
                    alignment=ft.alignment.center,
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
                    self.cboc_fixed,
                    self.ocr_btn,
                    ft.ElevatedButton(TRANSLATIONS['common_close'][self.langcode], on_click=self.close_dialog),
                ]),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.resulttext = ft.Text(value='', selectable=True)
        self.resultsmessage = ft.Text(value='', selectable=True)
        self.llmstatus_text = ft.Text(value='', selectable=True)
        self.result_crop_image = ft.Image(src='', width=300, height=300, fit=ft.ImageFit.CONTAIN)

        self.crop_image_int = ft.InteractiveViewer(
            min_scale=1,
            max_scale=5,
            boundary_margin=ft.margin.all(20),
            content=ft.Column([self.result_crop_image], width=300, height=300),
        )

        self.result_text_col = ft.Column(
            controls=[self.resulttext],
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
                self.llmstatus_text,
                ft.ElevatedButton(TRANSLATIONS['common_close'][self.langcode], on_click=self.close_result_page),
            ],
        )
        self.bibinfo_dialog = ft.AlertDialog(
            title=ft.Text('書誌情報'),
            content='',
            actions=[
                ft.TextButton('閉じる', on_click=self.close_bibinfo_page),
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
        self.start_x = e.local_x
        self.start_y = e.local_y
        self.current_x = e.local_x
        self.current_y = e.local_y
        self.selection_box.visible = True
        self.selection_box.left = self.start_x
        self.selection_box.top = self.start_y
        self.selection_box.width = 0
        self.selection_box.height = 0
        self.page.update()

    def _on_pan_update(self, e: ft.DragUpdateEvent):
        self.current_x = e.local_x
        self.current_y = e.local_y

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
                self.img_control.src_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
                self.img_control.src = None
                self.result_crop_image.src_base64 = self.img_control.src_base64
                self.page.open(self.dialog)
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

    def mini_ocr(self, e):
        if self.im_crop is None:
            return
        self.ocr_btn.disabled = True
        self.resultsmessage.value = ''
        self.page.update()
        try:
            allstart = time.time()
            filename_base = 'captureimg'
            npimg = np.array(self.im_crop)

            page_result = ocr._run_ocr_on_image_array(
                detector=self.detector,
                recognizer30=self.recognizer30,
                recognizer50=self.recognizer50,
                recognizer100=self.recognizer100,
                inputname=filename_base,
                img=npimg,
                outputpath=self.outputdirpath,
                save_viz=False,
            )

            final_text = page_result['text']
            self.resultsmessage.value = '{:.2f} sec'.format(time.time() - allstart)
            self.resulttext.value = final_text
            self.result_jsonstr = json.dumps(page_result['json_lines'], ensure_ascii=False)
            self.open_result_page()

        except Exception as e:
            print(f'OCR Error: {e}')
            self.resulttext.value = f'エラーが発生しました: {e}'
            self.open_result_page()
        finally:
            self.ocr_btn.disabled = False
            self.page.update()

    def open_dialog(self, e=None):
        self.start_capture()
        self.page.overlay.append(self.dialog)
        self.dialog.open = True
        self.page.update()

    def close_dialog(self, e):
        self.dialog.open = False
        self.page.update()

    def open_result_page(self):
        self.dialog.open = False
        self.page.overlay.append(self.result_dialog)
        self.result_dialog.open = True
        self.page.update()

    def close_result_page(self, e):
        self.result_dialog.open = False
        self.dialog.open = True
        self.page.update()

    def open_bibdlg_page(self, content):
        self.result_dialog.open = False
        self.bibinfo_dialog.open = True
        self.page.update()

    def close_bibinfo_page(self, e):
        self.bibinfo_dialog.open = False
        self.result_dialog.open = True
        self.page.update()

    def save_config(self):
        with open('userconf.yaml', 'w', encoding='utf-8') as wf:
            yaml.dump(self.config_obj, wf, default_flow_style=False, allow_unicode=True)


def main(page: ft.Page):
    parser = argparse.ArgumentParser(description='Argument for Inference using ONNXRuntime')
    parser.add_argument('--det-weights', type=str, required=False, help='Path to rtmdet onnx file', default='./src/model/deim-s-1024x1024.onnx')
    parser.add_argument('--det-classes', type=str, required=False, help='Path to list of class in yaml file', default='./src/config/ndl.yaml')
    parser.add_argument('--det-score-threshold', type=float, required=False, default=0.2)
    parser.add_argument('--det-conf-threshold', type=float, required=False, default=0.25)
    parser.add_argument('--det-iou-threshold', type=float, required=False, default=0.2)

    parser.add_argument('--rec-weights30', type=str, required=False, help='Path to parseq-tiny onnx file', default='./src/model/parseq-ndl-24x256-30-tiny-189epoch-tegaki3-r8data-202604.onnx')
    parser.add_argument('--rec-weights50', type=str, required=False, help='Path to parseq-tiny onnx file', default='./src/model/parseq-ndl-24x384-50-tiny-300epoch-tegaki3-r8data-202604.onnx')
    parser.add_argument('--rec-weights', type=str, required=False, help='Path to parseq-tiny onnx file', default='./src/model/parseq-ndl-24x768-100-tiny-153epoch-tegaki3-r8data-202604.onnx')
    parser.add_argument('--rec-classes', type=str, required=False, help='Path to list of class in yaml file', default='./src/config/NDLmoji.yaml')
    parser.add_argument('--device', type=str, required=False, help='Device use (cpu or cuda)', choices=['cpu', 'cuda'], default='cpu')
    args = parser.parse_args()

    page.title = 'NDLOCR-Lite-GUI'
    page.theme_mode = ft.ThemeMode.SYSTEM
    page.window.icon = os.path.join('assets', 'icon.png')
    page.window.width = 1024
    page.window.height = 900
    page.window.min_width = 1024
    page.window.min_height = 900
    page.window.icon = os.path.join('assets', 'icon.png')

    default_config = {
        'langcode': 'ja',
        'json': True,
        'xml': True,
        'tei': True,
        'txt': True,
        'pdf': False,
        'pdf_viztxt': False,
        'pdf_merge': True,
        'pdf_resolution': 300,
        'selected_output_path': None,
        'prompt': '',
    }
    load_obj = {}
    if os.path.exists('userconf.yaml'):
        with open('userconf.yaml', encoding='utf-8') as f:
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
        with open('userconf.yaml', 'w', encoding='utf-8') as wf:
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

    origin_detector = ocr.get_detector(args=args)
    origin_recognizer = ocr.get_recognizer(args=args)
    origin_recognizer30 = ocr.get_recognizer(args=args, weights_path=args.rec_weights30)
    origin_recognizer50 = ocr.get_recognizer(args=args, weights_path=args.rec_weights50)

    def renderui():
        page.clean()
        page.update()
        inputpathlist = []
        visualizepathlist = []
        outputtxtlist = []
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
                tmp_dir = os.path.abspath(os.path.join(os.getcwd(), PDFTMPPATH))
                return os.path.abspath(path).startswith(tmp_dir + os.sep)
            except Exception:
                return False

        def render_pdf_preview(pdf_path: str, filestem: str):
            os.makedirs(os.path.join(os.getcwd(), PDFTMPPATH), exist_ok=True)
            doc = pypdfium2.PdfDocument(pdf_path)
            try:
                pdfarray = doc.render(
                    pypdfium2.PdfBitmap.to_pil,
                    page_indices=[i for i in range(len(doc))],
                    scale=100 / 72,
                )
                for ix, image in enumerate(list(pdfarray)):
                    outputtmppath = os.path.join(os.getcwd(), PDFTMPPATH, '{}_{:05}.jpg'.format(filestem, ix))
                    inputpathlist.append(outputtmppath)
                    image = image.convert('RGB')
                    image.save(outputtmppath)
            finally:
                doc.close()

        def parts_control(flag:bool,allow_ocr_cancel:bool=False):
            file_upload_btn.disabled = flag
            directory_upload_btn.disabled = flag
            directory_output_btn.disabled = flag
            chkbx_visualize.disabled = flag
            customize_btn.disabled = flag
            preview_prev_btn.disabled = flag
            preview_next_btn.disabled = flag
            ocr_btn.disabled = flag
            crop_btn.disabled = flag
            cap_btn.disabled = flag
            localebutton.disabled = flag
            stop_ocr_btn.disabled=not (flag and allow_ocr_cancel)

        def process_pdf_with_original_layer(pdf_path: str, outputpath: str, alljsonobjlist: list, pdf_outpath_list: list, allsum: int):
            pdf_path_obj = Path(pdf_path)
            output_stem = pdf_path_obj.stem
            render_dpi = max(float(pdf_resolution_textfield.value), 1.0)
            render_scale = render_dpi / 72.0

            pdf_doc = pypdfium2.PdfDocument(str(pdf_path_obj))
            page_results = []
            all_json_contents = []
            page_infos = []
            all_text_pages = []
            all_page_xml = []

            try:
                page_count = len(pdf_doc)
                for page_index in range(page_count):
                    page_name = f'{output_stem}_{page_index + 1:05}.png'
                    progressmessage.value = f'{pdf_path_obj.name} page {page_index + 1}/{page_count}'
                    progressmessage.update()

                    rendered_pages = pdf_doc.render(
                        pypdfium2.PdfBitmap.to_pil,
                        page_indices=[page_index],
                        scale=render_scale,
                    )
                    pil_image = next(iter(rendered_pages)).convert('RGB')
                    img = np.array(pil_image)

                    page_result = ocr._run_ocr_on_image_array(
                        detector=origin_detector,
                        recognizer30=origin_recognizer30,
                        recognizer50=origin_recognizer50,
                        recognizer100=origin_recognizer,
                        inputname=page_name,
                        img=img,
                        outputpath=outputpath,
                        save_viz=chkbx_visualize.value,
                    )

                    page_results.append(page_result)
                    all_json_contents.append(page_result['json_lines'])
                    page_infos.append({
                        'page_index': page_index,
                        'img_width': page_result['img_width'],
                        'img_height': page_result['img_height'],
                        'img_name': page_result['img_name'],
                    })
                    all_text_pages.append(page_result['text'])
                    outputtxtlist.append(page_result['text'])
                    all_page_xml.append(page_result['page_xml'])

                    alljsonobjlist.append({
                        'contents': [page_result['json_lines']],
                        'imginfo': {
                            'img_width': page_result['img_width'],
                            'img_height': page_result['img_height'],
                            'img_path': str(pdf_path_obj),
                            'img_name': page_result['img_name'],
                        },
                    })

                    if chkbx_visualize.value:
                        viz_path = os.path.join(outputpath, f'viz_{page_name}')
                        if os.path.exists(viz_path):
                            visualizepathlist.append(viz_path)

                    progressbar.value += 1 / max(allsum, 1)
                    page.update()
            finally:
                pdf_doc.close()

            if chkbx_xml.value:
                with open(os.path.join(outputpath, output_stem + '.xml'), 'w', encoding='utf-8') as wf:
                    wf.write('<OCRDATASET>\n')
                    wf.write('\n'.join(all_page_xml))
                    wf.write('\n</OCRDATASET>')

            if chkbx_txt.value:
                with open(os.path.join(outputpath, output_stem + '.txt'), 'w', encoding='utf-8') as wf:
                    wf.write('\n\n'.join(all_text_pages))

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

            if chkbx_json.value:
                with open(os.path.join(outputpath, output_stem + '.json'), 'w', encoding='utf-8') as wf:
                    wf.write(json.dumps(pdf_json_obj, ensure_ascii=False, indent=2))

            if chkbx_pdf.value:
                output_pdf = os.path.join(outputpath, output_stem + '_text.pdf')
                ocr.embed_text_layer_pdf(
                    input_pdf=str(pdf_path_obj),
                    output_pdf=output_pdf,
                    page_results=page_results,
                    visible_text=chkbx_pdf_viztxt.value,
                )
                pdf_outpath_list.append(output_pdf)

        def request_ocr_cancel(e):
            if ocr_cancel_event.is_set():
                return
            ocr_cancel_event.set()
            stop_ocr_btn.disabled=True
            progressmessage.value=TRANSLATIONS["main_ocr_cancel_requested"][config_obj["langcode"]]
            page.update()

        def ocr_button_result(e):
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
                visualizepathlist.clear()
                alljsonobjlist = []
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
                    process_pdf_with_original_layer(pdf_path, outputpath, alljsonobjlist, pdf_outpath_list, allsum)

                for idx, inputpath in enumerate(standalone_inputpathlist):
                    if ocr_cancel_event.is_set():
                        cancelled=True
                        break
                    progressmessage.value = inputpath
                    progressmessage.update()
                    pil_image = Image.open(inputpath).convert('RGB')
                    img = np.array(pil_image)
                    start = time.time()
                    img_h, img_w = img.shape[:2]
                    imgname = os.path.basename(inputpath)

                    page_result = ocr._run_ocr_on_image_array(
                        detector=origin_detector,
                        recognizer30=origin_recognizer30,
                        recognizer50=origin_recognizer50,
                        recognizer100=origin_recognizer,
                        inputname=imgname,
                        img=img,
                        outputpath=outputpath,
                        save_viz=False,
                    )

                    allxmlstr = '<OCRDATASET>\n' + page_result['page_xml'] + '\n</OCRDATASET>'
                    alltextlist = [page_result['text']]
                    resjsonarray = page_result['json_lines']
                    outputtxtlist.append('\n'.join(alltextlist))

                    alljsonobj = {
                        'contents': [resjsonarray],
                        'imginfo': {
                            'img_width': img_w,
                            'img_height': img_h,
                            'img_path': inputpath,
                            'img_name': os.path.basename(inputpath),
                        },
                    }
                    alljsonobjlist.append(alljsonobj)

                    output_stem = os.path.splitext(os.path.basename(inputpath))[0]
                    if chkbx_xml.value:
                        with open(os.path.join(outputpath, output_stem + '.xml'), 'w', encoding='utf-8') as wf:
                            wf.write(allxmlstr)

                    if chkbx_visualize.value:
                        output_vizpath = os.path.join(outputpath, 'viz_' + os.path.basename(inputpath))
                        if output_vizpath.split('.')[-1] == 'jp2':
                            output_vizpath = output_vizpath[:-4] + '.jpg'
                        visualizepathlist.append(output_vizpath)
                        origin_detector.drawxml_detections(npimg=img, xmlstr=allxmlstr, categories=categories_org_name_index, outputimgpath=output_vizpath)

                    if chkbx_json.value:
                        with open(os.path.join(outputpath, output_stem + '.json'), 'w', encoding='utf-8') as wf:
                            wf.write(json.dumps(alljsonobj, ensure_ascii=False, indent=2))

                    if chkbx_txt.value:
                        with open(os.path.join(outputpath, output_stem + '.txt'), 'w', encoding='utf-8') as wtf:
                            wtf.write('\n'.join(alltextlist))

                    if chkbx_pdf.value:
                        pdf_outpath = os.path.join(outputpath, output_stem + '.pdf')
                        create_pdf_func(
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

                if chkbx_tei.value and len(alljsonobjlist)>0:
                    with open(os.path.join(outputpath, os.path.splitext(os.path.basename(inputpathlist[0]))[0] + '_tei.xml'), 'wb') as wf:
                        allxmlstrtei = convert_tei(alljsonobjlist)
                        wf.write(allxmlstrtei)

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
                    preview_text.value = outputtxtlist[0]
                if len(visualizepathlist) > 0:
                    preview_image.src = visualizepathlist[0]
                    current_visualizeimgname.value = os.path.basename(visualizepathlist[0])
                elif inputpathlist:
                    preview_image.src = inputpathlist[0]
                    current_visualizeimgname.value = os.path.basename(inputpathlist[0])
                preview_image.update()
                preview_text.update()

            except Exception as e:
                print(e)
                progressmessage.value = str(e)
                progressmessage.update()
            finally:
                ocr_cancel_event.clear()
                parts_control(False)
                page.update()

        def pick_files_result(e: ft.FilePickerResultEvent):
            if e.files:
                selected_input_path.value = e.files[0].path
                nonlocal inputpathlist, outputtxtlist, pdf_job_list
                inputpathlist.clear()
                outputtxtlist.clear()
                pdf_job_list.clear()
                ext = e.files[0].path.split('.')[-1].lower()
                if ext == 'pdf':
                    pdf_job_list.append(e.files[0].path)
                    filestem = os.path.basename(e.files[0].path)[:-4]
                    if config_obj['langcode'] == 'ja':
                        progressmessage.value = 'pdfファイルの前処理中…… {} '.format(e.files[0].path)
                    else:
                        progressmessage.value = 'preprocessing pdf…… {} '.format(e.files[0].path)
                    parts_control(True)
                    page.update()
                    for p in glob.glob(os.path.join(os.getcwd(), PDFTMPPATH, '*.jpg')):
                        if os.path.isfile(p):
                            os.remove(p)
                    os.makedirs(os.path.join(os.getcwd(), PDFTMPPATH), exist_ok=True)
                    render_pdf_preview(e.files[0].path, filestem)
                    if config_obj['langcode'] == 'ja':
                        progressmessage.value = 'pdfファイルの前処理完了'
                    else:
                        progressmessage.value = 'Preprocessing of pdf complete'
                    parts_control(False)
                    page.update()
                else:
                    inputpathlist.append(e.files[0].path)
                selector.set_image(inputpathlist)
                if selected_output_path.value is not None:
                    parts_control(False)
            selected_input_path.update()
            page.update()

        def pick_directory_result(e: ft.FilePickerResultEvent):
            if e.path:
                selected_input_path.value = e.path
                nonlocal inputpathlist, outputtxtlist, pdf_job_list
                inputpathlist.clear()
                outputtxtlist.clear()
                pdf_job_list.clear()

                for p in glob.glob(os.path.join(os.getcwd(), PDFTMPPATH, '*.jpg')):
                    if os.path.isfile(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
                os.makedirs(os.path.join(os.getcwd(), PDFTMPPATH), exist_ok=True)

                parts_control(True)
                crop_btn.disabled = True
                ocr_btn.disabled = True
                page.update()

                all_files_to_process = []
                pdf_filename_counter = Counter()

                for root, dirs, files in os.walk(e.path):
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
                            rel_path = os.path.relpath(inputpath, start=e.path)
                            filestem = os.path.splitext(rel_path)[0].replace(os.sep, '-')
                        else:
                            filestem = os.path.splitext(filename)[0]

                        if config_obj['langcode'] == 'ja':
                            progressmessage.value = 'pdfファイルの前処理中…… {} '.format(inputpath)
                        else:
                            progressmessage.value = 'preprocessing pdf…… {} '.format(inputpath)
                        page.update()

                        try:
                            render_pdf_preview(inputpath, filestem)
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

        def pick_output_result(e: ft.FilePickerResultEvent):
            nonlocal inputpathlist
            if e.path:
                selected_output_path.value = e.path
                selected_output_path.update()
                config_obj['selected_output_path'] = e.path
                save_config()
                selector.set_outputdir(e.path)
                capture_tool.outputdirpath = e.path
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
                preview_text.value = outputtxtlist[preview_index]
            preview_image.update()
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
                preview_text.value = outputtxtlist[preview_index]
            preview_image.update()
            preview_text.update()
            page.update()

        def handle_customize_dlg_modal_close(e):
            config_obj.update({
                'json': chkbx_json.value,
                'txt': chkbx_txt.value,
                'xml': chkbx_xml.value,
                'tei': chkbx_tei.value,
                'pdf': chkbx_pdf.value,
                'pdf_viztxt': chkbx_pdf_viztxt.value,
                'pdf_merge': chkbx_pdf_merge.value,
                'pdf_resolution': int(pdf_resolution_textfield.value),
            })
            save_config()
            page.close(customize_dlg_modal)

        def change_pdfstatus(e):
            chkbx_pdf_viztxt.disabled = not chkbx_pdf.value
            chkbx_pdf_viztxt.update()
            chkbx_pdf_merge.disabled = not chkbx_pdf.value
            chkbx_pdf_merge.update()
            pdf_resolution_textfield.disabled = not chkbx_pdf.value
            pdf_resolution_textfield.update()

        preview_image = ft.Image(src='dummy.dat', width=400, height=300, gapless_playback=True)
        preview_text = ft.Text(value='', height=300, selectable=True)

        pick_directory_dialog = ft.FilePicker(on_result=pick_directory_result)
        pick_output_dialog = ft.FilePicker(on_result=pick_output_result)
        pick_files_dialog = ft.FilePicker(on_result=pick_files_result)
        progressbar = ft.ProgressBar(width=400, value=0)
        selected_input_path = ft.Text(selectable=True)
        selected_output_path = ft.Text(config_obj['selected_output_path'], selectable=True)
        current_visualizeimgname = ft.Text(selectable=True)
        progressmessage = ft.Text()
        chkbx_visualize = ft.Checkbox(label=TRANSLATIONS['main_visualize_label'][config_obj['langcode']], value=True)
        chkbx_json = ft.Checkbox(label='JSON形式', value=config_obj['json'])
        chkbx_txt = ft.Checkbox(label='TXT形式', value=config_obj['txt'])
        chkbx_xml = ft.Checkbox(label='XML形式', value=config_obj['xml'])
        chkbx_tei = ft.Checkbox(label='TEI形式', value=config_obj['tei'])
        chkbx_pdf = ft.Checkbox(label='透明テキスト付PDF', value=config_obj['pdf'], on_change=change_pdfstatus)
        chkbx_pdf_viztxt = ft.Checkbox(label='PDFに青色で文字を重ねる', value=config_obj['pdf_viztxt'], disabled=not chkbx_pdf.value)
        chkbx_pdf_merge = ft.Checkbox(label='出力ファイルを1つのpdfにまとめる', value=config_obj['pdf_merge'], disabled=not chkbx_pdf.value)
        pdf_resolution_textfield = ft.TextField(label='pdfの出力解像度を指定する', value=str(config_obj['pdf_resolution']), width=200, disabled=not chkbx_pdf.value)
        file_upload_btn = ft.ElevatedButton(
            TRANSLATIONS['main_file_upload_btn'][config_obj['langcode']],
            icon=ft.Icons.UPLOAD_FILE,
            on_click=lambda _: pick_files_dialog.pick_files(allow_multiple=False),
        )
        directory_upload_btn = ft.ElevatedButton(
            TRANSLATIONS['main_directory_upload_btn'][config_obj['langcode']],
            icon=ft.Icons.FOLDER_OPEN,
            on_click=lambda _: pick_directory_dialog.get_directory_path(),
        )
        directory_output_btn = ft.ElevatedButton(
            TRANSLATIONS['main_directory_output_btn'][config_obj['langcode']],
            on_click=lambda _: pick_output_dialog.get_directory_path(),
        )
        ocr_btn = ft.ElevatedButton(
            text='OCR',
            on_click=ocr_button_result,
            style=ft.ButtonStyle(
                padding=30,
                shape=ft.RoundedRectangleBorder(radius=10),
            ),
            disabled=True,
        )
        stop_ocr_btn=ft.ElevatedButton(
            text=TRANSLATIONS["main_stop_ocr_btn"][config_obj["langcode"]],
            on_click=request_ocr_cancel,
            disabled=True)
        preview_image_col = ft.Column(
            controls=[preview_image],
            width=400,
            height=300,
            expand=False,
        )

        preview_image_int = ft.InteractiveViewer(
            min_scale=1,
            max_scale=10,
            boundary_margin=ft.margin.all(20),
            content=preview_image_col,
        )
        preview_text_col = ft.Column(
            controls=[preview_text],
            scroll=ft.ScrollMode.ALWAYS,
            width=600,
            height=300,
            expand=False,
        )
        preview_prev_btn = ft.ElevatedButton(text=TRANSLATIONS['main_prev_btn'][config_obj['langcode']], on_click=prev_image, disabled=True)
        preview_next_btn = ft.ElevatedButton(text=TRANSLATIONS['main_next_btn'][config_obj['langcode']], on_click=next_image, disabled=True)
        customize_btn = ft.ElevatedButton(TRANSLATIONS['main_customize_btn'][config_obj['langcode']], on_click=lambda e: page.open(customize_dlg_modal))
        customize_dlg_modal = ft.AlertDialog(
            modal=True,
            title=ft.Text(TRANSLATIONS['customize_dlg_modal_title'][config_obj['langcode']]),
            content=ft.Text(TRANSLATIONS['customize_dlg_modal_explain'][config_obj['langcode']]),
            actions=[
                chkbx_txt,
                chkbx_json,
                ft.Row([chkbx_xml, chkbx_tei]),
                ft.Row([chkbx_pdf, chkbx_pdf_viztxt]),
                ft.Row([chkbx_pdf_merge, pdf_resolution_textfield]),
                ft.TextButton('OK', on_click=handle_customize_dlg_modal_close),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        selector = ImageSelector(
            page,
            config_obj,
            detector=origin_detector,
            recognizer30=origin_recognizer30,
            recognizer50=origin_recognizer50,
            recognizer100=origin_recognizer,
            outputdirpath=selected_output_path.value,
        )

        capture_tool = CaptureTool(
            page,
            config_obj,
            detector=origin_detector,
            recognizer30=origin_recognizer30,
            recognizer50=origin_recognizer50,
            recognizer100=origin_recognizer,
        )
        if selected_output_path.value:
            capture_tool.outputdirpath = selected_output_path.value
        page.overlay.extend([
            customize_dlg_modal,
            pick_files_dialog,
            pick_directory_dialog,
            pick_output_dialog,
            selector.dialog,
            selector.zoom_dialog,
            selector.result_dialog,
            capture_tool.dialog,
            capture_tool.result_dialog,
        ])
        crop_btn = ft.ElevatedButton(
            text='Crop&OCR',
            on_click=selector.open_dialog,
            style=ft.ButtonStyle(
                padding=10,
                shape=ft.RoundedRectangleBorder(radius=10),
            ),
            disabled=True,
        )
        cap_btn = ft.ElevatedButton(
            text=TRANSLATIONS['main_cap_btn'][config_obj['langcode']],
            on_click=capture_tool.start_capture,
            style=ft.ButtonStyle(
                padding=10,
                shape=ft.RoundedRectangleBorder(radius=10),
            ),
            disabled=False,
        )
        explain_label = ft.Text(TRANSLATIONS['main_explain'][config_obj['langcode']])
        localebutton = ft.CupertinoSlidingSegmentedButton(
            selected_index=0 if config_obj['langcode'] == 'ja' else 1,
            thumb_color=ft.Colors.BLUE_400,
            on_change=handle_locale_change,
            controls=[ft.Text('日本語'), ft.Text('English')],
        )
        page.add(
            ft.Row([
                localebutton,
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
                ft.Column([chkbx_visualize, customize_btn]),
                ft.Column([progressmessage, progressbar]),
            ]),
            ft.Divider(),
            ft.Row([ft.Text(TRANSLATIONS['main_preview_label'][config_obj['langcode']]), preview_prev_btn, preview_next_btn, current_visualizeimgname]),
            ft.Row([preview_image_int, preview_text_col]),
        )
        page.update()

    renderui()


ft.app(main, assets_dir='assets')