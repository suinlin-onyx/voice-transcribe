# FunASR 转录服务 - 分层架构设计

## 项目结构

```
voice-transcribe/
├── interfaces/              # 接口定义层
│   ├── __init__.py         # 接口基类: IAudioSource, IVADProcessor, IASRProcessor...
│   └── socket_transport.py # Socket通信
├── processors/              # 处理器层 (可独立替换)
│   ├── __init__.py
│   ├── audio_source.py      # 音频采集: 麦克风/文件/流
│   ├── vad_processor.py    # VAD检测: SenseVoice/FSMN/simple
│   ├── asr_processor.py     # ASR识别: SenseVoice/Paraformer
│   ├── punc_processor.py    # 标点恢复: ct-punc
│   ├── hotword_manager.py   # 热词管理: 从文档提取
│   └── output_buffer.py     # 输出缓冲: 批量发送/确认
├── transcribe_server_v2.py  # 主服务器 (分层编排)
├── transcribe_server.py     # 原版服务器 (单文件)
└── README_ARCHITECTURE.md  # 本文档
```

## 架构概览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          TranscribeServer                               │
│                           (主入口/协调器)                                │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
        ▼                           ▼                           ▼
┌───────────────┐         ┌───────────────┐         ┌───────────────┐
│  AudioSource  │         │  VADProcessor │         │ HotwordManager│
│   (采集层)     │────────▶│   (VAD层)     │────────▶│   (热词层)     │
└───────────────┘  raw    └───────────────┘  vad    └───────────────┘
  麦克风/文件      audio    语音检测/分段      segments    热词注入
        │                           │                           │
        │                           ▼                           │
        │                 ┌───────────────┐                    │
        │                 │   ASRProcessor │                    │
        │                 │    (ASR层)      │◀───────────────────┘
        │                 └───────────────┘           hotwords
        │                   语音识别/转写               │
        │                           │                  │
        │                           ▼                  │
        │                 ┌───────────────┐           │
        │                 │  PuncProcessor │           │
        │                 │   (标点层)      │           │
        │                 └───────────────┘           │
        │                   标点恢复                   │
        │                           │                  │
        │                           ▼                  │
        │                 ┌───────────────┐           │
        │                 │ OutputBuffer  │◀──────────┘
        │                 │  (输出缓冲)    │  punctuation
        │                 └───────────────┘
        │                   批量发送/确认
        │                           │
        ▼                           ▼
┌───────────────────────────────────────────────┐
│              SocketTransport                    │
│               (通信层)                          │
│         TCP/Unix Socket 客户端通信              │
└───────────────────────────────────────────────┘
```

## 分层职责

### 1. AudioSource (音频采集层)

- **职责**: 统一音频输入源
- **实现**: 麦克风采集、音频文件、WAV流
- **接口**: `start()`, `stop()`, `on_audio(callback)`

### 2. VADProcessor (语音检测层)

- **职责**: 语音活动检测、说话人分离、静音检测
- **配置**: 可调参数
  - `threshold`: VAD 阈值
  - `min_speech_duration`: 最小语音时长
  - `max_speech_duration`: 最大语音时长
  - `silence_duration`: 静音阈值
- **接口**: `process(audio) -> List[SpeechSegment]`

### 3. ASRProcessor (语音识别层)

- **职责**: 语音到文本的转换
- **可配置**: SenseVoice / Paraformer / 其他模型
- **接口**: `recognize(segment) -> TextResult`
- **支持**: 热词注入 `set_hotwords(words)`

### 4. PuncProcessor (标点层)

- **职责**: 标点恢复
- **模型**: ct-punc (默认) / 端到端模型
- **接口**: `punctuate(text) -> PunctuatedText`

### 5. HotwordManager (热词管理层)

- **职责**: 
  - 热词加载 (文件/数据库/API)
  - 热词格式化 (注入ASR)
  - 热词动态更新
- **接口**: 
  - `load_from_file(path)`
  - `load_from_documents(doc_paths)`
  - `get_hotwords() -> List[str]`
  - `add_hotword(word)`, `remove_hotword(word)`

### 6. OutputBuffer (输出缓冲层)

- **职责**: 
  - 批量发送 (时间/字数阈值)
  - 重传机制
  - 格式保证 (不丢内容)
- **接口**: `push(text)`, `flush()`, `on_ack(callback)`

### 7. SocketTransport (通信层)

- **职责**: 
  - 命令解析
  - 数据传输
  - 连接管理
- **接口**: `send(data)`, `recv() -> Command`

## 消息流

```
[Audio] ──▶ [VAD] ──▶ [ASR + Hotword] ──▶ [Punc] ──▶ [Buffer] ──▶ [Socket] ──▶ [Client]
              │                                                              ▲
              │                                                              │
              └────────────────── silence signal ───────────────────────────┘
