# 当前任务清单

> 更新日期: 2026/05/01

---

## 一、已修复问题

| # | 问题 | 状态 | 解决方案 |
|---|------|------|----------|
| 1 | VAD 使用 wall-clock 而非音频位置 | ✅ 已修复 | 使用 sample position 追踪 |
| 2 | silence_timeout 太短 (2s→4s) | ✅ 已修复 | 改为 4s 给 ASR 足够音频 |
| 3 | max_speech_duration 太长 (30s→5s) | ✅ 已修复 | 改为 5s 分段输出 |
| 4 | 模型加载无进度提示 | ✅ 已修复 | 每 5s 打印 "Still loading..." |
| 5 | START 命令报错信息不明确 | ✅ 已修复 | 显示加载秒数 |
| 6 | 第一行输出有前导换行 | ✅ 已修复 | 去掉首行前的 \n |
| 7 | load_start 未定义错误 | ✅ 已修复 | 改用 self._load_start |
| 8 | VAD float32 类型错误 | ✅ 已修复 | 添加类型转换 |

---

## 二、已实现功能 (V4)

| # | 功能 | 状态 | 说明 |
|---|------|------|------|
| 1 | WebSocket 接口 | ✅ 已完成 | 端口 9876，协议对齐客户端 |
| 2 | HTTP 接口 | ✅ 已完成 | 端口 9877，健康检查+控制 |
| 3 | 状态主动推送 | ✅ 已完成 | idle/recognizing/no_audio |
| 4 | TextProcessor 热重启 | ✅ 已完成 | reload_text_processor |
| 5 | 热词重载 | ✅ 已完成 | hotwords_reload |
| 6 | 三层分离架构 | ✅ 已完成 | API Server + Model Server |
| 7 | 服务端单例 | ✅ 已完成 | Mutex (Win) / flock (Unix) |

---

## 三、待修复/改进

| # | 问题 | 优先级 | 状态 | 说明 |
|---|------|--------|------|------|
| 1 | 多音频源支持 | P1 | 代码已添加，待测试 | 同时监听麦克风+声卡 |
| 2 | VAD Errno 22 错误 | P1 | 已加类型转换，待验证 | 偶发，可能内存/缓冲问题 |
| 3 | 数字识别优化 | P2 | 待优化 | 如"6.3""7.2"被识别为中文，需保留阿拉伯数字 |
| 4 | 内存占用优化 | P1 | 待分析 | 服务占用2G+内存，分析优化空间 |
| 5 | 英文缩写多余空格 | P2 | 待优化 | 识别"ppt""pdf""gpt"时字母间有空格，需去除 |
| 6 | Paraformer 替代测试 | P1 | 待测试 | Paraformer内置标点，可能替代SenseVoice+PUNC |

---

## 四、潜在需求（待评估）

| # | 需求 | 背景 | 技术要点 | 状态 | 备注 |
|---|------|------|----------|------|------|
| 1 | **边转录边录音** | 用户希望录音同时获得：1) 实时转录文本 2) 原始音频文件 | MediaRecorder API 复用 MediaStream；定期落盘防内存溢出 | 🔮 调研中 | 详见 [[#4-1-边转录边录音]] |

### 4-1 边转录边录音

**用户场景**
- 会议记录：转录完成后，希望保留原始录音以便复核
- 学习笔记：转录课堂内容，同时保存录音用于复习
- 投资记录：转录钱总建议，同时保留原始音频

**技术方案**

```
麦克风 → MediaStream (同一来源)
         ├── 分支1: MediaRecorder → 保存 .webm 录音文件
         └── 分支2: FunASR → 实时转录
```

**内存压力分析**

| 时长 | WebM/Opus 压缩后 | 内存占用（定期落盘） |
|------|-----------------|-------------------|
| 10 分钟 | ~10 MB | ~10-20 MB ✅ |
| 1 小时 | ~60 MB | ~10-20 MB ✅ |
| 10 小时 | ~600 MB | ~10-20 MB ✅ |

**关键实现点**

1. **定期落盘**（防止内存溢出）
   ```javascript
   this.mediaRecorder.start(1000);  // 每秒触发一次
   this.mediaRecorder.ondataavailable = async (e) => {
     await this.appendToTempFile(e.data);  // 立即写入磁盘
   };
   ```

2. **流复用**（不需要两个麦克风）
   - 同一 MediaStream 可被多处使用
   - MediaRecorder 和 FunASR 共享同一个流

3. **文件输出**
   - 格式: WebM/Opus (高压缩率)
   - 路径: `attachments/录音_YYYY-MM-DD_HH-mm-ss.webm`
   - 嵌入: `![[录音_xxx.webm]]`

**实现难度**: ⭐⭐ 中等

**待确认**
- [ ] 是否需要与 Obsidian 核心录音机插件对齐？
- [ ] 录音文件是否需要单独管理（与转录分离）？
- [ ] 是否支持录音暂停/继续？

---

## 五、日常使用任务

### 启动服务
```bash
py -3.11 run.py
```

### WebSocket 测试
```python
import asyncio
import websockets
import json

async def test():
    async with websockets.connect("ws://127.0.0.1:9876") as ws:
        # 接收连接状态
        print(await ws.recv())

        # 开始识别
        await ws.send(json.dumps({
            "id": "test-1",
            "type": "state_update",
            "action": "start_recording",
            "payload": {},
            "timestamp": 1234567890000
        }))
        print(await ws.recv())

asyncio.run(test())
```

### HTTP 测试
```bash
curl http://127.0.0.1:9877/health
curl -X POST http://127.0.0.1:9877/control/start_recording
```

### 查看日志
```bash
# 最新日志
ls -t logs/api_server*.log | head -1 | xargs cat
```

---

## 六、已知限制

1. **VAD Errno 22** - 偶发，可能是内存/缓冲问题
2. **多设备** - MultiSource 已添加但未充分测试
3. **长音频** - 超过 5 秒会被分段输出
