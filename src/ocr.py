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
    assert os.path.isfile(classes_path), f"There's no classes file with name {weights_path}"
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
    assert os.path.isfile(classes_path), f"There's no classes file with name {weights_path}"

    charobj=None
    with open(classes_path,encoding="utf-8") as f:
        charobj=safe_load(f)
    charlist=list(charobj["model"]["charset_train"])
    
    recognizer = PARSEQ(model_path=weights_path,charlist=charlist,device=args.device)
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

def process(args):
    rawinputpathlist=[]
    inputpathlist=[]
    if args.sourcedir is not None:
        for inputpath in glob.glob(os.path.join(args.sourcedir,"*")):
            rawinputpathlist.append(inputpath)
    if args.sourceimg is not None:
        rawinputpathlist.append(args.sourceimg)
    
    for inputpath in rawinputpathlist:
        ext=inputpath.split(".")[-1]
        if ext.lower() in ["jpg","png","tiff","jp2","tif","jpeg","bmp"]:
            inputpathlist.append(inputpath)
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
                    type_name = classeslist[c_idx] if c_idx < len(classeslist) else "本文"
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
                "id": idx,"isVertical": "true","text": resultlinesall[idx],"isTextline": "true","confidence": conf, "class_index": c_idx}
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
    parser.add_argument("--output", type=str, required=True, help="Path to output directory")
    parser.add_argument("--viz", type=bool, required=False, help="Save visualized image",default=False)
    parser.add_argument("--det-weights", type=str, required=False, help="Path to deim onnx file", default=str(base_dir / "model" / "deim-s-1024x1024.onnx"))
    parser.add_argument("--det-classes", type=str, required=False, help="Path to list of class in yaml file", default=str(base_dir / "config" / "ndl.yaml"))
    parser.add_argument("--det-score-threshold", type=float, required=False, default=0.2)
    parser.add_argument("--det-conf-threshold", type=float, required=False, default=0.25)
    parser.add_argument("--det-iou-threshold", type=float, required=False, default=0.2)
    parser.add_argument("--simple-mode", type=bool, required=False, help="Read line with one model(Setting this option to True will slow down processing, but it simplifies the architecture and may slightly improve accuracy.)",default=False)
    parser.add_argument("--rec-weights30", type=str, required=False, help="Path to parseq-tiny onnx file", default=str(base_dir / "model" / "parseq-ndl-16x256-30-tiny-192epoch-tegaki3.onnx"))
    parser.add_argument("--rec-weights50", type=str, required=False, help="Path to parseq-tiny onnx file", default=str(base_dir / "model" / "parseq-ndl-16x384-50-tiny-146epoch-tegaki2.onnx"))
    parser.add_argument("--rec-weights", type=str, required=False, help="Path to parseq-tiny onnx file", default=str(base_dir / "model" / "parseq-ndl-16x768-100-tiny-165epoch-tegaki2.onnx"))
    parser.add_argument("--rec-classes", type=str, required=False, help="Path to list of class in yaml file", default=str(base_dir / "config" / "NDLmoji.yaml"))
    parser.add_argument("--device", type=str, required=False, help="Device use (cpu or cuda)", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--json-only", action="store_true", help="Disable .xml and .txt output and only output JSON")
    args = parser.parse_args()
    process(args)

if __name__=="__main__":
    main()