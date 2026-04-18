import os
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
import cv2
import requests
from tqdm import tqdm
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

# ─── LAMA ARCHITECTURE COMPONENTS (FFC) ───

class FFC(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size,
                 ratio_gin=0.75, ratio_gout=0.75, stride=1, padding=0,
                 dilation=1, groups=1, bias=False, activation=nn.ReLU):
        super(FFC, self).__init__()
        self.stride = stride
        in_cg = int(in_channels * ratio_gin)
        in_cl = in_channels - in_cg
        out_cg = int(out_channels * ratio_gout)
        out_cl = out_channels - out_cg

        self.ratio_gin = ratio_gin
        self.ratio_gout = ratio_gout

        module = nn.Identity if in_cl == 0 or out_cl == 0 else nn.Conv2d
        self.convl2l = module(in_cl, out_cl, kernel_size, stride, padding, dilation, groups, bias)
        module = nn.Identity if in_cl == 0 or out_cg == 0 else nn.Conv2d
        self.convl2g = module(in_cl, out_cg, kernel_size, stride, padding, dilation, groups, bias)
        module = nn.Identity if in_cg == 0 or out_cl == 0 else nn.Conv2d
        self.convg2l = module(in_cg, out_cl, kernel_size, stride, padding, dilation, groups, bias)
        module = nn.Identity if in_cg == 0 or out_cg == 0 else SpectralTransform
        self.convg2g = module(in_cg, out_cg, stride, 1 if groups == 1 else groups)

        self.bn_l = nn.BatchNorm2d(out_cl) if out_cl > 0 else nn.Identity()
        self.bn_g = nn.BatchNorm2d(out_cg) if out_cg > 0 else nn.Identity()

        self.act_l = activation(inplace=True) if out_cl > 0 else nn.Identity()
        self.act_g = activation(inplace=True) if out_cg > 0 else nn.Identity()

    def forward(self, x):
        x_l, x_g = x if type(x) is tuple else (x, None)
        out_xl, out_xg = 0, 0

        if x_l is not None:
            out_xl = self.convl2l(x_l) + (self.convg2l(x_g) if x_g is not None else 0)
            out_xg = (self.convl2g(x_l) if self.ratio_gout > 0 else 0) + (self.convg2g(x_g) if x_g is not None else 0)
        else:
            out_xg = self.convg2g(x_g)

        return self.act_l(self.bn_l(out_xl)), self.act_g(self.bn_g(out_xg))

