"""RobotRuntime adapter protocols and implementations."""

from .avatar3d import Avatar3DAdapter
from .base import AdapterContext, RobotAdapter
from .factory import AdapterBuildResult, adapter_config_options, build_profile_adapters
from .live2d import Live2DAdapter
from .mujoco import MujocoAdapter
from .reachy import ReachyAdapter
from .ros2 import ROS2Adapter

__all__ = [
    "AdapterContext",
    "AdapterBuildResult",
    "Avatar3DAdapter",
    "Live2DAdapter",
    "MujocoAdapter",
    "ReachyAdapter",
    "ROS2Adapter",
    "RobotAdapter",
    "adapter_config_options",
    "build_profile_adapters",
]