```

## 热词注入流程

```
1. 启动时: HotwordManager.load_from_documents([投资文档路径])
           ↓
2. 解析文档: 提取专业术语、机构名称、股票代码等
           ↓
3. 格式化热词: ["基本面", "市盈率", "半导体", ...]
           ↓
4. 注入ASR: ASRProcessor.set_hotwords(hotwords)
           ↓
5. 识别时: 热词自动提升权重
           ↓
6. 动态更新: 支持运行时添加新热词
```

## 配置文件

```yaml
# config.yaml
server:
  host: "127.0.0.1"
  port: 9876

audio:
  source: "microphone"  # microphone | file | stream
  sample_rate: 16000
  channels: 1

vad:
  enabled: true
  model: "sensevoice"  # sensevoice | fsmn-vad
  threshold: 0.5
  min_speech_duration: 0.3
  max_speech_duration: 30.0
  silence_duration: 2.0  # 停顿换行阈值

asr:
  model: "iic/SenseVoiceSmall"
  device: "cuda"
  hotwords_enabled: true
  hotwords_source: "documents"  # documents | file

punc:
  enabled: true
  model: "ct-punc"
  batch_size: 300

output:
  batch_interval: 0.3  # 秒
  batch_size: 50       # 字符数
  retry_count: 3
  retry_interval: 0.5

hotwords:
  document_paths:
    - "D:/arvin/obsidian_workpace/arvin-notes/00.raw/01.投资研究/"
  extract_patterns:
    - "\\[([^\\]]+)\\]"  # [[术语]]
    - "【([^】]+)】"     # 【术语】
  blacklist:
    - "相关"
    - "等"
```

## 使用方法

### 1. 运行新版服务器

```bash
cd D:\arvin\obsidian_workpace\voice-transcribe
py -3.11 transcribe_server_v2.py
```

### 2. 动态更新热词

```bash
# 连接到服务器
telnet 127.0.0.1 9876

# 发送重载热词命令
hotwords_reload
OK
```

### 3. 替换 VAD 模式

```python
# 在 transcribe_server_v2.py 中修改
config["vad"]["mode"] = "fsmn_vad"  # 或 "simple"

# 或者运行时切换
config["vad"]["mode"] = "simple"  # 无模型，简单能量检测
```

## 分层解耦优势

| 优势       | 说明                          |
| -------- | --------------------------- |
| **独立替换** | 可以单独替换 VAD 或 ASR 模型，无需改动其他层 |
| **热插拔**  | 支持运行时切换音频源 (麦克风 → 文件)       |
| **易于测试** | 每层有独立接口，可单独单元测试             |
| **配置灵活** | 通过配置文件调整各层参数                |
| **扩展性强** | 新增处理器只需实现接口契约               |

## 快速切换示例

### 替换 ASR 模型

```python
from processors import create_asr_processor

# 切换到 Paraformer
asr = create_asr_processor("iic/paraformer-zh")
asr.load_model()
```

### 添加新的标点模型

```python
from interfaces import IPuncProcessor

class MyPuncModel(IPuncProcessor):
    def load_model(self): ...
    def punctuate(self, text): ...

# 注入
server.punc_processor = MyPuncModel()
```

### 自定义音频源

```python
class WebRTCAudioSource(IAudioSource):
    # 实现接口方法
    ...

server.audio_source = WebRTCAudioSource()
```
