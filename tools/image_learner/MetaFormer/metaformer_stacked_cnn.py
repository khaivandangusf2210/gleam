import logging
import torch
import torch.nn as nn
from typing import Dict, Any, Optional, List
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

logger = logging.getLogger(__name__)

SUPPORTED_PREFIXES = (
    'identityformer_',
    'randformer_',
    'poolformerv2_',
    'convformer_',
    'caformer_',
)

try:
    from metaformer_models import default_cfgs as META_DEFAULT_CFGS
    from metaformer_models import (
        identityformer_s12, identityformer_s24, identityformer_s36, identityformer_m36, identityformer_m48,
        randformer_s12, randformer_s24, randformer_s36, randformer_m36, randformer_m48,
        poolformerv2_s12, poolformerv2_s24, poolformerv2_s36, poolformerv2_m36, poolformerv2_m48,
        convformer_s18, caformer_s18
    )
    META_MODELS_AVAILABLE = True
    logger.info("MetaFormer models imported successfully")
except Exception as e:
    META_MODELS_AVAILABLE = False
    logger.warning(f"MetaFormer models not available: {e}")


def _resolve_metaformer_ctor(model_name: str):
    # Prefer getattr to avoid importing every factory explicitly
    try:
        from metaformer_models import __dict__ as _factories
        if model_name in _factories and callable(_factories[model_name]):
            return _factories[model_name]
    except Exception:
        pass
    return None


