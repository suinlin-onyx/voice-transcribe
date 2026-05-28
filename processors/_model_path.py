"""
processors/_model_path.py - 模型路径工具
模型路径现在由 config/settings.json 的 models 段集中管理。
此模块保留作为兼容层：绝对路径直接返回，相对路径查找本地模型目录。
"""
import os

LOCAL_MODEL_ROOT = "D:/arvin/obsidian_workpace/models"


def resolve_model_path(model_id: str) -> str:
    """解析模型路径。绝对路径直接返回，否则尝试在本地模型目录查找。"""
    if not model_id:
        return model_id
    if os.path.isabs(model_id) or model_id.startswith("./") or model_id.startswith("../"):
        return model_id

    local_path = os.path.join(LOCAL_MODEL_ROOT, model_id)
    if os.path.isdir(local_path):
        return local_path

    return model_id
