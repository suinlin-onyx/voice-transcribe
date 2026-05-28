# FunASR 转录服务

实时语音转文字系统，基于 v4 三层分离架构。支持 PyTorch GPU / ONNX CPU 双引擎切换。

## 快速开始

```bash
cd D:\arvin\obsidian_workpace\su_obs_voice\voice-transcribe
py -3.11 run.py
```

**端口：**
- WebSocket: `ws://127.0.0.1:9886`
- HTTP: `http://127.0.0.1:9887`

## 引擎切换

通过 `config/settings.json` 切换：

```json
// GPU 模式 — SEACO Paraformer，支持热词，需 CUDA
"asr": { "engine": "funasr", "device": "cuda", "model": "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch", "hotwords_enabled": true }
"punc": { "enabled": true, "model": "ct-punc" }

// CPU 模式 — Paraformer-large ONNX INT8，无需 GPU
"asr": { "engine": "onnx", "device": "cpu", "model": "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx", "hotwords_enabled": false }
"punc": { "enabled": true, "model": "ct-punc-onnx" }
```

## 完整对比

### 磁盘占用

| 模型 | PyTorch | ONNX | 节省 |
|------|---------|------|------|
| ASR | 953 MB (SEACO) | 228 MB | -725 MB |
| VAD | 4 MB | 0.5 MB | -3.5 MB |
| PUNC | 1.2 GB | 1.0 GB | -200 MB |
| **合计** | **2.2 GB** | **1.2 GB** | **-1 GB** |

### 运行时内存

| 指标 | GPU (PyTorch 全) | CPU (ONNX 全) |
|------|------|------|
| ASR 模型 RAM | 1,107 MB | 820 MB |
| ASR 模型 VRAM | 955 MB | **0 MB** |
| 全部加载后 RAM | 1,665 MB | ~1,800 MB |
| 全部加载后 VRAM | 2,040 MB | **0 MB** |
| **进程总占用** | **~3.7 GB** | **~1.8 GB** |

### 功能对比

| | GPU (funasr) | CPU (onnx) |
|------|------|------|
| ASR 模型 | SEACO Paraformer-large | Paraformer-large INT8 |
| ASR 精度 | — | 同精度 (官方 benchmark CER 0% 损失) |
| 推理速度 | GPU 加速 | RTF 0.045 (实时 22x) |
| 热词支持 | 支持 | 不支持 |
| GPU 需求 | CUDA ≥2GB 显存 | 无需 GPU |
| 推理设备 | GPU | CPU, intra_op 4 线程 |

## 模型清单

| 模型 ID | 大小 | 用途 |
|------|------|------|
| `iic/speech_paraformer-large_...-onnx` | 228 MB | ASR (ONNX CPU) |
| `iic/speech_seaco_paraformer_large_...-pytorch` | 953 MB | ASR (PyTorch GPU 备用) |
| `iic/speech_fsmn_vad_...-pytorch` | 4 MB | VAD 语音检测 |
| `iic/punc_ct-transformer_...-large` | 1.2 GB | PUNC 标点 (PyTorch) |
| `punc_damo/...-onnx` | 1.0 GB | PUNC 标点 (ONNX) |
| `vad_damo/...-onnx` | 0.5 MB | VAD (ONNX, funasr-onnx-server 用) |

## 项目结构

```
voice-transcribe/
├── run.py                    — 入口
├── config.py                 — 配置加载
├── logger.py                 — 日志
├── requirements.txt          — 依赖
├── config/settings.json      — 配置文件
├── server/
│   ├── api_server.py         — WebSocket/HTTP API 层
│   └── model_server.py       — 模型推理管线 (VAD→ASR→PUNC→输出)
├── processors/
│   ├── asr_processor.py      — ASR 识别 (FunASR/ONNX 双引擎)
│   ├── vad_processor.py      — VAD 语音检测 (FSMN-VAD)
│   ├── punc_processor.py     — 标点恢复 (PyTorch/ONNX)
│   ├── text_processor.py     — 文本后处理 (去重/换行/格式)
│   ├── hotword_manager.py    — 热词管理
│   ├── audio_source.py       — 音频源 (麦克风)
│   └── _model_path.py        — 模型路径映射
├── utils/
│   └── single_instance.py    — 单实例锁
├── interfaces/               — 接口定义
├── hotwords/                 — 热词文件
├── docs/                     — 文档
└── logs/                     — 日志
```

## 服务控制

```bash
curl http://127.0.0.1:9887/health                       # 健康检查
curl -X POST http://127.0.0.1:9887/control/start_recording  # 开始
curl -X POST http://127.0.0.1:9887/control/stop_recording   # 停止
```

## 文档

- [统一协议文档](./PROTOCOL.md)
- [架构设计](./ARCHITECTURE_V4.md)