class MetaFormerStackedCNN(nn.Module):
    def __init__(
        self,
        height: int = 224,
        width: int = 224,
        num_channels: int = 3,
        output_size: int = 128,
        custom_model: str = "identityformer_s12",
        use_pretrained: bool = True,
        trainable: bool = True,
        conv_layers: Optional[List[Dict]] = None,
        num_conv_layers: Optional[int] = None,
        conv_activation: str = "relu",
        conv_dropout: float = 0.0,
        conv_norm: Optional[str] = None,
        conv_use_bias: bool = True,
        fc_layers: Optional[List[Dict]] = None,
        num_fc_layers: int = 1,
        fc_activation: str = "relu",
        fc_dropout: float = 0.0,
        fc_norm: Optional[str] = None,
        fc_use_bias: bool = True,
        **kwargs,
    ):
        super().__init__()
        print(f"MetaFormerStackedCNN encoder instantiated")
        print(f"Using MetaFormer model: {custom_model}")

        self.height = height
        self.width = width
        self.num_channels = num_channels
        self.output_size = output_size
        self.custom_model = custom_model
        self.use_pretrained = use_pretrained
        self.trainable = trainable

        logger.info(f"Initializing MetaFormerStackedCNN with model: {custom_model}")
        logger.info(f"Input: {num_channels}x{height}x{width} -> Output: {output_size}")

        self.channel_adapter = None
        if num_channels != 3:
            self.channel_adapter = nn.Conv2d(num_channels, 3, kernel_size=1, stride=1, padding=0)
            logger.info(f"Added channel adapter: {num_channels} -> 3 channels")

        self.size_adapter = None
        if height != 224 or width != 224:
            self.size_adapter = nn.AdaptiveAvgPool2d((224, 224))
            logger.info(f"Added size adapter: {height}x{width} -> 224x224")

        self.backbone = self._load_metaformer_backbone()
        self.feature_dim = self._get_feature_dim()

        self.fc_layers = self._create_fc_layers(
            input_dim=self.feature_dim,
            output_dim=output_size,
            num_layers=num_fc_layers,
            activation=fc_activation,
            dropout=fc_dropout,
            norm=fc_norm,
            use_bias=fc_use_bias,
            fc_layers_config=fc_layers,
        )

        if not trainable:
            for param in self.backbone.parameters():
                param.requires_grad = False
            logger.info("MetaFormer backbone frozen (trainable=False)")

        logger.info("MetaFormerStackedCNN initialized successfully")

    def _load_metaformer_backbone(self):
        if not META_MODELS_AVAILABLE:
            raise ImportError("MetaFormer models are not available")

        ctor = _resolve_metaformer_ctor(self.custom_model)
        if ctor is None:
            raise ValueError(f"Unknown MetaFormer model: {self.custom_model}")

        cfg = META_DEFAULT_CFGS.get(self.custom_model, {})
        weights_url = cfg.get('url')
        # track loading
        self._pretrained_loaded = False
        self._loaded_weights_url: Optional[str] = None
        if self.use_pretrained and weights_url:
            print(f"LOADING MetaFormer pretrained weights from: {weights_url}")
            logger.info(f"Loading pretrained weights from: {weights_url}")
        # Ensure we log whenever the factories call torch.hub.load_state_dict_from_url
        orig_loader = getattr(torch.hub, 'load_state_dict_from_url', None)
        def _wrapped_loader(url, *args, **kwargs):
            print(f"DOWNLOADING weights from: {url}")
            logger.info(f"DOWNLOADING weights from: {url}")
            self._pretrained_loaded = True
            self._loaded_weights_url = url
            result = orig_loader(url, *args, **kwargs)
            print(f"WEIGHTS DOWNLOADED successfully from: {url}")
            return result
        try:
            if self.use_pretrained and orig_loader is not None:
                torch.hub.load_state_dict_from_url = _wrapped_loader  # type: ignore[attr-defined]
            print(f"CREATING MetaFormer model: {self.custom_model} (pretrained={self.use_pretrained})")
            model = ctor(pretrained=self.use_pretrained, num_classes=1000)
            print(f"MetaFormer model CREATED: {self.custom_model}")
        finally:
            if orig_loader is not None:
                torch.hub.load_state_dict_from_url = orig_loader  # type: ignore[attr-defined]
        self._metaformer_weights_url = weights_url
        if self.use_pretrained:
            if self._pretrained_loaded:
                print(f"MetaFormer: pretrained weights loaded from {self._loaded_weights_url}")
                logger.info(f"MetaFormer: pretrained weights loaded from {self._loaded_weights_url}")
            else:
                # hard fail to guarantee correctness
                msg = "MetaFormer: pretrained weights were requested but not loaded"
                logger.error(msg)
                raise RuntimeError(msg)
        logger.info(f"Loaded MetaFormer backbone: {self.custom_model} (pretrained={self.use_pretrained})")
        return model

    def _get_feature_dim(self):
        with torch.no_grad():
            dummy_input = torch.randn(1, 3, 224, 224)
            features = self.backbone.forward_features(dummy_input)
            feature_dim = features.shape[-1]
        logger.info(f"MetaFormer feature dimension: {feature_dim}")
        return feature_dim

    def _create_fc_layers(self, input_dim, output_dim, num_layers, activation, dropout, norm, use_bias, fc_layers_config):
        layers = []
        if fc_layers_config:
            current_dim = input_dim
            for i, layer_config in enumerate(fc_layers_config):
                layer_output_dim = layer_config.get('output_size', output_dim if i == len(fc_layers_config) - 1 else current_dim)
                layers.append(nn.Linear(current_dim, layer_output_dim, bias=use_bias))
                if i < len(fc_layers_config) - 1:
                    if activation == "relu":
                        layers.append(nn.ReLU())
                    elif activation == "tanh":
                        layers.append(nn.Tanh())
                    elif activation == "sigmoid":
                        layers.append(nn.Sigmoid())
                    elif activation == "leaky_relu":
                        layers.append(nn.LeakyReLU())
                if dropout > 0:
                    layers.append(nn.Dropout(dropout))
                if norm == "batch":
                    layers.append(nn.BatchNorm1d(layer_output_dim))
                elif norm == "layer":
                    layers.append(nn.LayerNorm(layer_output_dim))
                current_dim = layer_output_dim
        else:
            if num_layers == 1:
                layers.append(nn.Linear(input_dim, output_dim, bias=use_bias))
            else:
                intermediate_dims = [input_dim]
                for i in range(num_layers - 1):
                    intermediate_dim = int(input_dim * (0.5 ** (i + 1)))
                    intermediate_dim = max(intermediate_dim, output_dim)
                    intermediate_dims.append(intermediate_dim)
                intermediate_dims.append(output_dim)
                for i in range(num_layers):
                    layers.append(nn.Linear(intermediate_dims[i], intermediate_dims[i + 1], bias=use_bias))
                    if i < num_layers - 1:
                        if activation == "relu":
                            layers.append(nn.ReLU())
                        elif activation == "tanh":
                            layers.append(nn.Tanh())
                        elif activation == "sigmoid":
                            layers.append(nn.Sigmoid())
                        elif activation == "leaky_relu":
                            layers.append(nn.LeakyReLU())
                    if dropout > 0:
                        layers.append(nn.Dropout(dropout))
                    if norm == "batch":
                        layers.append(nn.BatchNorm1d(intermediate_dims[i + 1]))
                    elif norm == "layer":
                        layers.append(nn.LayerNorm(intermediate_dims[i + 1]))
        return nn.Sequential(*layers)

    def forward(self, x):
        if x.shape[1] != 3:
            if self.channel_adapter is None:
                self.channel_adapter = nn.Conv2d(x.shape[1], 3, kernel_size=1, stride=1, padding=0).to(x.device)
                logger.info(f"Created dynamic channel adapter: {x.shape[1]} -> 3 channels")
            x = self.channel_adapter(x)
        if x.shape[2] != 224 or x.shape[3] != 224:
            if self.size_adapter is None:
                self.size_adapter = nn.AdaptiveAvgPool2d((224, 224)).to(x.device)
                logger.info(f"Created dynamic size adapter: {x.shape[2]}x{x.shape[3]} -> 224x224")
            x = self.size_adapter(x)
        features = self.backbone.forward_features(x)
        output = self.fc_layers(features)
        return {'encoder_output': output}

    @property
    def output_shape(self):
        return [self.output_size]


