from PIL import Image
import time
import yaml
import onnxruntime
import numpy as np
import cv2
from typing import Tuple, List

class PARSEQ:
    def __init__(self,
                 model_path: str,
                 charlist: [str],
                 original_size: Tuple[int, int] = (384, 32),
                 device: str = "CPU") -> None:
        self.model_path = model_path
        self.charlist = charlist

        self.device = device
        self.image_width, self.image_height = original_size
        self.create_session()

    def create_session(self) -> None:
        opt_session = onnxruntime.SessionOptions()
        opt_session.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        #opt_session.enable_cpu_mem_arena = False 
        #opt_session.execution_mode = onnxruntime.ExecutionMode.ORT_PARALLEL
        #opt_session.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_DISABLE_ALL
        providers = ['CPUExecutionProvider']
        if self.device.casefold() == "cpu":
            opt_session.intra_op_num_threads = 1
            opt_session.inter_op_num_threads = 1
        elif self.device.casefold() == "cuda":
            providers = ['CUDAExecutionProvider','CPUExecutionProvider']
        session = onnxruntime.InferenceSession(self.model_path,opt_session, providers=providers)
        self.session = session
        self.model_inputs = self.session.get_inputs()
        self.input_names = [self.model_inputs[i].name for i in range(len(self.model_inputs))]
        self.input_shape = self.model_inputs[0].shape
        self.model_output = self.session.get_outputs()
        self.output_names = [self.model_output[i].name for i in range(len(self.model_output))]
        self.input_height, self.input_width = self.input_shape[2:]

    def postprocess(self, outputs):
        predictions = np.squeeze(outputs).T
        scores = np.max(predictions[:, 4:], axis=1)
        predictions = predictions[scores > self.conf_thresold, :]
        scores = scores[scores > self.conf_thresold]
        class_ids = np.argmax(predictions[:, 4:], axis=1)

    def preprocess(self, img: np.ndarray) -> np.ndarray:
        h,w=img.shape[:2]
        if h>w*0.8:
            img=cv2.rotate(img,cv2.ROTATE_90_COUNTERCLOCKWISE)
        resized=cv2.resize(img,(self.input_width, self.input_height),interpolation=cv2.INTER_LINEAR)
        input_image=np.ascontiguousarray(resized[:,:,::-1]).astype(np.float32)
        input_image/=127.5
        input_image-=1.0
        input_image = input_image.transpose(2,0,1)
        return input_image[np.newaxis, :, :, :]
    
    def read(self, img: np.ndarray) -> List:
        if img is None or img.size == 0:
            return ""
        input_tensor = self.preprocess(img)
        outputs = self.session.run(self.output_names, {self.input_names[0]: input_tensor})[0]
        indices = np.argmax(outputs[0], axis=1)
        #print(indices.size,indices)
        stop_idx = np.where(indices == 0)[0]
        end_pos = stop_idx[0] if stop_idx.size > 0 else len(indices)
        resval = indices[:end_pos].tolist()
        resstr = "".join([self.charlist[i - 1] for i in resval])
        return resstr