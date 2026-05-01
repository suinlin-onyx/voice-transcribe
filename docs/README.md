# FunASR 转录服务开发文档

基于分层架构的实时语音转文字系统，支持热词注入、标点恢复、VAD 检测。

## 目录

1. [概述](#概述)
2. [快速开始](#快速开始)
3. [架构设计](#架构设计)
4. [线程模型对比](#线程模型对比)
5. [接口定义](#接口定义)
6. [处理器实现](#处理器实现)
7. [配置参考](#配置参考)
8. [热词管理](#热词管理)
9. [协议规范](#协议规范)
10. [故障排除](#故障排除)
11. [性能优化](#性能优化)

---

## 概述

### 功能特性

| 功能     | 说明                    |
| ------ | --------------------- |
| 实时转写   | 麦克风输入，即时输出文字，延迟 1-3 秒 |
| 热词注入   | 从 Obsidian 文档自动提取专业术语 |
| 自动标点   | ct-punc 模型自动添加标点符号    |
| VAD 检测 | 智能断句，2秒静音自动换行         |
| 分层架构   | 模块解耦，可替换/升级           |
| 异步架构   | asyncio + 线程池，高并发稳定   |

### 系统要求

- **Python**: 3.9+
- **CUDA**: 12.x + cuDNN
- **GPU**: RTX 4070 Ti Super 16GB (已验证)
- **系统**: Windows 10/11

---

## 快速开始

### 1. 安装依赖

```bash
cd D:\arvin\obsidian_workpace\voice-transcribe
pip install -r requirements.txt
```

`requirements.txt`:

```
funasr>=1.0
sounddevice>=0.4
numpy>=1.20
```

### 2. 启动服务

```bash
# 推荐: asyncio 架构 (v3)
py -3.11 transcribe_server_v3.py

# 或使用启动脚本
py -3.11 run.py

# 也可使用 threading 架构 (v2)
py -3.11 transcribe_server_v2.py
```

输出:

```
==================================================
FunASR Transcription Server v3.0 (asyncio)
==================================================
Loading models...
ASR model loaded
Punctuation model loaded
Loaded 256 hotwords
Server running on 127.0.0.1:9876
```

### 3. 客户端连接

Obsidian 插件会自动连接，也可手动测试:

```bash
telnet 127.0.0.1 9876
```

---

## 架构设计

### 项目结构

```
voice-transcribe/
├── interfaces/                    # 接口定义层
│   ├── __init__.py               # 接口基类
│   └── socket_transport.py       # Socket 通信
├── processors/                    # 处理器层 (可独立替换)
│   ├── audio_source.py           # 音频采集
│   ├── vad_processor.py          # VAD 检测
│   ├── asr_processor.py          # ASR 识别
│   ├── punc_processor.py          # 标点恢复
│   ├── hotword_manager.py        # 热词管理
│   └── output_buffer.py          # 输出缓冲
├── hotwords/                      # 热词文件目录
│   ├── investment.txt            # 投资术语 (手动编辑)
│   ├── custom.txt               # 自定义热词
│   └── extracted.txt            # 自动提取的热词
├── config.py                     # 配置文件
├── run.py                        # 启动脚本
├── test_pipeline.py              # 测试脚本
├── transcribe_server_v3.py       # asyncio 架构 (推荐)
├── transcribe_server_v2.py       # threading 架构
├── transcribe_server.py          # 原版单线程服务器
├── README.md                     # 开发文档
└── README_ARCHITECTURE.md        # 架构文档
```

### v3 asyncio 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    asyncio Event Loop (主线程)                      │
├─────────────────────────────────────────────────────────────────┤
│  _socket_server() ────→ _handle_client() (异步 I/O)               │
│  _audio_loop() ──────→ VAD ──────→ _asr_queue                   │
│  _asr_worker() ──────→ ThreadPool: ASR ──→ _punc_queue          │
│  _punc_worker() ────→ ThreadPool: Punc ──→ _send_to_client      │
└─────────────────────────────────────────────────────────────────┘
                              ↕
              ┌─────────────────────────────────┐
              │     ThreadPoolExecutor         │
              │  ┌─────────┐ ┌─────────┐       │
              │  │  ASR-1  │ │  ASR-2  │       │
              │  └─────────┘ └─────────┘       │
              │  ┌─────────┐ ┌─────────┐       │
              │  │ Punc-1  │ │ Punc-2  │       │
              │  └─────────┘ └─────────┘       │
              └─────────────────────────────────┘
```

### 分层架构

```
┌─────────────────────────────────────────────────────────┐
│                    TranscribeServer                      │
│                      (协调层)                           │
└─────────────────────────────────────────────────────────┘
                          │
    ┌─────────┬───────────┼───────────┬─────────┐
    │         │           │           │         │
    ▼         ▼           ▼           ▼         ▼
┌───────┐ ┌───────┐ ┌───────────┐ ┌─────────┐ ┌───────────┐
│Audio  │ │  VAD  │ │   ASR     │ │  Punc   │ │  Buffer   │
│Source │ │Processor│ │Processor  │ │Processor│ │           │
└───────┘ └───────┘ └───────────┘ └─────────┘ └───────────┘
```

### 数据流

```
麦克风 → AudioSource → VADProcessor → ASRProcessor → PuncProcessor → OutputBuffer → Socket → 客户端
                              ↑
                         HotwordManager
```

### 模块说明

| 模块             | 职责     | 可替换性                       |
| -------------- | ------ | -------------------------- |
| AudioSource    | 音频采集   | 可替换为文件源/WebRTC             |
| VADProcessor   | 语音活动检测 | 可切换 sensevoice/fsmn/simple |
| ASRProcessor   | 语音识别   | 可切换 SenseVoice/Paraformer  |
| PuncProcessor  | 标点恢复   | 可替换其他标点模型                  |
| HotwordManager | 热词管理   | 可扩展数据源                     |
| OutputBuffer   | 输出缓冲   | 可实现重传机制                    |

---

## 线程模型对比

### 版本对比

| 版本     | 架构                       | 稳定性    | 性能    | 推荐场景     |
| ------ | ------------------------ | ------ | ----- | -------- |
| v1     | 单线程 + 同步调用               | 一般     | 较低    | 测试/原型    |
| v2     | threading + 队列           | 良好     | 中等    | 通用场景     |
| **v3** | **asyncio + ThreadPool** | **优秀** | **高** | **生产环境** |

### v1: 单线程同步

```python
# 问题：所有操作串行，阻塞主循环
while True:
    audio = record()      # 阻塞
    result = asr(audio)   # 阻塞 100-500ms
    send(result)          # 阻塞
```

### v2: threading 队列

```python
# 问题：多线程管理复杂，队列同步复杂
audio_thread = Thread(target=audio_loop)
asr_thread = Thread(target=asr_loop)
audio_queue = Queue()
asr_queue = Queue()
# 线程间同步、锁、死锁风险
```

### v3: asyncio + ThreadPool (推荐)

```python
# 优势：
# - 单线程事件循环，无 GIL 问题
# - 线程池管理 ASR/Punc，自动复用
# - asyncio.Queue 自动背压控制
# - 异常隔离，不会级联崩溃
# - 高并发，可处理多客户端

async def run(self):
    # 异步任务
    await asyncio.gather(
        self._socket_server(),
        self._audio_loop(),
        self._asr_worker(),
        self._punc_worker(),
    )

async def _asr_worker(self):
    loop = asyncio.get_event_loop()
    while self._running:
        segment = await self._asr_queue.get()
        # 在线程池执行，不阻塞事件循环
        result = await loop.run_in_executor(self._executor, self._asr_recognize, segment)
```

### 关键设计

#### 1. 背压控制

```python
# 队列满时丢弃最旧的，防止内存溢出
if self._asr_queue.full():
    try:
        self._asr_queue.get_nowait()  # 丢弃
    except asyncio.QueueEmpty:
        pass
await self._asr_queue.put(segment)
```

#### 2. 超时机制

```python
# 所有队列操作带超时，防止永久阻塞
await asyncio.wait_for(self._asr_queue.get(), timeout=0.1)
```

#### 3. 异常隔离

```python
# 任务级异常捕获，不影响其他任务
try:
    result = await loop.run_in_executor(...)
except Exception as e:
    print(f"ASR error: {e}")  # 不影响其他任务
```

#### 4. 优雅关闭

```python
async def shutdown(self):
    self._running = False
    for task in self._tasks:
        task.cancel()
    await asyncio.gather(*self._tasks, return_exceptions=True)
    self._executor.shutdown(wait=True)
```

---

## 接口定义

### IAudioSource

```python
from interfaces import IAudioSource, AudioChunk

class MicrophoneSource(IAudioSource):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def is_active(self) -> bool: ...
    def on_audio(self, callback: Callable[[AudioChunk], None]) -> None: ...
```

### 数据结构

```python
@dataclass
class AudioChunk:
    data: np.ndarray      # float32, shape (samples,)
    sample_rate: int      # 采样率 (默认 16000)
    timestamp: float      # 相对时间戳

@dataclass
class SpeechSegment:
    audio: np.ndarray
    start_time: float
    end_time: float
    is_final: bool

@dataclass
class TextResult:
    text: str
    timestamp: float
    confidence: float = 1.0
```

---

## 处理器实现

### AudioSource

```python
from processors.audio_source import MicrophoneSource

source = MicrophoneSource(
    sample_rate=16000,
    channels=1,
    device=None,  # None = 默认设备
    chunk_duration=0.1,  # 100ms 每块
)

def on_audio(chunk: AudioChunk):
    print(f"Got {len(chunk.data)} samples")

source.on_audio(on_audio)
source.start()

# 使用完毕后
source.stop()
```

**设备选择**:

```python
import sounddevice as sd
print(sd.query_devices())  # 列出所有设备

source = MicrophoneSource(device=2)  # 使用设备 2
```

### VADProcessor

```python
from processors.vad_processor import create_vad_processor, VADConfig

config = VADConfig(
    mode="sensevoice",           # sensevoice | fsmn_vad | simple
    threshold=0.5,
    min_speech_duration=0.3,
    max_speech_duration=30.0,
    silence_timeout=2.0,         # 停顿换行阈值
)

vad = create_vad_processor("sensevoice", asr_model=asr_model, config=config)

# 处理音频
segments = vad.process(audio_chunk.data)
```

**VAD 模式对比**:

| 模式         | 精度  | 延迟  | 资源占用 | 适用场景   |
| ---------- | --- | --- | ---- | ------ |
| sensevoice | 高   | 低   | GPU  | 生产环境   |
| fsmn_vad   | 高   | 中   | GPU  | 独立 VAD |
| simple     | 中   | 极低  | CPU  | 备用/测试  |

### ASRProcessor

```python
from processors.asr_processor import create_asr_processor

asr = create_asr_processor("iic/SenseVoiceSmall", device="cuda")
asr.load_model()

# 设置热词
asr.set_hotwords([
    "基本面", "市盈率", "半导体",
    "困境反转", "护城河"
])

# 识别
result = asr.recognize(segment)
print(result.text)
```

### PuncProcessor

```python
from processors.punc_processor import create_punc_processor

punc = create_punc_processor("ct-punc", enabled=True)
punc.load_model()

# 添加标点
result = punc.punctuate("今天上证指数涨了5个点")
# -> "今天上证指数涨了5个点。"
```

---

## 配置参考

### 完整配置

```python
config = {
    "server": {
        "host": "127.0.0.1",
        "port": 9876,
    },
    "audio": {
        "sample_rate": 16000,
        "channels": 1,
        "device": None,
    },
    "vad": {
        "enabled": True,
        "mode": "sensevoice",
        "threshold": 0.5,
        "min_speech_duration": 0.3,
        "max_speech_duration": 30.0,
        "silence_timeout": 2.0,
    },
    "asr": {
        "model": "iic/SenseVoiceSmall",
        "device": "cuda",
        "hotwords_enabled": True,
    },
    "punc": {
        "enabled": True,
        "model": "ct-punc",
    },
    "output": {
        "batch_interval": 0.3,
        "batch_size": 50,
    },
    "hotwords": {
        "load_from_notes": True,
        "notes_path": "D:/arvin/obsidian_workpace/arvin-notes/00.raw/01.投资研究/",
        "hotwords_dir": "hotwords/",
        "save_path": "hotwords/extracted.txt",
    },
}

server = TranscribeServerV2(config)
server.run()
```

### 配置参数说明

#### server

| 参数   | 类型     | 默认值         | 说明   |
| ---- | ------ | ----------- | ---- |
| host | string | "127.0.0.1" | 监听地址 |
| port | int    | 9876        | 监听端口 |

#### vad

| 参数                  | 类型     | 默认值          | 说明         |
| ------------------- | ------ | ------------ | ---------- |
| mode                | string | "sensevoice" | VAD 模式     |
| threshold           | float  | 0.5          | 检测阈值 (0-1) |
| min_speech_duration | float  | 0.3          | 最小语音时长 (秒) |
| silence_timeout     | float  | 2.0          | 静音超时 (秒)   |

#### asr

| 参数     | 类型     | 默认值                   | 说明     |
| ------ | ------ | --------------------- | ------ |
| model  | string | "iic/SenseVoiceSmall" | ASR 模型 |
| device | string | "cuda"                | 设备类型   |

---

## 热词管理

### 热词目录结构

```
hotwords/
├── investment.txt    # 投资术语 (预置，可手动编辑)
├── custom.txt        # 自定义热词 (用户添加)
└── extracted.txt     # 从文档自动提取 (自动生成)
```

### 热词文件格式

每行一个词，支持中文和英文：

```
基本面
市盈率
半导体
护城河
困境反转
AI
```

### HotwordManager API

```python
from processors.hotword_manager import HotwordManager

hw = HotwordManager()

# 从热词目录加载 (investment.txt + custom.txt)
hw.load_from_hotwords_dir("hotwords/")

# 从 Obsidian 文档提取热词
hw.load_from_investment_notes("D:/vault/00.raw/01.投资研究/")

# 手动添加/删除
hw.add_hotword("人形机器人")
hw.remove_hotword("某个词")

# 保存热词到文件
hw.save_to_file("hotwords/extracted.txt")

# 获取热词列表
hotwords = hw.get_hotwords()
print(f"共 {len(hotwords)} 个热词")

# 注入 ASR
asr.set_hotwords(hotwords)
```

### 配置中的热词设置

```python
config = {
    "hotwords": {
        "load_from_notes": True,
        "notes_path": "D:/arvin/obsidian_workpace/arvin-notes/00.raw/01.投资研究/",
        "hotwords_dir": "hotwords/",           # 热词文件目录
        "save_path": "hotwords/extracted.txt", # 提取后保存路径
    },
}
```

### 热词提取规则

| 模式           | 示例                   | 提取结果       |
| ------------ | -------------------- | ---------- |
| `[[术语]]`     | `[[半导体]]`            | 半导体        |
| `[[显示\|链接]]` | `[[SenseVoice\|模型]]` | SenseVoice |
| `【术语】`       | `【困境反转】`             | 困境反转       |
| `**术语**`     | `**护城河**`            | 护城河        |

### 动态重载热词

```bash
# 连接到服务器
telnet 127.0.0.1 9876

# 重载热词
hotwords_reload
OK
```

| 模式           | 示例                   | 提取结果       |
| ------------ | -------------------- | ---------- |
| `[[术语]]`     | `[[半导体]]`            | 半导体        |
| `[[显示\|链接]]` | `[[SenseVoice\|模型]]` | SenseVoice |
| `【术语】`       | `【困境反转】`             | 困境反转       |
| `**术语**`     | `**护城河**`            | 护城河        |

---

## 协议规范

### 命令协议

| 命令                | 说明      |
| ----------------- | ------- |
| `start`           | 开始转写    |
| `stop`            | 停止转写    |
| `quit`            | 断开连接    |
| `status`          | 查询状态    |
| `hotwords_reload` | 重载热词    |
| `punc_on/off`     | 启用/禁用标点 |

### 响应格式

```
OK                      # 命令成功
OK: status transcribing # 状态查询
ERROR: message          # 错误
```

---

## 故障排除

### 模型加载失败

```bash
pip install funasr
```

### GPU 不可用

```python
config["asr"]["device"] = "cpu"
```

### 端口被占用

```bash
netstat -ano | findstr :9876
taskkill /PID <pid> /F
```

### 音频设备问题

```python
import sounddevice as sd
print(sd.query_devices())  # 列出设备

config["audio"]["device"] = 1  # 指定设备
```

### 非语音内容过滤

键盘声、打字声、咳嗽等被误识别为语音。

**原因**：VAD 阈值过低，无置信度过滤

**解决方案**：

1. 调整 VAD 参数（在 `asr_processor.py`）：
   
   ```python
   result = self.model.generate(
    input=segment.audio,
    batch_size_s=300,
    merge_vad=True,
    vad_filter=True,           # 开启 VAD 过滤
    vad_threshold=0.6,          # 提高阈值 (0-1)
   )
   ```

2. 后处理过滤单字/无意义内容：
   
   ```python
   def filter_noise(text: str) -> str:
    """过滤非语音内容"""
    import re
    # 过滤单字
    if len(text.strip()) <= 1:
        return ""
    # 过滤纯标点
    if re.match(r'^[\.\,\!\?\。\，\！\？]+$', text.strip()):
        return ""
    return text
   ```

详细文档：`docs/VAD_FILTER_NOTES.md`

---

## 性能优化

### GPU 优化

```python
config["asr"]["device"] = "cuda"
```

### 批处理优化

```python
config["output"]["batch_interval"] = 0.1  # 减少延迟
config["output"]["batch_size"] = 100      # 增大批量
```

### VAD 优化

```python
config["vad"]["mode"] = "simple"  # 使用简单 VAD (CPU)
config["vad"]["threshold"] = 0.3  # 更敏感
```