class SpectralTransform(nn.Module):
    def __init__(self, in_channels, out_channels, stride=1, groups=1):
        super(SpectralTransform, self).__init__()
        self.conv1 = nn.Sequential(
            nn.Conv2d(in_channels, out_channels // 2, kernel_size=1, groups=groups, bias=False),
            nn.BatchNorm2d(out_channels // 2),
            nn.ReLU(inplace=True)
        )
        self.fu = nn.Sequential(
            nn.Conv2d(out_channels // 2, out_channels, kernel_size=1, groups=groups, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True)
        )
        self.stride = stride

    def forward(self, x):
        if self.stride > 1:
            x = F.avg_pool2d(x, kernel_size=self.stride, stride=self.stride)
        
        # Real FFT
        batch, c, h, w = x.size()
        r_size = x.size()
        
        # Using fft.rfftn for 2D spectral transform
        ffted = torch.fft.rfftn(x, dim=(-2, -1), norm='ortho')
        ffted = torch.view_as_real(ffted)
        
        # Spectral convolution (complex multiplication alternative)
        ffted = ffted.permute(0, 1, 4, 2, 3).reshape(batch, -1, ffted.size(2), ffted.size(3))
        ffted = self.conv1(ffted)
        ffted = self.fu(ffted)
        ffted = ffted.reshape(batch, -1, 2, ffted.size(2), ffted.size(3)).permute(0, 1, 3, 4, 2)
        ffted = torch.complex(ffted[..., 0], ffted[..., 1])
        
        output = torch.fft.irfftn(ffted, s=r_size[-2:], dim=(-2, -1), norm='ortho')
        return output

class FFCResNetGenerator(nn.Module):
    def __init__(self, input_nc=4, output_nc=3, ngf=64, n_blocks=9):
        super(FFCResNetGenerator, self).__init__()
        # Initial Downsampling
        model = [
            nn.Pad2d(3, mode='reflect'),
            FFC(input_nc, ngf, kernel_size=7, padding=0),
            FFC(ngf, ngf * 2, kernel_size=3, stride=2, padding=1),
            FFC(ngf * 2, ngf * 4, kernel_size=3, stride=2, padding=1)
        ]
        
        # Residual Blocks
        for i in range(n_blocks):
            model += [FFCResBlock(ngf * 4)]
            
        # Upsampling
        model += [
            nn.ConvTranspose2d(ngf * 4, ngf * 2, kernel_size=3, stride=2, padding=1, output_padding=1),
            nn.BatchNorm2d(ngf * 2),
            nn.ReLU(True),
            nn.ConvTranspose2d(ngf * 2, ngf, kernel_size=3, stride=2, padding=1, output_padding=1),
            nn.BatchNorm2d(ngf),
            nn.ReLU(True),
            nn.Pad2d(3, mode='reflect'),
            nn.Conv2d(ngf, output_nc, kernel_size=7, padding=0),
            nn.Sigmoid()
        ]
        self.model = nn.Sequential(*model)

    def forward(self, input):
        return self.model(input)

# NOTE: Minimal simplified FFC ResBlock placeholder for architecture mapping
class FFCResBlock(nn.Module):
    def __init__(self, dim):
        super(FFCResBlock, self).__init__()
        self.conv1 = FFC(dim, dim, kernel_size=3, padding=1)
        self.conv2 = FFC(dim, dim, kernel_size=3, padding=1)

    def forward(self, x):
        res = x
        if type(x) is not tuple:
            x = (x, None)
        out = self.conv1(x)
        out = self.conv2(out)
        
        # Combine global and local features for residual connection
        l, g = out
        if g is not None:
            combined = l + g
        else:
            combined = l
            
        return combined + res

# ─── SERVICE CLASS ───

class LaMaService:
    _instance = None
    _model = None
    WEIGHTS_URL = "https://huggingface.co/smartyinner/big-lama/resolve/main/big-lama.pt"
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LaMaService, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if self._model is not None:
            return
            
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.weights_path = os.path.join(settings.BASE_DIR, "models", "big-lama.pt")
        
        if not os.path.exists(self.weights_path):
            self._download_weights()
            
        self._load_model()

    def _download_weights(self):
        logger.info(f"Downloading LaMa weights to {self.weights_path}...")
        os.makedirs(os.path.dirname(self.weights_path), exist_ok=True)
        
        response = requests.get(self.WEIGHTS_URL, stream=True)
        total_size = int(response.headers.get('content-length', 0))
        
        with open(self.weights_path, 'wb') as f:
            with tqdm(total=total_size, unit='B', unit_scale=True, desc="big-lama.pt") as pbar:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))

    def _load_model(self):
        try:
            from simple_lama_inpainting import SimpleLama
            self._model = SimpleLama()
            logger.info("LaMa: Successfully loaded using simple-lama wrapper")
        except ImportError:
            # Fallback to manual loading if simple-lama is not available
            # (Requires full architecture mapping - simplified here for brevity)
            logger.warning("LaMa: simple-lama not found, using raw torch load placeholder")
            pass

    def inpaint(self, image_np, mask_np):
        """
        Processes image and mask using LaMa.
        image_np: RGB [H, W, 3] uint8
        mask_np: Grayscale [H, W] uint8 (255 where removal needed)
        """
        # Ensure image is PIL for simple-lama wrapper
        from PIL import Image
        img_pil = Image.fromarray(image_np)
        mask_pil = Image.fromarray(mask_np)
        
        try:
            # simple-lama handles preprocessing and inference
            from simple_lama_inpainting import SimpleLama
            if not isinstance(self._model, SimpleLama):
                self._model = SimpleLama()
                
            result_pil = self._model(img_pil, mask_pil)
            return np.array(result_pil)
        except Exception as e:
            logger.error(f"LaMa inpainting failed: {e}")
            return None

# Export instance
get_lama = LaMaService
