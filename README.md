# FunASR 转录服务

基于分层架构的实时语音转文字系统，支持热词注入、标点恢复、VAD 检测。

## 快速开始

```bash
cd D:\arvin\obsidian_workpace\voice-transcribe
py -3.11 run.py
```

**默认端口：**
- WebSocket: `ws://127.0.0.1:9876`
- HTTP: `http://127.0.0.1:9877`

## 服务控制

```bash
# 健康检查
curl http://127.0.0.1:9877/health

# 开始转写
curl -X POST http://127.0.0.1:9877/control/start_recording

# 停止转写
curl -X POST http://127.0.0.1:9877/control/stop_recording
```

## 文档

- [统一协议文档](./docs/PROTOCOL.md) - WebSocket/HTTP 接口定义
- [架构设计](./docs/ARCHITECTURE_V4.md) - 分层架构说明
- [任务清单](./docs/TASKS.md) - 待优化功能列表
