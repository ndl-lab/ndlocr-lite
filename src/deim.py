from PIL import Image, ImageDraw
import time
import yaml
import onnxruntime
import numpy as np
import cv2
from typing import Tuple, List
import xml.etree.ElementTree as ET

class DEIM:
    def __init__(self,
                 model_path: str,
                 class_mapping_path: str,
                 original_size: Tuple[int, int] = (1280,1280),
                 score_threshold: float = 0.1,
                 conf_threshold: float = 0.1,
                 iou_threshold: float = 0.4,
                 device: str = "CPU") -> None:
        self.model_path = model_path
        self.class_mapping_path = class_mapping_path
        self.image_width, self.image_height = original_size
        self.device = device
        self.score_threshold = score_threshold
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.colorlist=[(0, 0, 0), (255, 0, 0), (0, 0, 142), (0, 0, 230), (106, 0, 228),
                        (0, 60, 100), (0, 80, 100), (0, 0, 70), (0, 0, 192), (250, 170, 30),
                        (100, 170, 30), (220, 220, 0), (175, 116, 175), (250, 0, 30),(165, 42, 42), (255, 77, 255),(255,0,0)]
        self.create_session()

    def create_session(self) -> None:
        opt_session = onnxruntime.SessionOptions()
        opt_session.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        #opt_session.execution_mode = onnxruntime.ExecutionMode.ORT_PARALLEL
        #ExecutionMode.ORT_PARALLEL
        #opt_session.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_DISABLE_ALL
        providers = ['CPUExecutionProvider']
        if self.device.casefold() == "cuda":
            providers = ['CUDAExecutionProvider','CPUExecutionProvider']
        session = onnxruntime.InferenceSession(self.model_path,opt_session, providers=providers)
        self.session = session
        self.model_inputs = self.session.get_inputs()
        self.input_names = [self.model_inputs[i].name for i in range(len(self.model_inputs))]
        self.input_shape = self.model_inputs[0].shape
        self.model_output = self.session.get_outputs()
        self.output_names = [self.model_output[i].name for i in range(len(self.model_output))]
        self.input_height, self.input_width = self.input_shape[2:]

        if self.class_mapping_path is not None:
            with open(self.class_mapping_path, 'r') as file:
                yaml_file = yaml.safe_load(file)
                self.classes = yaml_file['names']
                self.color_palette = np.random.uniform(0, 255, size=(len(self.classes), 3))

    def preprocess(self, img: np.ndarray) -> np.ndarray:
        max_wh=max(img.shape[0],img.shape[1])
        paddedimg=np.zeros((max_wh,max_wh,3),dtype=np.uint8)
        paddedimg[:img.shape[0],:img.shape[1],:]=img
        self.image_width=max_wh
        self.image_height=max_wh
        resized=cv2.resize(paddedimg,(self.input_width, self.input_height),interpolation=cv2.INTER_CUBIC)
        input_image=resized.astype(np.float32)
        input_image/=255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        input_image-=mean
        input_image/=std
        input_image = input_image.transpose(2,0,1)
        input_tensor = input_image[np.newaxis, :, :, :].astype(np.float32)
        return input_tensor
    
    def xywh2xyxy(self, x):
        # Convert bounding box (x, y, w, h) to bounding box (x1, y1, x2, y2)
        y = np.copy(x)
        y[..., 0] = x[..., 0] - x[..., 2] / 2
        y[..., 1] = x[..., 1] - x[..., 3] / 2
        y[..., 2] = x[..., 0] + x[..., 2] / 2
        y[..., 3] = x[..., 1] + x[..., 3] / 2
        return y 
    
    def postprocess(self, outputs):
        
        if len(outputs)==4:
            class_ids,bboxes,scores,char_counts=outputs
            class_ids=np.squeeze(class_ids)
            predictions = np.squeeze(bboxes)
            scores=np.squeeze(scores)
            char_counts=np.squeeze(char_counts)
        elif len(outputs)==3:
            class_ids,bboxes,scores=outputs
            class_ids=np.squeeze(class_ids)
            predictions = np.squeeze(bboxes)
            scores=np.squeeze(scores)
            char_counts=np.array([100.0]*scores.shape[0])

        predictions = predictions[scores > self.conf_threshold, :]
        scores = scores[scores > self.conf_threshold]
        scales = np.array([
            self.image_width / self.input_width, 
            self.image_height / self.input_width, 
            self.image_width / self.input_width, 
            self.image_height / self.input_width
        ], dtype=np.float32)
        boxes = (predictions[:, :4] * scales).astype(np.int32)
        detections = []
        for bbox, score, label,char_count in zip(boxes, scores, class_ids,char_counts):
            class_index=int(label)-1
            detections.append({
                "class_index": class_index,
                "confidence": score,
                "box": bbox,
                "pred_char_count":char_count,
                "class_name": self.classes[class_index]#"line_main"
            })
        #print(len(detections))
        #print(char_counts)
        return detections
    
    def get_label_name(self, class_id: int) -> str:
        return self.classes[class_id]
        
    def detect(self, img: np.ndarray) -> List:
        input_tensor = self.preprocess(img)
        #print(self.input_shape)
        outputs = self.session.run(self.output_names, {self.input_names[0]: input_tensor,self.input_names[1]:np.array([[self.input_height, self.input_width]],np.int64)})
        return self.postprocess(outputs)
    
    def draw_detections(self, npimg: np.ndarray, detections: List):
        pil_image = Image.fromarray(npimg)
        draw = ImageDraw.Draw(pil_image)
        for detection in detections:
            # バウンディングボックスの座標を抽出
            x1, y1, x2, y2 = detection['box']
            class_id = detection['class_index']
            confidence = detection['confidence']
            # クラスIDに対する色を取得
            color = self.colorlist[class_id]  # RGB形式で青色
            # 画像にバウンディングボックスを描画
            draw.rectangle([x1, y1, x2, y2], outline=color, width=2)
        return pil_image
    
    def drawxml_detections(self, npimg: np.ndarray, xmlstr: str,categories:dict,outputimgpath:str):
        root = ET.fromstring(xmlstr)
        pil_image = Image.fromarray(npimg)
        draw = ImageDraw.Draw(pil_image)
        for child in root.iter():
            if child.tag=="LINE":
                class_id=categories[child.get("TYPE")]["id"]
                x1=int(child.get("X"))
                y1=int(child.get("Y"))
                x2=x1+int(child.get("WIDTH"))
                y2=y1+int(child.get("HEIGHT"))
            elif child.tag=="POLYGON":
                class_id=0
                xlist=[int(t) for t in child.get("POINTS").split(",") ][0::2]
                ylist=[int(t) for t in child.get("POINTS").split(",") ][1::2]
                x1,y1,x2,y2=min(xlist),min(ylist),max(xlist),max(ylist)
            else:
                continue
            color = self.colorlist[class_id]
            draw.rectangle([x1, y1, x2, y2], outline=color, width=4)
        pil_image.save(outputimgpath)
            
