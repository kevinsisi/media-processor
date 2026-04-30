#!/usr/bin/env bash
set -euo pipefail

echo "[1/3] Host nvidia-smi (WSL2 should show RTX 2070):"
nvidia-smi || { echo "FAIL: nvidia-smi not available on host"; exit 1; }

echo
echo "[2/3] Docker GPU passthrough via nvidia/cuda image:"
docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi || {
  echo "FAIL: docker --gpus all did not surface GPU";
  exit 1;
}

echo
echo "[3/3] PyTorch CUDA availability:"
docker run --rm --gpus all pytorch/pytorch:2.5.0-cuda12.4-cudnn9-runtime python -c \
  "import torch; print('cuda_available:', torch.cuda.is_available()); print('device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE')"

echo
echo "All GPU checks passed."
