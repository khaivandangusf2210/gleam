FROM pytorch/pytorch:2.0.0-cuda11.7-cudnn8-runtime

LABEL maintainer="GLEAM Project" \
      version="1.0.0" \
      description="Docker image for TRIDENT Galaxy tools"

# Install system dependencies
RUN export DEBIAN_FRONTEND=noninteractive && \
    apt-get update --fix-missing -qq && \
    apt-get install -y -q \
    build-essential \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    openslide-tools \
    python3-openslide \
    git \
    wget \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /trident

# Clone TRIDENT repository
RUN git clone https://github.com/mahmoodlab/TRIDENT.git /trident && \
    cd /trident && \
    git checkout main

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
    ipywidgets \
    tqdm \
    h5py \
    matplotlib \
    opencv-python \
    geopandas \
    huggingface_hub \
    transformers \
    timm==0.9.16 \
    einops-exts \
    scipy \
    segmentation-models-pytorch \
    safetensors \
    openslide-python \
    Pillow \
    pandas

# Install TRIDENT in editable mode
RUN pip install -e /trident

# Set environment variables
ENV PYTHONPATH="/trident:$PYTHONPATH"
ENV HF_HOME="/trident/hf_cache"
ENV TORCH_HOME="/trident/hf_cache"

# Create cache directory
RUN mkdir -p /trident/hf_cache

# Set default command
CMD ["/bin/bash"]
