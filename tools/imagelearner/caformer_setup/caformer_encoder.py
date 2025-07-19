import logging

import torch
import torch.nn as nn

logger = logging.getLogger(__name__)

try:
    from .caformer_models import caformer_s18, caformer_b36, caformer_s36, caformer_m36
    CAFORMER_AVAILABLE = True
    logger.info("CAFormer models imported successfully")
except ImportError as e:
    logger.warning(f"CAFormer models not available: {e}")
    CAFORMER_AVAILABLE = False


class CAFormerFeatureExtractor(nn.Module):
    def __init__(self,
                 model_name: str = "caformer_s18",
                 input_size: tuple = (224, 224),
                 num_channels: int = 3,
                 output_size: int = 512,
                 use_pretrained: bool = True):
        super().__init__()

        self.model_name = model_name
        self.input_size = input_size
        self.num_channels = num_channels
        self.output_size = output_size
        self.use_pretrained = use_pretrained

        self.channel_adapter = None
        if num_channels != 3:
            self.channel_adapter = nn.Conv2d(num_channels, 3, kernel_size=1, stride=1, padding=0)
            logger.info(f"Added channel adapter: {num_channels} -> 3 channels")

        self.size_adapter = None
        if input_size != (224, 224):
            self.size_adapter = nn.AdaptiveAvgPool2d((224, 224))
            logger.info(f"Added size adapter: {input_size} -> (224, 224)")

        self.caformer = self._load_caformer_model()
        self.feature_dim = self._get_feature_dim()

        if self.feature_dim != output_size:
            self.output_projection = nn.Linear(self.feature_dim, output_size)
        else:
            self.output_projection = nn.Identity()

        logger.info(f"CAFormer feature extractor initialized: {model_name}")
        logger.info(f"Input: {num_channels}x{input_size[0]}x{input_size[1]} -> Output: {output_size}")

    def _load_caformer_model(self):
        if not CAFORMER_AVAILABLE:
            raise ImportError("CAFormer models are not available")

        model_map = {
            'caformer_s18': caformer_s18,
            'caformer_s36': caformer_s36,
            'caformer_m36': caformer_m36,
            'caformer_b36': caformer_b36,
        }

        if self.model_name not in model_map:
            raise ValueError(f"Unknown CAFormer model: {self.model_name}")

        model = model_map[self.model_name](pretrained=self.use_pretrained, num_classes=1000)
        logger.info(f"Loaded {self.model_name} (pretrained={self.use_pretrained})")
        return model

    def _get_feature_dim(self):
        if hasattr(self.caformer, 'head') and hasattr(self.caformer.head, 'fc1'):
            return self.caformer.head.fc1.in_features
        elif hasattr(self.caformer, 'head') and hasattr(self.caformer.head, 'in_features'):
            return self.caformer.head.in_features
        else:
            with torch.no_grad():
                dummy_input = torch.randn(1, 3, 224, 224)
                features = self.caformer.forward_features(dummy_input)
                return features.shape[-1]

    def forward(self, x):
        if self.channel_adapter is not None:
            x = self.channel_adapter(x)

        if self.size_adapter is not None:
            x = self.size_adapter(x)

        features = self.caformer.forward_features(x)
        output = self.output_projection(features)

        return output


class LudwigCAFormerEncoder(nn.Module):
    def __init__(self,
                 height: int = 224,
                 width: int = 224,
                 num_channels: int = 3,
                 out_channels: int = 512,
                 model_name: str = "caformer_s18",
                 use_pretrained: bool = True,
                 **kwargs):
        super().__init__()

        self.height = height
        self.width = width
        self.num_channels = num_channels
        self.out_channels = out_channels
        self.model_name = model_name
        self.use_pretrained = use_pretrained

        self.feature_extractor = CAFormerFeatureExtractor(
            model_name=model_name,
            input_size=(height, width),
            num_channels=num_channels,
            output_size=out_channels,
            use_pretrained=use_pretrained
        )

        logger.info(f"Ludwig CAFormer encoder initialized: {model_name}")

    def forward(self, inputs, **kwargs):
        features = self.feature_extractor(inputs)

        return {
            'encoder_output': features,
            'encoder_output_state': features
        }

    @property
    def output_shape(self):
        return [self.out_channels]


def register_caformer_with_ludwig():
    if not CAFORMER_AVAILABLE:
        logger.warning("CAFormer models not available, skipping registration")
        return False

    try:
        from ludwig.encoders.registry import register_encoder
        from ludwig.encoders.image.base import ImageEncoder

        class CAFormerImageEncoder(ImageEncoder):
            def __init__(self,
                         height: int = 224,
                         width: int = 224,
                         num_channels: int = 3,
                         model_name: str = "caformer_s18",
                         use_pretrained: bool = True,
                         **kwargs):
                super().__init__()
                self.encoder = LudwigCAFormerEncoder(
                    height=height,
                    width=width,
                    num_channels=num_channels,
                    model_name=model_name,
                    use_pretrained=use_pretrained,
                    **kwargs
                )

            def forward(self, inputs, **kwargs):
                return self.encoder(inputs, **kwargs)

            @property
            def output_shape(self):
                return self.encoder.output_shape

        register_encoder("caformer", CAFormerImageEncoder)
        logger.info("CAFormer encoder registered with Ludwig")
        return True

    except ImportError as e:
        logger.warning(f"Could not register CAFormer with Ludwig: {e}")
        return False


def test_caformer_encoder():
    """Test the CAFormer encoder functionality"""
    if not CAFORMER_AVAILABLE:
        print("CAFormer models not available, skipping test")
        return

    try:
        # Test feature extractor
        extractor = CAFormerFeatureExtractor(
            model_name="caformer_s18",
            input_size=(224, 224),
            num_channels=3,
            output_size=512,
            use_pretrained=False
        )

        # Test forward pass
        dummy_input = torch.randn(2, 3, 224, 224)
        output = extractor(dummy_input)

        print("  CAFormer feature extractor test passed")
        print(f"  Input shape: {dummy_input.shape}")
        print(f"  Output shape: {output.shape}")

        # Test Ludwig encoder
        encoder = LudwigCAFormerEncoder(
            height=224,
            width=224,
            num_channels=3,
            out_channels=512,
            model_name="caformer_s18",
            use_pretrained=False
        )
        ludwig_output = encoder(dummy_input)

        print("  Ludwig CAFormer encoder test passed")
        print(f"  Output keys: {list(ludwig_output.keys())}")
        print(f"  Encoder output shape: {ludwig_output['encoder_output'].shape}")

    except Exception as e:
        print(f" CAFormer encoder test failed: {e}")


if __name__ == "__main__":
    test_caformer_encoder()
