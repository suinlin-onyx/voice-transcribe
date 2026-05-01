# VAD 非语音过滤问题

## 问题描述

当前转录服务会将非语音内容（如键盘打字声、咳嗽、清嗓子等）错误识别为语音并输出：

```
。
 I.
。
 How.
。
 I.
```

## 原因分析

1. **VAD 阈值过低** - 将噪声识别为语音
2. **无置信度过滤** - 低质量识别结果直接输出
3. **无内容过滤** - 单字/无意义内容未过滤

## SenseVoice 参数

```python
# 当前调用
result = asr.generate(
    input=chunk,
    batch_size_s=300,
    merge_vad=True
)

# 可尝试的参数
result = asr.generate(
    input=chunk,
    batch_size_s=300,
    merge_vad=True,
    vad_filter=True,           # 开启 VAD 过滤
    vad_threshold=0.5,          # 调整检测阈值 (0-1)
    sentence_level_vad=True,   # 句子级 VAD
)
```

## 解决方案

### 方案 1：调整 VAD 参数

```python
# 在 asr_processor.py 中修改
def recognize(self, segment):
    result = self.model.generate(
        input=segment.audio,
        batch_size_s=300,
        hotwords=self._hotwords if self._hotwords else None,
        # 添加 VAD 过滤参数
        vad_filter=True,
        # 或者
        sentence_level_vad=True,
    )
```

### 方案 2：后处理过滤 ✅ 已实现

已在 `processors/asr_processor.py` 中实现：

```python
def filter_noise(self, text: str) -> str:
    """过滤非语音内容"""
    if not text:
        return ""

    # 过滤纯标点符号
    if re.match(r'^[\.\,\!\?\。\，\！\？\s]+$', text):
        return ""

    # 过滤单字（中文单字或英文单词太短）
    if len(text.strip()) <= 1:
        return ""

    # 过滤无意义内容（全是字母且太短）
    if re.match(r'^[a-zA-Z\s]+$', text) and len(text.strip()) < 3:
        return ""

    # 过滤纯数字
    if re.match(r'^[\d\s\.\,\-]+$', text.strip()):
        return ""

    return text
```

### 方案 3：音频级过滤

在 VAD 层检测音频能量，过滤静音/噪声段：

```python
def filter_low_energy(self, audio: np.ndarray, threshold: float = 0.01) -> bool:
    """判断音频是否为有效语音"""
    energy = np.sqrt(np.mean(audio ** 2))
    return energy > threshold
```

## 待验证

需要测试的参数组合：

| 参数 | 说明 | 待验证 |
|-----|------|-------|
| `vad_filter` | VAD 过滤 | 是 |
| `vad_threshold` | 检测阈值 | 0.3-0.7 |
| `sentence_level_vad` | 句子级 VAD | 是 |
| 能量阈值 | 音频能量过滤 | 0.01-0.05 |

## 参考

- FunASR 文档
- SenseVoice 模型参数
