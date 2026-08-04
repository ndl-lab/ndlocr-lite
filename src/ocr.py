import sys
sys.setrecursionlimit(5000)
import os
import numpy as np
from PIL import Image
import xml.etree.ElementTree as ET
from pathlib import Path
from deim import DEIM
from parseq import PARSEQ

from yaml import safe_load
from concurrent.futures import ThreadPoolExecutor
import time
import shutil
import json
import glob
from reading_order.xy_cut.eval import eval_xml
from ndl_parser import convert_to_xml_string3

class RecogLine:
    def __init__(self,npimg:np.ndarray,idx:int,pred_char_cnt:int,pred_str:str=""):
        self.npimg = npimg
        self.idx   = idx
        self.pred_char_cnt = pred_char_cnt
        self.pred_str = pred_str
    def __lt__(self, other):
        return self.idx < other.idx

def process_cascade(alllineobj:RecogLine,recognizer30,recognizer50,recognizer100,is_cascade=True):
    targetdflist30,targetdflist50,targetdflist100,targetdflist200=[],[],[],[]
    for lineobj in alllineobj:
        if lineobj.pred_char_cnt==3 and is_cascade:
            targetdflist30.append(lineobj)
        elif lineobj.pred_char_cnt==2 and is_cascade:
            targetdflist50.append(lineobj)
        else:
            targetdflist100.append(lineobj)
    targetdflistall=[]
    with ThreadPoolExecutor(thread_name_prefix="thread") as executor:
        resultlines30,resultlines50,resultlines100,resultlines200=[],[],[],[]
        if len(targetdflist30)>0:
            resultlines30 = executor.map(recognizer30.read, [t.npimg for t in targetdflist30])
            resultlines30 = list(resultlines30)
        for i in range(len(targetdflist30)):
            pred_str=resultlines30[i]
            lineobj=targetdflist30[i]
            if len(pred_str)>=25:
                targetdflist50.append(lineobj)
            else:
                lineobj.pred_str=pred_str
                targetdflistall.append(lineobj)
        if len(targetdflist50)>0:
            resultlines50 = executor.map(recognizer50.read, [t.npimg for t in targetdflist50])
            resultlines50 = list(resultlines50)
        for i in range(len(targetdflist50)):
            pred_str=resultlines50[i]
            lineobj=targetdflist50[i]
            if len(pred_str)>=45:
                targetdflist100.append(lineobj)
            else:
                lineobj.pred_str=pred_str
                targetdflistall.append(lineobj)
        if len(targetdflist100)>0:
            resultlines100 = executor.map(recognizer100.read, [t.npimg for t in targetdflist100])
            resultlines100 = list(resultlines100)
        for i in range(len(targetdflist100)):
            pred_str=resultlines100[i]
            lineobj=targetdflist100[i]
            lineobj.pred_str=pred_str
            if len(pred_str)>=98 and lineobj.npimg.shape[0]<lineobj.npimg.shape[1]:
                baseimg=lineobj.npimg
                tmplineobj_1=RecogLine(npimg=baseimg[:,:baseimg.shape[1]//2,:],idx=lineobj.idx,pred_char_cnt=100)
                tmplineobj_2=RecogLine(npimg=baseimg[:,baseimg.shape[1]//2:,:],idx=lineobj.idx,pred_char_cnt=100)
                targetdflist200.append(tmplineobj_1)
                targetdflist200.append(tmplineobj_2)
            else:
                targetdflistall.append(lineobj)
        if len(targetdflist200)>0:
            resultlines200 = executor.map(recognizer100.read, [t.npimg for t in targetdflist200])
            resultlines200 = list(resultlines200)
            for i in range(0,len(targetdflist200)-1,2):
                ia=targetdflist200[i]
                lineobj=RecogLine(npimg=None,idx=ia.idx,pred_char_cnt=100,pred_str=resultlines200[i]+resultlines200[i+1])
                targetdflistall.append(lineobj)
        targetdflistall=sorted(targetdflistall)
        resultlinesall=[t.pred_str for t in targetdflistall]
    return resultlinesall

def get_detector(args):
    weights_path = args.det_weights
    classes_path = args.det_classes
    assert os.path.isfile(weights_path), f"There's no weight file with name {weights_path}"
    assert os.path.isfile(classes_path), f"There's no classes file with name {classes_path}"
    detector = DEIM(model_path=weights_path,
                      class_mapping_path=classes_path,
                      score_threshold=args.det_score_threshold,
                      conf_threshold=args.det_conf_threshold,
                      iou_threshold=args.det_iou_threshold,
                      device=args.device)
    return detector

def get_recognizer(args,weights_path=None):
    if weights_path is None:
        weights_path = args.rec_weights
    classes_path = args.rec_classes

    assert os.path.isfile(weights_path), f"There's no weight file with name {weights_path}"
    assert os.path.isfile(classes_path), f"There's no classes file with name {classes_path}"

    charobj=None
    with open(classes_path,encoding="utf-8") as f:
        charobj=safe_load(f)
    charlist=list(charobj["model"]["charset_train"])
    
    recognizer = PARSEQ(model_path=weights_path,charlist=charlist,device=args.device)
    if getattr(args, 'enable_tcy', False):
        from tcy_wrapper import TateChuYokoWrapper
        tcy_kwargs = {k: v for k, v in vars(args).items() if k.startswith('tcy_') and k != 'enable_tcy' and v is not None}
        recognizer = TateChuYokoWrapper(recognizer, **tcy_kwargs)
    return recognizer

def inference_on_detector(args,inputname:str,npimage:np.ndarray,outputpath:str,issaveimg:bool=True):
    print("[INFO] Intialize Model")
    detector = get_detector(args)
    print("[INFO] Inference Image")
    detections = detector.detect(npimage)
    classeslist=list(detector.classes.values())
    if issaveimg:
        drawimage = npimage.copy()
        pil_image =detector.draw_detections(drawimage, detections=detections)
        os.makedirs(outputpath,exist_ok=True)
        output_filepath = os.path.join(outputpath,f"viz_{Path(inputname).name}")
        if output_filepath.split(".")[-1]=="jp2":
            output_filepath=output_filepath[:-4]+".jpg"
        print(f"[INFO] Saving result on {output_filepath}")
        pil_image.save(output_filepath)
    return detections,classeslist

def process_detector(detector,inputname:str,npimage:np.ndarray,outputpath:str,issaveimg:bool=True):
    detections = detector.detect(npimage)
    classeslist=list(detector.classes.values())
    if issaveimg:
        drawimage = npimage.copy()
        pil_image =detector.draw_detections(drawimage, detections=detections)
        os.makedirs(outputpath,exist_ok=True)
        output_filepath = os.path.join(outputpath,f"viz_{Path(inputname).name}")
        if output_filepath.split(".")[-1]=="jp2":
            output_filepath=output_filepath[:-4]+".jpg"
        print(f"[INFO] Saving result on {output_filepath}")
        pil_image.save(output_filepath)
    return detections,classeslist

def _run_ocr_on_image_array(
    detector,
    recognizer30,
    recognizer50,
    recognizer100,
    inputname: str,
    img: np.ndarray,
    outputpath: str,
    save_viz: bool = False,
):
    img_h, img_w = img.shape[:2]
    detections, classeslist = process_detector(
        detector=detector,
        inputname=inputname,
        npimage=img,
        outputpath=outputpath,
        issaveimg=save_viz,
    )
    resultobj = [dict(), dict()]
    resultobj[0][0] = list()
    for i in range(17):
        resultobj[1][i] = []
    for det in detections:
        xmin, ymin, xmax, ymax = det["box"]
        conf = det["confidence"]
        if det["class_index"] == 0:
            resultobj[0][0].append([xmin, ymin, xmax, ymax])
        resultobj[1][det["class_index"]].append([xmin, ymin, xmax, ymax, conf, det["pred_char_count"]])

    xmlstr = convert_to_xml_string3(img_w, img_h, inputname, classeslist, resultobj)
    xmlstr = "<OCRDATASET>" + xmlstr + "</OCRDATASET>"
    root = ET.fromstring(xmlstr)
    eval_xml(root, logger=None)

    alllineobj = []
    tatelinecnt = 0
    alllinecnt = 0

    for idx, lineobj in enumerate(root.findall(".//LINE")):
        xmin = int(lineobj.get("X"))
        ymin = int(lineobj.get("Y"))
        line_w = int(lineobj.get("WIDTH"))
        line_h = int(lineobj.get("HEIGHT"))
        try:
            pred_char_cnt = float(lineobj.get("PRED_CHAR_CNT"))
        except Exception:
            pred_char_cnt = 100.0
        if line_h > line_w:
            tatelinecnt += 1
        alllinecnt += 1
        lineimg = img[ymin:ymin + line_h, xmin:xmin + line_w, :]
        alllineobj.append(RecogLine(lineimg, idx, pred_char_cnt))

    if len(alllineobj) == 0 and len(detections) > 0:
        page = root.find("PAGE")
        for idx, det in enumerate(detections):
            xmin, ymin, xmax, ymax = det["box"]
            line_w = int(xmax - xmin)
            line_h = int(ymax - ymin)
            if line_w <= 0 or line_h <= 0:
                continue
            line_elem = ET.SubElement(page, "LINE")
            c_idx = int(det["class_index"])
            type_name = "本文"
            line_elem.set("TYPE", type_name)
            line_elem.set("X", str(int(xmin)))
            line_elem.set("Y", str(int(ymin)))
            line_elem.set("WIDTH", str(line_w))
            line_elem.set("HEIGHT", str(line_h))
            line_elem.set("CONF", f"{det['confidence']:0.3f}")
            pred_char_cnt = det.get("pred_char_count", 100.0)
            line_elem.set("PRED_CHAR_CNT", f"{pred_char_cnt:0.3f}")
            if line_h > line_w:
                tatelinecnt += 1
            alllinecnt += 1
            lineimg = img[int(ymin):int(ymax), int(xmin):int(xmax), :]
            alllineobj.append(RecogLine(lineimg, idx, pred_char_cnt))

    resultlinesall = process_cascade(
        alllineobj,
        recognizer30,
        recognizer50,
        recognizer100,
        is_cascade=True,
    )

    resjsonarray = []
    text_layer_lines = []
    for idx, lineobj in enumerate(root.findall(".//LINE")):
        text = resultlinesall[idx] if idx < len(resultlinesall) else ""
        lineobj.set("STRING", text)
        xmin = int(lineobj.get("X"))
        ymin = int(lineobj.get("Y"))
        line_w = int(lineobj.get("WIDTH"))
        line_h = int(lineobj.get("HEIGHT"))
        is_vertical = line_h > line_w
        try:
            conf = float(lineobj.get("CONF"))
        except Exception:
            conf = 0.0

        type_str = lineobj.get("TYPE", "")
        c_idx = classeslist.index(type_str) if type_str in classeslist else 1
        resjsonarray.append({
            "boundingBox": [
                [xmin, ymin],
                [xmin, ymin + line_h],
                [xmin + line_w, ymin],
                [xmin + line_w, ymin + line_h],
            ],
            "id": idx,
            "isVertical": "true" if is_vertical else "false",
            "text": text,
            "isTextline": "true",
            "confidence": conf,
            "class_index": c_idx,
        })
        text_layer_lines.append({
            "x": xmin,
            "y": ymin,
            "width": line_w,
            "height": line_h,
            "text": text,
            "is_vertical": is_vertical,
        })

    page_xml = ET.tostring(root.find("PAGE"), encoding="unicode")
    page_text = "\n".join(resultlinesall)
    return {
        "page_xml": page_xml,
        "text": page_text,
        "json_lines": resjsonarray,
        "text_layer_lines": text_layer_lines,
        "img_width": img_w,
        "img_height": img_h,
        "img_name": inputname,
        "line_count": alllinecnt,
        "vertical_line_count": tatelinecnt,
    }

def _text_layer_font_size(width: float, height: float, text: str, is_vertical: bool):
    text_len = max(len(text), 1)
    if is_vertical:
        size = min(width * 0.95, (height / text_len) * 1.8)
    else:
        size = min(height * 0.95, (width / text_len) * 1.8)
    return max(1.0, min(size, 72.0))

def _draw_text_layer_line(
    canvas_obj,
    line: dict,
    img_width: int,
    img_height: int,
    page_width: float,
    page_height: float,
    visible: bool,
):
    text = line["text"]
    if not text:
        return
    scale_x = page_width / max(img_width, 1)
    scale_y = page_height / max(img_height, 1)
    x = line["x"] * scale_x
    y_top = page_height - line["y"] * scale_y
    width = line["width"] * scale_x
    height = line["height"] * scale_y
    if width <= 0 or height <= 0:
        return
    is_vertical = line["is_vertical"]
    fontsize = _text_layer_font_size(width, height, text, is_vertical)
    if is_vertical:
        canvas_obj.setFont("HeiseiMin-W3", fontsize)
        draw_x = x + width * 0.5
        draw_y = y_top
    else:
        canvas_obj.setFont("HeiseiKakuGo-W5", fontsize)
        draw_x = x
        draw_y = y_top - height + max((height - fontsize) * 0.5, 0)
    if visible:
        canvas_obj.setFillColorRGB(0, 0, 1)
    canvas_obj.drawString(draw_x, draw_y, text)

def embed_text_layer_pdf(input_pdf: str, output_pdf: str, page_results: list, visible_text: bool = False):
    try:
        from io import BytesIO
        from pypdf import PdfReader, PdfWriter
        from reportlab.pdfgen import canvas
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    except ImportError as exc:
        raise RuntimeError(
            "PDF text-layer output requires pypdf and reportlab. Install dependencies from requirements.txt."
        ) from exc

    output_path = Path(output_pdf)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.resolve() == Path(input_pdf).resolve():
        raise ValueError("Output PDF must be different from the input PDF.")

    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5", isVertical=False))
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3", isVertical=True))

    reader = PdfReader(input_pdf)
    if len(reader.pages) != len(page_results):
        raise ValueError(f"PDF page count mismatch: {len(reader.pages)} pages, {len(page_results)} OCR results")

    writer = PdfWriter()
    if reader.metadata:
        writer.add_metadata({key: str(value) for key, value in reader.metadata.items() if value is not None})
    for page, page_result in zip(reader.pages, page_results):
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)
        overlay_buffer = BytesIO()
        overlay_canvas = canvas.Canvas(overlay_buffer, pagesize=(page_width, page_height))
        if visible_text:
            overlay_canvas.setFillColorRGB(0, 0, 1)
        else:
            overlay_canvas.setFillAlpha(0)
        for line in page_result["text_layer_lines"]:
            _draw_text_layer_line(
                canvas_obj=overlay_canvas,
                line=line,
                img_width=page_result["img_width"],
                img_height=page_result["img_height"],
                page_width=page_width,
                page_height=page_height,
                visible=visible_text,
            )
        overlay_canvas.save()
        overlay_buffer.seek(0)
        overlay_reader = PdfReader(overlay_buffer)
        page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)

    with open(output_path, "wb") as wf:
        writer.write(wf)

def process_pdf_documents(args, pdf_paths: list[str]):
    try:
        import pypdfium2
    except ImportError as exc:
        raise RuntimeError(
            "PDF input requires pypdfium2. Install dependencies from requirements.txt."
        ) from exc
    try:
        import pypdf  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "PDF text-layer output requires pypdf. Install dependencies from requirements.txt."
        ) from exc

    if len(pdf_paths) > 1 and getattr(args, "pdf_output", None):
        print("--pdf-output can only be used with a single PDF input.")
        return

    if not os.path.exists(args.output):
        print("Output Directory is not found.")
        return

    print("[INFO] Intialize Model")
    detector = get_detector(args)
    recognizer100 = get_recognizer(args=args)
    recognizer30 = get_recognizer(args=args, weights_path=args.rec_weights30)
    recognizer50 = get_recognizer(args=args, weights_path=args.rec_weights50)

    render_scale = max(float(getattr(args, "pdf_render_dpi", 150)), 1.0) / 72.0

    for pdf_path in pdf_paths:
        start = time.time()
        pdf_path_obj = Path(pdf_path)
        output_stem = pdf_path_obj.stem
        pdf_doc = pypdfium2.PdfDocument(str(pdf_path_obj))
        page_results = []
        all_json_contents = []
        page_infos = []
        all_text_pages = []
        all_page_xml = []

        for page_index in range(len(pdf_doc)):
            page_name = f"{output_stem}_{page_index + 1:05}.png"
            print(f"[INFO] OCR PDF page {page_index + 1}/{len(pdf_doc)}: {pdf_path_obj.name}")
            rendered_pages = pdf_doc.render(
                pypdfium2.PdfBitmap.to_pil,
                page_indices=[page_index],
                scale=render_scale,
            )
            pil_image = next(iter(rendered_pages)).convert("RGB")
            img = np.array(pil_image)
            page_result = _run_ocr_on_image_array(
                detector=detector,
                recognizer30=recognizer30,
                recognizer50=recognizer50,
                recognizer100=recognizer100,
                inputname=page_name,
                img=img,
                outputpath=args.output,
                save_viz=args.viz,
            )
            page_results.append(page_result)
            all_json_contents.append(page_result["json_lines"])
            page_infos.append({
                "page_index": page_index,
                "img_width": page_result["img_width"],
                "img_height": page_result["img_height"],
                "img_name": page_result["img_name"],
            })
            all_text_pages.append(page_result["text"])
            all_page_xml.append(page_result["page_xml"])

        pdf_doc.close()

        if not getattr(args, "json_only", False):
            with open(os.path.join(args.output, output_stem + ".xml"), "w", encoding="utf-8") as wf:
                wf.write("<OCRDATASET>\n")
                wf.write("\n".join(all_page_xml))
                wf.write("\n</OCRDATASET>")
            with open(os.path.join(args.output, output_stem + ".txt"), "w", encoding="utf-8") as wtf:
                wtf.write("\n\n".join(all_text_pages))

        with open(os.path.join(args.output, output_stem + ".json"), "w", encoding="utf-8") as wf:
            alljsonobj = {
                "contents": all_json_contents,
                "pdfinfo": {
                    "pdf_path": str(pdf_path_obj),
                    "pdf_name": pdf_path_obj.name,
                    "page_count": len(page_results),
                    "render_dpi": float(getattr(args, "pdf_render_dpi", 150)),
                },
                "pages": page_infos,
            }
            wf.write(json.dumps(alljsonobj, ensure_ascii=False, indent=2))

        output_pdf = getattr(args, "pdf_output", None)
        if not output_pdf:
            output_pdf = os.path.join(args.output, output_stem + "_text.pdf")
        print(f"[INFO] Writing text-layer PDF: {output_pdf}")
        embed_text_layer_pdf(
            input_pdf=str(pdf_path_obj),
            output_pdf=output_pdf,
            page_results=page_results,
            visible_text=getattr(args, "pdf_visible_text", False),
        )
        print("Total PDF calculation time:", time.time() - start)

def process(args):
    rawinputpathlist=[]
    inputpathlist=[]
    pdfpathlist=[]
    if args.sourcedir is not None:
        for inputpath in glob.glob(os.path.join(args.sourcedir,"*")):
            rawinputpathlist.append(inputpath)
    if args.sourceimg is not None:
        rawinputpathlist.append(args.sourceimg)
    if args.sourcepdf is not None:
        pdfpathlist.append(args.sourcepdf)
    for inputpath in rawinputpathlist:
        ext=inputpath.split(".")[-1]
        if ext.lower() in ["jpg","png","tiff","jp2","tif","jpeg","bmp","webp"]:
            inputpathlist.append(inputpath)
        elif ext.lower() in ["pdf",]:
            pdfpathlist.append(inputpath)

    if len(pdfpathlist) > 0:
        process_pdf_documents(args, pdfpathlist)
        if len(inputpathlist) == 0:
            return
    if len(inputpathlist)==0:
        print("Images are not found.")
        return
    if not os.path.exists(args.output):
        print("Output Directory is not found.")
        return
    
    detector=get_detector(args)
    recognizer100=get_recognizer(args=args)
    recognizer30=get_recognizer(args=args,weights_path=args.rec_weights30)
    recognizer50=get_recognizer(args=args,weights_path=args.rec_weights50)
    tatelinecnt=0
    alllinecnt=0
    
    for inputpath in inputpathlist:
        ext=inputpath.split(".")[-1]
        pil_image = Image.open(inputpath).convert('RGB')
        img = np.array(pil_image)
        start = time.time()
        allxmlstr="<OCRDATASET>\n"
        alltextlist=[]
        resjsonarray=[]
        imgname=os.path.basename(inputpath)
        img_h,img_w=img.shape[:2]
        detections,classeslist=process_detector(detector,inputname=imgname,npimage=img,outputpath=args.output,issaveimg=args.viz)
        e1=time.time()
        resultobj=[dict(),dict()]
        resultobj[0][0]=list()
        for i in range(17):
            resultobj[1][i]=[]
        for det in detections:
            xmin,ymin,xmax,ymax=det["box"]
            conf=det["confidence"]
            char_count=det["pred_char_count"]
            if det["class_index"]==0:
                resultobj[0][0].append([xmin,ymin,xmax,ymax])
            resultobj[1][det["class_index"]].append([xmin,ymin,xmax,ymax,conf,char_count])
        xmlstr=convert_to_xml_string3(img_w, img_h, imgname, classeslist, resultobj)
        xmlstr="<OCRDATASET>"+xmlstr+"</OCRDATASET>"
        # print(xmlstr)
        root = ET.fromstring(xmlstr)
        eval_xml(root, logger=None)
        alllineobj = []
        alltextlist = []

        for idx, lineobj in enumerate(root.findall(".//LINE")):
            xmin = int(lineobj.get("X"))
            ymin = int(lineobj.get("Y"))
            line_w = int(lineobj.get("WIDTH"))
            line_h = int(lineobj.get("HEIGHT"))
            try:
                pred_char_cnt = float(lineobj.get("PRED_CHAR_CNT"))
            except:
                pred_char_cnt = 100.0
            
            if line_h > line_w:
                tatelinecnt += 1
            alllinecnt += 1
            # 部分画像の切り出し
            lineimg = img[ymin:ymin+line_h, xmin:xmin+line_w, :]
            linerecogobj = RecogLine(lineimg, idx, pred_char_cnt)
            alllineobj.append(linerecogobj)

        if len(alllineobj) == 0 and len(detections) > 0:
            # LINE 要素がないが検出がある場合は検出領域を LINE として扱う
            page = root.find("PAGE")
            for idx, det in enumerate(detections):
                xmin, ymin, xmax, ymax = det["box"]
                line_w = int(xmax - xmin)
                line_h = int(ymax - ymin)
                if line_w > 0 and line_h > 0:
                    line_elem = ET.SubElement(page, "LINE")
                    c_idx = int(det["class_index"])
                    type_name = "本文"
                    line_elem.set("TYPE", type_name)
                    line_elem.set("X", str(int(xmin)))
                    line_elem.set("Y", str(int(ymin)))
                    line_elem.set("WIDTH", str(line_w))
                    line_elem.set("HEIGHT", str(line_h))
                    line_elem.set("CONF", f"{det['confidence']:0.3f}")
                    pred_char_cnt = det.get("pred_char_count", 100.0)
                    line_elem.set("PRED_CHAR_CNT", f"{pred_char_cnt:0.3f}")
                    if line_h > line_w:
                        tatelinecnt += 1
                    alllinecnt += 1
                    lineimg = img[int(ymin):int(ymax), int(xmin):int(xmax), :]
                    linerecogobj = RecogLine(lineimg, idx, pred_char_cnt)
                    alllineobj.append(linerecogobj)

        # 認識プロセス
        resultlinesall = process_cascade(
            alllineobj, recognizer30, recognizer50, recognizer100, is_cascade=True
        )
        alltextlist.append("\n".join(resultlinesall))
        
        for idx,lineobj in enumerate(root.findall(".//LINE")):
            lineobj.set("STRING",resultlinesall[idx])
            xmin=int(lineobj.get("X"))
            ymin=int(lineobj.get("Y"))
            line_w=int(lineobj.get("WIDTH"))
            line_h=int(lineobj.get("HEIGHT"))
            try:
                conf=float(lineobj.get("CONF"))
            except:
                conf=0.0
            
            # XML TYPE -> c_idx
            type_str = lineobj.get("TYPE", "")
            c_idx = classeslist.index(type_str) if type_str in classeslist else 1

            jsonobj={"boundingBox": [[xmin,ymin],[xmin,ymin+line_h],[xmin+line_w,ymin],[xmin+line_w,ymin+line_h]],
                "id": idx,"isVertical": "true" if line_h > line_w else "false","text": resultlinesall[idx],"isTextline": "true","confidence": conf, "class_index": c_idx}
            resjsonarray.append(jsonobj)

        allxmlstr+=(ET.tostring(root.find("PAGE"), encoding='unicode')+"\n")
        allxmlstr+="</OCRDATASET>"
        if alllinecnt>0 and tatelinecnt/alllinecnt>0.5:
            alltextlist=alltextlist[::-1]
        output_stem = os.path.splitext(os.path.basename(inputpath))[0]
        
        if not getattr(args, "json_only", False):
            with open(os.path.join(args.output,output_stem+".xml"),"w",encoding="utf-8") as wf:
                wf.write(allxmlstr)
                
        with open(os.path.join(args.output,output_stem+".json"),"w",encoding="utf-8") as wf:
            alljsonobj={
                "contents":[resjsonarray],
                "imginfo": {
                    "img_width": img_w,
                    "img_height": img_h,
                    "img_path":inputpath,
                    "img_name":os.path.basename(inputpath)
                }
            }
            alljsonstr=json.dumps(alljsonobj,ensure_ascii=False,indent=2)
            wf.write(alljsonstr)
            
        if not getattr(args, "json_only", False):
            with open(os.path.join(args.output,output_stem+".txt"),"w",encoding="utf-8") as wtf:
                wtf.write("\n".join(alltextlist))
        print("Total calculation time (Detection + Recognition):",time.time()-start)

def main():
    import argparse
    from pathlib import Path
    base_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Arguments for NDLkotenOCR-Lite")

    parser.add_argument("--sourcedir", type=str, required=False, help="Path to image directory")
    parser.add_argument("--sourceimg", type=str, required=False, help="Path to image directory")
    parser.add_argument("--sourcepdf", type=str, required=False, help="Path to source PDF")
    parser.add_argument("--output", type=str, required=True, help="Path to output directory")
    parser.add_argument("--viz", type=bool, required=False, help="Save visualized image",default=False)
    parser.add_argument("--pdf-output", type=str, required=False, help="Path to output text-layer PDF")
    parser.add_argument("--pdf-render-dpi", "--pdf-dpi", dest="pdf_render_dpi", type=float, required=False, default=150.0, help="DPI used to render PDF pages for OCR")
    parser.add_argument("--pdf-visible-text", action="store_true", help="Draw PDF text layer visibly in blue for debugging")
    parser.add_argument("--det-weights", type=str, required=False, help="Path to deim onnx file", default=str(base_dir / "model" / "deim-s-1024x1024.onnx"))
    parser.add_argument("--det-classes", type=str, required=False, help="Path to list of class in yaml file", default=str(base_dir / "config" / "ndl.yaml"))
    parser.add_argument("--det-score-threshold", type=float, required=False, default=0.2)
    parser.add_argument("--det-conf-threshold", type=float, required=False, default=0.25)
    parser.add_argument("--det-iou-threshold", type=float, required=False, default=0.2)
    parser.add_argument("--simple-mode", type=bool, required=False, help="Read line with one model(Setting this option to True will slow down processing, but it simplifies the architecture and may slightly improve accuracy.)",default=False)
    parser.add_argument("--rec-weights30", type=str, required=False, help="Path to parseq-tiny onnx file", default=str(base_dir / "model" / "parseq-ndl-24x256-30-tiny-189epoch-tegaki3-r8data-202604.onnx"))
    parser.add_argument("--rec-weights50", type=str, required=False, help="Path to parseq-tiny onnx file", default=str(base_dir / "model" / "parseq-ndl-24x384-50-tiny-300epoch-tegaki3-r8data-202604.onnx"))
    parser.add_argument("--rec-weights", type=str, required=False, help="Path to parseq-tiny onnx file", default=str(base_dir / "model" / "parseq-ndl-24x768-100-tiny-153epoch-tegaki3-r8data-202604.onnx"))
    parser.add_argument("--rec-classes", type=str, required=False, help="Path to list of class in yaml file", default=str(base_dir / "config" / "NDLmoji.yaml"))
    parser.add_argument("--device", type=str, required=False, help="Device use (cpu or cuda)", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--enable-tcy", action="store_true", dest="enable_tcy", default=False, help="Enable tate-chuu-yoko (縦中横) detection for vertical text (e.g. newspaper OCR)")
    parser.add_argument("--json-only", action="store_true", help="Disable .xml and .txt output and only output JSON")
    args, remaining = parser.parse_known_args()
    if args.enable_tcy and remaining:
        from tcy_wrapper import add_tcy_arguments
        tcy_parser = add_tcy_arguments(parser)
        tcy_args = tcy_parser.parse_args(remaining)
        for k, v in vars(tcy_args).items():
            if v is not None:
                setattr(args, k, v)
    args = parser.parse_args()
    process(args)

if __name__=="__main__":
    main()