def create_metaformer_stacked_cnn(model_name: str, **kwargs) -> MetaFormerStackedCNN:
    encoder = MetaFormerStackedCNN(custom_model=model_name, **kwargs)
    return encoder


def patch_ludwig_stacked_cnn():
    return patch_ludwig_direct()


def _is_supported_metaformer(custom_model: Optional[str]) -> bool:
    return bool(custom_model) and custom_model.startswith(SUPPORTED_PREFIXES)


def patch_ludwig_direct():
    try:
        from ludwig.encoders.image.base import Stacked2DCNN
        original_stacked_cnn_init = Stacked2DCNN.__init__
        
        # Store custom_model in a global variable during config preparation
        _metaformer_model = getattr(patch_ludwig_direct, '_metaformer_model', None)

        def patched_stacked_cnn_init(self, *args, **kwargs):
            custom_model = getattr(patch_ludwig_direct, '_metaformer_model', None)
            
            if _is_supported_metaformer(custom_model):
                print(f"DETECTED MetaFormer model: {custom_model}")
                print(f"MetaFormer encoder is being loaded and used.")
                # Initialize base class to keep Ludwig internals intact
                original_stacked_cnn_init(self, *args, **kwargs)
                # Create our MetaFormer encoder and graft behavior
                mf_encoder = create_metaformer_stacked_cnn(custom_model, **kwargs)
                # ensure base attributes won't be used accidentally
                for attr in ("conv_layers", "fc_layers", "combiner", "output_shape", "reduce_output"):
                    if hasattr(self, attr):
                        try:
                            setattr(self, attr, getattr(mf_encoder, attr, None))
                        except Exception:
                            pass
                self.forward = mf_encoder.forward
                if hasattr(mf_encoder, 'backbone'):
                    self.backbone = mf_encoder.backbone
                if hasattr(mf_encoder, 'fc_layers'):
                    self.fc_layers = mf_encoder.fc_layers
                if hasattr(mf_encoder, 'custom_model'):
                    self.custom_model = mf_encoder.custom_model
                # explicit confirmation logs
                try:
                    url_info = getattr(mf_encoder, '_loaded_weights_url', None)
                    loaded_flag = getattr(mf_encoder, '_pretrained_loaded', False)
                    if loaded_flag and url_info:
                        print(f"CONFIRMED: MetaFormer '{custom_model}' using pretrained weights from: {url_info}")
                        logger.info(f"CONFIRMED: MetaFormer '{custom_model}' using pretrained weights from: {url_info}")
                    else:
                        print(f"CONFIRMED: MetaFormer '{custom_model}' using randomly initialized weights (no pretrained)")
                        logger.info(f"CONFIRMED: MetaFormer '{custom_model}' using randomly initialized weights")
                except Exception:
                    pass
            else:
                original_stacked_cnn_init(self, *args, **kwargs)

        Stacked2DCNN.__init__ = patched_stacked_cnn_init
        return True
    except Exception as e:
        logger.error(f"Failed to apply MetaFormer direct patch: {e}")
        return False


def set_current_metaformer_model(model_name: str):
    """Store the current MetaFormer model name for the patch to use."""
    patch_ludwig_direct._metaformer_model = model_name


