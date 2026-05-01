"""
config.py - 配置文件
"""
import os

# 项目根目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 模型缓存目录
MODEL_CACHE_DIR = os.path.join(SCRIPT_DIR, "models")

# Obsidian vault 路径
OBSIDIAN_VAULT_PATH = os.path.join(
    os.path.dirname(SCRIPT_DIR),
    "arvin-notes"
)

# 默认配置
DEFAULT_CONFIG = {
    "server": {
        "host": "127.0.0.1",
        "port": 9876,
    },
    "audio": {
        "sample_rate": 16000,
        "channels": 1,
        "device": None,  # None = 默认设备
    },
    "vad": {
        "enabled": True,
        "mode": "sensevoice",  # sensevoice | fsmn_vad | simple
        "threshold": 0.5,
        "min_speech_duration": 0.3,
        "max_speech_duration": 5.0,
        "silence_timeout": 4.0,  # 停顿换行阈值 (ASR需要约2s最小音频)
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
        "retry_count": 3,
    },
    "hotwords": {
        "load_from_notes": True,
        "notes_path": os.path.join(OBSIDIAN_VAULT_PATH, "00.raw", "01.投资研究"),
        "hotwords_dir": os.path.join(SCRIPT_DIR, "hotwords"),  # 热词文件目录
        "save_path": os.path.join(SCRIPT_DIR, "hotwords", "extracted.txt"),  # 提取后保存路径
    },
}


def load_config(config_path: str = None) -> dict:
    """加载配置"""
    if config_path and os.path.exists(config_path):
        import json
        with open(config_path, 'r', encoding='utf-8') as f:
            user_config = json.load(f)
        # 合并配置
        config = DEFAULT_CONFIG.copy()
        for section, values in user_config.items():
            if section in config:
                config[section].update(values)
        return config
    return DEFAULT_CONFIG.copy()


def save_config(config: dict, config_path: str) -> None:
    """保存配置"""
    import json
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
