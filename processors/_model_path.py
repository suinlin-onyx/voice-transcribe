"""
processors/_model_path.py - 模型路径解析
优先使用本地模型，不存在则使用 ModelScope ID 触发下载
"""
import os

# 本地模型根目录
LOCAL_MODEL_ROOT = "D:/arvin/obsidian_workpace/models"

# 模型 ID 到本地子路径的映射
# 如果本地路径不存在，resolve_model_path 会返回原始 ID，触发 FunASR 下载
MODEL_PATH_MAP = {
    # ASR 模型
    "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch": "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx": "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx",
    # VAD 模型
    "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    # 标点模型
    "iic/punc_ct-transformer_cn-en-common-vocab471067-large": "iic/punc_ct-transformer_cn-en-common-vocab471067-large",
    "ct-punc": "iic/punc_ct-transformer_cn-en-common-vocab471067-large",
    "ct-punc-onnx": "punc_damo/damo/punc_ct-transformer_cn-en-common-vocab471067-large-onnx",
}


def resolve_model_path(model_id: str) -> str:
    """
    解析模型路径。

    如果本地存在对应模型，返回本地路径；否则返回原始 ID 触发下载。

    Args:
        model_id: ModelScope 模型 ID 或本地路径

    Returns:
        本地路径或原始 ID
    """
    if model_id is None:
        return model_id

    # 如果已经是本地路径，直接返回
    if os.path.isabs(model_id) or model_id.startswith("./") or model_id.startswith("../"):
        return model_id

    # 查找映射
    local_subpath = MODEL_PATH_MAP.get(model_id)
    if local_subpath:
        local_path = os.path.join(LOCAL_MODEL_ROOT, local_subpath)
        if os.path.isdir(local_path):
            return local_path

    # 本地不存在，返回原始 ID 让 FunASR 下载
    return model_id
